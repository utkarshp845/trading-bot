from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.strategy_ma import StrategyConfig, compute_indicators, evaluate_signal_from_metrics, generate_signal


UTC = ZoneInfo("UTC")


class StrategyOptimizationTests(unittest.TestCase):
    def _build_bars(self) -> pd.DataFrame:
        index = pd.date_range(start=datetime(2026, 3, 31, 13, 30, tzinfo=UTC), periods=320, freq="5min")
        close = [100 + (i * 0.15) for i in range(320)]
        return pd.DataFrame(
            {
                "open": [x - 0.05 for x in close],
                "high": [x + 0.2 for x in close],
                "low": [x - 0.2 for x in close],
                "close": close,
                "volume": [10000 + (i * 25) for i in range(320)],
            },
            index=index,
        )

    def _ready_long_metrics(self, **overrides) -> dict:
        metrics = {
            "price": 101.0,
            "sma_fast": 102.0,
            "sma_slow": 100.0,
            "adx": 30.0,
            "atr": 0.4,
            "atr_pct": 0.004,
            "volume": 1200.0,
            "volume_ma": 1000.0,
            "volume_ratio": 1.2,
            "sma_spread_atr": 2.5,
            "sma_spread_pct": 0.01,
            "trend_ema": 99.0,
            "price_distance_from_trend_ema_pct": 0.02,
            "momentum_pct": 0.01,
            "adx_delta": 0.5,
            "long_time_window": "09:40-11:30",
            "short_time_window": "09:40-11:30",
            "price_distance_from_vwap_pct": 0.01,
            "price_distance_from_open_pct": 0.01,
            "regime_ema": 98.0,
            "regime_adx": 25.0,
            "regime_atr_pct": 0.01,
            "regime_slope_pct": 0.01,
            "regime_on": True,
            "regime_bearish": False,
            "regime_side": "bullish",
            "long_pullback_depth_atr": 0.8,
            "short_pullback_depth_atr": 0.8,
            "pullback_depth_bucket_long": "0.8-1.2 ATR",
            "pullback_depth_bucket_short": "0.8-1.2 ATR",
            "long_reaccel_ok": True,
            "short_reaccel_ok": False,
            "spike_bar": False,
            "bar_range_atr": 1.0,
            "bar_body_atr": 0.3,
        }
        metrics.update(overrides)
        return metrics

    def test_baseline_signal_contains_optimization_metrics(self):
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
            trend_ema_period=10,
            regime_ema_period=12,
            regime_adx_period=5,
            regime_slope_lookback_bars=2,
            pullback_min_depth_atr=0.1,
            pullback_max_depth_atr=10.0,
            reaccel_min_bar_body_atr=0.0,
            min_volume_ratio=0.1,
            entry_windows=((0, 2359),),
        )
        bars = compute_indicators(self._build_bars(), cfg)
        signal, metrics, _ = generate_signal(bars, cfg)
        self.assertIn(signal, {"LONG", "SHORT", "HOLD"})
        self.assertIn("signal_strength", metrics)
        self.assertIn("entry_window_bucket", metrics)
        self.assertIn("volume_ratio", metrics)
        self.assertIn("regime_on", metrics)
        self.assertIn("pullback_depth_atr", metrics)
        self.assertIn("spike_bar", metrics)

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
        signal, reasons = evaluate_signal_from_metrics(self._ready_long_metrics(), cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("adx_below_threshold", reasons)
        self.assertIn("trend_up_no_entry", reasons)

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
        metrics = self._ready_long_metrics(volume_ratio=1.05)
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
        metrics = self._ready_long_metrics(price=100.0, trend_ema=100.5, price_distance_from_trend_ema_pct=0.002)
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
        metrics = self._ready_long_metrics(momentum_pct=0.003, adx_delta=0.1)
        signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("momentum_filter_failed", reasons)
        self.assertIn("adx_not_accelerating", reasons)

    def test_regime_off_blocks_long_entry(self):
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
        )
        signal, reasons = evaluate_signal_from_metrics(self._ready_long_metrics(regime_on=False), cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("regime_filter_failed", reasons)

    def test_pullback_and_reacceleration_allow_long_entry(self):
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
        )
        metrics = self._ready_long_metrics()
        signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
        self.assertEqual(signal, "LONG")
        self.assertEqual(reasons, ["long_entry_filters_passed"])

    def test_spike_bar_blocks_entry(self):
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
        )
        signal, reasons = evaluate_signal_from_metrics(self._ready_long_metrics(spike_bar=True), cfg)
        self.assertEqual(signal, "HOLD")
        self.assertIn("spike_bar_blocked", reasons)


if __name__ == "__main__":
    unittest.main()
