import os
import unittest
from unittest.mock import patch

from bot.optimize_strategy import _recommended_env_block, iter_candidates, score_candidate


class OptimizeStrategyTests(unittest.TestCase):
    def test_iter_candidates_enforces_fast_less_than_slow(self):
        with patch.dict(
            os.environ,
            {
                "OPT_SMA_FAST_VALUES": "20,60",
                "OPT_SMA_SLOW_VALUES": "20,50",
                "OPT_ADX_THRESHOLD_VALUES": "20",
                "OPT_LONG_ADX_THRESHOLD_VALUES": "25",
                "OPT_ATR_MAX_PCT_VALUES": "0.0045",
                "OPT_LONG_ATR_MAX_PCT_VALUES": "0.0035",
                "OPT_VOLUME_MIN_MULTIPLIER_VALUES": "1.0",
                "OPT_LONG_VOLUME_MIN_MULTIPLIER_VALUES": "1.1",
                "OPT_SHORT_VOLUME_MIN_MULTIPLIER_VALUES": "1.0",
                "OPT_TRAIL_ATR_MULTIPLIER_VALUES": "1.5",
                "OPT_MAX_BARS_IN_TRADE_VALUES": "12",
                "OPT_REVERSAL_SIGNAL_STRENGTH_VALUES": "35",
                "OPT_ENTRY_WINDOWS_VALUES": "0940-1130",
            },
            clear=False,
        ):
            candidates = iter_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["SMA_FAST"], "20")
        self.assertEqual(candidates[0]["SMA_SLOW"], "50")

    def test_score_candidate_rewards_robust_test_results(self):
        weak_full = {"profit_factor": 0.95, "max_drawdown": -0.02}
        weak_train = {"net_pnl": 2.0, "positive_windows": 2}
        weak_test = {
            "trade_count": 20,
            "net_pnl": 0.5,
            "profit_factor": 1.01,
            "positive_windows": 1,
            "median_window_net_pnl": -0.1,
            "max_drawdown": -0.03,
        }
        strong_full = {"profit_factor": 1.15, "max_drawdown": -0.01}
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
                "OPT_MAX_CANDIDATES": "3",
                "OPT_SMA_FAST_VALUES": "10,20,30",
                "OPT_SMA_SLOW_VALUES": "40,50,60",
                "OPT_ADX_THRESHOLD_VALUES": "20,25",
                "OPT_LONG_ADX_THRESHOLD_VALUES": "25",
                "OPT_ATR_MAX_PCT_VALUES": "0.0045",
                "OPT_LONG_ATR_MAX_PCT_VALUES": "0.0035",
                "OPT_VOLUME_MIN_MULTIPLIER_VALUES": "1.0",
                "OPT_LONG_VOLUME_MIN_MULTIPLIER_VALUES": "1.1",
                "OPT_SHORT_VOLUME_MIN_MULTIPLIER_VALUES": "1.0",
                "OPT_TRAIL_ATR_MULTIPLIER_VALUES": "1.5",
                "OPT_MAX_BARS_IN_TRADE_VALUES": "12",
                "OPT_REVERSAL_SIGNAL_STRENGTH_VALUES": "35",
                "OPT_ENTRY_WINDOWS_VALUES": "0940-1130,0940-1130,1400-1545",
            },
            clear=False,
        ):
            candidates = iter_candidates()

        self.assertEqual(len(candidates), 3)


if __name__ == "__main__":
    unittest.main()
