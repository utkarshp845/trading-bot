from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

from bot.strategy_ma import _in_valid_trade_window_et, classify_time_window_et


UTC = ZoneInfo("UTC")


class StrategyTimeWindowTests(unittest.TestCase):
    def test_allows_bar_that_closes_at_morning_window_end(self):
        bar_open_utc = datetime(2026, 3, 31, 15, 25, tzinfo=UTC)  # 11:25 ET open, 11:30 ET close
        self.assertTrue(_in_valid_trade_window_et(bar_open_utc, 5))

    def test_blocks_bar_that_closes_after_morning_window_end(self):
        bar_open_utc = datetime(2026, 3, 31, 15, 30, tzinfo=UTC)  # 11:30 ET open, 11:35 ET close
        self.assertFalse(_in_valid_trade_window_et(bar_open_utc, 5))

    def test_allows_bar_that_closes_at_afternoon_window_end(self):
        bar_open_utc = datetime(2026, 3, 31, 19, 40, tzinfo=UTC)  # 15:40 ET open, 15:45 ET close
        self.assertTrue(_in_valid_trade_window_et(bar_open_utc, 5))

    def test_blocks_bar_that_closes_after_afternoon_window_end(self):
        bar_open_utc = datetime(2026, 3, 31, 19, 45, tzinfo=UTC)  # 15:45 ET open, 15:50 ET close
        self.assertFalse(_in_valid_trade_window_et(bar_open_utc, 5))

    def test_custom_morning_only_windows_block_afternoon_bar(self):
        bar_open_utc = datetime(2026, 3, 31, 18, 0, tzinfo=UTC)  # 14:00 ET open, 14:05 ET close
        self.assertIsNone(classify_time_window_et(bar_open_utc, 5, ((940, 1130),)))


if __name__ == "__main__":
    unittest.main()
