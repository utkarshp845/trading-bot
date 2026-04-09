from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

from bot.risk import RiskConfig, evaluate_entry_risk


UTC = ZoneInfo("UTC")


class RiskTests(unittest.TestCase):
    def test_stale_bar_uses_bar_close_timestamp_details(self):
        now_utc = datetime(2026, 4, 9, 14, 35, 10, tzinfo=UTC)
        latest_bar_close = datetime(2026, 4, 9, 14, 35, 0, tzinfo=UTC)

        result = evaluate_entry_risk(
            RiskConfig(5, 0.01, 0.0, 3, 60, 0.02),
            trades_today=0,
            consecutive_losses=0,
            daily_start_equity=100000.0,
            current_equity=100000.0,
            last_bar_ts=latest_bar_close.isoformat(),
            position_notional=1000.0,
            now_utc=now_utc,
            stale_bar_timestamp_basis="bar_close",
        )

        self.assertTrue(result.allow_entries)
        self.assertEqual(result.stale_bar_details["current_runtime_timestamp"], now_utc.isoformat())
        self.assertEqual(result.stale_bar_details["latest_bar_timestamp"], latest_bar_close.isoformat())
        self.assertEqual(result.stale_bar_details["computed_bar_age_seconds"], 10.0)
        self.assertEqual(result.stale_bar_details["allowed_max_age"], 60)
        self.assertEqual(result.stale_bar_details["timestamp_basis"], "bar_close")

    def test_stale_bar_flags_open_timestamp_when_it_is_too_old(self):
        now_utc = datetime(2026, 4, 9, 14, 35, 10, tzinfo=UTC)
        latest_bar_open = datetime(2026, 4, 9, 14, 30, 0, tzinfo=UTC)

        result = evaluate_entry_risk(
            RiskConfig(5, 0.01, 0.0, 3, 60, 0.02),
            trades_today=0,
            consecutive_losses=0,
            daily_start_equity=100000.0,
            current_equity=100000.0,
            last_bar_ts=latest_bar_open.isoformat(),
            position_notional=1000.0,
            now_utc=now_utc,
            stale_bar_timestamp_basis="bar_open",
        )

        self.assertFalse(result.allow_entries)
        self.assertIn("stale_bar_data", result.reasons)
        self.assertEqual(result.stale_bar_details["computed_bar_age_seconds"], 310.0)
        self.assertEqual(result.stale_bar_details["timestamp_basis"], "bar_open")
        self.assertTrue(result.stale_bar_details["is_stale"])


if __name__ == "__main__":
    unittest.main()
