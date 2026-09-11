#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시간표 -> 매일관리 주간 자동 생성 스크립트
==========================================

5단계 루틴을 실제로 실행하는 스크립트입니다.

  1. 시간표 생성        : 지난주 같은 요일의 시간표 행을 참고해 이번 주 시간표를 생성.
                          (반/시간/강사/요일/교재이름을 가져오고, 수업내용·숙제·검사일·학원단어·단어시험일은 비움.
                           학생 관계는 이 단계에서 채우지 않음.)
                          이미 이번 주 (반, 시간) 행이 존재하면 새로 만들지는 않되,
                          그 행의 강사DB가 비어 있으면 지난주 소스 기준으로 채워 넣는다(백필).
  2. 수업 배정 결정     : 오늘 시간표의 반/시간과 학생 DB2의 재원생 관계를 대조.
                          같은 학생·시간에 정상반과 임시반이 모두 있으면 임시반을 선택하고,
                          임시반끼리 충돌하면 임의 선택하지 않고 해당 학생을 보류.
  3. 시간표 학생 연결   : 결정된 학생을 시간표의 학생 DB1 관계에 먼저 반영.
  4. 매일관리 생성/교정 : (날짜, 학생, 시간) 기준으로 기존 행을 먼저 찾고,
                          있으면 시간표 기준 관계를 교정하며 없을 때만 새로 생성.
  5. 검사일/시험일 연결 : 시간표 전체에서 검사일/단어시험일이 "오늘"인 행을 찾아,
                          같은 반·시간대의 매일관리 행(4단계에서 이미 생성된 것)에
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

스케줄링 예시 (GitHub Actions, 월~금 06:00 KST = 전날 21:00 UTC)
------------------------------------------------------------------
.github/workflows/weekly_generate.yml 에:

    name: weekly-daily-management
    on:
      schedule:
        - cron: "0 21 * * 0-4"
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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
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

# 반 이름으로 임시반을 식별한다. 워크스페이스에서 사용하는 접두사가 늘어나면
# GitHub Actions 변수 TEMP_CLASS_PREFIXES에 쉼표로 추가할 수 있다.
# 예: TEMP_CLASS_PREFIXES="[임시],[금],[시험]"
TEMP_CLASS_PREFIXES = tuple(
    item.strip()
    for item in os.environ.get("TEMP_CLASS_PREFIXES", "[임시],[금]").split(",")
    if item.strip()
)

# 시간표 생성 시 교재 관계만 유지. 수업내용/숙제/시험 범위 및 날짜는 복사하지 않음.
CARRY_OVER_PROPS = ["교재이름"]

# 실행 시작 시 실제 스키마를 조회해서 채워지는 값들 (혹시 모를 이름 불일치 방지용 안전장치)
BAN_PROP_TT: str = ""       # 시간표 DB의 "반 DB" 속성 실제 이름
BAN_PROP_DAILY: str = ""    # 매일관리 DB의 "반 DB" 속성 실제 이름
STUDENT_PROP_TT: Optional[str] = None
STUDENT_STATUS_TYPE = "select"
BAN_PROP_STUDENT: str = ""  # 학생 DB2의 "반 DB" 속성 실제 이름


