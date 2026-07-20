from datetime import datetime, timedelta, timezone
import json
import unittest

import pandas as pd

from bot.report_monitor import _latest_metric_lines, _near_miss_rows, _reason_count_sections, _runtime_health


class ReportMonitorTests(unittest.TestCase):
    def test_runtime_health_ignores_validation_samples_and_flags_stale_cycles(self):
        now = datetime.now(timezone.utc)
        runs = pd.DataFrame(
            [
                {"ts": pd.Timestamp(now - timedelta(minutes=30)), "note": ""},
                {"ts": pd.Timestamp(now), "note": "runtime_validation_sample"},
            ]
        )

        health = _runtime_health(runs, max_age_minutes=15, now=now)

        self.assertFalse(health["healthy"])
        self.assertEqual(health["status"], "stale")
        self.assertAlmostEqual(health["age_minutes"], 30.0)

    def test_runtime_health_flags_validation_only_database(self):
        now = datetime.now(timezone.utc)
        runs = pd.DataFrame([{"ts": pd.Timestamp(now), "note": "runtime_validation_sample"}])

        health = _runtime_health(runs, now=now)

        self.assertFalse(health["healthy"])
        self.assertEqual(health["status"], "validation_only")

    def test_reason_count_sections_counts_24h_and_7d_rejections(self):
        now = datetime.now(timezone.utc)
        runs = pd.DataFrame(
            [
                {"ts": pd.Timestamp(now - timedelta(hours=1)), "reasons": "regime_filter_failed;trend_down_no_entry", "note": ""},
                {"ts": pd.Timestamp(now - timedelta(days=2)), "reasons": "momentum_filter_failed", "note": ""},
                {"ts": pd.Timestamp(now - timedelta(days=8)), "reasons": "spike_bar_blocked", "note": ""},
            ]
        )

        counts = _reason_count_sections(runs)

        self.assertEqual(counts["last_24h"]["regime_filter_failed"], 1)
        self.assertEqual(counts["last_24h"]["momentum_filter_failed"], 0)
        self.assertEqual(counts["last_7d"]["regime_filter_failed"], 1)
        self.assertEqual(counts["last_7d"]["momentum_filter_failed"], 1)
        self.assertEqual(counts["last_7d"]["spike_bar_blocked"], 0)

    def test_near_miss_rows_include_one_or_two_true_blockers(self):
        now = pd.Timestamp(datetime.now(timezone.utc))
        metrics = {
            "regime_side": "bullish",
            "momentum_pct": 0.002,
            "pullback_depth_atr": 0.35,
            "bar_range_atr": 1.2,
            "volume_ratio": 1.3,
            "signal_strength": 42.0,
        }
        runs = pd.DataFrame(
            [
                {
                    "ts": now,
                    "signal": "HOLD",
                    "desired_action": "HOLD",
                    "position_qty": 0.0,
                    "price": 100.0,
                    "reasons": "pullback_depth_out_of_range;trend_up_no_entry",
                    "note": "",
                    "metrics_json": json.dumps(metrics),
                },
                {
                    "ts": now,
                    "signal": "HOLD",
                    "desired_action": "HOLD",
                    "position_qty": 0.0,
                    "price": 100.0,
                    "reasons": "regime_filter_failed;pullback_depth_out_of_range;momentum_filter_failed;trend_up_no_entry",
                    "note": "",
                    "metrics_json": json.dumps(metrics),
                },
                {
                    "ts": now,
                    "signal": "HOLD",
                    "desired_action": "HOLD",
                    "position_qty": 0.0,
                    "price": 100.0,
                    "reasons": "indicators_not_ready",
                    "note": "runtime_validation_sample",
                    "metrics_json": "{}",
                },
            ]
        )

        near_misses = _near_miss_rows(runs)

        self.assertEqual(len(near_misses), 1)
        self.assertEqual(near_misses[0]["blockers"], ["pullback_depth_out_of_range"])
        self.assertEqual(near_misses[0]["regime_side"], "bullish")

    def test_latest_metric_lines_formats_strategy_metrics(self):
        metrics = {
            "regime_side": "bearish",
            "momentum_pct": -0.004,
            "pullback_depth_atr": 1.4,
            "bar_range_atr": 2.1,
            "volume_ratio": 1.25,
            "signal_strength": 88.7,
        }
        line = _latest_metric_lines(pd.Series({"metrics_json": json.dumps(metrics)}))[0]

        self.assertIn("regime_side=bearish", line)
        self.assertIn("momentum_pct=-0.004000", line)
        self.assertIn("pullback_depth_atr=1.4000", line)
        self.assertIn("signal_strength=88.7000", line)


if __name__ == "__main__":
    unittest.main()
