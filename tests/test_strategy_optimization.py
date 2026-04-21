from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.strategy_ma import StrategyConfig, compute_indicators, evaluate_signal_from_metrics, generate_signal


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

    def test_stricter_long_volume_threshold_can_block_longs(self):
        cfg = StrategyConfig(
            sma_fast=5,
            sma_slow=10,
            adx_period=5,
            adx_threshold=5,
            atr_period=5,
            atr_max_pct=0.05,
            volume_ma_period=5,
            volume_min_multiplier=1.0,
            timeframe_minutes=5,
            trail_atr_multiplier=1.5,
            max_bars_in_trade=12,
            long_volume_min_multiplier=1.2,
            short_volume_min_multiplier=0.8,
        )
        metrics = {
            "price": 100.0,
            "sma_fast": 101.0,
            "sma_slow": 100.0,
            "adx": 30.0,
            "atr": 0.4,
            "atr_pct": 0.004,
            "volume": 1050.0,
            "volume_ma": 1000.0,
            "volume_ratio": 1.05,
            "sma_spread_atr": 2.5,
            "sma_spread_pct": 0.01,
            "long_time_window": "09:40-11:30",
            "short_time_window": "09:40-11:30",
            "price_distance_from_vwap_pct": 0.01,
            "price_distance_from_open_pct": 0.01,
        }
        signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("volume_below_threshold", reasons)
        self.assertIn("trend_up_no_entry", reasons)

    def test_trend_ema_filter_can_block_long_entries(self):
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
            trend_ema_period=55,
            long_min_trend_ema_distance_pct=0.001,
        )
        metrics = {
            "price": 100.0,
            "sma_fast": 101.0,
            "sma_slow": 99.0,
            "adx": 30.0,
            "atr": 0.4,
            "atr_pct": 0.004,
            "volume": 1200.0,
            "volume_ma": 1000.0,
            "volume_ratio": 1.2,
            "sma_spread_atr": 2.5,
            "sma_spread_pct": 0.01,
            "trend_ema": 100.5,
            "price_distance_from_trend_ema_pct": 0.002,
            "momentum_pct": 0.01,
            "adx_delta": 0.5,
            "long_time_window": "09:40-11:30",
            "short_time_window": "09:40-11:30",
            "price_distance_from_vwap_pct": 0.01,
            "price_distance_from_open_pct": 0.01,
        }
        signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("trend_ema_filter_failed", reasons)
        self.assertIn("trend_up_no_entry", reasons)

    def test_momentum_and_adx_acceleration_filters_can_block_entry(self):
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
            momentum_lookback_bars=3,
            long_min_momentum_pct=0.005,
            min_adx_delta=0.25,
        )
        metrics = {
            "price": 100.0,
            "sma_fast": 101.0,
            "sma_slow": 99.0,
            "adx": 30.0,
            "atr": 0.4,
            "atr_pct": 0.004,
            "volume": 1200.0,
            "volume_ma": 1000.0,
            "volume_ratio": 1.2,
            "sma_spread_atr": 2.5,
            "sma_spread_pct": 0.01,
            "trend_ema": 99.0,
            "price_distance_from_trend_ema_pct": 0.01,
            "momentum_pct": 0.003,
            "adx_delta": 0.1,
            "long_time_window": "09:40-11:30",
            "short_time_window": "09:40-11:30",
            "price_distance_from_vwap_pct": 0.01,
            "price_distance_from_open_pct": 0.01,
        }
        signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("momentum_filter_failed", reasons)
        self.assertIn("adx_not_accelerating", reasons)


if __name__ == "__main__":
    unittest.main()
