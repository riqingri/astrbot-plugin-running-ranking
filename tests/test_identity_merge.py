"""Bound identities share historical records and leaderboard totals."""
import importlib
import unittest
from datetime import datetime
from unittest.mock import patch

import test_newbie_points as fixtures
from test_newbie_points import Plugin, stubs

with patch.dict('sys.modules', stubs):
    IdentityMixin = importlib.import_module('services.identity').IdentityMixin
    RunningMixin = importlib.import_module('services.running').RunningMixin


class BoundPlugin(IdentityMixin, RunningMixin, Plugin):
    def get_today_start(self):
        return datetime(2026, 9, 12)

    def get_week_start(self):
        return datetime(2026, 9, 7)

    def get_month_start(self):
        return datetime(2026, 9, 1)


class IdentityMergeTests(unittest.TestCase):
    execute = fixtures.PointsTests.execute
    points = fixtures.PointsTests.points
    assert_rebuild_matches = fixtures.PointsTests.assert_rebuild_matches
    # Reuse the real database fixture and scoring assertions.
    def setUp(self):
        fixtures.PointsTests.setUp(self)
        self.plugin.__class__ = BoundPlugin
        self.plugin.db_path = self.tmp.name + '/running.db'
        self.plugin.init_database()
        self.execute("UPDATE newbie_users SET user_id='12345'")

    def seed(self):
        self.execute('INSERT INTO newbie_users VALUES (?,?,?,?,?,?,?,?)',
                     ('g', '2026_fall', 'openid', 'test', 'male', self.started, 1, self.started))
        for uid, day in [('12345', 10), ('openid', 11), ('openid', 12)]:
            when = f'2026-09-{day}T12:00:00'
            self.execute('INSERT INTO newbie_running_records(group_id,semester,user_id,distance,created_at) VALUES (?,?,?,?,?)',
                         ('g', '2026_fall', uid, 4, when))
            with self.plugin.get_conn() as conn:
                conn.execute('INSERT INTO running_records(user_id,user_name,group_id,distance,run_time,created_at) VALUES (?,?,?,?,?,?)',
                             (uid, 'test', 'g', 4, when, when))
        for uid in ['12345', 'openid']:
            for _ in range(2):
                self.execute('INSERT INTO newbie_training_records(group_id,semester,user_id,admin_id,created_at) VALUES (?,?,?,?,?)',
                             ('g', '2026_fall', uid, 'admin', self.started))
            self.execute('INSERT INTO newbie_running_points(group_id,semester,user_id,year,week,month,points,created_at) VALUES (?,?,?,?,?,?,?,?)',
                         ('g', '2026_fall', uid, 2026, 1, 9, 2, self.started))

    def test_binding_merges_history_and_recalculates_points(self):
        self.seed()
        self.plugin.set_id_mapping('openid', '12345')
        for period, distance in [('today', 4), ('week', 12), ('month', 12), ('total', 12)]:
            rows = self.plugin.query_ranking('g', period)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], '12345')
            self.assertEqual(rows[0][2], distance)
        stats = self.plugin.get_user_stats('g', '12345')
        self.assertEqual(stats['training_points'], 3)
        self.assertEqual(stats['total_points'], 4)
        self.assertEqual(len(self.execute('SELECT * FROM newbie_users')), 1)
        self.assertEqual(self.plugin.get_newbie_leaderboard('g').count('test'), 1)
        self.assert_rebuild_matches()
        # Repeated binding/startup neither duplicates runs nor changes points.
        self.plugin.set_id_mapping('openid', '12345')
        self.plugin.reconcile_bound_users()
        self.assertEqual(self.plugin.get_user_stats('g', '12345'), stats)
        self.assertEqual(len(self.execute('SELECT * FROM newbie_running_records')), 3)
        event = type('Event', (), {'get_sender_id': lambda self: 'openid',
                                  'get_platform_name': lambda self: 'qq_official'})()
        self.assertEqual(self.plugin.get_user_id(event), '12345')

    def test_existing_binding_and_unbound_same_name(self):
        self.seed()
        self.execute('INSERT INTO qq_openid_map VALUES (?,?,?)', ('openid', '12345', self.started))
        self.execute('INSERT INTO newbie_users VALUES (?,?,?,?,?,?,?,?)',
                     ('g', '2026_fall', '67890', 'test', 'male', self.started, 1, self.started))
        self.plugin.reconcile_bound_users()
        self.assertEqual(len(self.execute('SELECT * FROM newbie_users')), 2)
        self.assertEqual(self.plugin.get_newbie_leaderboard('g').count('test'), 2)
        self.assertEqual(self.plugin.query_ranking('g', 'total')[0][2], 12)

    def test_merge_failure_rolls_back_both_databases_and_binding(self):
        self.seed()
        with patch.object(self.plugin, 'update_week_points', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.plugin.set_id_mapping('openid', '12345')
        self.assertEqual(self.execute('SELECT * FROM qq_openid_map'), [])
        self.assertEqual(len(self.execute('SELECT * FROM newbie_users')), 2)
        self.assertEqual(len(self.plugin.query_ranking('g', 'total')), 2)

    def test_multiple_openids_without_existing_qq_profile(self):
        self.seed()
        self.execute("DELETE FROM newbie_users WHERE user_id='12345'")
        self.execute('INSERT INTO newbie_users VALUES (?,?,?,?,?,?,?,?)',
                     ('other-group', '2026_fall', 'second-openid', 'other name', 'female', self.started, 1, self.started))
        self.execute('INSERT INTO qq_openid_map VALUES (?,?,?)', ('openid', '12345', self.started))
        self.execute('INSERT INTO qq_openid_map VALUES (?,?,?)', ('second-openid', '12345', self.started))
        self.plugin.reconcile_bound_users()
        users = self.execute('SELECT * FROM newbie_users ORDER BY group_id')
        self.assertEqual([row['user_id'] for row in users], ['12345', '12345'])
        self.assertEqual([row['group_id'] for row in users], ['g', 'other-group'])
        self.assertEqual(users[1]['gender'], 'female')
        self.assertEqual(self.plugin.get_user_stats('g', '12345')['total_points'], 4)
        self.assertEqual(self.plugin.get_user_stats('other-group', '12345')['total_points'], 0)
        self.assertEqual(self.plugin.query_ranking('other-group', 'total'), [])
        # Automatic binding must respect an existing explicit mapping.
        self.plugin.save_id_mapping('openid', '67890')
        self.assertEqual(self.plugin.lookup_qq_id('openid'), '12345')
        self.assertEqual(self.plugin.query_ranking('g', 'total')[0][0], '12345')
