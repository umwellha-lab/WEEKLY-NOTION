import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cache_sync', Path(__file__).resolve().parents[1] / 'sync_teacher_cache.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
DAY = '2026-10-02'
T = '11111111-1111-1111-1111-111111111111'
C = '22222222-2222-2222-2222-222222222222'
S = '33333333-3333-3333-3333-333333333333'
B = '44444444-4444-4444-4444-444444444444'
L = '55555555-5555-5555-5555-555555555555'
D = '66666666-6666-6666-6666-666666666666'
V = '2026-10-02T01:02:03.000Z'
def text(value): return {'type': 'rich_text', 'rich_text': [{'plain_text': value}]}
def relation(*ids): return {'type': 'relation', 'relation': [{'id': i} for i in ids]}
def select(value): return {'type': 'select', 'select': {'name': value}}
def date(value): return {'type': 'date', 'date': {'start': value}}
def page(i, p): return {'id': i, 'properties': p, 'last_edited_time': V}
def fixtures():
    lesson = page(L, {'반 DB': relation(C), '강사DB': relation(T), '교재이름': relation(B), '시간': select('3시 20'), '출제일': date(DAY), '오늘 수업내용': text(' Reading\n'), '숙제+교재단어': text(' WB\n'), '학원단어': text('20')})
    previous = page('77777777-7777-7777-7777-777777777777', {**lesson['properties'], '출제일': date('2026-10-01'), '검사일': date(DAY), '단어시험일': date('2026-10-03')})
    daily = page(D, {'담당': relation(T), '반 DB': relation(C), '학생': relation(S), '출제 시간표': relation(L), '수업시간': select('0320'), '과제': select('A'), '오답수(학원단어)': {'type':'number','number':0}, '만점(학원)': {'type':'number','number':20}, '검사할 숙제': {'type':'rollup','rollup':{'array':[text('old WB'),text('')]}}, '시험 범위': {'type':'formula','formula':{'string':'19'}}})
    data = {sync.TEACHER_DB_ID:[page(T, {'이름':text('Peter T')})], sync.CLASS_DB_ID:[page(C, {'반 이름':text('11')})], sync.STUDENT_DB_ID:[page(S, {'이름':text('Example')})], sync.BOOK_DB_ID:[page(B, {'이름':text('Reader')})], sync.DAILY_DB_ID:[daily]}
    def query(source, filter_obj=None):
        if source == sync.TIMETABLE_DB_ID: return [previous] if 'or' in filter_obj else [lesson]
        return data[source]
    return data, lesson, previous, query

class TeacherCacheTests(unittest.TestCase):
    def test_complete_records_match_site(self):
        data, lesson, previous, query = fixtures()
        with patch.object(sync, 'query_db', side_effect=query): caches, lessons, daily = sync.build_cache(DAY)
        self.assertEqual((lessons,daily), (1,1))
        body=caches[0]['data']; self.assertTrue(body['complete']);self.assertEqual(body['cache_version'],2)
        row=body['schedule'][0]
        self.assertEqual(row['last_edited_time'],V);self.assertEqual(row['lesson'],' Reading\n')
        self.assertEqual(row['books'],['Reader']);self.assertEqual(row['due'][0]['words'],'')
        self.assertEqual(row['due'][0]['homework'],' WB\n')
        r=body['daily_management'][0]
        self.assertEqual(r['student_name'],'Example');self.assertEqual(r['class_name'],'11')
        self.assertEqual(r['teacher_id'],T);self.assertTrue(r['linked'])
        self.assertEqual(r['academy_vocab_wrong'],0);self.assertIsNone(r['textbook_vocab_wrong'])
        self.assertEqual(r['homework_text'],'old WB');self.assertEqual(r['assignment'],' WB\n')
        self.assertEqual(r['test_range'],'19');self.assertEqual(r['last_edited_time'],V)
    def test_empty_days_include_all_teachers(self):
        data, _, _, _ = fixtures();data[sync.DAILY_DB_ID]=[]
        with patch.object(sync,'query_db',side_effect=lambda source, f=None: [] if source==sync.TIMETABLE_DB_ID else data[source]):
            caches, _, _ = sync.build_cache(DAY)
        self.assertEqual(len(caches),1);self.assertEqual(caches[0]['data']['schedule'],[])
        self.assertTrue(caches[0]['data']['complete'])
    def test_failed_dependency_does_not_upload(self):
        with patch.object(sync,'query_db',side_effect=RuntimeError('read failed')),patch.object(sync,'push_cache') as upload:
            with self.assertRaises(RuntimeError):sync.main()
            upload.assert_not_called()
    def test_unresolved_teacher_stops_snapshot(self):
        data, lesson, _, query=fixtures();lesson['properties']['강사DB']=relation('unknown')
        with patch.object(sync,'query_db',side_effect=query):
            with self.assertRaisesRegex(RuntimeError,'teacher relation'):sync.build_cache(DAY)
    def test_due_respects_books_and_teacher_change(self):
        _, lesson, previous, _=fixtures();previous['properties']['강사DB']=relation('new')
        self.assertEqual(len(sync.due_for_lesson(lesson,[previous],DAY,{sync.norm_id(B):'Reader'})),1)
        previous['properties']['교재이름']=relation('other')
        self.assertEqual(sync.due_for_lesson(lesson,[previous],DAY,{}),[])
    def test_pagination_cycle_and_archived_rows(self):
        result={'results':[{'id':'old','archived':True},{'id':'live'}],'has_more':True,'next_cursor':'repeat'}
        with patch.object(sync,'notion_request',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'pagination'):sync.query_db('source')
        result['has_more']=False
        with patch.object(sync,'notion_request',return_value=result):self.assertEqual(sync.query_db('source'),[{'id':'live'}])
    def test_truncated_relation_is_not_silently_cached(self):
        with self.assertRaises(RuntimeError):sync.rel({'type':'relation','relation':[],'has_more':True})

if __name__ == '__main__':unittest.main()