def resolve_all_property_names() -> None:
    global BAN_PROP_TT, BAN_PROP_DAILY, BAN_PROP_STUDENT, STUDENT_PROP_TT, STUDENT_STATUS_TYPE
    log.info("실제 속성명 조회 중 (이모지 제거 후 '반 DB'로 통일됨)...")
    tt_schema = get_database_schema(TIMETABLE_DB_ID)
    daily_schema = get_database_schema(DAILY_DB_ID)
    student_schema = get_database_schema(STUDENT_DB_ID)
    BAN_PROP_TT = resolve_property_name(tt_schema, "반 DB")
    BAN_PROP_DAILY = resolve_property_name(daily_schema, "반 DB")
    BAN_PROP_STUDENT = resolve_property_name(student_schema, "반 DB")
    # 강사DB는 relation 속성인지 확인하되, 관계 대상 DB ID 노출 여부로 실행을 막지 않는다.
    # Notion은 연결 권한/응답 형태에 따라 relation 설정의 database_id를 생략할 수 있다.
    # 실제 강사 관계 값은 STEP 1에서 소스 행별로 검증하므로 빈 강사 데이터는 생성되지 않는다.
    teacher_prop = tt_schema.get("properties", {}).get("강사DB", {})
    if teacher_prop.get("type") != "relation":
        raise RuntimeError("시간표의 강사DB 속성이 relation이 아닙니다. 속성 유형을 확인하세요.")
    if not teacher_prop.get("relation", {}).get("database_id"):
        log.warning("강사DB 관계 대상 ID가 API 응답에 없지만 행의 관계 값 검증을 계속합니다.")

    STUDENT_STATUS_TYPE = student_schema["properties"]["상태"]["type"]
    if STUDENT_STATUS_TYPE not in ("select", "status"):
        raise RuntimeError("학생 상태 속성은 select 또는 status여야 합니다.")
    student_relations = [name for name, prop in tt_schema["properties"].items()
                         if prop.get("type") == "relation" and
                         _normalize_id(prop["relation"].get("database_id", "")) == _normalize_id(STUDENT_DB_ID)]
    if len(student_relations) > 1:
        raise RuntimeError(f"시간표의 학생 관계가 여러 개입니다: {student_relations}")
    STUDENT_PROP_TT = student_relations[0] if student_relations else None
    if STUDENT_PROP_TT is None:
        log.warning("시간표에 학생 DB2 직접 관계가 없어 매일관리의 학생 관계만 연결합니다.")
    log.info("  시간표: %r / 매일관리: %r / 학생DB2: %r", BAN_PROP_TT, BAN_PROP_DAILY, BAN_PROP_STUDENT)

# 테스트용: TARGET_DATE 환경변수(YYYY-MM-DD)가 있으면 "오늘"을 그 날짜로 취급한다.
# 평일이 아닐 때(주말) 실제 평일 데이터를 흉내내서 미리 검증하고 싶을 때 사용.
_TARGET_DATE_OVERRIDE = os.environ.get("TARGET_DATE", "").strip()

DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"

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


def get_database_schema(database_id: str) -> dict:
    database_id = _normalize_id(database_id)
    return _request("GET", f"/databases/{database_id}")


def resolve_property_name(schema: dict, keyword: str) -> str:
    """스키마에서 keyword(부분 문자열)를 포함하는 실제 속성 이름을 찾는다.
    이모지 앞글자가 눈으로는 같아 보여도 유니코드가 다를 수 있어,
    하드코딩 대신 항상 실제 API가 돌려준 속성 이름을 그대로 사용한다."""
    props = schema.get("properties", {})
    if keyword in props:
        return keyword
    matches = [name for name in props if keyword in name]
    if not matches:
        raise RuntimeError(f"'{keyword}' 포함 속성을 찾지 못함. 실제 속성명: {list(props.keys())}")
    if len(matches) > 1:
        raise RuntimeError(f"{keyword!r} 속성이 여러 개여서 특정할 수 없음: {matches}")
    return matches[0]


def create_page(database_id: str, properties: dict) -> dict:
    database_id = _normalize_id(database_id)
    body = {"parent": {"database_id": database_id}, "properties": properties}
    if DRY_RUN:
        log.info("[DRY_RUN] create_page(%s): %s", database_id, list(properties.keys()))
        # 드라이런에서도 이후 단계가 방금 만든 값을 그대로 읽어 검증할 수 있도록,
        # 실제로 쓰려던 properties를 그대로 돌려준다 (실제 쓰기는 하지 않음).
        return {"id": f"dry-run-fake-{id(properties)}", "properties": properties}
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
        if prop.get("type") == "title" or "title" in prop:
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", p.get("text", {}).get("content", "")) for p in parts)
    return ""


