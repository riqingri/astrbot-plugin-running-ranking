"""Exercise running confirmation replies against a real SQLite database."""
import asyncio
import importlib
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch


class Plain:
    def __init__(self, text):
        self.text = text


class Node:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Image:
    pass


def decorator(*args, **kwargs):
    return lambda value: value


stubs = {name: types.ModuleType(name) for name in (
    'astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.star',
    'astrbot.api.message_components', 'astrbot.api.web', 'running_test_plugin',
)}
stubs['astrbot.api'].logger = logging.getLogger(__name__)
stubs['astrbot.api.event'].AstrMessageEvent = object
stubs['astrbot.api.event'].filter = types.SimpleNamespace(
    event_message_type=decorator, EventMessageType=types.SimpleNamespace(ALL=0))
stubs['astrbot.api.star'].Context = object
stubs['astrbot.api.star'].Star = type('Star', (), {})
stubs['astrbot.api.star'].register = decorator
stubs['astrbot.api.message_components'].Node = Node
stubs['astrbot.api.message_components'].Plain = Plain
stubs['astrbot.api.message_components'].Image = Image
stubs['astrbot.api.web'].json_response = lambda value: value
stubs['astrbot.api.web'].request = object()
stubs['running_test_plugin'].__path__ = [str(Path(__file__).resolve().parents[1])]
with patch.dict(sys.modules, stubs):
    main = importlib.import_module('running_test_plugin.main')


async def replies(generator):
    return [node.content[0].text async for node in generator]


class RunningDistanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.plugin = main.RunningRankPlugin.__new__(main.RunningRankPlugin)
        self.plugin.db_path = str(Path(tmp.name) / 'running.db')
        self.plugin.init_database()
        self.plugin.get_user_info = lambda event: ('u', 'runner', 'g')
        self.plugin.reply_result = lambda event, node: node
        self.now = datetime(2026, 10, 14, 18)
        self.plugin.get_today_start = lambda: datetime(2026, 10, 14)
        self.plugin.get_week_start = lambda: datetime(2026, 10, 12)
        self.plugin.get_month_start = lambda: datetime(2026, 10, 1)

    def record(self, when, distance, user='u', group='g'):
        conn = self.plugin.get_conn()
        try:
            conn.execute(
                'INSERT INTO running_records '
                '(user_id, user_name, group_id, distance, run_time, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (user, 'runner', group, distance, when, self.now.isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def test_confirmation_includes_monday_history_and_current_run(self):
        self.record('2026-10-12 08:00:00', 4.02)
        self.record('2026-10-12T19:00:00', 5)
        self.record('2026-10-13 18:00:00', 6)
        self.record('2026-10-11T23:59:59.999999', 100)
        self.record('2026-10-19 00:00:00', 100)
        self.record('2026-10-14T12:00:00', 100, user='other')
        self.record('2026-10-14T12:00:00', 100, group='other')
        self.plugin.pending_images = {'g:u': {
            'type': 'running_proof', 'distance': 4.02,
            'expire_time': self.now + timedelta(minutes=5),
        }}
        event = types.SimpleNamespace(message_obj=types.SimpleNamespace(message=[Image()]))
        with patch.object(main, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now
            messages = asyncio.run(replies(self.plugin.handle_pending_image(event)))
        self.assertEqual(len(messages), 1)
        self.assertIn('本次：4.02 km', messages[0])
        self.assertIn('本周累计：19.04 km', messages[0])
        self.assertEqual(self.plugin.pending_images, {})
        summary = asyncio.run(replies(self.plugin.my_distance(event)))
        self.assertIn('本周：19.04 km', summary[0])

    def test_week_boundaries_and_invalid_dates(self):
        self.record('2026-10-11T23:59:59.999999', 100)
        self.record('2026-10-12 00:00:00', 2)
        self.record('2026-10-18T23:59:59.999999', 3)
        self.record('2026-10-19 00:00:00', 7)
        self.record('invalid', 100)
        self.assertEqual(self.plugin.get_week_distance('g', 'u', self.now), 5)
        self.assertEqual(self.plugin.get_week_distance('g', 'u', datetime(2026, 10, 19)), 7)
        self.assertEqual(self.plugin.get_week_distance('g', 'missing', self.now), 0)

    def test_week_crossing_year_boundary(self):
        self.record('2026-12-28 00:00:00', 4)
        self.record('2027-01-03T23:59:59.999999', 5)
        self.record('2027-01-04T00:00:00', 100)
        self.assertEqual(self.plugin.get_week_distance('g', 'u', datetime(2027, 1, 1)), 9)


if __name__ == '__main__':
    unittest.main()
