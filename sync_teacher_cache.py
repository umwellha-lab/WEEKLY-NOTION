#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion -> Supabase teacher daily cache sync.

- Reads today's timetable and daily-management rows from Notion.
- Groups them by teacher.
- Sends one cache payload per teacher to a Supabase Edge Function.
- GitHub Actions authenticates to the Edge Function with GitHub OIDC,
  so no Supabase secret is stored in GitHub.

Required env:
  NOTION_TOKEN
  ACTIONS_ID_TOKEN_REQUEST_URL
  ACTIONS_ID_TOKEN_REQUEST_TOKEN

Optional env:
  TARGET_DATE=YYYY-MM-DD
"""

import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
NOTION_VERSION = "2025-09-03"
NOTION_API = "https://api.notion.com/v1"

TIMETABLE_DB_ID = "b21fa408-2f04-82b5-a2df-879caf869d71"
DAILY_DB_ID = "39ffa408-2f04-8063-a428-000ba691f9fd"
TEACHER_DB_ID = "8116e2aa-5ab7-467a-adb0-a36a63fb81fd"
CLASS_DB_ID = "39ffa408-2f04-8037-84da-000b6396f76a"
STUDENT_DB_ID = "6f6fa408-2f04-82b3-b538-87a9cefe649e"
BOOK_DB_ID = "3a0fa408-2f04-80ce-8483-000b410a202c"

EDGE_URL = (
    "https://axwxxqxmozxluduonzql.supabase.co"
    "/functions/v1/teacher-cache-ingest"
)
OIDC_AUDIENCE = "supabase-cache-ingest"

KST = ZoneInfo("Asia/Seoul")


def headers():
    if not NOTION_TOKEN:
        raise RuntimeError("NOTION_TOKEN is missing")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_request(method, path, body=None, retries=5):
    url = f"{NOTION_API}{path}"
    for attempt in range(retries):
        r = requests.request(
            method,
            url,
            headers=headers(),
            json=body,
            timeout=40,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "1")) + 1
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            raise RuntimeError(
                f"Notion API {r.status_code} {path}: {r.text[:1000]}"
            )
        time.sleep(0.35)
        return r.json()
    raise RuntimeError(f"Notion API retries exceeded: {path}")


def query_db(db_id, filter_obj=None):
    out = []
    body = {"page_size": 100}
    if filter_obj:
        body["filter"] = filter_obj

    cursor = None
    seen = set()
    while True:
        req = dict(body)
        if cursor:
            req["start_cursor"] = cursor
        data = notion_request(
            "POST",
            f"/data_sources/{db_id}/query",
            req,
        )
        out.extend(row for row in data["results"] if not row.get("archived") and not row.get("in_trash"))
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")
        if not cursor or cursor in seen:
            raise RuntimeError("Notion pagination did not advance")
        seen.add(cursor)


def rich_text(prop):
    if not prop:
        return None
    typ = prop.get("type")
    items = prop.get(typ, []) if typ in ("title", "rich_text") else []
    value = "".join(x.get("plain_text", "") for x in items).strip()
    return value or None


def select_name(prop):
    if not prop or prop.get("type") != "select":
        return None
    item = prop.get("select")
    return item.get("name") if item else None


def number_value(prop):
    if not prop or prop.get("type") != "number":
        return None
    return prop.get("number")


def checkbox_value(prop):
    if not prop or prop.get("type") != "checkbox":
        return False
    return bool(prop.get("checkbox"))


def date_start(prop):
    if not prop or prop.get("type") != "date":
        return None
    item = prop.get("date")
    return item.get("start") if item else None


def relation_ids(prop):
    if not prop or prop.get("type") != "relation":
        return []
    return [x.get("id") for x in prop.get("relation", []) if x.get("id")]


def norm_id(value):
    return (value or "").replace("-", "").lower()


def clean_teacher(name):
    value = (name or "").strip()
    value = re.sub(r"\s*T$", "", value).strip()
    return value


def teacher_map():
    result = {}
    for page in query_db(TEACHER_DB_ID):
        name = rich_text(page.get("properties", {}).get("이름"))
        if name:
            result[norm_id(page.get("id"))] = clean_teacher(name)
    return result


def class_name_from_title(title):
    parts = [x.strip() for x in (title or "").split("|")]
    return parts[1] if len(parts) >= 2 else ""


def teacher_name_from_title(title):
    parts = [x.strip() for x in (title or "").split("|")]
    return clean_teacher(parts[2]) if len(parts) >= 3 else ""


def text(prop):
    """Match the Site plain() function: preserve whitespace for write fingerprints."""
    prop = prop or {}
    return "".join(x.get("plain_text") or x.get("text", {}).get("content", "")
                   for x in (prop.get("title") or prop.get("rich_text") or []))


def display_value(prop):
    prop = prop or {}
    if prop.get("type") == "rollup":
        return "\n".join(filter(None, (display_value(x) for x in prop.get("rollup", {}).get("array", []))))
    if prop.get("type") == "formula":
        return str(prop.get("formula", {}).get("string") or "")
    return text(prop)


def names(rows, field):
    return {norm_id(r["id"]): text(r.get("properties", {}).get(field)) for r in rows}


def rel(prop):
    if (prop or {}).get("has_more"):
        raise RuntimeError("Truncated Notion relation; refusing an incomplete cache")
    return [norm_id(x) for x in relation_ids(prop)]


def page_version(page):
    value = page.get("last_edited_time")
    if not value:
        raise RuntimeError("Notion page edit version missing")
    return value


def notion_url(page_id):
    return "https://www.notion.so/" + norm_id(page_id)


def due_for_lesson(lesson, sources, day, book_names):
    """Same class/book/slot rules as Site lib/lesson-due.ts."""
    p = lesson.get("properties", {})
    classes, books = set(rel(p.get("반 DB"))), set(rel(p.get("교재이름")))
    result = []
    for source in sources:
        if norm_id(source["id"]) == norm_id(lesson["id"]):
            continue
        q = source.get("properties", {})
        assigned = (date_start(q.get("출제일")) or "")[:10]
        if not assigned or assigned > day or not classes.intersection(rel(q.get("반 DB"))):
            continue
        source_books = rel(q.get("교재이름"))
        book_match = bool(books and source_books)
        if book_match:
            if not books.intersection(source_books):
                continue
        elif (not select_name(p.get("시간"))
              or select_name(p.get("시간")) != select_name(q.get("시간"))
              or not set(rel(p.get("강사DB"))).intersection(rel(q.get("강사DB")))):
            continue
        homework = text(q.get("숙제+교재단어")) if (date_start(q.get("검사일")) or "")[:10] == day else ""
        words = text(q.get("학원단어")) if (date_start(q.get("단어시험일")) or "")[:10] == day else ""
        if not homework.strip() and not words.strip():
            continue
        result.append({"id": source["id"], "assignedDate": assigned,
                       "books": [book_names.get(x) or "교재명 확인 필요" for x in source_books],
                       "homework": homework, "words": words,
                       "matchNote": "" if book_match else "교재 미입력 · 같은 반·시간·담당 기준",
                       "notionUrl": notion_url(source["id"])})
    return sorted(result, key=lambda r: (r["assignedDate"], r["id"]))


def build_cache(target_date):
    # Read every dependency before creating/uploading a snapshot. Failed or
    # truncated reads must never replace the last complete cache with partial data.
    datetime.strptime(target_date, "%Y-%m-%d")
    staff = query_db(TEACHER_DB_ID)
    teachers = {norm_id(p["id"]): clean_teacher(text(p.get("properties", {}).get("이름"))) for p in staff}
    if len([n for n in teachers.values() if n]) != len(set(n for n in teachers.values() if n)):
        raise RuntimeError("Ambiguous teacher names")
    timetable = query_db(TIMETABLE_DB_ID, {"property": "출제일", "date": {"equals": target_date}})
    daily = query_db(DAILY_DB_ID, {"property": "날짜", "date": {"equals": target_date}})
    classes = query_db(CLASS_DB_ID)
    students = query_db(STUDENT_DB_ID)
    books = query_db(BOOK_DB_ID)
    due_sources = query_db(TIMETABLE_DB_ID, {"or": [
        {"property": "검사일", "date": {"equals": target_date}},
        {"property": "단어시험일", "date": {"equals": target_date}},
    ]})
    cn, sn, bn = names(classes, "반 이름"), names(students, "이름"), names(books, "이름")
    groups = {n: {"schedule": [], "daily_management": []} for n in teachers.values() if n}

    def assigned(p, field):
        ids = rel(p.get(field))
        if not ids or any(not teachers.get(i) for i in ids):
            raise RuntimeError("Missing or unresolved teacher relation; refusing partial cache")
        return ids

    for page in timetable:
        p = page.get("properties", {})
        ids = assigned(p, "강사DB")
        row = {
            "id": page["id"], "notion_page_id": page["id"],
            "notion_url": notion_url(page["id"]), "last_edited_time": page_version(page),
            "time": select_name(p.get("시간")) or "",
            "class_name": " · ".join(filter(None, (cn.get(i) for i in rel(p.get("반 DB"))))) or "반 미지정",
            "lesson": text(p.get("오늘 수업내용")), "homework": text(p.get("숙제+교재단어")),
            "academy_vocab": text(p.get("학원단어")),
            "check_date": (date_start(p.get("검사일")) or "")[:10],
            "vocab_test_date": (date_start(p.get("단어시험일")) or "")[:10],
            "books": [bn.get(i) or "교재명 확인 필요" for i in rel(p.get("교재이름"))],
            "due": due_for_lesson(page, due_sources, target_date, bn),
        }
        for teacher_id in ids:
            groups[teachers[teacher_id]]["schedule"].append(dict(row))

    for page in daily:
        p = page.get("properties", {})
        ids = assigned(p, "담당")
        class_ids, student_ids = relation_ids(p.get("반 DB")), relation_ids(p.get("학생"))
        rel(p.get("반 DB")); rel(p.get("학생"))
        source_ids = rel(p.get("출제 시간표"))
        source = next((t for t in timetable if norm_id(t["id"]) in source_ids), {})
        source_p = source.get("properties", {})
        student_id = norm_id(student_ids[0]) if student_ids else ""
        row = {
            "id": page["id"], "notion_page_id": page["id"],
            "notion_url": notion_url(page["id"]), "last_edited_time": page_version(page),
            "student_name": sn.get(student_id) or "학생 연결 확인 필요", "linked": bool(sn.get(student_id)),
            "class_id": class_ids[0] if class_ids else "",
            "class_name": cn.get(norm_id(class_ids[0])) or "반 미지정" if class_ids else "반 미지정",
            "time": select_name(p.get("수업시간")) or "",
            "homework_grade": select_name(p.get("과제")), "blank_test_grade": select_name(p.get("백지")),
            "academy_vocab_wrong": number_value(p.get("오답수(학원단어)")),
            "academy_vocab_total": number_value(p.get("만점(학원)")),
            "textbook_vocab_wrong": number_value(p.get("오답수(교재)")),
            "textbook_vocab_total": number_value(p.get("만점(교재)")),
            "absent": checkbox_value(p.get("결석 체크")),
            "class_ids": class_ids, "student_ids": student_ids,
            "lesson": text(source_p.get("오늘 수업내용")), "homework_text": display_value(p.get("검사할 숙제")),
            "assignment": text(source_p.get("숙제+교재단어")), "academy_vocab": text(source_p.get("학원단어")),
            "test_range": display_value(p.get("시험 범위")),
            "followup": " · ".join(filter(None, [display_value(p.get("재시험 대상 과목")), display_value(p.get("재검사 대상 과목"))])),
        }
        for teacher_id in ids:
            # Keep Notion's original primary teacher ID: secondary-teacher views
            # safely fall back in the Site until its multi-teacher adapter exists.
            groups[teachers[teacher_id]]["daily_management"].append({**row, "teacher_id": relation_ids(p.get("담당"))[0]})

    pages = staff + timetable + daily + classes + students + books + due_sources
    updated_at = max((page_version(p) for p in pages), default=datetime.now(KST).isoformat())
    caches = [{"teacher": teacher, "notion_updated_at": updated_at, "data": {
        **data, "source_date": target_date, "source": "Notion", "cache_version": 2, "complete": True,
    }} for teacher, data in sorted(groups.items())]
    return caches, len(timetable), len(daily)


def github_oidc_token():
    request_url = os.environ.get(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "",
    ).strip()
    request_token = os.environ.get(
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "",
    ).strip()
    if not request_url or not request_token:
        raise RuntimeError("GitHub OIDC environment is missing")

    sep = "&" if "?" in request_url else "?"
    url = request_url + sep + urlencode({"audience": OIDC_AUDIENCE})
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {request_token}"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"GitHub OIDC request failed {r.status_code}: {r.text[:500]}"
        )
    value = r.json().get("value")
    if not value:
        raise RuntimeError("GitHub OIDC token value missing")
    return value


def push_cache(target_date, caches):
    token = github_oidc_token()
    r = requests.post(
        EDGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"date": target_date, "caches": caches},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"Supabase cache ingest failed {r.status_code}: {r.text[:1000]}"
        )
    return r.json()


def ping_ingest():
    token = github_oidc_token()
    r = requests.post(
        EDGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"ping": True},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(
            f"Supabase ingest auth ping failed {r.status_code}: {r.text[:1000]}"
        )
    return r.json()


def main():
    target_date = os.environ.get("TARGET_DATE", "").strip()
    if not target_date:
        target_date = datetime.now(KST).date().isoformat()

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date):
        raise RuntimeError("TARGET_DATE must be YYYY-MM-DD")

    caches, timetable_count, daily_count = build_cache(target_date)

    if not caches:
        ping = ping_ingest()
        print(
            json.dumps(
                {
                    "ok": True,
                    "date": target_date,
                    "message": "No cache rows to sync",
                    "timetable_rows": timetable_count,
                    "daily_rows": daily_count,
                    "supabase": ping,
                },
                ensure_ascii=False,
            )
        )
        return

    result = push_cache(target_date, caches)
    summary = {
        "ok": True,
        "date": target_date,
        "teachers": len(caches),
        "timetable_rows": timetable_count,
        "daily_rows": daily_count,
        "supabase": result,
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