def get_relation_ids(page: dict, prop_name: str) -> list[str]:
    prop = page.get("properties", {}).get(prop_name)
    if not prop or "relation" not in prop:
        return []
    if prop.get("has_more"):
        relations = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = _request("GET", f"/pages/{_normalize_id(page['id'])}/properties/{prop['id']}?{urlencode(params)}")
            relations.extend(item["relation"] for item in data["results"])
            if not data.get("has_more"):
                break
            cursor = data["next_cursor"]
        prop["relation"] = relations
        prop["has_more"] = False
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
    if not prop or "rich_text" not in prop:
        return ""
    return "".join(p.get("plain_text", p.get("text", {}).get("content", "")) for p in prop.get("rich_text", []))


def title_lookup(page_id: str) -> str:
    """어떤 페이지든 title 속성 텍스트를 가져온다 (반/강사 이름 조회용)."""
    return get_title_text(get_page(page_id))


# --------------------------------------------------------------------------
# STEP 1: 시간표 생성 (지난주 참고)
# --------------------------------------------------------------------------

def step1_generate_timetable(today: date) -> list[dict]:
    """지난주 같은 요일의 시간표를 복사해 이번 주(=today) 시간표를 생성한다.
    이미 today 날짜로 생성된 (같은 반/시간) 행이 있으면 새로 만들지는 않되,
    그 기존 행의 강사DB가 비어 있고 지난주 소스에 강사DB가 있으면 채워 넣는다(백필).
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
    existing_by_key: dict[tuple, dict] = {}
    for row in existing_today:
        ban = tuple(sorted(_normalize_id(i) for i in get_relation_ids(row, BAN_PROP_TT)))
        key = (ban, get_select_name(row, "시간"))
        existing_keys.add(key)
        existing_by_key[key] = row

    # 기존 담당은 유지하고, 담당이 필요한 행만 지난주 자료로 검증한다.
    # 같은 반/시간에 서로 다른 강사가 있으면 임의로 첫 강사를 고르지 않는다.
    source_teachers = {}
    for src in source_rows:
        key = (tuple(sorted(_normalize_id(i) for i in get_relation_ids(src, BAN_PROP_TT))), get_select_name(src, "시간"))
        ids = get_relation_ids(src, "강사DB")
        source_teachers.setdefault(key, set()).add(tuple(sorted(_normalize_id(i) for i in ids)))
    for key, choices in source_teachers.items():
        existing = existing_by_key.get(key)
        if existing is not None and get_relation_ids(existing, "강사DB"):
            continue
        if len(choices) != 1 or not next(iter(choices)):
            raise RuntimeError(f"강사DB가 비어 있거나 지난주 강사가 서로 달라 생성/보완할 수 없습니다: {key}. 원본과 Integration 접근을 확인하세요.")

    today_rows = list(existing_today)

    for src in source_rows:
        ban_ids = get_relation_ids(src, BAN_PROP_TT)
        time_slot = get_select_name(src, "시간")
        key = (tuple(sorted(_normalize_id(i) for i in ban_ids)), time_slot)
        if key in existing_keys:
            # 이미 이번 주 행이 있으면 새로 만들지는 않되,
            # 강사DB가 비어 있는 기존 행이면 지난주 소스 기준으로 채워 넣는다.
            # (강사DB 로직이 없던 예전 버전 스크립트가 먼저 만들어둔 행을 복구하기 위함)
            existing_row = existing_by_key.get(key)
            if existing_row is not None:
                if not get_select_name(existing_row, "요일"):
                    patch_row(existing_row, {"요일": {"select": {"name": get_select_name(src, "요일") or "월화수목금토일"[today.weekday()]}}})
                existing_teacher_ids = get_relation_ids(existing_row, "강사DB")
                src_teacher_ids = get_relation_ids(src, "강사DB")
                if not existing_teacher_ids and src_teacher_ids:
                    patch_row(existing_row, {
                        "강사DB": {"relation": [{"id": i} for i in src_teacher_ids]}
                    })
                    log.info(
                        "  강사DB 백필: 반=%s 시간=%s -> page=%s",
                        ban_ids, time_slot, existing_row["id"],
                    )
                existing_book_ids = get_relation_ids(existing_row, "교재이름")
                src_book_ids = get_relation_ids(src, "교재이름")
                if not existing_book_ids and src_book_ids:
                    patch_row(existing_row, {
                        "교재이름": {"relation": [{"id": i} for i in src_book_ids]}
                    })
                    log.info(
                        "  교재이름 백필: 반=%s 시간=%s -> page=%s",
                        ban_ids, time_slot, existing_row["id"],
                    )
            continue  # 이미 이번 주 것이 있으면 새 행은 만들지 않음 (중복 생성 방지)

        properties: dict[str, Any] = {
            "출제일": {"date": {"start": today.isoformat()}},
        }
        if time_slot:
            properties["시간"] = {"select": {"name": time_slot}}
        weekday = get_select_name(src, "요일") or "월화수목금토일"[today.weekday()]
        if weekday:
            properties["요일"] = {"select": {"name": weekday}}
        if ban_ids:
            properties[BAN_PROP_TT] = {"relation": [{"id": i} for i in ban_ids]}
        teacher_ids = get_relation_ids(src, "강사DB")
        if teacher_ids:
            properties["강사DB"] = {"relation": [{"id": i} for i in teacher_ids]}

        # 교재이름 관계만 복사
        for prop_name in CARRY_OVER_PROPS:
            src_prop = src.get("properties", {}).get(prop_name)
            if not src_prop:
                continue
            ptype = src_prop.get("type")
            if ptype == "rich_text" and src_prop.get("rich_text"):
                properties[prop_name] = {"rich_text": src_prop["rich_text"]}
            elif ptype == "relation" and src_prop.get("relation"):
                properties[prop_name] = {"relation": src_prop["relation"]}

        # 명시적으로 비워 기본값이나 지난주 입력이 새 시간표에 이어지지 않게 한다.
        for text_prop in ("오늘 수업내용", "숙제+교재단어", "학원단어"):
            properties[text_prop] = {"rich_text": []}
        for date_prop in ("검사일", "단어시험일"):
            properties[date_prop] = {"date": None}

        properties["데이터생성"] = {"select": {"name": "생성완료"}}

        new_page = create_page(TIMETABLE_DB_ID, properties)
        log.info("  생성됨: 반=%s 시간=%s -> page=%s", ban_ids, time_slot, new_page["id"])
        today_rows.append(new_page)
        existing_keys.add(key)
        existing_by_key[key] = new_page

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
                {"property": BAN_PROP_STUDENT, "relation": {"contains": ban_id}},
                {"property": "상태", STUDENT_STATUS_TYPE: {"equals": "재원"}},
            ]
        },
    )


def patch_row(row: dict, properties: dict) -> None:
    """성공한 쓰기를 메모리에도 반영해 후속 단계가 오래된 관계를 덮어쓰지 않게 한다."""
    update_page(row["id"], properties)
    row.setdefault("properties", {}).update(properties)


def merge_relation(row: dict, name: str, ids: list[str]) -> bool:
    existing = get_relation_ids(row, name)
    seen = {_normalize_id(i) for i in existing}
    merged = list(existing)
    for item in ids:
        if _normalize_id(item) not in seen:
            merged.append(item)
            seen.add(_normalize_id(item))
    if merged == existing:
        return False
    patch_row(row, {name: {"relation": [{"id": i} for i in merged]}})
    return True


def daily_slot(value: Optional[str]) -> str:
    if value in TIME_MAP:
        return TIME_MAP[value]
    if value in TIME_MAP.values():
        return value
    raise RuntimeError(f"수업시간 매핑을 확인해야 합니다: {value!r}")


def is_temporary_class(class_title: str) -> bool:
    """현재 운영 중인 임시반 이름인지 판별한다.

    임시반 기간 자체는 오늘 시간표에 해당 반이 있는지로 제한한다. 따라서 학생 DB에
    임시반 관계가 남아 있어도 오늘 같은 시간의 시간표가 없으면 우선 대상으로 보지 않는다.
    """
    return "임시" in class_title or any(class_title.startswith(prefix) for prefix in TEMP_CLASS_PREFIXES)


def replace_relation(row: dict, name: str, ids: list[str]) -> bool:
    """관계를 정확히 ids로 맞춘다. 순서만 다른 경우에는 쓰지 않는다."""
    existing = get_relation_ids(row, name)
    if {_normalize_id(i) for i in existing} == {_normalize_id(i) for i in ids}:
        return False
    patch_row(row, {name: {"relation": [{"id": i} for i in ids]}})
    return True


def choose_preferred_assignment(candidates: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """한 학생·시간의 후보 중 하나만 고른다.

    임시반 후보가 하나라도 있으면 정상반 후보는 제외한다. 같은 시간에 서로 다른
    임시반이 둘 이상이거나 같은 반의 시간표가 중복되어 있으면 임의로 고르지 않는다.
    """
    if not candidates:
        return None, "후보 없음"
    temporary = [item for item in candidates if item["is_temporary"]]
    pool = temporary or candidates
    class_ids = {_normalize_id(item["class_id"]) for item in pool}
    if len(class_ids) != 1:
        kind = "임시반" if temporary else "정상반"
        names = sorted({item["class_title"] for item in pool})
        return None, f"같은 시간에 {kind}이 여러 개 연결됨: {names}"
    timetable_ids = {_normalize_id(item["timetable"]["id"]) for item in pool}
    if len(timetable_ids) != 1:
        return None, f"같은 반·시간의 시간표가 여러 개임: {sorted(timetable_ids)}"
    return pool[0], None


def build_preferred_assignments(timetable_rows: list[dict]) -> tuple[dict, dict, list[dict]]:
    """오늘 시간표에서 학생·시간별 최종 수업과 시간표별 학생 명단을 만든다."""
    class_cache: dict[str, list[dict]] = {}
    title_cache: dict[str, str] = {}
    candidates_by_student_slot: dict[tuple[str, str], list[dict]] = {}

    def cached_title(page_id: str) -> str:
        key = _normalize_id(page_id)
        if key not in title_cache:
            title_cache[key] = title_lookup(page_id)
        return title_cache[key]

    for tt in timetable_rows:
        slot = daily_slot(get_select_name(tt, "시간"))
        teacher_ids = get_relation_ids(tt, "강사DB")
        if not teacher_ids:
            log.warning("강사 없는 시간표는 학생 배정에서 보류: %s", tt["id"])
            continue
        for class_id in get_relation_ids(tt, BAN_PROP_TT):
            normalized_class_id = _normalize_id(class_id)
            class_title = cached_title(class_id)
            if normalized_class_id not in class_cache:
                class_cache[normalized_class_id] = get_active_students_for_class(class_id)
            students = class_cache[normalized_class_id]
            log.info("  반 [%s] 재원 학생 후보 %d명", class_title, len(students))
            for student in students:
                student_id = student["id"]
                key = (_normalize_id(student_id), slot)
                candidates_by_student_slot.setdefault(key, []).append({
                    "student": student,
                    "student_id": student_id,
                    "slot": slot,
                    "class_id": class_id,
                    "class_title": class_title,
                    "is_temporary": is_temporary_class(class_title),
                    "timetable": tt,
                    "teacher_ids": teacher_ids,
                    "teacher_title": ", ".join(cached_title(i) for i in teacher_ids),
                    "weekday": get_select_name(tt, "요일"),
                })

    selected: dict[tuple[str, str], dict] = {}
    conflicts: list[dict] = []
    rosters: dict[str, dict[str, str]] = {
        _normalize_id(tt["id"]): {} for tt in timetable_rows
    }
    for key, candidates in candidates_by_student_slot.items():
        assignment, reason = choose_preferred_assignment(candidates)
        if assignment is None:
            student = candidates[0]["student"]
            conflict = {
                "student_id": candidates[0]["student_id"],
                "student_name": get_title_text(student),
                "slot": key[1],
                "reason": reason,
            }
            conflicts.append(conflict)
            log.error(
                "수업 배정 보류: 학생=%s 시간=%s 사유=%s",
                conflict["student_name"] or conflict["student_id"], key[1], reason,
            )
            continue
        selected[key] = assignment
        timetable_id = _normalize_id(assignment["timetable"]["id"])
        rosters[timetable_id][_normalize_id(assignment["student_id"])] = assignment["student_id"]
    return selected, rosters, conflicts


def step2_3_generate_daily(today: date, timetable_rows: list[dict]) -> list[dict]:
    log.info("STEP 2~4: 임시반 우선 배정 + 시간표 학생 연결 + 매일관리 upsert")
    existing_daily = query_database_all(
        DAILY_DB_ID, filter_obj={"property": "날짜", "date": {"equals": today.isoformat()}},
    )
    by_student_slot: dict[tuple[str, str], dict] = {}
    duplicate_daily_keys: set[tuple[str, str]] = set()
    for row in existing_daily:
        students = get_relation_ids(row, "학생")
        if len(students) > 1:
            raise RuntimeError(f"매일관리 한 행에 학생이 여러 명 연결됨: {row['id']}")
        if students:
            key = (_normalize_id(students[0]), get_select_name(row, "수업시간"))
            if key in by_student_slot:
                duplicate_daily_keys.add(key)
                log.error(
                    "기존 매일관리 중복으로 해당 학생·시간만 보류: %s, %s",
                    by_student_slot[key]["id"], row["id"],
                )
                continue
            by_student_slot[key] = row
    for key in duplicate_daily_keys:
        by_student_slot.pop(key, None)

    selected, rosters, conflicts = build_preferred_assignments(timetable_rows)

    # 매일관리를 만들기 전에 시간표 학생 관계부터 최종 배정 결과로 맞춘다.
    if STUDENT_PROP_TT:
        for tt in timetable_rows:
            roster = rosters[_normalize_id(tt["id"])]
            replace_relation(tt, STUDENT_PROP_TT, list(roster.values()))

    # 중복 행은 어느 쪽이 정답인지 추측할 수 없으므로 후속 검사/시험 연결에서도 제외한다.
    created_rows = []
    for row in existing_daily:
        students = get_relation_ids(row, "학생")
        key = (_normalize_id(students[0]), get_select_name(row, "수업시간")) if students else None
        if key not in duplicate_daily_keys:
            created_rows.append(row)
    corrected = 0
    created = 0
    for key, assignment in selected.items():
        if key in duplicate_daily_keys:
            continue
        tt = assignment["timetable"]
        student = assignment["student"]
        student_id = assignment["student_id"]
        class_id = assignment["class_id"]
        class_title = assignment["class_title"]
        teacher_ids = assignment["teacher_ids"]
        weekday = assignment["weekday"]
        slot = assignment["slot"]
        teacher_title = assignment["teacher_title"]
        row_title = f"{today.strftime('%Y.%m.%d')} | {slot} | {class_title} | {get_title_text(student)} | {teacher_title}"
        existing = by_student_slot.get(key)
        if existing is not None:
            patch: dict[str, Any] = {}
            if {_normalize_id(i) for i in get_relation_ids(existing, BAN_PROP_DAILY)} != {_normalize_id(class_id)}:
                patch[BAN_PROP_DAILY] = {"relation": [{"id": class_id}]}
            if {_normalize_id(i) for i in get_relation_ids(existing, "출제 시간표")} != {_normalize_id(tt["id"])}:
                patch["출제 시간표"] = {"relation": [{"id": tt["id"]}]}
            if {_normalize_id(i) for i in get_relation_ids(existing, "담당")} != {_normalize_id(i) for i in teacher_ids}:
                patch["담당"] = {"relation": [{"id": i} for i in teacher_ids]}
            if weekday and get_select_name(existing, "요일") != weekday:
                patch["요일"] = {"select": {"name": weekday}}
            if patch:
                patch["이름"] = {"title": [{"text": {"content": row_title}}]}
                patch_row(existing, patch)
                corrected += 1
            continue

        properties: dict[str, Any] = {
            "이름": {"title": [{"text": {"content": row_title}}]},
            "날짜": {"date": {"start": today.isoformat()}},
            "학생": {"relation": [{"id": student_id}]},
            BAN_PROP_DAILY: {"relation": [{"id": class_id}]},
            "수업시간": {"select": {"name": slot}},
            "출제 시간표": {"relation": [{"id": tt["id"]}]},
            "담당": {"relation": [{"id": i} for i in teacher_ids]},
        }
        if weekday:
            properties["요일"] = {"select": {"name": weekday}}
        new_row = create_page(DAILY_DB_ID, properties)
        created_rows.append(new_row)
        by_student_slot[key] = new_row
        created += 1

    log.info(
        "STEP 2~4 완료: 시간표 배정=%d, 매일관리 신규=%d, 교정=%d, 수업충돌=%d, 기존중복=%d, 처리대상=%d",
        len(selected), created, corrected, len(conflicts), len(duplicate_daily_keys), len(created_rows),
    )
    return created_rows


# --------------------------------------------------------------------------
# STEP 4: 검사일/단어시험일 기준 연결 추가 (새 행 생성 금지)
# --------------------------------------------------------------------------

def step4_link_exam_days(today: date, daily_rows: list[dict], timetable_rows: Optional[list[dict]] = None) -> None:
    log.info("STEP 4: 검사일/단어시험일 기준 연결 추가")
    by_class_slot = {}
    for row in daily_rows:
        if (get_date_start(row, "날짜") or "")[:10] != today.isoformat():
            continue
        for ban_id in get_relation_ids(row, BAN_PROP_DAILY):
            slot = get_select_name(row, "수업시간")
            by_class_slot.setdefault((_normalize_id(ban_id), slot), []).append(row)

    for date_prop in ("검사일", "단어시험일"):
        matches = query_database_all(
            TIMETABLE_DB_ID, filter_obj={"property": date_prop, "date": {"equals": today.isoformat()}},
        )
        # 드라이런에서 생성된 시간표는 서버 조회에 없으므로 함께 검증한다.
        unique_matches = {_normalize_id(tt["id"]): tt for tt in matches}
        for tt in timetable_rows or []:
            if get_date_start(tt, date_prop) == today.isoformat():
                unique_matches[_normalize_id(tt["id"])] = tt
        for tt in unique_matches.values():
            daily_time = daily_slot(get_select_name(tt, "시간"))
            targets = {}
            for ban_id in get_relation_ids(tt, BAN_PROP_TT):
                for row in by_class_slot.get((_normalize_id(ban_id), daily_time), []):
                    targets[row["id"]] = row
            if not targets:
                log.warning("검사/시험 연결 대상 없음: 속성=%s 시간=%s 시간표=%s", date_prop, daily_time, tt["id"])
                continue
            tt_teachers = {_normalize_id(i) for i in get_relation_ids(tt, "강사DB")}
            linked = 0
            for row in targets.values():
                teachers = {_normalize_id(i) for i in get_relation_ids(row, "담당")}
                if teachers and tt_teachers and not teachers.intersection(tt_teachers):
                    log.warning("담당 불일치로 연결 보류: daily=%s timetable=%s", row["id"], tt["id"])
                    continue
                if merge_relation(row, date_prop, [tt["id"]]):
                    linked += 1
            log.info("%s 연결 추가: 시간표=%s, %d건", date_prop, tt["id"], linked)


def get_target_date(now: Optional[datetime] = None) -> date:
    if _TARGET_DATE_OVERRIDE:
        return date.fromisoformat(_TARGET_DATE_OVERRIDE)
    current = now if now is not None else datetime.now(ZoneInfo("Asia/Seoul"))
    if current.tzinfo is None:
        raise ValueError("시간대가 있는 datetime이 필요합니다.")
    return current.astimezone(ZoneInfo("Asia/Seoul")).date()


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    today = get_target_date()
    log.info("=== 실행 시작: %s (DRY_RUN=%s) ===", today, DRY_RUN)

    resolve_all_property_names()

    timetable_rows = step1_generate_timetable(today)
    daily_rows = step2_3_generate_daily(today, timetable_rows)
    step4_link_exam_days(today, daily_rows, timetable_rows)

    log.info("=== 실행 완료 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log.exception("실행 중 오류 발생: %s", exc)
        sys.exit(1)
