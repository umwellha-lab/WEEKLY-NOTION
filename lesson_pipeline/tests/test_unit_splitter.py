import sys
import tempfile
import unittest
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unit_splitter import extract_units


class TestUnitSplitter(unittest.TestCase):
    def fixture(self, directory, pages):
        src = Path(directory) / "textbook.pdf"
        doc = fitz.open()
        for line in pages:
            page = doc.new_page()
            if line:
                page.insert_text((65, 75), line, fontsize=12)
        doc.save(src)
        return src

    def test_two_units_running_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self.fixture(tmp, [
                "Contents\nUnit 1\nUnit 2",
                "UNIT 01\nThe History of Art: Passage one and vocabulary",
                "UNIT 01\nReading passage continued with some more words",
                "UNIT 02\nWhy Water Freezes: A clear passage and test",
                "UNIT 02\nMore reading content with several sentences",
            ])
            out = Path(tmp) / "out"
            m = extract_units(pdf, out)
            self.assertFalse(m["requires_review"])
            self.assertEqual([(x["start_page"], x["end_page"]) for x in m["units"]], [(2, 3), (4, 5)])
            self.assertEqual(m["unassigned_pages"], [1])
            self.assertTrue((out / "Unit_01" / "Unit_01_원본.pdf").exists())
            self.assertTrue((out / "분할제외페이지_원본보관.pdf").exists())

    def test_scanned_pdf_stops_in_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self.fixture(tmp, ["", "", ""])
            out = Path(tmp) / "out"
            m = extract_units(pdf, out)
            self.assertTrue(m["requires_review"])
            self.assertEqual(m["units"], [])
            self.assertFalse((out / "Unit_01").exists())

    def test_nonconsecutive_blocks_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = self.fixture(tmp, [
                "UNIT 1\nUnit one reading passage and grammar",
                "UNIT 3\nUnit three reading passage and grammar",
            ])
            out = Path(tmp) / "out"
            m = extract_units(pdf, out)
            self.assertTrue(m["requires_review"])
            self.assertIn("strictly consecutive", " ".join(m["warnings"]))
            self.assertFalse((out / "Unit_01").exists())


if __name__ == "__main__":
    unittest.main()
