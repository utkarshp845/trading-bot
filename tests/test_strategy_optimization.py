from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.strategy_ma import StrategyConfig, compute_indicators, generate_signal


UTC = ZoneInfo("UTC")


class StrategyOptimizationTests(unittest.TestCase):
    def _build_bars(self) -> pd.DataFrame:
        index = pd.date_range(start=datetime(2026, 3, 31, 13, 30, tzinfo=UTC), periods=80, freq="5min")
        close = [100 + (i * 0.15) for i in range(80)]
        return pd.DataFrame(
            {
                "open": close,
                "high": [x + 0.2 for x in close],
                "low": [x - 0.2 for x in close],
                "close": close,
                "volume": [10000 + (i * 25) for i in range(80)],
            },
            index=index,
        )

    def test_baseline_signal_contains_optimization_metrics(self):
        bars = compute_indicators(
            self._build_bars(),
            StrategyConfig(
                sma_fast=5,
                sma_slow=10,
                adx_period=5,
                adx_threshold=5,
                atr_period=5,
                atr_max_pct=0.05,
                volume_ma_period=5,
                volume_min_multiplier=0.1,
                timeframe_minutes=5,
                trail_atr_multiplier=1.5,
                max_bars_in_trade=12,
            ),
        )
        signal, metrics, _ = generate_signal(
            bars,
            StrategyConfig(
                sma_fast=5,
                sma_slow=10,
                adx_period=5,
                adx_threshold=5,
                atr_period=5,
                atr_max_pct=0.05,
                volume_ma_period=5,
                volume_min_multiplier=0.1,
                timeframe_minutes=5,
                trail_atr_multiplier=1.5,
                max_bars_in_trade=12,
            ),
        )
        self.assertIn(signal, {"LONG", "SHORT", "HOLD"})
        self.assertIn("signal_strength", metrics)
        self.assertIn("entry_window_bucket", metrics)
        self.assertIn("volume_ratio", metrics)

    def test_asymmetric_thresholds_can_block_one_side(self):
        cfg = StrategyConfig(
            sma_fast=5,
            sma_slow=10,
            adx_period=5,
            adx_threshold=5,
            atr_period=5,
            atr_max_pct=0.05,
            volume_ma_period=5,
            volume_min_multiplier=0.1,
            timeframe_minutes=5,
            trail_atr_multiplier=1.5,
            max_bars_in_trade=12,
            long_adx_threshold=100.0,
            short_adx_threshold=1.0,
        )
        bars = compute_indicators(self._build_bars(), cfg)
        signal, _, reasons = generate_signal(bars, cfg)
        self.assertNotEqual(signal, "LONG")
        self.assertTrue(any(reason in reasons for reason in {"adx_below_threshold", "trend_up_no_entry"}))


if __name__ == "__main__":
    unittest.main()
