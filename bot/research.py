from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.broker_alpaca import get_historical_bars, make_clients
from bot.metrics import add_condition_buckets, best_worst_conditions, closed_trade_summary, max_drawdown, summarize_by_group
from bot.paths import REPORTS_DIR, ensure_runtime_dirs
from bot.risk import RiskConfig
from bot.strategy_ma import StrategyConfig, build_strategy_config_from_env, compute_indicators, generate_signal
from bot.trade_controls import ReplayState, compute_entry_qty, evaluate_replay_entry, record_replay_entry, record_replay_exit, sync_replay_day


ET = ZoneInfo("America/New_York")


@dataclass
class ReplayPosition:
    side: str
    qty: float
    entry_price: float
    entry_ts: object
    high_water: float
    low_water: float
    entry_metrics: dict


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_strategy_config(timeframe_minutes: int) -> StrategyConfig:
    return build_strategy_config_from_env(timeframe_minutes)


def apply_slippage(price: float, side: str, slippage_per_share: float) -> float:
    if side == "buy":
        return price + slippage_per_share
    return price - slippage_per_share


def _current_entry_meta(ts, signal: str, metrics: dict, state: ReplayState) -> dict:
    entry_ts = pd.Timestamp(ts, tz="UTC") if not isinstance(ts, pd.Timestamp) else ts
    return {
        "entry_signal_side": "long" if signal == "LONG" else "short",
        "adx": metrics.get("adx"),
        "atr_pct": metrics.get("atr_pct"),
        "volume_ratio": metrics.get("volume_ratio"),
        "sma_spread_pct": metrics.get("sma_spread_pct"),
        "entry_window_bucket": metrics.get("entry_window_bucket"),
        "signal_strength": metrics.get("signal_strength"),
        "decision_price": metrics.get("price"),
        "entry_regime_on": metrics.get("regime_on"),
        "entry_regime_side": metrics.get("regime_side"),
        "entry_regime_slope_pct": metrics.get("regime_slope_pct"),
        "entry_regime_adx": metrics.get("regime_adx"),
        "entry_pullback_depth_atr": metrics.get("pullback_depth_atr"),
        "entry_pullback_depth_bucket": metrics.get("pullback_depth_bucket"),
        "entry_after_prior_loss": state.last_exit_was_loss,
        "entry_hour_of_day": int(entry_ts.tz_convert(ET).hour),
        "entry_day_of_week": entry_ts.tz_convert(ET).strftime("%A"),
    }


def _trade_record(position: ReplayPosition, ts, fill_price: float, pnl: float, side: str, price: float) -> dict:
    entry_ts = pd.Timestamp(position.entry_ts, tz="UTC") if not isinstance(position.entry_ts, pd.Timestamp) else position.entry_ts
    exit_ts = pd.Timestamp(ts, tz="UTC") if not isinstance(ts, pd.Timestamp) else ts
    return {
        "entry_ts": str(position.entry_ts),
        "exit_ts": str(ts),
        "side": side,
        "qty": position.qty,
        "entry_price": position.entry_price,
        "exit_price": fill_price,
        "pnl": pnl,
        "return_pct": ((fill_price - position.entry_price) / position.entry_price) if side == "long" else ((position.entry_price - fill_price) / position.entry_price),
        "entry_signal_side": position.entry_metrics.get("entry_signal_side"),
        "entry_adx": position.entry_metrics.get("adx"),
        "entry_atr_pct": position.entry_metrics.get("atr_pct"),
        "entry_volume_ratio": position.entry_metrics.get("volume_ratio"),
        "entry_sma_spread_pct": position.entry_metrics.get("sma_spread_pct"),
        "entry_window_bucket": position.entry_metrics.get("entry_window_bucket"),
        "entry_regime_on": position.entry_metrics.get("entry_regime_on"),
        "entry_regime_side": position.entry_metrics.get("entry_regime_side"),
        "entry_regime_slope_pct": position.entry_metrics.get("entry_regime_slope_pct"),
        "entry_regime_adx": position.entry_metrics.get("entry_regime_adx"),
        "entry_pullback_depth_atr": position.entry_metrics.get("entry_pullback_depth_atr"),
        "entry_pullback_depth_bucket": position.entry_metrics.get("entry_pullback_depth_bucket"),
        "entry_after_prior_loss": position.entry_metrics.get("entry_after_prior_loss"),
        "entry_hour_of_day": position.entry_metrics.get("entry_hour_of_day"),
        "entry_day_of_week": position.entry_metrics.get("entry_day_of_week"),
        "hold_seconds": (exit_ts - entry_ts).total_seconds(),
        "realized_slippage_estimate": abs(fill_price - float(position.entry_metrics.get("decision_price") or price)),
    }


