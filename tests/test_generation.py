import copy
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import generate_weekly_daily_management as g


TODAY = date(2026, 9, 8)


def rel(*ids):
    return {"relation": [{"id": i} for i in ids]}


def row(pid, **props):
    return {"id": pid, "properties": props}


def timetable(pid="tt1", teacher="teacher1", slot="7시 20", class_id="class1"):
    return row(pid, 반=rel(class_id), 시간={"select": {"name": slot}},
               강사DB=rel(teacher), 요일={"select": {"name": "화"}},
               검사일={"date": {"start": TODAY.isoformat()}},
               단어시험일={"date": {"start": TODAY.isoformat()}})


def daily(pid="daily1", teacher="teacher1", class_id="class1"):
    return row(pid, 반=rel(class_id), 학생=rel("student1"), 담당=rel(teacher),
               수업시간={"select": {"name": "0720"}},
               날짜={"date": {"start": TODAY.isoformat()}})


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.globals = patch.multiple(g, BAN_PROP_TT="반", BAN_PROP_DAILY="반",
                                      BAN_PROP_STUDENT="반", STUDENT_PROP_TT=None,
                                      DRY_RUN=True, _TARGET_DATE_OVERRIDE="")
        self.globals.start()
        self.addCleanup(self.globals.stop)

    def test_new_timetable_copies_teacher_weekday_but_clears_lesson_fields(self):
        source = timetable()
        for name in ("오늘 수업내용", "숙제+교재단어", "학원단어"):
            source["properties"][name] = {"type": "rich_text", "rich_text": [{"text": {"content": "previous lesson"}}]}
        with patch.object(g, "query_database_all", side_effect=[[source], []]):
            result = g.step1_generate_timetable(TODAY)
        props = result[0]["properties"]
        self.assertEqual(g.get_relation_ids(result[0], "강사DB"), ["teacher1"])
        self.assertEqual(g.get_select_name(result[0], "요일"), "화")
        for name in ("오늘 수업내용", "숙제+교재단어", "학원단어"):
            self.assertEqual(props[name], {"rich_text": []})
        for name in ("검사일", "단어시험일"):
            self.assertEqual(props[name], {"date": None})

    def test_existing_timetable_fills_weekday_without_erasing_lesson(self):
        existing = timetable()
        existing["properties"]["요일"] = {"select": None}
        existing["properties"]["숙제+교재단어"] = {"rich_text": [{"text": {"content": "current homework"}}]}
        with patch.object(g, "query_database_all", side_effect=[[timetable()], [existing]]), patch.object(g, "update_page"):
            result = g.step1_generate_timetable(TODAY)
        self.assertEqual(g.get_select_name(result[0], "요일"), "화")
        self.assertEqual(g.get_rich_text(result[0], "숙제+교재단어"), "current homework")

    def test_missing_or_conflicting_source_teacher_prevents_creation(self):
        missing = timetable()
        missing["properties"]["강사DB"] = rel()
        for sources in ([missing], [timetable(), timetable("tt2", teacher="other")]):
            with patch.object(g, "query_database_all", side_effect=[sources, []]), patch.object(g, "create_page") as create:
                with self.assertRaises(RuntimeError):
                    g.step1_generate_timetable(TODAY)
                create.assert_not_called()

    def test_korean_date_at_scheduled_utc_time(self):
        self.assertEqual(g.get_target_date(datetime(2026, 9, 7, 21, tzinfo=timezone.utc)), TODAY)
        self.assertEqual(g.get_target_date(datetime(2026, 9, 7, 14, 59, tzinfo=timezone.utc)), date(2026, 9, 7))
        with patch.object(g, "_TARGET_DATE_OVERRIDE", "2026-09-04"):
            self.assertEqual(g.get_target_date(), date(2026, 9, 4))

    def test_multiple_due_relations_preserved_and_rerun_noop(self):
        target = daily()
        target["properties"]["검사일"] = rel("older")
        with patch.object(g, "query_database_all", return_value=[timetable("tt1"), timetable("tt2")]), patch.object(g, "update_page") as update:
            g.step4_link_exam_days(TODAY, [target])
            self.assertEqual(g.get_relation_ids(target, "검사일"), ["older", "tt1", "tt2"])
            self.assertEqual(g.get_relation_ids(target, "단어시험일"), ["tt1", "tt2"])
            count = update.call_count
            g.step4_link_exam_days(TODAY, [target])
            self.assertEqual(update.call_count, count)

    def test_teacher_check_is_per_student(self):
        wrong, right = daily("wrong", "other"), daily("right")
        with patch.object(g, "query_database_all", return_value=[timetable()]), patch.object(g, "update_page"):
            g.step4_link_exam_days(TODAY, [wrong, right])
        self.assertEqual(g.get_relation_ids(wrong, "검사일"), [])
        self.assertEqual(g.get_relation_ids(right, "검사일"), ["tt1"])

    def test_dry_run_includes_new_timetables_and_never_writes(self):
        target = daily()
        with patch.object(g, "query_database_all", return_value=[]), patch.object(g, "_request") as request:
            g.step4_link_exam_days(TODAY, [target], [timetable()])
        request.assert_not_called()
        self.assertEqual(g.get_relation_ids(target, "검사일"), ["tt1"])

    def test_existing_daily_backfilled_without_duplicate(self):
        target = daily()
        target["properties"]["담당"] = rel()
        student = row("student1", 이름={"type": "title", "title": [{"plain_text": "Test Student"}]})
        with patch.object(g, "query_database_all", return_value=[target]), patch.object(g, "get_active_students_for_class", return_value=[student]), patch.object(g, "title_lookup", return_value="Test"), patch.object(g, "create_page") as create, patch.object(g, "update_page") as update:
            result = g.step2_3_generate_daily(TODAY, [timetable()])
            self.assertEqual(len(result), 1)
            self.assertEqual(g.get_relation_ids(target, "출제 시간표"), ["tt1"])
            self.assertEqual(g.get_relation_ids(target, "담당"), ["teacher1"])
            count = update.call_count
            g.step2_3_generate_daily(TODAY, [timetable()])
            self.assertEqual(update.call_count, count)
            create.assert_not_called()

    def test_two_slots_create_two_rows_and_connect_students(self):
        student = row("student1", 이름={"title": [{"text": {"content": "Test"}}]})
        slots = [timetable(), timetable("tt2", slot="8시 40")]
        with patch.object(g, "query_database_all", return_value=[]), patch.object(g, "get_active_students_for_class", return_value=[student]) as students, patch.object(g, "title_lookup", return_value="Test"), patch.object(g, "STUDENT_PROP_TT", "수강생"), patch.object(g, "update_page"):
            result = g.step2_3_generate_daily(TODAY, slots)
        self.assertEqual([g.get_select_name(r, "수업시간") for r in result], ["0720", "0840"])
        self.assertEqual(students.call_count, 1)
        for tt in slots:
            self.assertEqual(g.get_relation_ids(tt, "수강생"), ["student1"])

    def test_temporary_class_wins_before_timetable_roster_and_daily_creation(self):
        student = row("student1", 이름={"title": [{"text": {"content": "장민준"}}]})
        mixed = timetable("mixed-tt", class_id="normal-class")
        mixed["properties"]["반"] = rel("normal-class", "temp-class")

        def class_students(_class_id):
            return [student]

        def page_title(page_id):
            return {
                "normal-class": "45-중1",
                "temp-class": "[임시] 환서1 32/46",
                "teacher1": "Teacher",
            }.get(page_id, page_id)

        with patch.object(g, "query_database_all", return_value=[]), \
                patch.object(g, "get_active_students_for_class", side_effect=class_students), \
                patch.object(g, "title_lookup", side_effect=page_title), \
                patch.object(g, "STUDENT_PROP_TT", "학생 DB1"), \
                patch.object(g, "update_page"):
            rows = g.step2_3_generate_daily(TODAY, [mixed])

        self.assertEqual(g.get_relation_ids(mixed, "학생 DB1"), ["student1"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(g.get_relation_ids(rows[0], "반"), ["temp-class"])
        self.assertEqual(g.get_relation_ids(rows[0], "출제 시간표"), ["mixed-tt"])

    def test_existing_normal_daily_is_corrected_to_temporary_without_new_row(self):
        student = row("student1", 이름={"title": [{"text": {"content": "장민준"}}]})
        existing = daily(class_id="normal-class")
        existing["properties"]["출제 시간표"] = rel("normal-tt")
        normal = timetable("normal-tt", class_id="normal-class")
        temporary = timetable("temp-tt", teacher="temp-teacher", class_id="temp-class")

        def page_title(page_id):
            return {
                "normal-class": "45-중1",
                "temp-class": "[금] 천재이 1",
                "teacher1": "Normal Teacher",
                "temp-teacher": "Temp Teacher",
            }.get(page_id, page_id)

        with patch.object(g, "query_database_all", return_value=[existing]), \
                patch.object(g, "get_active_students_for_class", return_value=[student]), \
                patch.object(g, "title_lookup", side_effect=page_title), \
                patch.object(g, "update_page"), patch.object(g, "create_page") as create:
            rows = g.step2_3_generate_daily(TODAY, [normal, temporary])

        create.assert_not_called()
        self.assertEqual(rows, [existing])
        self.assertEqual(g.get_relation_ids(existing, "반"), ["temp-class"])
        self.assertEqual(g.get_relation_ids(existing, "담당"), ["temp-teacher"])
        self.assertEqual(g.get_relation_ids(existing, "출제 시간표"), ["temp-tt"])

    def test_two_temporary_classes_same_slot_are_reported_and_not_generated(self):
        student = row("student1", 이름={"title": [{"text": {"content": "장민준"}}]})
        first = timetable("temp-1", class_id="class-a")
        second = timetable("temp-2", class_id="class-b")

        def page_title(page_id):
            return {"class-a": "[임시] A", "class-b": "[임시] B"}.get(page_id, page_id)

        with patch.object(g, "query_database_all", return_value=[]), \
                patch.object(g, "get_active_students_for_class", return_value=[student]), \
                patch.object(g, "title_lookup", side_effect=page_title), \
                patch.object(g, "STUDENT_PROP_TT", "학생 DB1"), \
                patch.object(g, "update_page"), patch.object(g, "create_page") as create:
            rows = g.step2_3_generate_daily(TODAY, [first, second])

        create.assert_not_called()
        self.assertEqual(rows, [])
        self.assertEqual(g.get_relation_ids(first, "학생 DB1"), [])
        self.assertEqual(g.get_relation_ids(second, "학생 DB1"), [])

    def test_existing_duplicate_blocks_only_that_student_slot(self):
        duplicate_a = daily("duplicate-a")
        duplicate_b = daily("duplicate-b")
        other_student = row("student2", 이름={"title": [{"text": {"content": "Other"}}]})
        target_timetable = timetable()

        with patch.object(g, "query_database_all", return_value=[duplicate_a, duplicate_b]), \
                patch.object(g, "get_active_students_for_class", return_value=[
                    row("student1", 이름={"title": [{"text": {"content": "Duplicate"}}]}),
                    other_student,
                ]), patch.object(g, "title_lookup", return_value="Test"), \
                patch.object(g, "update_page"):
            rows = g.step2_3_generate_daily(TODAY, [target_timetable])

        self.assertEqual(len(rows), 1)
        self.assertEqual(g.get_relation_ids(rows[0], "학생"), ["student2"])

    def test_timetable_teacher_backfill_visible_in_returned_rows(self):
        existing = timetable()
        existing["properties"]["강사DB"] = rel()
        with patch.object(g, "query_database_all", side_effect=[[timetable()], [existing]]), patch.object(g, "update_page"), patch.object(g, "create_page") as create:
            rows = g.step1_generate_timetable(TODAY)
        self.assertEqual(g.get_relation_ids(rows[0], "강사DB"), ["teacher1"])
        create.assert_not_called()

    def test_wrong_slot_not_linked_and_no_daily_created(self):
        with patch.object(g, "query_database_all", return_value=[timetable(slot="8시 40")]), patch.object(g, "update_page") as update, patch.object(g, "create_page") as create:
            g.step4_link_exam_days(TODAY, [daily()])
        update.assert_not_called()
        create.assert_not_called()

    def test_relation_pagination_keeps_all_existing_links(self):
        target = row("daily1", 검사일={"id": "abc", "relation": [{"id": "truncated"}], "has_more": True})
        pages = [{"results": [{"relation": {"id": "first"}}], "has_more": True, "next_cursor": "next"},
                 {"results": [{"relation": {"id": "second"}}], "has_more": False}]
        with patch.object(g, "_request", side_effect=pages) as request, patch.object(g, "update_page"):
            g.merge_relation(target, "검사일", ["third"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(g.get_relation_ids(target, "검사일"), ["first", "second", "third"])

    def test_ambiguous_property_and_unknown_time_rejected(self):
        with self.assertRaises(RuntimeError):
            g.resolve_property_name({"properties": {"A 반 DB": {}, "B 반 DB": {}}}, "반 DB")
        self.assertEqual(g.resolve_property_name({"properties": {"반 DB": {}, "과거 반 DB": {}}}, "반 DB"), "반 DB")
        with self.assertRaises(RuntimeError):
            g.daily_slot("unknown")

    def test_reenrollment_filter_uses_schema_status_type(self):
        with patch.object(g, "STUDENT_STATUS_TYPE", "status"), patch.object(g, "query_database_all") as query:
            g.get_active_students_for_class("class1")
        self.assertEqual(query.call_args.kwargs["filter_obj"]["and"][1], {"property": "상태", "status": {"equals": "재원"}})


if __name__ == "__main__":
    unittest.main()
