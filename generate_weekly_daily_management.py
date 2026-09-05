#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시간표 -> 매일관리 주간 자동 생성 스크립트
==========================================

4단계 루틴을 실제로 실행하는 스크립트입니다.

  1. 시간표 생성        : 지난주 같은 요일의 시간표 행을 참고해 이번 주 시간표를 생성.
                          (진도/숙제처럼 "이어지는 맥락"이 필요한 필드만 지난 시간표에서 가져옴.
                           학생 관계는 이 단계에서 채우지 않음.)
  2. 학생 연결          : 새로 생성된 시간표 행의 '⭕ 반 DB' 관계를 보고,
                          해당 반에 속한 학생 중 학생 DB2의 상태가 '재원'인 학생만 연결.
  3. 매일관리 생성      : 연결된 (재원) 학생마다 매일관리 행을 하나씩 생성.
                          반/시간/담당/날짜는 시간표에서 그대로 상속.
  4. 검사일/시험일 연결 : 시간표 전체에서 검사일/단어시험일이 "오늘"인 행을 찾아,
                          같은 반·시간대의 매일관리 행(3단계에서 이미 생성된 것)에
                          '검사일'/'단어시험일' 관계만 추가. 새 매일관리 행은 만들지 않음.

실행 전 필수 준비
------------------
1. Notion Integration을 만들고, 아래 3개 데이터베이스에 그 Integration을 연결(Connect)해야 합니다.
     - ⭕ 시간표/ 숙제 입력2
     - ⭕ 매일 관리 DB
     - ⭕ 학생 DB2
2. 환경변수 NOTION_TOKEN 에 Integration Secret을 넣어주세요.
     export NOTION_TOKEN="secret_xxx..."
3. 아래 CONFIG의 데이터베이스 ID가 실제 워크스페이스와 일치하는지 확인하세요.
     (대시(-) 유무는 상관없습니다. 이 스크립트는 자동으로 정규화합니다.)
4. 처음 실행할 때는 DRY_RUN=1 로 먼저 돌려서 로그만 확인하고,
   문제가 없으면 DRY_RUN=0 으로 다시 돌리세요.
     DRY_RUN=1 python3 generate_weekly_daily_management.py

스케줄링 예시 (GitHub Actions, 매주 일요일 21:00 KST = 12:00 UTC)
------------------------------------------------------------------
.github/workflows/weekly_generate.yml 에:

    name: weekly-daily-management
    on:
      schedule:
        - cron: "0 12 * * 0"
      workflow_dispatch: {}
    jobs:
      run:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-python@v5
            with:
              python-version: "3.11"
          - run: pip install requests
          - run: python3 generate_weekly_daily_management.py
            env:
              NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
              DRY_RUN: "0"
