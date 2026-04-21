from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from bot.risk import RiskConfig, evaluate_entry_risk, parse_ts, trading_day_et


ET = ZoneInfo("America/New_York")


@dataclass
class ReplayState:
    trades_today: int = 0
    consecutive_losses: int = 0
    daily_start_equity: float | None = None
    last_trade_ts: str | None = None
    last_entry_signal_strength: float | None = None
    last_entry_signal_side: str | None = None
    trading_day: str | None = None


@dataclass
class SessionExitDecision:
    should_exit: bool
    reason: str | None = None


def bars_since(last_trade_ts: str | None, bars: pd.DataFrame) -> int | None:
    if not last_trade_ts or bars.empty:
        return None
    last_trade = parse_ts(last_trade_ts)
    if last_trade is None:
        return None
    return int((bars.index > last_trade).sum())


def compute_entry_qty(
    mode: str,
    base_qty: int,
    equity: float | None,
    last_price: float | None,
    atr_value: float | None,
    max_position_notional_pct: float,
    target_position_notional_pct: float | None = None,
    atr_risk_per_trade_pct: float = 0.0025,
    fractional: bool = False,
) -> float:
    if base_qty <= 0:
        return 0
    if mode == "fixed" or equity is None or last_price is None or last_price <= 0:
        return base_qty

    round_fn = (lambda x: round(x, 8)) if fractional else math.floor

    cap_notional_pct = target_position_notional_pct if target_position_notional_pct is not None else max_position_notional_pct
    if max_position_notional_pct > 0:
        cap_notional_pct = min(cap_notional_pct, max_position_notional_pct)
    capped_qty = round_fn((equity * cap_notional_pct) / last_price) if cap_notional_pct > 0 else base_qty

    if mode == "notional_cap":
        return max(0, capped_qty)

    if mode == "atr_risk":
        if atr_value is None or atr_value <= 0:
            return 0
        atr_qty = round_fn((equity * atr_risk_per_trade_pct) / atr_value)
        if capped_qty > 0:
            atr_qty = min(atr_qty, capped_qty)
        return max(0, atr_qty)

    return base_qty


def should_allow_reentry_during_cooldown(
    state: ReplayState,
    signal: str,
    current_strength: float | None,
    require_signal_strength_improvement: bool,
    min_signal_strength_delta: float = 0.0,
) -> bool:
    if not require_signal_strength_improvement:
        return False
    if current_strength is None or state.last_entry_signal_strength is None:
        return False
    if signal not in {"LONG", "SHORT"}:
        return False
    return current_strength >= (state.last_entry_signal_strength + min_signal_strength_delta)


def sync_replay_day(state: ReplayState, ts, equity: float) -> None:
    current_day = trading_day_et(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts)
    if state.trading_day != current_day:
        state.trading_day = current_day
        state.trades_today = 0
        state.daily_start_equity = equity


def evaluate_replay_entry(
    state: ReplayState,
    bars: pd.DataFrame,
    ts,
    signal: str,
    signal_strength: float | None,
    equity: float,
    last_bar_ts: object,
    position_notional: float | None,
    cooldown_bars: int,
    risk_config: RiskConfig,
    require_signal_strength_improvement: bool,
    min_signal_strength_delta: float,
) -> list[str]:
    sync_replay_day(state, ts, equity)
    reasons: list[str] = []

    risk_eval = evaluate_entry_risk(
        risk_config,
        trades_today=state.trades_today,
        consecutive_losses=state.consecutive_losses,
        daily_start_equity=state.daily_start_equity,
        current_equity=equity,
        last_bar_ts=last_bar_ts,
        position_notional=position_notional,
        now_utc=parse_ts(last_bar_ts),
    )
    reasons.extend(risk_eval.reasons)

    since = bars_since(state.last_trade_ts, bars)
    if since is not None and since < cooldown_bars:
        if should_allow_reentry_during_cooldown(
            state,
            signal,
            signal_strength,
            require_signal_strength_improvement=require_signal_strength_improvement,
            min_signal_strength_delta=min_signal_strength_delta,
        ):
            reasons.append("cooldown_overridden_stronger_signal")
        else:
            reasons.append("cooldown")

    return reasons


def record_replay_entry(state: ReplayState, ts, signal: str, signal_strength: float | None) -> None:
    state.last_trade_ts = ts.isoformat()
    state.trades_today += 1
    state.last_entry_signal_strength = signal_strength
    state.last_entry_signal_side = "long" if signal == "LONG" else "short" if signal == "SHORT" else None


def record_replay_exit(state: ReplayState, ts, pnl: float) -> None:
    state.last_trade_ts = ts.isoformat()
    if pnl > 0:
        state.consecutive_losses = 0
    else:
        state.consecutive_losses += 1


def evaluate_session_exit(
    position_qty: float,
    entry_ts: str | None,
    allow_overnight_holding: bool,
    flatten_before_close_minutes: int,
    now_utc: datetime | None = None,
) -> SessionExitDecision:
    if position_qty == 0:
        return SessionExitDecision(False, None)

    current_utc = now_utc or datetime.now(timezone.utc)
    current_day = trading_day_et(current_utc)
    current_et = current_utc.astimezone(ET)

    if current_et.weekday() < 5 and not allow_overnight_holding:
        if entry_ts is None:
            return SessionExitDecision(True, "inherited_position_missing_entry_ts")
        entry_dt = parse_ts(entry_ts)
        if entry_dt is None:
            return SessionExitDecision(True, "inherited_position_invalid_entry_ts")
        if trading_day_et(entry_dt) != current_day:
            return SessionExitDecision(True, "overnight_position_detected")

    if flatten_before_close_minutes > 0 and current_et.weekday() < 5:
        market_close_et = current_et.replace(hour=16, minute=0, second=0, microsecond=0)
        flatten_start_et = market_close_et - timedelta(minutes=flatten_before_close_minutes)
        if flatten_start_et <= current_et < market_close_et:
            return SessionExitDecision(True, f"session_flatten_window({flatten_before_close_minutes}m_before_close)")

    return SessionExitDecision(False, None)
