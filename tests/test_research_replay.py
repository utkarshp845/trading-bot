from datetime import datetime
import os
from unittest.mock import patch
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.research import build_strategy_config, run_replay


UTC = ZoneInfo("UTC")


class ResearchReplayTests(unittest.TestCase):
    def _bars(self, periods: int = 80) -> pd.DataFrame:
        index = pd.date_range(start=datetime(2026, 3, 31, 13, 30, tzinfo=UTC), periods=periods, freq="5min")
        close = [100 + (i * 0.15) for i in range(periods)]
        return pd.DataFrame(
            {
                "open": close,
                "high": [x + 0.2 for x in close],
                "low": [x - 0.2 for x in close],
                "close": close,
                "volume": [10000 + (i * 25) for i in range(periods)],
            },
            index=index,
        )

    def test_reversal_threshold_can_prevent_weak_reversal_exit(self):
        bars = self._bars(periods=4)
        metrics_sequence = [
            {"price": 100.0, "atr": 1.0, "bar_ts": bars.index[0].isoformat(), "signal_strength": 50.0},
            {"price": 101.0, "atr": 1.0, "bar_ts": bars.index[1].isoformat(), "signal_strength": 10.0},
            {"price": 102.0, "atr": 1.0, "bar_ts": bars.index[2].isoformat(), "signal_strength": 10.0},
            {"price": 103.0, "atr": 1.0, "bar_ts": bars.index[3].isoformat(), "signal_strength": 10.0},
        ]
        signals = [("LONG", ["long_entry_filters_passed"]), ("SHORT", ["short_entry_filters_passed"]), ("LONG", ["long_entry_filters_passed"]), ("LONG", ["long_entry_filters_passed"])]

        def fake_generate_signal(_slice_df, _cfg):
            idx = len(_slice_df) - 1
            signal, reasons = signals[idx]
            return signal, dict(metrics_sequence[idx]), reasons

        base_env = {
            "MAX_TRADES_PER_DAY": "5",
            "MAX_DAILY_DRAWDOWN_PCT": "0.01",
            "MAX_DAILY_LOSS": "0",
            "MAX_CONSECUTIVE_LOSSES": "3",
            "MAX_POSITION_NOTIONAL_PCT": "0.02",
            "TARGET_POSITION_NOTIONAL_PCT": "0.02",
            "ATR_RISK_PER_TRADE_PCT": "0.0025",
            "COOLDOWN_BARS": "0",
            "REENTRY_REQUIRES_SIGNAL_STRENGTH_IMPROVEMENT": "false",
            "REENTRY_MIN_SIGNAL_STRENGTH_DELTA": "0",
            "RESEARCH_COMMISSION_PER_TRADE": "0",
            "RESEARCH_SLIPPAGE_PER_SHARE": "0",
        }

        with patch("bot.research.compute_indicators", return_value=bars), patch("bot.research.generate_signal", side_effect=fake_generate_signal):
            with patch.dict(os.environ, {**base_env, "REVERSAL_SIGNAL_STRENGTH_MIN": "0"}, clear=False):
                _, weak_threshold_trades = run_replay(bars, build_strategy_config(5), "fixed", 1, 100000)
            with patch.dict(os.environ, {**base_env, "REVERSAL_SIGNAL_STRENGTH_MIN": "25"}, clear=False):
                _, stronger_threshold_trades = run_replay(bars, build_strategy_config(5), "fixed", 1, 100000)

        self.assertEqual(len(weak_threshold_trades), 1)
        self.assertEqual(len(stronger_threshold_trades), 0)

    def test_research_pipeline_smoke_scenarios(self):
        bars = self._bars()
        scenarios = [
            {
                "ENTRY_WINDOWS": "0940-1130,1400-1545",
                "LONG_ENTRY_WINDOWS": "0940-1130,1400-1545",
                "SHORT_ENTRY_WINDOWS": "0940-1130,1400-1545",
                "VOLUME_MIN_MULTIPLIER": "0.8",
                "REVERSAL_SIGNAL_STRENGTH_MIN": "0",
            },
            {
                "ENTRY_WINDOWS": "0940-1130",
                "LONG_ENTRY_WINDOWS": "0940-1130",
                "SHORT_ENTRY_WINDOWS": "0940-1130",
                "VOLUME_MIN_MULTIPLIER": "1.05",
                "REVERSAL_SIGNAL_STRENGTH_MIN": "35",
            },
            {
                "ENTRY_WINDOWS": "0940-1130",
                "LONG_ENTRY_WINDOWS": "0940-1130",
                "SHORT_ENTRY_WINDOWS": "0940-1130",
                "LONG_ADX_THRESHOLD": "30",
                "LONG_ATR_MAX_PCT": "0.003",
                "LONG_VOLUME_MIN_MULTIPLIER": "1.15",
                "SHORT_VOLUME_MIN_MULTIPLIER": "1.0",
                "REVERSAL_SIGNAL_STRENGTH_MIN": "35",
            },
            {
                "ENTRY_WINDOWS": "0940-1130",
                "LONG_ENTRY_WINDOWS": "0940-1130",
                "SHORT_ENTRY_WINDOWS": "0940-1130",
                "VOLUME_MIN_MULTIPLIER": "1.05",
                "REVERSAL_SIGNAL_STRENGTH_MIN": "45",
            },
        ]
        base_env = {
            "SMA_FAST": "5",
            "SMA_SLOW": "10",
            "ADX_PERIOD": "5",
            "ADX_THRESHOLD": "5",
            "ATR_PERIOD": "5",
            "ATR_MAX_PCT": "0.05",
            "VOLUME_MA_PERIOD": "5",
            "TRAIL_ATR_MULTIPLIER": "1.5",
            "MAX_BARS_IN_TRADE": "12",
            "MAX_TRADES_PER_DAY": "5",
            "MAX_DAILY_DRAWDOWN_PCT": "0.01",
            "MAX_DAILY_LOSS": "0",
            "MAX_CONSECUTIVE_LOSSES": "3",
            "MAX_POSITION_NOTIONAL_PCT": "0.02",
            "TARGET_POSITION_NOTIONAL_PCT": "0.02",
            "ATR_RISK_PER_TRADE_PCT": "0.0025",
            "COOLDOWN_BARS": "2",
            "REENTRY_REQUIRES_SIGNAL_STRENGTH_IMPROVEMENT": "false",
            "REENTRY_MIN_SIGNAL_STRENGTH_DELTA": "0",
            "RESEARCH_COMMISSION_PER_TRADE": "0",
            "RESEARCH_SLIPPAGE_PER_SHARE": "0.01",
        }

        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                with patch.dict(os.environ, {**base_env, **scenario}, clear=False):
                    cfg = build_strategy_config(5)
                    equity_df, trades = run_replay(bars, cfg, "fixed", 1, 100000)
                self.assertFalse(equity_df.empty)
                self.assertIsInstance(trades, list)


if __name__ == "__main__":
    unittest.main()
