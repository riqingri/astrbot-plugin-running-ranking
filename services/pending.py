from datetime import datetime, timedelta


class PendingTasks(dict):
    """Small bounded waiting queue; abandoned requests expire on subsequent inserts."""

    def __init__(self, ttl_seconds=300, max_entries=4096):
        super().__init__()
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries

    def __setitem__(self, key, value):
        now = datetime.now()
        for old_key, task in list(self.items()):
            expires = task.get("expire_time")
            if expires is None:
                expires = task["created_at"] + timedelta(seconds=self.ttl_seconds)
            if expires < now:
                del self[old_key]
        # Refresh insertion order when the same user submits a replacement.
        self.pop(key, None)
        if len(self) >= self.max_entries:
            del self[next(iter(self))]
        super().__setitem__(key, value)
