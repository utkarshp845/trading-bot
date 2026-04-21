from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import pandas as pd

from bot.risk import RiskConfig
from bot.trade_controls import ReplayState, evaluate_replay_entry, evaluate_session_exit, sync_replay_day


UTC = ZoneInfo("UTC")


class TradeControlsTests(unittest.TestCase):
    def _bars(self) -> pd.DataFrame:
        index = pd.date_range(start=datetime(2026, 3, 31, 13, 30, tzinfo=UTC), periods=6, freq="5min")
        return pd.DataFrame({"close": [100, 101, 102, 103, 104, 105]}, index=index)

    def test_replay_entry_blocks_max_trades(self):
        bars = self._bars()
        state = ReplayState(trades_today=2, daily_start_equity=100000, trading_day="2026-03-31")
        reasons = evaluate_replay_entry(
            state,
            bars.iloc[:3],
            bars.index[2],
            "LONG",
            40.0,
            100000,
            bars.index[2].isoformat(),
            position_notional=1000.0,
            cooldown_bars=2,
            risk_config=RiskConfig(2, 0.01, 0, 3, 0, 0.02),
            require_signal_strength_improvement=False,
            min_signal_strength_delta=0.0,
        )
        self.assertIn("max_trades_hit", reasons)

    def test_replay_entry_blocks_drawdown(self):
        bars = self._bars()
        state = ReplayState(daily_start_equity=100000, trading_day="2026-03-31")
        reasons = evaluate_replay_entry(
            state,
            bars.iloc[:3],
            bars.index[2],
            "LONG",
            40.0,
            98000,
            bars.index[2].isoformat(),
            position_notional=1000.0,
            cooldown_bars=2,
            risk_config=RiskConfig(5, 0.01, 0, 3, 0, 0.02),
            require_signal_strength_improvement=False,
            min_signal_strength_delta=0.0,
        )
        self.assertTrue(any(reason.startswith("daily_drawdown_limit_hit") for reason in reasons))

    def test_replay_entry_honors_cooldown(self):
        bars = self._bars()
        state = ReplayState(last_trade_ts=bars.index[1].isoformat(), daily_start_equity=100000, trading_day="2026-03-31")
        reasons = evaluate_replay_entry(
            state,
            bars.iloc[:3],
            bars.index[2],
            "LONG",
            40.0,
            100000,
            bars.index[2].isoformat(),
            position_notional=1000.0,
            cooldown_bars=3,
            risk_config=RiskConfig(5, 0.01, 0, 3, 0, 0.02),
            require_signal_strength_improvement=False,
            min_signal_strength_delta=0.0,
        )
        self.assertIn("cooldown", reasons)

    def test_sync_replay_day_resets_trade_counter_on_new_day(self):
        state = ReplayState(trades_today=3, daily_start_equity=100000, trading_day="2026-03-31")
        sync_replay_day(state, datetime(2026, 4, 1, 13, 30, tzinfo=UTC), 99500)
        self.assertEqual(state.trades_today, 0)
        self.assertEqual(state.daily_start_equity, 99500)

    def test_replay_entry_blocks_after_max_entry_failures(self):
        bars = self._bars()
        state = ReplayState(
            daily_start_equity=100000,
            trading_day="2026-03-31",
            entry_failures_today=2,
            entry_failures_day_utc="2026-03-31",
        )
        reasons = evaluate_replay_entry(
            state,
            bars.iloc[:3],
            bars.index[2],
            "LONG",
            40.0,
            100000,
            bars.index[2].isoformat(),
            position_notional=1000.0,
            cooldown_bars=0,
            risk_config=RiskConfig(5, 0.01, 0, 3, 0, 0.02),
            require_signal_strength_improvement=False,
            min_signal_strength_delta=0.0,
            max_consecutive_entry_failures_per_day=2,
        )
        self.assertIn("max_entry_failures_hit", reasons)

    def test_sync_replay_day_resets_entry_failures_on_utc_rollover(self):
        state = ReplayState(
            trades_today=3,
            daily_start_equity=100000,
            trading_day="2026-03-31",
            entry_failures_today=2,
            entry_failures_day_utc="2026-03-31",
        )
        sync_replay_day(state, datetime(2026, 4, 1, 0, 5, tzinfo=UTC), 99500)
        self.assertEqual(state.entry_failures_today, 0)
        self.assertEqual(state.entry_failures_day_utc, "2026-04-01")
        self.assertEqual(state.trades_today, 3)

    def test_session_exit_flattens_inherited_position_when_overnight_holding_disabled(self):
        now_utc = datetime(2026, 4, 1, 14, 35, tzinfo=UTC)
        decision = evaluate_session_exit(
            position_qty=1.0,
            entry_ts="2026-03-31T19:55:00+00:00",
            allow_overnight_holding=False,
            flatten_before_close_minutes=5,
            now_utc=now_utc,
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.reason, "overnight_position_detected")

    def test_session_exit_flattens_unknown_broker_position_when_overnight_holding_disabled(self):
        now_utc = datetime(2026, 4, 1, 14, 35, tzinfo=UTC)
        decision = evaluate_session_exit(
            position_qty=1.0,
            entry_ts=None,
            allow_overnight_holding=False,
            flatten_before_close_minutes=5,
            now_utc=now_utc,
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.reason, "inherited_position_missing_entry_ts")

    def test_session_exit_flattens_near_close_even_for_same_day_position(self):
        now_utc = datetime(2026, 4, 1, 19, 56, tzinfo=UTC)
        decision = evaluate_session_exit(
            position_qty=1.0,
            entry_ts="2026-04-01T14:35:00+00:00",
            allow_overnight_holding=False,
            flatten_before_close_minutes=5,
            now_utc=now_utc,
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.reason, "session_flatten_window(5m_before_close)")


if __name__ == "__main__":
    unittest.main()