def run_replay(
    bars: pd.DataFrame,
    cfg: StrategyConfig,
    sizing_mode: str,
    base_qty: int,
    starting_equity: float,
    slippage_multiplier: float = 1.0,
) -> tuple[pd.DataFrame, list[dict]]:
    commission = float(os.getenv("RESEARCH_COMMISSION_PER_TRADE", "0"))
    slippage = float(os.getenv("RESEARCH_SLIPPAGE_PER_SHARE", "0.01")) * slippage_multiplier
    cooldown_bars = int(os.getenv("COOLDOWN_BARS", "2"))
    max_position_notional_pct = float(os.getenv("MAX_POSITION_NOTIONAL_PCT", "0.02"))
    target_position_notional_pct = float(os.getenv("TARGET_POSITION_NOTIONAL_PCT", str(max_position_notional_pct)))
    atr_risk_per_trade_pct = float(os.getenv("ATR_RISK_PER_TRADE_PCT", "0.0025"))
    require_signal_strength_improvement = _env_flag("REENTRY_REQUIRES_SIGNAL_STRENGTH_IMPROVEMENT", False)
    min_signal_strength_delta = float(os.getenv("REENTRY_MIN_SIGNAL_STRENGTH_DELTA", "0"))
    max_consecutive_entry_failures_per_day = int(os.getenv("MAX_CONSECUTIVE_ENTRY_FAILURES_PER_DAY", "0"))
    allow_shorts = cfg.allow_shorts
    hard_stop_atr_mult = float(os.getenv("HARD_STOP_ATR_MULT", "0"))
    is_crypto = _env_flag("IS_CRYPTO", False)
    risk_config = RiskConfig(
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "5")),
        max_daily_drawdown_pct=float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.01")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "0")),
        max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")),
        max_bar_age_seconds=0,
        max_position_notional_pct=max_position_notional_pct,
    )
    bars2 = compute_indicators(bars, cfg)

    equity = starting_equity
    position: ReplayPosition | None = None
    state = ReplayState(daily_start_equity=starting_equity)
    equity_rows: list[dict] = []
    trades: list[dict] = []

    for i in range(len(bars2)):
        slice_df = bars2.iloc[: i + 1]
        signal, metrics, reasons = generate_signal(slice_df, cfg)
        row = bars2.iloc[i]
        ts = bars2.index[i]
        price = float(metrics.get("price") or row["close"])
        atr_value = float(metrics.get("atr") or 0.0) if metrics.get("atr") is not None else None
        signal_strength = float(metrics.get("signal_strength") or 0.0)
        sync_replay_day(state, ts, equity)
        exited_this_bar = False

        if position is not None:
            position.high_water = max(position.high_water, price)
            position.low_water = min(position.low_water, price)
            trail_mult = cfg.trail_atr_multiplier_for(position.side)

            if position.side == "long":
                should_exit = False
                if (
                    hard_stop_atr_mult > 0
                    and atr_value is not None
                    and price < position.entry_price - (hard_stop_atr_mult * atr_value)
                ):
                    should_exit = True
                if not should_exit and cfg.exit_on_regime_invalidation and metrics.get("regime_on") is False:
                    should_exit = True

                if not should_exit and atr_value is not None:
                    protective_stop = None
                    if cfg.enable_breakeven_stop and position.high_water >= position.entry_price + (cfg.breakeven_after_atr_multiple * atr_value):
                        protective_stop = position.entry_price
                    if cfg.enable_profit_lock and position.high_water >= position.entry_price + (cfg.profit_lock_after_atr_multiple * atr_value):
                        profit_lock_stop = position.entry_price + (cfg.profit_lock_atr_multiple * atr_value)
                        protective_stop = max(protective_stop or profit_lock_stop, profit_lock_stop)
                    if protective_stop is not None and price < protective_stop:
                        should_exit = True

                if (
                    not should_exit
                    and atr_value is not None
                    and position.high_water >= position.entry_price + (cfg.trail_after_atr_multiple * atr_value)
                ):
                    trailing_stop = position.high_water - (trail_mult * atr_value)
                    if price < trailing_stop:
                        should_exit = True

                bars_held = int((bars2.index[: i + 1] > position.entry_ts).sum())
                if not should_exit and bars_held >= cfg.max_bars_in_trade_for("long") and price <= position.entry_price:
                    should_exit = True

                if should_exit:
                    fill_price = apply_slippage(price, "sell", slippage)
                    pnl = (fill_price - position.entry_price) * position.qty - (2 * commission)
                    equity += pnl
                    record_replay_exit(state, ts, pnl, count_as_entry_failure=(pnl <= 0))
                    trades.append(_trade_record(position, ts, fill_price, pnl, "long", price))
                    position = None
                    exited_this_bar = True
            else:
                should_exit = False
                if (
                    hard_stop_atr_mult > 0
                    and atr_value is not None
                    and price > position.entry_price + (hard_stop_atr_mult * atr_value)
                ):
                    should_exit = True
                if not should_exit and cfg.exit_on_regime_invalidation and metrics.get("regime_bearish") is False:
                    should_exit = True

                if not should_exit and atr_value is not None:
                    protective_stop = None
                    if cfg.enable_breakeven_stop and position.low_water <= position.entry_price - (cfg.breakeven_after_atr_multiple * atr_value):
                        protective_stop = position.entry_price
                    if cfg.enable_profit_lock and position.low_water <= position.entry_price - (cfg.profit_lock_after_atr_multiple * atr_value):
                        profit_lock_stop = position.entry_price - (cfg.profit_lock_atr_multiple * atr_value)
                        protective_stop = min(protective_stop or profit_lock_stop, profit_lock_stop)
                    if protective_stop is not None and price > protective_stop:
                        should_exit = True

                if (
                    not should_exit
                    and atr_value is not None
                    and position.low_water <= position.entry_price - (cfg.trail_after_atr_multiple * atr_value)
                ):
                    trailing_stop = position.low_water + (trail_mult * atr_value)
                    if price > trailing_stop:
                        should_exit = True

                bars_held = int((bars2.index[: i + 1] > position.entry_ts).sum())
                if not should_exit and bars_held >= cfg.max_bars_in_trade_for("short") and price >= position.entry_price:
                    should_exit = True

                if should_exit:
                    fill_price = apply_slippage(price, "buy", slippage)
                    pnl = (position.entry_price - fill_price) * position.qty - (2 * commission)
                    equity += pnl
                    record_replay_exit(state, ts, pnl, count_as_entry_failure=False)
                    trades.append(_trade_record(position, ts, fill_price, pnl, "short", price))
                    position = None
                    exited_this_bar = True

        can_attempt_short = allow_shorts and signal == "SHORT"
        can_attempt_long = signal == "LONG"
        if position is None and not exited_this_bar and (can_attempt_long or can_attempt_short):
            qty = compute_entry_qty(
                sizing_mode,
                base_qty,
                equity,
                price,
                atr_value,
                max_position_notional_pct,
                target_position_notional_pct=target_position_notional_pct,
                atr_risk_per_trade_pct=atr_risk_per_trade_pct,
                fractional=is_crypto,
            )
            entry_blockers = evaluate_replay_entry(
                state,
                slice_df,
                ts,
                signal,
                signal_strength,
                equity,
                metrics.get("bar_close_ts") or metrics.get("bar_ts"),
                position_notional=(price * qty) if qty > 0 else None,
                cooldown_bars=cooldown_bars,
                risk_config=risk_config,
                require_signal_strength_improvement=require_signal_strength_improvement,
                min_signal_strength_delta=min_signal_strength_delta,
                max_consecutive_entry_failures_per_day=max_consecutive_entry_failures_per_day,
            )
            if qty <= 0:
                entry_blockers.append("position_sizing_blocked")

            if qty > 0 and not [reason for reason in entry_blockers if reason != "cooldown_overridden_stronger_signal"]:
                fill_side = "buy" if signal == "LONG" else "sell"
                fill_price = apply_slippage(price, fill_side, slippage)
                position = ReplayPosition(
                    side="long" if signal == "LONG" else "short",
                    qty=qty,
                    entry_price=fill_price,
                    entry_ts=ts,
                    high_water=price,
                    low_water=price,
                    entry_metrics=_current_entry_meta(ts, signal, metrics, state),
                )
                equity -= commission
                record_replay_entry(state, ts, signal, signal_strength)
                reasons = list(reasons) + entry_blockers
            else:
                reasons = list(reasons) + entry_blockers
        elif position is None and not exited_this_bar and signal == "SHORT" and not allow_shorts:
            reasons = list(reasons) + ["short_signal_diagnostic_only"]

        equity_rows.append({"ts": str(ts), "equity": equity, "signal": signal, "reasons": ";".join(reasons)})

    return pd.DataFrame(equity_rows), trades


