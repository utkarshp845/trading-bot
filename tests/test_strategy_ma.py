from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.strategy_ma import classify_time_window_et


UTC = ZoneInfo("UTC")


class StrategyTimeWindowTests(unittest.TestCase):
    def test_classify_time_window_uses_bar_close_in_et(self):
        bar_open_utc = datetime(2026, 4, 2, 14, 15, tzinfo=UTC)
        bucket = classify_time_window_et(bar_open_utc, 5, ((930, 1600),))
        self.assertEqual(bucket, "09:30-16:00")

    def test_classify_time_window_rejects_premarket_bar_close(self):
        bar_open_utc = datetime(2026, 4, 2, 13, 20, tzinfo=UTC)
        bucket = classify_time_window_et(bar_open_utc, 5, ((930, 1600),))
        self.assertIsNone(bucket)

    def test_classify_time_window_accepts_pandas_timestamp_inputs(self):
        bar_open_utc = pd.Timestamp("2026-04-02T14:15:00Z")
        bucket = classify_time_window_et(bar_open_utc, 5, ((930, 1600),))
        self.assertEqual(bucket, "09:30-16:00")


if __name__ == "__main__":
    unittest.main()
