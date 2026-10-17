#!/usr/bin/env python3
"""Conservatively split a textbook PDF into Unit PDFs without rewriting any page.

This module NEVER infers missing boundaries. Ambiguities halt processing.
Workflow must treat requires_review as a hard stop for downstream creation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
import fitz

UNIT_RX = re.compile(r"(?im)^\s*(?:part\s+\d+[\s:–-]+)?(?:unit|u\.)\s*0*(\d{1,3})(?:\b|\s*[:.\-–])")
TOC_RX = re.compile(r"(?im)\b(?:table\s+of\s+contents|contents|index\s+of\s+units)\b|목\s*차")
END_RX = re.compile(r"(?im)^\s*(?:answer\s+key|appendix|glossary|index)\s*$|^\s*(?:정답|부록|색인)\s*$")


@dataclass(frozen=True)
class UnitSpan:
    unit: int
    start_page: int  # first PDF page, 1-based
    end_page: int    # inclusive, 1-based

    @property
    def pages(self) -> int:
        return self.end_page - self.start_page + 1


def heading_on_page(page: fitz.Page) -> int | None:
    """Return unique visible top-of-page Unit number or None."""
    cutoff = page.rect.height * 0.50
    blocks = page.get_text("blocks")
    head = "\n".join(str(b[4]) for b in blocks if len(b) >= 5 and b[1] <= cutoff)
    if TOC_RX.search(head):
        return None
    matches = {int(m.group(1)) for m in UNIT_RX.finditer(head)}
    if len(matches) != 1:
        return None
    return next(iter(matches))


def find_spans(doc: fitz.Document) -> tuple[list[UnitSpan], list[str], list[int]]:
    count = doc.page_count
    warnings: list[str] = []
    if count == 0:
        return [], ["Empty PDF"], []
    markers = [(i + 1, heading_on_page(doc[i])) for i in range(count)]
    markers = [(page, unit) for page, unit in markers if unit is not None]
    if not markers:
        return [], ["No verifiable Unit headings in extractable page text"], list(range(1, count + 1))
    starts: list[tuple[int, int]] = []
    last = None
    for page, unit in markers:
        if unit != last:
            starts.append((page, unit))
            last = unit
    nums = [n for _, n in starts]
    if len(set(nums)) != len(nums):
        warnings.append("A Unit number reappears after a different Unit; ambiguous content order")
    if len(starts) < 2:
        warnings.append("Only one distinct Unit heading; unable to validate an entire textbook's boundaries")
    if any(b != a + 1 for a, b in zip(nums, nums[1:])):
        warnings.append("Unit numbering is not strictly consecutive")
    for page_num, _ in starts:
        if len(doc[page_num - 1].get_text().strip()) < 25:
            warnings.append(f"Page {page_num} has too little extractable text for boundary validation")
    last_end = count
    for i in range(starts[-1][0], count):
        lines = doc[i].get_text().splitlines()[:6]
        if any(END_RX.match(line.strip()) for line in lines):
            last_end = i
            break
    spans = [UnitSpan(unit=n, start_page=s,
                      end_page=(starts[i+1][0] - 1 if i+1 < len(starts) else last_end))
             for i, (s, n) in enumerate(starts)]
    if any(x.pages < 1 for x in spans):
        warnings.append("At least one Unit has zero pages")
    covered = {p for s in spans for p in range(s.start_page, s.end_page + 1)}
    unassigned = sorted(set(range(1, count + 1)) - covered)
    return spans, warnings, unassigned


def extract_units(input_file: str | Path, output_dir: str | Path, *, review_override: bool = False) -> dict:
    source = Path(input_file)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with fitz.open(source) as doc:
        spans, warnings, unassigned = find_spans(doc)
        manifest = {
            "source": source.name,
            "total_pages": doc.page_count,
            "units": [asdict(s) | {"pages": s.pages} for s in spans],
            "unassigned_pages": unassigned,
            "warnings": warnings,
            "requires_review": bool(warnings),
            "original_untouched": True,
            "status": "review_required" if warnings else "ready",
        }
        (out / "unit_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if warnings and not review_override:
            return manifest
        for span in spans:
            sub = out / f"Unit_{span.unit:02d}"
            sub.mkdir(exist_ok=True)
            unit = fitz.open()
            unit.insert_pdf(doc, from_page=span.start_page - 1, to_page=span.end_page - 1)
            dest = sub / f"Unit_{span.unit:02d}_원본.pdf"
            unit.save(dest, garbage=3, deflate=True)
            with fitz.open(dest) as check:
                if check.page_count != span.pages:
                    raise RuntimeError(f"Page-count verification failed: {dest}")
        if unassigned:
            extra = fitz.open()
            for page in unassigned:
                extra.insert_pdf(doc, from_page=page - 1, to_page=page - 1)
            extra.save(out / "분할제외페이지_원본보관.pdf", garbage=3, deflate=True)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--review-override", action="store_true", help="Explicitly allow unverified unit boundaries")
    args = parser.parse_args()
    manifest = extract_units(args.pdf, args.output, review_override=args.review_override)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 2 if manifest["requires_review"] and not args.review_override else 0


if __name__ == "__main__":
    sys.exit(main())
