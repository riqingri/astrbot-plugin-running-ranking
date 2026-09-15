from datetime import datetime, timedelta


class TimeMixin:
    """时间范围工具方法。"""

    def get_today_start(self):

        now = datetime.now()

        return now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    def get_week_start(self):

        now = datetime.now()

        monday = (
            now
            - timedelta(
                days=now.weekday()
            )
        )

        return monday.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    def get_month_start(self):

        now = datetime.now()

        return now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
