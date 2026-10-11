import unittest
from datetime import datetime, timedelta
from services.pending import PendingTasks


class PendingTests(unittest.TestCase):
    def test_abandoned_tasks_expire_for_both_formats(self):
        queue = PendingTasks()
        queue['run'] = {'created_at': datetime.now() - timedelta(minutes=6)}
        queue['image'] = {'expire_time': datetime.now() - timedelta(seconds=1)}
        queue['active'] = {'created_at': datetime.now()}
        self.assertEqual(list(queue), ['active'])

    def test_capacity_and_refresh(self):
        queue = PendingTasks(max_entries=2)
        for key in ['first', 'second', 'first', 'third']:
            queue[key] = {'created_at': datetime.now()}
        self.assertEqual(list(queue), ['first', 'third'])


if __name__ == '__main__':
    unittest.main()
