"""Run with: python -B -m unittest discover -s tests -v (no AstrBot runtime required)."""
import asyncio
import importlib
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

# Only mock the messaging/web framework; exercise the actual services and SQLite schema.
stubs = {name: types.ModuleType(name) for name in (
    'astrbot', 'astrbot.api', 'astrbot.api.event',
    'astrbot.api.message_components', 'astrbot.api.web',
)}
stubs['astrbot.api'].logger = logging.getLogger(__name__)
stubs['astrbot.api.event'].AstrMessageEvent = object
stubs['astrbot.api.message_components'].Node = object
stubs['astrbot.api.message_components'].Plain = object
stubs['astrbot.api.web'].json_response = lambda value: value
stubs['astrbot.api.web'].request = object()
with patch.dict(sys.modules, stubs):
    newbie = importlib.import_module('services.newbie')
    web = importlib.import_module('web_api')
from database import DatabaseMixin


class Plugin(newbie.NewbieMixin, web.WebApiMixin, DatabaseMixin):
    def get_newbie_conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class PointsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugin = Plugin()
        self.plugin.path = str(Path(self.tmp.name) / 'newbie.db')
        self.plugin.newbie_conn = self.plugin.get_newbie_conn()
        self.plugin.init_newbie_database()
        self.plugin.newbie_conn.close()
        self.started = '2026-09-09T15:00:00'
        self.execute('INSERT INTO newbie_users VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     ('g', '2026_fall', 'u', 'test', 'male', self.started, 1, self.started))

    def execute(self, sql, args=()):
        conn = self.plugin.get_newbie_conn()
        try:
            cursor = conn.execute(sql, args)
            rows = cursor.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def run_record(self, when, distance=4):
        self.execute('INSERT INTO newbie_running_records (group_id, semester, user_id, distance, created_at) VALUES (?, ?, ?, ?, ?)',
                     ('g', '2026_fall', 'u', distance, when))

    def recompute(self, when):
        self.plugin.recompute_week_points('g', '2026_fall', 'u', datetime.fromisoformat(when))

    def points(self):
        return {(r['year'], r['week']): r['points'] for r in self.execute('SELECT * FROM newbie_running_points')}

    def assert_rebuild_matches(self):
        expected, changes = self.plugin._calculate_newbie_points_rebuild()
        self.assertEqual({(k[3], k[4]): v['points'] for k, v in expected.items()}, self.points())
        self.assertEqual(changes, [])

    def test_all_join_weekdays_and_stage_boundaries(self):
        monday = datetime(2026, 9, 7)
        for weekday in range(7):
            start = monday + timedelta(days=weekday, hours=15)
            for stage, weeks in [(2, 4), (3, 8), (4, 12)]:
                boundary = monday + timedelta(weeks=weeks + bool(weekday))
                for ref, expected in [(boundary-timedelta(microseconds=1), stage-1), (boundary, stage)]:
                    with self.subTest(start=start, ref=ref):
                        self.assertEqual(self.plugin.get_running_stage(start.isoformat(), ref)[0], expected)
                        self.assertEqual(self.plugin.get_stage_week(start.isoformat(), ref)[0], expected)
            first_end = monday + timedelta(weeks=1 if weekday == 0 else 2)
            self.assertEqual(self.plugin.get_program_week(start.isoformat(), first_end-timedelta(microseconds=1))[1], 1)
            self.assertEqual(self.plugin.get_program_week(start.isoformat(), first_end), (first_end, 2))
        self.assertEqual(self.plugin.get_running_stage(self.started, datetime(2026,10,11,16)), (1,1))
        self.assertEqual(self.plugin.get_stage_week(self.started, datetime(2026,10,12)), (2,1))

    def test_merged_first_week_and_distance_filter(self):
        # Before enrollment and short runs must not contribute; week 2 is excluded.
        for when, km in [('2026-09-09T14:00:00',4), ('2026-09-09T15:00:00',4),
                         ('2026-09-13T12:00:00',4), ('2026-09-20T23:59:59',4),
                         ('2026-09-15T12:00:00',1), ('2026-09-21T00:00:00',4)]:
            self.run_record(when, km)
        self.recompute('2026-09-20T23:59:59')
        self.assertEqual(self.points(), {(2026,1):1})
        self.run_record('2026-09-19T12:00:00')
        self.recompute('2026-09-09T15:00:00')
        self.assertEqual(self.points(), {(2026,1):2})
        self.assert_rebuild_matches()

    def test_short_runs_and_delete_remove_points(self):
        for day in [10,11,12]:
            self.run_record(f'2026-09-{day}T12:00:00',1)
        self.recompute('2026-09-12T12:00:00')
        self.assertEqual(self.points(), {})
        self.execute('UPDATE newbie_running_records SET distance = 4')
        self.recompute('2026-09-12T12:00:00')
        self.assertEqual(self.points(), {(2026,1):1})
        self.execute('DELETE FROM newbie_running_records WHERE id = 1')
        self.recompute('2026-09-12T12:00:00')
        self.assertEqual(self.points(), {})
        self.assert_rebuild_matches()

    def test_stage_two_uses_new_distance_and_frequency(self):
        for day in [12,13,14,15]:
            self.run_record(f'2026-10-{day}T00:00:00',5)
        for day in [16,17]:
            self.run_record(f'2026-10-{day}T12:00:00',4)
        self.recompute('2026-10-12T00:00:00')
        self.assertEqual(self.points(), {(2026,5):1})
        self.assert_rebuild_matches()

    def test_rebuild_endpoints_replace_stale_points_and_are_idempotent(self):
        for day in [10,11,12]:
            self.run_record(f'2026-09-{day}T12:00:00')
        self.execute('INSERT INTO newbie_running_points (group_id,semester,user_id,year,week,month,points,created_at) VALUES (?,?,?,?,?,?,?,?)',
                     ('g','2026_fall','u',2026,99,9,10,self.started))
        with patch.object(web, '_read_body', AsyncMock(return_value={'group_id':'g'})):
            preview = asyncio.run(self.plugin._api_preview_newbie_points_rebuild())['data']
            self.assertEqual(preview['changes'][0]['old_points'],10)
            self.assertEqual(preview['changes'][0]['new_points'],1)
            self.assertEqual(self.points(), {(2026,99):10})
            result = asyncio.run(self.plugin._api_apply_newbie_points_rebuild())['data']
            self.assertEqual(result['written'],1)
            self.assertEqual(self.points(), {(2026,1):1})
            self.assert_rebuild_matches()
            asyncio.run(self.plugin._api_apply_newbie_points_rebuild())
            self.assert_rebuild_matches()

    def test_no_total_running_cap_and_training_cap_unchanged(self):
        self.started='2026-09-07T15:00:00'
        self.execute('UPDATE newbie_users SET points_started_at=?', (self.started,))
        for week in range(17):
            ref = datetime(2026,9,7,16) + timedelta(weeks=week)
            for day in range(6):
                self.run_record((ref+timedelta(days=day)).isoformat(),6)
            self.recompute(ref.isoformat())
        self.assertEqual(sum(self.points().values()),34)
        self.assert_rebuild_matches()
        for _ in range(20):
            self.execute('INSERT INTO newbie_training_records(group_id,semester,user_id,admin_id,created_at) VALUES (?,?,?,?,?)',
                         ('g','2026_fall','u','admin',self.started))
        self.assertEqual(self.plugin.get_user_stats('g','u')['training_points'],12)

    def test_year_boundary_and_female_threshold(self):
        self.started = '2026-12-30T15:00:00'
        self.execute("UPDATE newbie_users SET gender='female', points_started_at=?",(self.started,))
        for when in ['2026-12-30T16:00:00','2027-01-03T12:00:00','2027-01-10T12:00:00']:
            self.run_record(when,2)
        self.recompute('2027-01-10T12:00:00')
        self.assertEqual(self.points(),{(2026,1):1})
        self.assert_rebuild_matches()

    def prepare_photo_confirmation(self):
        main_path = str(Path(self.tmp.name) / 'main.db')
        conn = sqlite3.connect(main_path)
        conn.execute('CREATE TABLE running_records (user_id TEXT, user_name TEXT, group_id TEXT, distance REAL, run_time TEXT, created_at TEXT)')
        conn.close()
        self.plugin.get_conn = lambda: sqlite3.connect(main_path)
        self.plugin.get_group_id = lambda event: 'g'
        self.plugin.get_user_id = lambda event: 'u'
        self.plugin.get_user_info = lambda event: ('u', 'test')
        self.plugin.get_display_name = lambda event, name: name

    def test_fourth_run_reply_with_legacy_timestamp_format(self):
        self.prepare_photo_confirmation()
        for hour in [8, 10, 12]:
            self.run_record(f'2026-09-21 {hour:02d}:00:00')
        # The points page/rebuild already recognizes all three historical runs.
        with patch.object(web, '_read_body', AsyncMock(return_value={'group_id': 'g'})):
            asyncio.run(self.plugin._api_apply_newbie_points_rebuild())
        self.assertEqual(self.points(), {(2026, 2): 1})

        with patch.object(newbie, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 21, 18)
            message, _ = asyncio.run(self.plugin.confirm_newbie_running(object(), 4.02))
        self.assertIn('本周完成：4 次', message)
        self.assertIn('本周积分：2 分', message)
        self.assertIn('本次积分变化：+1', message)
        self.assertIn('本周最高积分已完成', message)
        self.assertEqual(self.points(), {(2026, 2): 2})
        self.assert_rebuild_matches()

    def test_mixed_timestamp_week_boundaries(self):
        for when in ['2026-09-21 00:00:00', '2026-09-23T12:00:00',
                     '2026-09-27 23:59:59.999999', '2026-09-28 00:00:00',
                     '2026-09-28 12:00:00',
                     '2026-09-20T23:59:59.999999', 'invalid']:
            self.run_record(when)
        self.recompute('2026-09-23T12:00:00')
        self.assertEqual(self.points(), {(2026, 2): 1})
        self.assert_rebuild_matches()

    def test_mixed_timestamp_enrollment_boundary(self):
        self.execute('UPDATE newbie_users SET points_started_at=?',
                     ('2026-09-09T15:00:00.123456',))
        for when in ['2026-09-09 15:00:00.123455', '2026-09-09 15:00:00.123456',
                     '2026-09-09T16:00:00', '2026-09-10 12:00:00']:
            self.run_record(when)
        self.recompute('2026-09-10T12:00:00')
        self.assertEqual(self.points(), {(2026, 1): 1})
        self.assert_rebuild_matches()

    def test_photo_confirmation_rechecks_stage_and_matches_rebuild(self):
        self.prepare_photo_confirmation()

        class Clock(datetime):
            current = datetime(2026,9,20,12)

            @classmethod
            def now(cls):
                return cls.current

        self.run_record('2026-09-09T15:00:00')
        self.run_record('2026-09-13T12:00:00')
        with patch.object(newbie, 'datetime', Clock):
            image = types.SimpleNamespace(convert_to_file_path=AsyncMock())
            event = types.SimpleNamespace(message_obj=types.SimpleNamespace(message=[image]))
            message, _ = asyncio.run(self.plugin.confirm_newbie_running(event, 4))
            image.convert_to_file_path.assert_not_awaited()
            self.assertIn('本周积分：1 分', message)
            self.assertEqual(self.points(), {(2026,1):1})
            self.assert_rebuild_matches()
            # A 4km submission made before the boundary is no longer eligible
            # when its photo arrives in stage 2.
            Clock.current = datetime(2026,10,12)
            message, _ = asyncio.run(self.plugin.confirm_newbie_running(object(), 4))
            self.assertIn('未达标', message)
            self.assertEqual(len(self.execute('SELECT * FROM newbie_running_records')), 3)

    def test_import_limits_reject_before_inserting(self):
        header = 'group_id,semester,user_id,distance,created_at\n'
        row = 'g,2026_fall,u,4,2026-09-10T12:00:00\n'
        result = self.plugin._import_table('newbie_running_records', header + row * 10001)
        self.assertEqual(result['success'], 0)
        self.assertEqual(len(self.execute('SELECT * FROM newbie_running_records')), 0)
        result = self.plugin._import_table('newbie_running_records', 'x' * 2000001)
        self.assertEqual(result['success'], 0)
        self.assertIn('过大', result['errors'][0]['reason'])

    def test_streamed_csv_import_preserves_points_and_mirror(self):
        header = 'group_id,semester,user_id,distance,created_at\n'
        csv_text = header + ''.join(f'g,2026_fall,u,4,2026-09-{day}T12:00:00\n' for day in [10,13,20])
        mirrored = []
        self.plugin._mirror_newbie_running_to_main = mirrored.extend
        result = self.plugin._import_table('newbie_running_records', csv_text)
        self.assertEqual(result['success'], 3)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(len(mirrored), 3)
        self.assertEqual(self.points(), {(2026,1):1})
        self.assert_rebuild_matches()

    def test_five_two_point_rows_show_ten_on_leaderboard(self):
        self.execute("UPDATE newbie_users SET group_id='1033668754', user_id='2665256394'")
        for week in range(1, 6):
            self.execute(
                'INSERT INTO newbie_running_points(group_id,semester,user_id,year,week,month,points,created_at) VALUES (?,?,?,?,?,?,?,?)',
                ('1033668754', '2026_fall', '2665256394', 2026, week, 10 if week == 5 else 9, 2, self.started),
            )
        with self.assertLogs(newbie.logger, level='INFO') as logs:
            board = self.plugin.get_newbie_leaderboard('1033668754', self_user_id='2665256394')
        self.assertIn('你的排名：第1名 ｜ 积分 10', board)
        self.assertIn('test ｜ 积分 10', board)
        self.assertIn('2665256394', logs.output[0])
        self.assertIn("'跑步积分': 10", logs.output[0])
        self.assertIn(self.plugin.path, logs.output[0])

    def test_unstarted_and_invalid_start(self):
        for start in [None, '', 'invalid', '2026-09-10T00:00:00']:
            self.assertEqual(self.plugin.get_program_week(start,datetime(2026,9,9)), (None,0))
        self.execute('UPDATE newbie_users SET points_started=0')
        for day in [10,11,12]:
            self.run_record(f'2026-09-{day}T12:00:00')
        self.recompute('2026-09-12T12:00:00')
        self.assertEqual(self.points(),{})
        self.assert_rebuild_matches()


if __name__ == '__main__':
    unittest.main()
