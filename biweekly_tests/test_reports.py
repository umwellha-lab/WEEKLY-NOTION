import copy
from datetime import date
import unittest
from unittest.mock import patch
import generate_biweekly_reports as app


def prop(kind, value):
    return {'type': kind, kind: value}


def student():
    return {'id': 'a' * 32, 'properties': {
        '이름': prop('title', [{'plain_text': 'TEST'}]),
        '반 DB': prop('relation', []), '상태': prop('select', {'name': '재원'})}}


def record(i, score=None, grade=None, absent=False):
    return {'id': f'{i:032x}', 'properties': {
        '학생': prop('relation', [{'id': 'a' * 32}]),
        '날짜': prop('date', {'start': '2026-09-14'}),
        '과제': prop('select', {'name': grade} if grade else None),
        '백지': prop('select', None),
        '결석 체크': prop('checkbox', absent),
        '재시험 완료': prop('checkbox', False),
        '교재단어점수(숫자)': prop('number', score),
        '학원단어점수(숫자)': prop('number', None)}}


START, END, TODAY = date(2026, 9, 14), date(2026, 9, 27), date(2026, 9, 28)


def aggregate(rows):
    return app.aggregate(student(), rows, START, END, TODAY)


def as_page(properties):
    return {'id': 'b' * 32, 'properties': {
        k: {'type': next(iter(v)), **copy.deepcopy(v)} for k, v in properties.items()}}


class FakeNotion:
    def __init__(self, reports=None):
        self.reports = reports or []
        self.writes = []

    def query(self, source, filter=None):
        return {app.STUDENTS: [student()], app.DAILY: [record(1, 0)],
                app.REPORTS: self.reports}[source]

    def request(self, method, path, body):
        self.writes.append((method, body))
        if method == 'POST':
            self.reports.append(as_page(body['properties']))
        else:
            self.reports[0]['properties'].update(as_page(body['properties'])['properties'])
        return {}


class ReportsTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('STUDENTS', 'test-students'), ('DAILY', 'test-daily'), ('REPORTS', 'test-reports')]:
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_anchor_cross_year_and_completed_period(self):
        self.assertEqual(app.period(date(2026, 9, 28)), (START, END))
        self.assertEqual(app.period(date(2026, 10, 5)), (START, END))
        self.assertEqual(app.period(date(2026, 10, 12))[0], date(2026, 9, 28))
        start, end = app.period(date(2027, 1, 5))
        self.assertEqual((end - start).days, 13)
        self.assertEqual((start - START).days % 14, 0)
        with self.assertRaises(app.SafeError):
            app.period(date(2026, 9, 26))
        with self.assertRaises(app.SafeError):
            app.period(TODAY, '2026-09-15')

    def test_zero_is_not_missing(self):
        result = aggregate([record(1, 0), record(2, 100), record(3)])
        self.assertEqual(result['교재단어 평균']['number'], 50)
        self.assertEqual(result['교재단어 평가수']['number'], 2)
        self.assertIsNone(result['학원단어 평균']['number'])

    def test_absence_dates_and_record_deduplication(self):
        row = record(1, grade='A', absent=True)
        result = aggregate([row, row, record(2, absent=True)])
        self.assertEqual(result['원본 기록수']['number'], 2)
        self.assertEqual(result['결석 체크수']['number'], 2)
        self.assertEqual(result['결석일수']['number'], 1)
        self.assertEqual(result['과제 미기록수']['number'], 1)

    def test_bad_scores_and_truncated_relations_stop(self):
        for score in (-1, 101, float('nan')):
            with self.assertRaises(app.SafeError):
                aggregate([record(1, score)])
        s = student()
        s['properties']['반 DB']['has_more'] = True
        with self.assertRaises(app.SafeError):
            app.aggregate(s, [], START, END, TODAY)

    def test_query_paginates(self):
        client = app.Notion('test-token')
        with patch.object(client, 'request', side_effect=[
            {'results': [1], 'has_more': True, 'next_cursor': 'next'},
            {'results': [2], 'has_more': False}]) as request:
            self.assertEqual(client.query('test'), [1, 2])
            self.assertEqual(request.call_count, 2)

    def run_sync(self, client, write):
        schema = {k: {'type': next(iter(v))} for k, v in aggregate([]).items()}
        with patch.object(app, 'validate_schema', return_value=schema):
            return app.sync(client, START, END, TODAY, write)

    def test_rerun_updates_preserves_teacher_fields(self):
        client = FakeNotion()
        self.run_sync(client, True)
        client.reports[0]['properties']['교사 의견'] = prop('rich_text', [{'plain_text': 'KEEP'}])
        self.run_sync(client, True)
        self.assertEqual(len(client.reports), 1)
        self.assertEqual([w[0] for w in client.writes], ['POST', 'PATCH'])
        self.assertNotIn('교사 의견', client.writes[-1][1]['properties'])
        self.assertNotIn('상태', client.writes[-1][1]['properties'])
        self.assertEqual(app.field(client.reports[0], '교사 의견'), 'KEEP')

    def test_locked_and_dry_run_never_write(self):
        for state in app.LOCKED:
            report = as_page(aggregate([]))
            report['properties']['상태'] = prop('select', {'name': state})
            client = FakeNotion([report])
            self.assertEqual(self.run_sync(client, True)['locked'], 1)
            self.assertEqual(client.writes, [])
        client = FakeNotion()
        self.run_sync(client, False)
        self.assertEqual(client.writes, [])

    def test_duplicates_fail_before_writes(self):
        report = as_page(aggregate([]))
        client = FakeNotion([report, copy.deepcopy(report)])
        with self.assertRaises(app.SafeError):
            self.run_sync(client, True)
        self.assertEqual(client.writes, [])


if __name__ == '__main__':
    unittest.main()