"""

import os
import sys
import time
import logging
from datetime import date, timedelta
from typing import Any, Optional

import requests

# --------------------------------------------------------------------------
# CONFIG - 실제 워크스페이스 값으로 확인/수정하세요
# --------------------------------------------------------------------------

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"

# 대화에서 확인된 데이터베이스(페이지) ID. 대시 유무 상관없음.
TIMETABLE_DB_ID = "388fa4082f0480908ce9d71175973068"   # ⭕ 시간표/ 숙제 입력2
DAILY_DB_ID = "39ffa4082f0480c9a32adee0defc8e74"        # ⭕ 매일 관리 DB
STUDENT_DB_ID = "39ffa4082f0480849f0dd09074a59a58"      # ⭕ 학생 DB2

# 시간표 "시간"(select) -> 매일관리 "수업시간"(select) 매핑
TIME_MAP = {
    "3시 20": "0320",
    "4시 30": "0430",
    "5시 20": "0520",
    "6시 30": "0630",
    "7시 20": "0720",
    "8시 40": "0840",
}

# 시간표에서 "이어지는 맥락"이 필요해 그대로 복사할 필드들 (학생 관계는 제외)
CARRY_OVER_PROPS = ["오늘 수업내용", "숙제+교재단어", "학원단어", "교재이름"]

DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"

# 테스트용: TARGET_DATE 환경변수(YYYY-MM-DD)가 있으면 "오늘"을 그 날짜로 취급한다.
# 평일이 아닐 때(주말) 실제 평일 데이터를 흉내내서 미리 검증하고 싶을 때 사용.
_TARGET_DATE_OVERRIDE = os.environ.get("TARGET_DATE", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weekly-gen")


# --------------------------------------------------------------------------
# 저수준 Notion API 헬퍼
# --------------------------------------------------------------------------

def _normalize_id(raw_id: str) -> str:
    return raw_id.replace("-", "")


def _headers() -> dict:
    if not NOTION_TOKEN:
        raise RuntimeError("환경변수 NOTION_TOKEN이 설정되지 않았습니다.")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, json_body: Optional[dict] = None, max_retries: int = 5) -> dict:
    url = f"{API_BASE}{path}"
    for attempt in range(max_retries):
        resp = requests.request(method, url, headers=_headers(), json=json_body, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "1")) + 1
            log.warning("Rate limited. %s초 대기 후 재시도...", wait)
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            log.error("Notion API 오류 %s: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        time.sleep(0.35)  # Notion API는 초당 3req 권장 -> 여유있게 슬리핑
        return resp.json()
    raise RuntimeError(f"{max_retries}회 재시도 후에도 실패: {path}")


def query_database_all(database_id: str, filter_obj: Optional[dict] = None) -> list[dict]:
    """데이터베이스의 모든 페이지를 페이지네이션 처리하며 가져온다."""
    database_id = _normalize_id(database_id)
    results: list[dict] = []
    body: dict[str, Any] = {"page_size": 100}
    if filter_obj:
        body["filter"] = filter_obj
    cursor = None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        data = _request("POST", f"/databases/{database_id}/query", body)
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def get_page(page_id: str) -> dict:
    page_id = _normalize_id(page_id)
    return _request("GET", f"/pages/{page_id}")


def create_page(database_id: str, properties: dict) -> dict:
    database_id = _normalize_id(database_id)
    body = {"parent": {"database_id": database_id}, "properties": properties}
    if DRY_RUN:
        log.info("[DRY_RUN] create_page(%s): %s", database_id, list(properties.keys()))
        return {"id": "dry-run-fake-id"}
    return _request("POST", "/pages", body)


def update_page(page_id: str, properties: dict) -> dict:
    page_id = _normalize_id(page_id)
    body = {"properties": properties}
    if DRY_RUN:
        log.info("[DRY_RUN] update_page(%s): %s", page_id, list(properties.keys()))
        return {"id": page_id}
    return _request("PATCH", f"/pages/{page_id}", body)


# --------------------------------------------------------------------------
# 프로퍼티 읽기 헬퍼
# --------------------------------------------------------------------------

def get_title_text(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts)
    return ""


def get_relation_ids(page: dict, prop_name: str) -> list[str]:
    prop = page.get("properties", {}).get(prop_name)
    if not prop or prop.get("type") != "relation":
        return []
    return [r["id"] for r in prop.get("relation", [])]


def get_select_name(page: dict, prop_name: str) -> Optional[str]:
    prop = page.get("properties", {}).get(prop_name)
    if not prop:
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None


def get_date_start(page: dict, prop_name: str) -> Optional[str]:
    prop = page.get("properties", {}).get(prop_name)
    if not prop:
        return None
    d = prop.get("date")
    return d.get("start") if d else None


def get_rich_text(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name)
    if not prop or prop.get("type") != "rich_text":
        return ""
    return "".join(p.get("plain_text", "") for p in prop.get("rich_text", []))


def title_lookup(page_id: str) -> str:
    """어떤 페이지든 title 속성 텍스트를 가져온다 (반/강사 이름 조회용)."""
    return get_title_text(get_page(page_id))


# --------------------------------------------------------------------------
# STEP 1: 시간표 생성 (지난주 참고)
# --------------------------------------------------------------------------

def step1_generate_timetable(today: date) -> list[dict]:
    """지난주 같은 요일의 시간표를 복사해 이번 주(=today) 시간표를 생성한다.
    이미 today 날짜로 생성된 (같은 반/시간) 행이 있으면 건너뛴다.
    반환값: 오늘 생성/확인된 시간표 페이지 리스트 (신규+기존 포함, 3~4단계에서 사용).
    """
    last_week = today - timedelta(days=7)
    log.info("STEP 1: %s(지난주) 기준으로 %s(이번주) 시간표 생성", last_week, today)

    source_rows = query_database_all(
        TIMETABLE_DB_ID,
        filter_obj={"property": "출제일", "date": {"equals": last_week.isoformat()}},
    )
    log.info("  지난주 시간표 %d건 발견", len(source_rows))

    existing_today = query_database_all(
        TIMETABLE_DB_ID,
        filter_obj={"property": "출제일", "date": {"equals": today.isoformat()}},
    )
    existing_keys = set()
    for row in existing_today:
        ban = tuple(sorted(get_relation_ids(row, "⭕ 반 DB")))
        existing_keys.add((ban, get_select_name(row, "시간")))

    today_rows = list(existing_today)

    for src in source_rows:
        ban_ids = get_relation_ids(src, "⭕ 반 DB")
        time_slot = get_select_name(src, "시간")
        key = (tuple(sorted(ban_ids)), time_slot)
        if key in existing_keys:
            continue  # 이미 이번 주 것이 있으면 건너뜀 (중복 생성 방지)

        properties: dict[str, Any] = {
            "date:출제일:start": today.isoformat(),
            "요일": {"select": {"name": get_select_name(src, "요일")}},
        }
        if time_slot:
            properties["시간"] = {"select": {"name": time_slot}}
        strand_days = get_select_name(src, "요일")
        if ban_ids:
            properties["⭕ 반 DB"] = {"relation": [{"id": i} for i in ban_ids]}
        teacher_ids = get_relation_ids(src, "강사DB")
        if teacher_ids:
            properties["강사DB"] = {"relation": [{"id": i} for i in teacher_ids]}

        # 진도/숙제처럼 이어지는 맥락 필드 복사
        for prop_name in CARRY_OVER_PROPS:
            src_prop = src.get("properties", {}).get(prop_name)
            if not src_prop:
                continue
            ptype = src_prop.get("type")
            if ptype == "rich_text" and src_prop.get("rich_text"):
                properties[prop_name] = {"rich_text": src_prop["rich_text"]}
            elif ptype == "relation" and src_prop.get("relation"):
                properties[prop_name] = {"relation": src_prop["relation"]}

        # 검사일/단어시험일이 지난주 기준으로 있었다면 1주 뒤로 밀어서 유지
        for date_prop in ("검사일", "단어시험일"):
            start = get_date_start(src, date_prop)
            if start:
                try:
                    d = date.fromisoformat(start[:10])
                    properties[f"date:{date_prop}:start"] = (d + timedelta(days=7)).isoformat()
                except ValueError:
                    pass

        properties["데이터생성"] = {"select": {"name": "생성완료"}}

        new_page = create_page(TIMETABLE_DB_ID, properties)
        log.info("  생성됨: 반=%s 시간=%s -> page=%s", ban_ids, time_slot, new_page["id"])
        today_rows.append(new_page)
        existing_keys.add(key)

    log.info("STEP 1 완료: 오늘 기준 시간표 %d건", len(today_rows))
    return today_rows


# --------------------------------------------------------------------------
# STEP 2 + 3: 반 DB 기준 재원 학생 연결 + 매일관리 생성
# --------------------------------------------------------------------------

def get_active_students_for_class(ban_id: str) -> list[dict]:
    """학생 DB2에서 해당 반(ban_id)에 속하고 상태=='재원'인 학생만 반환."""
    return query_database_all(
        STUDENT_DB_ID,
        filter_obj={
            "and": [
                {"property": "⭕ 반 DB", "relation": {"contains": ban_id}},
                {"property": "상태", "select": {"equals": "재원"}},
            ]
        },
    )


def step2_3_generate_daily(today: date, timetable_rows: list[dict]) -> list[dict]:
    log.info("STEP 2+3: 반 DB 기준 재원 학생 연결 + 매일관리 생성")

    existing_daily = query_database_all(
        DAILY_DB_ID,
        filter_obj={"property": "날짜", "date": {"equals": today.isoformat()}},
    )
    existing_keys = set()
    for row in existing_daily:
        student_ids = get_relation_ids(row, "학생")
        if student_ids:
            existing_keys.add(student_ids[0])

    created_rows = list(existing_daily)

    for tt in timetable_rows:
        ban_ids = get_relation_ids(tt, "⭕ 반 DB")
        if not ban_ids:
            continue
        ban_id = ban_ids[0]
        time_slot = get_select_name(tt, "시간")
        weekday = get_select_name(tt, "요일")
        teacher_ids = get_relation_ids(tt, "강사DB")

        ban_title = title_lookup(ban_id)
        daily_time = TIME_MAP.get(time_slot, time_slot or "")
        teacher_title = title_lookup(teacher_ids[0]) if teacher_ids else ""

        students = get_active_students_for_class(ban_id)
        log.info("  반 [%s] 재원 학생 %d명", ban_title, len(students))

        for stu in students:
            stu_id = stu["id"]
            if stu_id in existing_keys:
                continue  # 이미 오늘 생성됨 (중복 방지)

            stu_name = get_title_text(stu)
            row_title = f"{today.strftime('%Y.%m.%d')} | {daily_time} | {ban_title} | {stu_name} | {teacher_title}"

            properties: dict[str, Any] = {
                "이름": {"title": [{"text": {"content": row_title}}]},
                "date:날짜:start": today.isoformat(),
                "학생": {"relation": [{"id": stu_id}]},
                "⭕ 반 DB": {"relation": [{"id": ban_id}]},
            }
            if teacher_ids:
                properties["담당"] = {"relation": [{"id": teacher_ids[0]}]}
            if daily_time:
                properties["수업시간"] = {"select": {"name": daily_time}}
            if weekday:
                properties["요일"] = {"select": {"name": weekday}}
            properties["출제 시간표"] = {"relation": [{"id": tt["id"]}]}

            new_row = create_page(DAILY_DB_ID, properties)
            created_rows.append(new_row)
            existing_keys.add(stu_id)

    log.info("STEP 2+3 완료: 오늘 매일관리 총 %d건", len(created_rows))
    return created_rows


# --------------------------------------------------------------------------
# STEP 4: 검사일/단어시험일 기준 연결 추가 (새 행 생성 금지)
# --------------------------------------------------------------------------

def step4_link_exam_days(today: date, daily_rows: list[dict]) -> None:
    log.info("STEP 4: 검사일/단어시험일 기준 연결 추가")

    # 오늘 매일관리 행을 (반, 수업시간) 기준으로 그룹핑
    by_class_slot: dict[tuple, list[dict]] = {}
    for row in daily_rows:
        ban_ids = get_relation_ids(row, "⭕ 반 DB")
        if not ban_ids:
            continue
        slot = get_select_name(row, "수업시간")
        by_class_slot.setdefault((ban_ids[0], slot), []).append(row)

    for date_prop, daily_relation_prop in (("검사일", "검사일"), ("단어시험일", "단어시험일")):
        matches = query_database_all(
            TIMETABLE_DB_ID,
            filter_obj={"property": date_prop, "date": {"equals": today.isoformat()}},
        )
        log.info("  '%s'==오늘 인 시간표 %d건", date_prop, len(matches))

        for tt in matches:
            ban_ids = get_relation_ids(tt, "⭕ 반 DB")
            if not ban_ids:
                continue
            ban_id = ban_ids[0]
            tt_time_slot = get_select_name(tt, "시간")
            daily_time = TIME_MAP.get(tt_time_slot, tt_time_slot or "")

            targets = by_class_slot.get((ban_id, daily_time), [])
            if not targets:
                log.warning(
                    "  [경고] 반=%s 시간=%s 에 해당하는 매일관리 행을 찾지 못함 (시간표=%s) - 건너뜀",
                    title_lookup(ban_id), daily_time, tt["id"],
                )
                continue

            # 대표 학생 1건으로 담당/반 일치 검증
            rep = targets[0]
            rep_teacher = get_relation_ids(rep, "담당")
            tt_teacher = get_relation_ids(tt, "강사DB")
            if rep_teacher and tt_teacher and rep_teacher[0] != tt_teacher[0]:
                log.warning("  [경고] 담당 불일치로 연결 보류: daily=%s timetable=%s", rep["id"], tt["id"])
                continue

            for row in targets:
                existing_rel = get_relation_ids(row, daily_relation_prop)
                if tt["id"] in existing_rel:
                    continue
                update_page(row["id"], {
                    daily_relation_prop: {"relation": [{"id": i} for i in existing_rel + [tt["id"]]]}
                })
            log.info("  연결 완료: 반=%s 시간=%s -> %d개 매일관리 행", title_lookup(ban_id), daily_time, len(targets))


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    if _TARGET_DATE_OVERRIDE:
        today = date.fromisoformat(_TARGET_DATE_OVERRIDE)
        log.info("[TEST MODE] TARGET_DATE 지정됨 -> 오늘을 %s 로 취급", today)
    else:
        today = date.today()
    log.info("=== 실행 시작: %s (DRY_RUN=%s) ===", today, DRY_RUN)

    timetable_rows = step1_generate_timetable(today)
    daily_rows = step2_3_generate_daily(today, timetable_rows)
    step4_link_exam_days(today, daily_rows)

    log.info("=== 실행 완료 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log.exception("실행 중 오류 발생: %s", exc)
        sys.exit(1)
