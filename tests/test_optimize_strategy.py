import os
import unittest
from unittest.mock import patch

from bot.optimize_strategy import CandidateResult, _candidate_grid, _recommended_env_block, acceptance_checks, iter_candidates, score_candidate


SINGLE_VALUE_GRID_ENV = {
    "OPT_ADX_THRESHOLD_VALUES": "20",
    "OPT_LONG_ADX_THRESHOLD_VALUES": "22",
    "OPT_ATR_MAX_PCT_VALUES": "0.015",
    "OPT_LONG_ATR_MAX_PCT_VALUES": "0.012",
    "OPT_MIN_SMA_SPREAD_ATR_MULT_VALUES": "0.1",
    "OPT_MIN_VOLUME_RATIO_VALUES": "1.05",
    "OPT_TRAIL_ATR_MULTIPLIER_VALUES": "1.5",
    "OPT_TRAIL_AFTER_ATR_MULTIPLE_VALUES": "1.5",
    "OPT_MAX_BARS_IN_TRADE_VALUES": "18",
    "OPT_MAX_TRADES_PER_DAY_VALUES": "3",
    "OPT_COOLDOWN_BARS_VALUES": "6",
    "OPT_REGIME_ADX_MIN_VALUES": "18",
    "OPT_REGIME_MIN_SLOPE_PCT_VALUES": "0.0015",
    "OPT_LONG_MIN_MOMENTUM_PCT_VALUES": "0.002",
    "OPT_MIN_ADX_DELTA_VALUES": "0.1",
    "OPT_PULLBACK_MIN_DEPTH_ATR_VALUES": "0.3",
    "OPT_PULLBACK_MAX_DEPTH_ATR_VALUES": "1.5",
    "OPT_REACCEL_MIN_BAR_BODY_ATR_VALUES": "0.2",
    "OPT_SPIKE_BAR_MAX_RANGE_ATR_VALUES": "2.0",
    "OPT_ENTRY_WINDOWS_VALUES": "0000-2359",
}


class OptimizeStrategyTests(unittest.TestCase):
    def test_iter_candidates_enforces_fast_less_than_slow(self):
        with patch.dict(
            os.environ,
            {
                **SINGLE_VALUE_GRID_ENV,
                "OPT_SMA_FAST_VALUES": "20,60",
                "OPT_SMA_SLOW_VALUES": "20,50",
            },
            clear=False,
        ):
            candidates = iter_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["SMA_FAST"], "20")
        self.assertEqual(candidates[0]["SMA_SLOW"], "50")

    def test_btc_default_grid_includes_activity_bottleneck_values(self):
        with patch.dict(os.environ, {"SYMBOL": "BTC/USD", "BOT_MARKET": "btc"}, clear=True):
            grid = _candidate_grid()

        self.assertEqual(grid["LONG_ADX_THRESHOLD"], ["20", "22", "25"])
        self.assertEqual(grid["REGIME_MIN_SLOPE_PCT"], ["0.001", "0.0015", "0.002"])
        self.assertEqual(grid["LONG_MIN_MOMENTUM_PCT"], ["0.0015", "0.002", "0.003"])
        self.assertEqual(grid["MIN_ADX_DELTA"], ["0", "0.1", "0.25"])
        self.assertEqual(grid["MIN_SMA_SPREAD_ATR_MULT"], ["0", "0.1", "0.2"])
        self.assertEqual(grid["PULLBACK_MIN_DEPTH_ATR"], ["0.2", "0.3", "0.4"])
        self.assertEqual(grid["PULLBACK_MAX_DEPTH_ATR"], ["1.2", "1.5", "1.8"])
        self.assertEqual(grid["MIN_VOLUME_RATIO"], ["1.00", "1.05", "1.10"])
        self.assertEqual(grid["SPIKE_BAR_MAX_RANGE_ATR"], ["1.8", "2.0", "2.2"])
        self.assertEqual(grid["ATR_MAX_PCT"], ["0.015"])
        self.assertEqual(grid["LONG_ATR_MAX_PCT"], ["0.012"])

    def test_score_candidate_rewards_robust_test_results(self):
        weak_full = {"profit_factor": 0.95, "max_drawdown": -0.02, "trades_per_day": 2.0}
        weak_train = {"net_pnl": 2.0, "positive_windows": 2}
        weak_test = {
            "trade_count": 20,
            "net_pnl": 0.5,
            "profit_factor": 1.01,
            "positive_windows": 1,
            "median_window_net_pnl": -0.1,
            "max_drawdown": -0.03,
        }
        strong_full = {"profit_factor": 1.15, "max_drawdown": -0.01, "trades_per_day": 3.0}
        strong_train = {"net_pnl": 4.0, "positive_windows": 3}
        strong_test = {
            "trade_count": 20,
            "net_pnl": 3.0,
            "profit_factor": 1.2,
            "positive_windows": 3,
            "median_window_net_pnl": 0.5,
            "max_drawdown": -0.01,
        }

        self.assertGreater(
            score_candidate(strong_full, strong_train, strong_test),
            score_candidate(weak_full, weak_train, weak_test),
        )

    def test_recommended_env_block_contains_key_parameters(self):
        block = _recommended_env_block(
            {
                "SMA_FAST": "20",
                "SMA_SLOW": "50",
                "ADX_THRESHOLD": "25",
                "ENTRY_WINDOWS": "0940-1130",
                "LONG_ENTRY_WINDOWS": "0940-1130",
                "SHORT_ENTRY_WINDOWS": "0940-1130",
            }
        )

        self.assertIn("SMA_FAST=20", block)
        self.assertIn("SMA_SLOW=50", block)
        self.assertIn("ENTRY_WINDOWS=0940-1130", block)

    def test_iter_candidates_respects_max_candidates_cap(self):
        with patch.dict(
            os.environ,
            {
                **SINGLE_VALUE_GRID_ENV,
                "OPT_MAX_CANDIDATES": "3",
                "OPT_SMA_FAST_VALUES": "10,20,30",
                "OPT_SMA_SLOW_VALUES": "40,50,60",
            },
            clear=False,
        ):
            candidates = iter_candidates()

        self.assertEqual(len(candidates), 3)

    def test_acceptance_checks_require_baseline_improvement_and_slippage_survival(self):
        baseline = CandidateResult(
            params={},
            score=10.0,
            full_summary={"profit_factor": 1.05, "expectancy": 0.01, "trades_per_day": 1.0, "max_drawdown": -0.01},
            train_summary={"positive_windows": 2},
            test_summary={"trade_count": 10, "net_pnl": 1.0, "profit_factor": 1.05},
            slippage_2x_summary={"profit_factor": 0.95},
            window_count=3,
            positive_test_windows=1,
            positive_train_windows=2,
        )
        candidate = CandidateResult(
            params={"ALLOW_SHORTS": "false"},
            score=20.0,
            full_summary={"profit_factor": 1.2, "expectancy": 0.05, "trades_per_day": 1.5, "max_drawdown": -0.02},
            train_summary={"positive_windows": 3},
            test_summary={"trade_count": 15, "net_pnl": 2.0, "profit_factor": 1.15},
            slippage_2x_summary={"profit_factor": 1.01},
            window_count=3,
            positive_test_windows=2,
            positive_train_windows=3,
        )

        checks = acceptance_checks(candidate, baseline)

        self.assertTrue(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
