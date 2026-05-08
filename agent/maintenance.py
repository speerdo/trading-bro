"""
Maintenance window checker

Friday 5:00–6:00 PM ET: exchange closed.
Quarterly 3-hour window: also closed.
"""

from datetime import datetime, time as dt_time, timedelta
from typing import Optional

import pytz
from loguru import logger


class MaintenanceWindow:
    """Checks if Coinbase FCM is in maintenance."""

    WEEKLY_START = dt_time(17, 0)   # 5:00 PM ET
    WEEKLY_END = dt_time(18, 0)     # 6:00 PM ET
    WEEKLY_DAY = 4                   # Friday

    def __init__(self):
        self.tz = pytz.timezone("America/New_York")

    def is_open(self) -> bool:
        """Returns True if market is OPEN (not in maintenance)."""
        now = datetime.now(self.tz)
        weekday = now.weekday()
        current_time = now.time()

        # Friday 5-6 PM ET
        if weekday == self.WEEKLY_DAY and self.WEEKLY_START <= current_time < self.WEEKLY_END:
            logger.info("Weekly maintenance window (Fri 5-6 PM ET) — skipping")
            return False

        return True

    def time_to_next_maintenance(self) -> Optional[float]:
        """Seconds until next Friday 5PM ET maintenance."""
        now = datetime.now(self.tz)
        days_ahead = self.WEEKLY_DAY - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
        next_maintenance = now.replace(hour=17, minute=0, second=0, microsecond=0)
        next_maintenance += timedelta(days=days_ahead)
        if next_maintenance < now:
            next_maintenance += timedelta(days=7)
        return (next_maintenance - now).total_seconds()