def walk_forward_splits(index: pd.DatetimeIndex, train_days: int, test_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    if index.empty:
        return []
    dates = pd.Series(index.tz_convert(ET).date, index=index).drop_duplicates().tolist()
    splits = []
    start = 0
    while start + train_days + test_days <= len(dates):
        train_start = pd.Timestamp(dates[start], tz=ET).tz_convert("UTC")
        train_end = pd.Timestamp(dates[start + train_days - 1], tz=ET).tz_convert("UTC") + timedelta(days=1)
        test_start = pd.Timestamp(dates[start + train_days], tz=ET).tz_convert("UTC")
        test_end = pd.Timestamp(dates[start + train_days + test_days - 1], tz=ET).tz_convert("UTC") + timedelta(days=1)
        splits.append((train_start, train_end, test_start, test_end))
        start += test_days
    return splits


def summarize_replay(trades_df: pd.DataFrame, equity_df: pd.DataFrame) -> dict:
    summary = closed_trade_summary(trades_df)
    summary["max_drawdown"] = max_drawdown(equity_df["equity"]) if not equity_df.empty else None
    if not trades_df.empty and "entry_ts" in trades_df.columns:
        entry_ts = pd.to_datetime(trades_df["entry_ts"], utc=True, errors="coerce")
        days = max(1, entry_ts.dt.tz_convert(ET).dt.date.nunique())
        summary["trades_per_day"] = float(len(trades_df) / days)
    else:
        summary["trades_per_day"] = 0.0
    return summary


def _slippage_stress_payload(
    bars: pd.DataFrame,
    cfg: StrategyConfig,
    sizing_mode: str,
    base_qty: int,
    starting_equity: float,
) -> list[dict]:
    payload: list[dict] = []
    for multiplier in (1.0, 2.0):
        stress_equity, stress_trades = run_replay(
            bars,
            cfg,
            sizing_mode,
            base_qty,
            starting_equity,
            slippage_multiplier=multiplier,
        )
        payload.append(
            {
                "slippage_multiplier": multiplier,
                "summary": summarize_replay(add_condition_buckets(pd.DataFrame(stress_trades)), stress_equity),
            }
        )
    return payload


def _acceptance_summary(full_summary: dict, walk_rows: list[dict], slippage_stress: list[dict]) -> dict:
    positive_walk_windows = sum(1 for row in walk_rows if float(row["summary"].get("net_pnl") or 0.0) > 0.0)
    total_walk_windows = len(walk_rows)
    stress_2x = next((row["summary"] for row in slippage_stress if row["slippage_multiplier"] == 2.0), {})
    checks = {
        "profit_factor_gt_1_10": (full_summary.get("profit_factor") or 0.0) > 1.10,
        "positive_expectancy": (full_summary.get("expectancy") or 0.0) > 0.0,
        "trades_per_day_between_1_and_5": 1.0 <= float(full_summary.get("trades_per_day") or 0.0) <= 5.0,
        "walk_forward_positive_ratio_at_least_two_thirds": total_walk_windows > 0 and positive_walk_windows >= math.ceil((2 * total_walk_windows) / 3),
        "non_negative_under_2x_slippage": (stress_2x.get("net_pnl") or 0.0) >= 0.0,
    }
    return {
        "checks": checks,
        "all_passed": all(checks.values()),
        "positive_walk_windows": positive_walk_windows,
        "total_walk_windows": total_walk_windows,
    }


def write_report(path_md, path_json, payload: dict) -> None:
    lines = [
        "# Research Report",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone(ET)}",
        f"- Symbol: {payload['symbol']}",
        f"- Timeframe: {payload['timeframe_minutes']}m",
        f"- Sizing mode: {payload['sizing_mode']}",
        "",
        "## Full Sample Summary",
    ]
    full = payload["full_summary"]
    lines.extend(
        [
            f"- Trades: {full['trade_count']}",
            f"- Net P&L: {full['net_pnl']}",
            f"- Profit factor: {full['profit_factor']}",
            f"- Win rate: {full['win_rate']}",
            f"- Avg trade: {full['avg_pnl']}",
            f"- Expectancy: {full['expectancy']}",
            f"- Max drawdown: {full['max_drawdown']}",
            f"- Trades per day: {full['trades_per_day']}",
            "",
            "## Walk Forward",
        ]
    )
    if payload["walk_forward"]:
        for row in payload["walk_forward"]:
            lines.append(
                f"- Train {row['train_start']} to {row['train_end']} | Test {row['test_start']} to {row['test_end']} | net={row['summary']['net_pnl']} pf={row['summary']['profit_factor']} trades={row['summary']['trade_count']}"
            )
    else:
        lines.append("- No walk-forward windows generated.")

    lines.extend(["", "## Slippage Stress"])
    for row in payload.get("slippage_stress", []):
        summary = row["summary"]
        lines.append(
            f"- {row['slippage_multiplier']}x slippage: net={summary['net_pnl']} pf={summary['profit_factor']} expectancy={summary['expectancy']} trades/day={summary['trades_per_day']}"
        )

    acceptance = payload.get("acceptance", {})
    if acceptance:
        lines.extend(["", "## Acceptance Checks"])
        lines.append(f"- All passed: {acceptance.get('all_passed')}")
        lines.append(
            f"- Positive walk-forward windows: {acceptance.get('positive_walk_windows')}/{acceptance.get('total_walk_windows')}"
        )
        for name, passed in acceptance.get("checks", {}).items():
            lines.append(f"- {name}: {passed}")

    for title, rows in (
        ("## By Month", payload["by_month"]),
        ("## By Hour", payload["by_hour"]),
        ("## By Side", payload["by_side"]),
        ("## By Session", payload["by_session"]),
        ("## By Regime", payload["by_regime"]),
        ("## By Pullback Depth", payload["by_pullback_depth"]),
        ("## By Weekday", payload["by_weekday"]),
        ("## By Prior Loss", payload["by_prior_loss"]),
    ):
        lines.extend(["", title])
        if rows:
            for row in rows[:8]:
                lines.append(f"- {row['bucket']}: trades={row['trade_count']} net={row['net_pnl']} avg={row['avg_pnl']} pf={row['profit_factor']}")
        else:
            lines.append("- No data.")

    lines.extend(["", "## Best Conditions"])
    lines.extend(payload["best_conditions"] or ["- No repeatable winners yet."])
    lines.extend(["", "## Worst Conditions"])
    lines.extend(payload["worst_conditions"] or ["- No repeatable losers yet."])
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    load_dotenv()
    ensure_runtime_dirs()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    symbol = os.getenv("SYMBOL", "SPY").strip().upper()
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "5"))
    sizing_mode = os.getenv("RESEARCH_SIZING_MODE", os.getenv("POSITION_SIZING_MODE", "fixed")).strip().lower() or "fixed"
    base_qty = int(os.getenv("QTY", "1"))
    lookback_days = int(os.getenv("RESEARCH_LOOKBACK_DAYS", "90"))
    train_days = int(os.getenv("RESEARCH_TRAIN_DAYS", "30"))
    test_days = int(os.getenv("RESEARCH_TEST_DAYS", "10"))
    starting_equity = float(os.getenv("RESEARCH_STARTING_EQUITY", "100000"))

    trading, data = make_clients()
    del trading
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    bars = get_historical_bars(data, symbol, timeframe_minutes, start=start, end=end, limit=None)
    cfg = build_strategy_config(timeframe_minutes)
    equity_df, trades = run_replay(bars, cfg, sizing_mode, base_qty, starting_equity)
    trades_df = add_condition_buckets(pd.DataFrame(trades))

    full_summary = summarize_replay(trades_df, equity_df)
    walk_rows = []
    for train_start, train_end, test_start, test_end in walk_forward_splits(bars.index, train_days, test_days):
        test_bars = bars[(bars.index >= test_start) & (bars.index < test_end)]
        if test_bars.empty:
            continue
        test_equity, test_trades = run_replay(test_bars, cfg, sizing_mode, base_qty, starting_equity)
        walk_rows.append(
            {
                "train_start": str(train_start),
                "train_end": str(train_end),
                "test_start": str(test_start),
                "test_end": str(test_end),
                "summary": summarize_replay(add_condition_buckets(pd.DataFrame(test_trades)), test_equity),
            }
        )

    if not trades_df.empty:
        trades_df["entry_ts"] = pd.to_datetime(trades_df["entry_ts"], utc=True, errors="coerce")
        trades_df["entry_month"] = trades_df["entry_ts"].dt.tz_convert(ET).dt.to_period("M").astype(str)
        trades_df["entry_hour"] = trades_df["entry_ts"].dt.tz_convert(ET).dt.hour.astype(str)
        trades_df["entry_weekday"] = trades_df["entry_ts"].dt.tz_convert(ET).dt.day_name()

    slippage_stress = _slippage_stress_payload(bars, cfg, sizing_mode, base_qty, starting_equity)
    payload = {
        "symbol": symbol,
        "timeframe_minutes": timeframe_minutes,
        "sizing_mode": sizing_mode,
        "strategy_config": asdict(cfg),
        "full_summary": full_summary,
        "walk_forward": walk_rows,
        "slippage_stress": slippage_stress,
        "acceptance": _acceptance_summary(full_summary, walk_rows, slippage_stress),
        "by_month": summarize_by_group(trades_df, "entry_month"),
        "by_hour": summarize_by_group(trades_df, "entry_hour"),
        "by_side": summarize_by_group(trades_df, "entry_signal_side"),
        "by_session": summarize_by_group(trades_df, "session_bucket"),
        "by_regime": summarize_by_group(trades_df, "regime_bucket"),
        "by_pullback_depth": summarize_by_group(trades_df, "pullback_depth_bucket"),
        "by_weekday": summarize_by_group(trades_df, "entry_weekday"),
        "by_prior_loss": summarize_by_group(trades_df, "prior_loss_bucket"),
        "best_conditions": best_worst_conditions(
            trades_df,
            ["session_bucket", "entry_signal_side", "regime_bucket", "pullback_depth_bucket", "volume_ratio_bucket", "hold_bucket"],
        )[0],
        "worst_conditions": best_worst_conditions(
            trades_df,
            ["session_bucket", "entry_signal_side", "regime_bucket", "pullback_depth_bucket", "volume_ratio_bucket", "hold_bucket"],
        )[1],
    }

    stem = os.getenv("RESEARCH_OUTPUT_STEM", "research_latest").strip() or "research_latest"
    write_report(REPORTS_DIR / f"{stem}.md", REPORTS_DIR / f"{stem}.json", payload)
    print(f"Wrote {REPORTS_DIR / f'{stem}.md'}")
    print(f"Wrote {REPORTS_DIR / f'{stem}.json'}")


if __name__ == "__main__":
    main()
