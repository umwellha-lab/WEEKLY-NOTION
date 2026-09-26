#!/usr/bin/env python3
"""Aggregate Notion daily records. Default: dry run; logs contain counts only."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
import math
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

STUDENTS = os.getenv('NOTION_STUDENTS_SOURCE_ID', '')
DAILY = os.getenv('NOTION_DAILY_SOURCE_ID', '')
REPORTS = os.getenv('NOTION_REPORTS_SOURCE_ID', '')
ANCHOR = date(2026, 9, 14)
LOCKED = {'검수 완료', '발행'}
SCORES = {'교재단어': '교재단어점수(숫자)', '학원단어': '학원단어점수(숫자)'}


class SafeError(Exception):
    """Error message safe for public Actions logs."""


def normalized(value):
    return value.replace('-', '').lower()


def field(page, name):
    prop = page.get('properties', {}).get(name)
    if prop is None:
        raise SafeError('필수 속성이 누락되었습니다. 스키마와 연결 권한을 확인하세요.')
    kind = prop['type']
    value = prop.get(kind)
    if kind in ('title', 'rich_text'):
        return ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in value)
    if kind in ('select', 'status'):
        return value['name'] if value else None
    if kind == 'relation':
        if prop.get('has_more'):
            raise SafeError('관계 속성이 잘렸습니다. 관계 전체 조회가 필요합니다.')
        return [x['id'] for x in value]
    if kind == 'date':
        return value
    return value


def local_date(value):
    if len(value) == 10:
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise SafeError('시간대가 없는 날짜 값입니다.')
    return parsed.astimezone(ZoneInfo('Asia/Seoul')).date()


def period(today, start=None):
    if start:
        first = date.fromisoformat(start)
        if first < ANCHOR or (first - ANCHOR).days % 14:
            raise SafeError('시작일은 2026-09-14부터 14일 간격이어야 합니다.')
    else:
        completed = (today - ANCHOR).days // 14
        if completed < 1:
            raise SafeError('완료된 2주 기간이 없습니다. 수동 시작일을 지정하세요.')
        first = ANCHOR + timedelta(days=(completed - 1) * 14)
    return first, first + timedelta(days=13)


class Notion:
    def __init__(self, token):
        if not token:
            raise SafeError('NOTION_TOKEN이 없습니다.')
        self.token = token

    def request(self, method, path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        request = Request('https://api.notion.com/v1/' + path, data=payload, method=method,
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Notion-Version': '2025-09-03',
                                   'Content-Type': 'application/json'})
        create = method == 'POST' and path == 'pages'
        for attempt in range(5):
            time.sleep(0.35)
            try:
                with urlopen(request, timeout=45) as response:
                    return json.load(response)
            except HTTPError as error:
                # Creation has no API idempotency key. Do not retry ambiguous writes.
                if error.code == 429 or (error.code >= 500 and not create):
                    if attempt < 4:
                        delay = error.headers.get('Retry-After', '')
                        time.sleep(min(float(delay) if delay.isdigit() else 2 ** attempt, 30))
                        continue
                raise SafeError(f'Notion HTTP {error.code}. 연결 권한/스키마 확인 후 재실행하세요.') from None
            except (URLError, TimeoutError):
                if not create and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise SafeError('Notion 통신 실패. 생성 결과 확인 후 재실행하세요.') from None

    def query(self, source, filter=None):
        body = {'page_size': 100}
        if filter:
            body['filter'] = filter
        rows = []
        while True:
            response = self.request('POST', f'data_sources/{source}/query', body)
            rows.extend(response['results'])
            if not response.get('has_more'):
                return rows
            cursor = response.get('next_cursor')
            if not cursor or cursor == body.get('start_cursor'):
                raise SafeError('페이지네이션 응답이 올바르지 않습니다.')
            body['start_cursor'] = cursor


def text_prop(value, kind='rich_text'):
    return {kind: [{'type': 'text', 'text': {'content': value}}]}


def relation(ids):
    unique = list(dict.fromkeys(ids))
    if len(unique) > 100:
        raise SafeError('한 학생의 관계 기록이 100건을 초과했습니다. 수동 검토가 필요합니다.')
    return {'relation': [{'id': value} for value in unique]}


def aggregate(student, rows, start, end, today):
    rows = list({normalized(r['id']): r for r in rows}.values())
    days = [local_date(field(r, '날짜')['start']) for r in rows]
    absent = [local_date(field(r, '날짜')['start']) for r in rows if field(r, '결석 체크')]
    key = f'{normalized(student["id"])}_{start}_{end}'
    result = {
        '성과표': text_prop(f'{field(student, "이름")} | {start:%Y.%m.%d}~{end:%m.%d}', 'title'),
        '학생': relation([student['id']]), '반': relation(field(student, '반 DB')),
        '원본 매일기록': relation([r['id'] for r in rows]),
        '보고 기간': {'date': {'start': str(start), 'end': str(end)}},
        '기록 기준일': {'date': {'start': str(max(days))} if days else None},
        '상태': {'select': {'name': '집계 완료' if rows else '기록 없음'}},
        '집계키': text_prop(key),
        '검수 메모': text_prop(f'{today} 집계. 단어 평균은 기존 숫자 속성 기준. '
                              '미기록은 미실시/미입력 구분 불가. 결석 미체크는 출석 확정 아님.'),
    }
    counts = {'원본 기록수': len(rows), '기록일수': len(set(days)),
              '결석 체크수': len(absent), '결석일수': len(set(absent)),
              '재시험 완료수': sum(bool(field(r, '재시험 완료')) for r in rows)}
    for label in ('과제', '백지'):
        grades = [field(r, label) for r in rows]
        if any(g is not None and g not in ('A', 'B', 'C', 'D') for g in grades):
            raise SafeError('알 수 없는 평가 등급입니다.')
        distribution = Counter(g for g in grades if g)
        counts[label + ' 평가수'] = sum(distribution.values())
        counts[label + ' 미기록수'] = len(rows) - sum(distribution.values())
        result[label + ' 등급분포'] = text_prop(' / '.join(f'{g} {distribution[g]}건' for g in 'ABCD'))
    for label, prop in SCORES.items():
        values = [field(r, prop) for r in rows if field(r, prop) is not None]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or
               not math.isfinite(v) or not 0 <= v <= 100 for v in values):
            raise SafeError('0~100 범위 밖 점수가 있습니다. 원본 확인이 필요합니다.')
        counts[label + ' 평균'] = round(sum(values) / len(values), 1) if values else None
        counts[label + ' 평가수'] = len(values)
    result.update({name: {'number': value} for name, value in counts.items()})
    return result


def validate_schema(client):
    if not all((STUDENTS, DAILY, REPORTS)) or len({STUDENTS, DAILY, REPORTS}) != 3:
        raise SafeError('세 개의 NOTION_*_SOURCE_ID 설정을 확인하세요.')
    schemas = {source: client.request('GET', f'data_sources/{source}')['properties']
               for source in (STUDENTS, DAILY, REPORTS)}
    needed = {
        STUDENTS: {'이름': 'title', '상태': 'select', '반 DB': 'relation'},
        DAILY: {'학생': 'relation', '날짜': 'date', '과제': 'select', '백지': 'select',
                '결석 체크': 'checkbox', '재시험 완료': 'checkbox',
                **{x: 'number' for x in SCORES.values()}},
        REPORTS: {'성과표': 'title', '학생': 'relation', '집계키': 'rich_text',
                  '보고 기간': 'date', '상태': 'select'},
    }
    for source, props in needed.items():
        for name, kind in props.items():
            if schemas[source].get(name, {}).get('type') != kind:
                raise SafeError('필수 속성 유형 불일치. 노션 스키마를 확인하세요.')
    for source, prop, target in ((DAILY, '학생', STUDENTS), (REPORTS, '학생', STUDENTS),
                                  (REPORTS, '원본 매일기록', DAILY)):
        actual = schemas[source].get(prop, {}).get('relation', {}).get('data_source_id', '')
        if normalized(actual) != normalized(target):
            raise SafeError('Relation 대상 또는 연결 권한을 확인하세요.')
    return schemas[REPORTS]


def sync(client, start, end, today, write=False):
    schema = validate_schema(client)
    students = client.query(STUDENTS, {'property': '상태', 'select': {'equals': '재원'}})
    if not students:
        raise SafeError('재원생이 0명입니다. 연결 권한과 상태를 확인하세요.')
    cutoff = min(end, today)
    records = client.query(DAILY, {'and': [
        {'property': '날짜', 'date': {'on_or_after': str(start)}},
        {'property': '날짜', 'date': {'on_or_before': str(cutoff)}}]})
    # Read all reports: keyless legacy rows and duplicate identities must be detected too.
    existing = client.query(REPORTS)
    index = {}
    for report in existing:
        key = field(report, '집계키')
        span, students_in_report = field(report, '보고 기간'), field(report, '학생')
        if span and span.get('start') == str(start) and span.get('end') == str(end):
            if len(students_in_report) != 1:
                raise SafeError('해당 기간 성과표의 학생 연결이 올바르지 않습니다.')
            expected = f'{normalized(students_in_report[0])}_{start}_{end}'
            if key and key != expected:
                raise SafeError('집계키와 학생/기간이 일치하지 않습니다.')
            key = expected
        if key:
            if key in index:
                raise SafeError('중복 성과표가 있습니다. 중복 정리 후 재실행하세요.')
            index[key] = report
    groups = defaultdict(list)
    counts = Counter()
    enrolled = {normalized(s['id']) for s in students}
    for record in records:
        related = field(record, '학생')
        if len(related) != 1:
            counts['unlinked_records'] += 1
            continue
        sid = normalized(related[0])
        if sid not in enrolled:
            counts['non_enrolled_records'] += 1
            continue
        stamp = field(record, '날짜')
        if not stamp or not start <= local_date(stamp['start']) <= cutoff:
            raise SafeError('조회 기간 밖 기록이 있습니다.')
        groups[sid].append(record)
    operations = []
    for student in students:
        props = aggregate(student, groups[normalized(student['id'])], start, end, today)
        for name, value in props.items():
            if schema.get(name, {}).get('type') != next(iter(value)):
                raise SafeError('성과표 속성 유형이 집계 코드와 다릅니다.')
        key = props['집계키']['rich_text'][0]['text']['content']
        previous = index.get(key)
        if previous and field(previous, '상태') in LOCKED:
            counts['locked'] += 1
            continue
        if previous:
            # Never modify reviewer status, teacher comments, next goals, or page body.
            props.pop('상태')
        operations.append((previous, props))
    # All input is checked before the first mutation.
    for previous, props in operations:
        if write:
            if previous:
                client.request('PATCH', 'pages/' + previous['id'], {'properties': props})
            else:
                client.request('POST', 'pages', {'parent': {'type': 'data_source_id',
                               'data_source_id': REPORTS}, 'properties': props})
        counts['updated' if previous else 'created'] += 1
    counts['students'] = len(students)
    counts['records'] = sum(len(r) for r in groups.values())
    return dict(counts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default=os.getenv('PERIOD_START', ''))
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args()
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    if args.scheduled and ((today - ANCHOR).days < 14 or (today - ANCHOR).days % 14):
        print('이번 주는 2주 집계일이 아닙니다.')
        return
    start, end = period(today, args.start)
    if start > today:
        raise SafeError('미래 기간은 집계할 수 없습니다.')
    write = args.write or os.getenv('APPLY_CHANGES', '').lower() == 'true'
    counts = sync(Notion(os.getenv('NOTION_TOKEN', '')), start, end, today, write)
    print(json.dumps({'period': f'{start}/{end}', 'write': write, **counts}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except SafeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Do not expose request payloads, student records, or authentication headers.
        print('예상하지 못한 오류입니다. 입력 날짜 및 API 응답 형식을 확인하세요.', file=sys.stderr)
        sys.exit(1)
