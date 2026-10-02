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
NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

TIMETABLE_DB_ID = "388fa4082f0480908ce9d71175973068"
DAILY_DB_ID = "39ffa4082f0480c9a32adee0defc8e74"
TEACHER_DB_ID = "3526cec7c3f9476a9168da17d62b5fa5"

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
    while True:
        req = dict(body)
        if cursor:
            req["start_cursor"] = cursor
        data = notion_request(
            "POST",
            f"/databases/{db_id}/query",
            req,
        )
        out.extend(data.get("results", []))
        if not data.get("has_more"):
            return out
        cursor = data.get("next_cursor")


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
    return (value or "").replace("-", "")


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


def build_cache(target_date):
    teachers = teacher_map()

    timetable = query_db(
        TIMETABLE_DB_ID,
        {
            "property": "출제일",
            "date": {"equals": target_date},
        },
    )
    daily = query_db(
        DAILY_DB_ID,
        {
            "property": "날짜",
            "date": {"equals": target_date},
        },
    )

    groups = {}

    def ensure(name):
        if not name:
            return None
        return groups.setdefault(
            name,
            {"schedule": [], "daily_management": []},
        )

    for page in timetable:
        p = page.get("properties", {})
        title = rich_text(p.get("날짜 반이름 담당"))
        rel = relation_ids(p.get("강사DB"))
        teacher = teachers.get(norm_id(rel[0])) if rel else None
        teacher = teacher or teacher_name_from_title(title)
        bucket = ensure(teacher)
        if bucket is None:
            continue

        bucket["schedule"].append(
            {
                "time": select_name(p.get("시간")),
                "class_name": class_name_from_title(title),
                "lesson": rich_text(p.get("오늘 수업내용")),
                "homework": rich_text(p.get("숙제+교재단어")),
                "academy_vocab": rich_text(p.get("학원단어")),
                "check_date": date_start(p.get("검사일")),
                "vocab_test_date": date_start(p.get("단어시험일")),
                "notion_page_id": page.get("id"),
            }
        )

    for page in daily:
        p = page.get("properties", {})
        rel = relation_ids(p.get("담당"))
        teacher = teachers.get(norm_id(rel[0])) if rel else None
        bucket = ensure(teacher)
        if bucket is None:
            continue

        bucket["daily_management"].append(
            {
                "student_name": rich_text(p.get("이름")),
                "time": select_name(p.get("수업시간")),
                "homework_grade": select_name(p.get("과제")),
                "blank_test_grade": select_name(p.get("백지")),
                "academy_vocab_wrong": number_value(
                    p.get("오답수(학원단어)")
                ),
                "academy_vocab_total": number_value(p.get("만점(학원)")),
                "textbook_vocab_wrong": number_value(
                    p.get("오답수(교재)")
                ),
                "textbook_vocab_total": number_value(p.get("만점(교재)")),
                "absent": checkbox_value(p.get("결석 체크")),
                "class_ids": relation_ids(p.get("반 DB")),
                "student_ids": relation_ids(p.get("학생")),
                "notion_page_id": page.get("id"),
            }
        )

    caches = []
    for teacher, data in sorted(groups.items()):
        caches.append(
            {
                "teacher": teacher,
                "notion_updated_at": datetime.now(KST).isoformat(),
                "data": {
                    "schedule": data["schedule"],
                    "daily_management": data["daily_management"],
                    "source_date": target_date,
                    "source": "Notion",
                    "cache_version": 1,
                },
            }
        )

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
