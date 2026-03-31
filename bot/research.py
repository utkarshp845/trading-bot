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
from bot.strategy_ma import StrategyConfig, compute_indicators, generate_signal, parse_entry_windows


ET = ZoneInfo("America/New_York")


@dataclass
class ReplayPosition:
    side: str
    qty: int
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


def _env_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def build_strategy_config(timeframe_minutes: int) -> StrategyConfig:
    default_windows = ((940, 1130), (1400, 1545))
    return StrategyConfig(
        sma_fast=int(os.getenv("SMA_FAST", "20")),
        sma_slow=int(os.getenv("SMA_SLOW", "50")),
        adx_period=int(os.getenv("ADX_PERIOD", "14")),
        adx_threshold=float(os.getenv("ADX_THRESHOLD", "20")),
        atr_period=int(os.getenv("ATR_PERIOD", "14")),
        atr_max_pct=float(os.getenv("ATR_MAX_PCT", "0.0045")),
        volume_ma_period=int(os.getenv("VOLUME_MA_PERIOD", "20")),
        volume_min_multiplier=float(os.getenv("VOLUME_MIN_MULTIPLIER", os.getenv("VOLUME_THRESHOLD_MULTIPLIER", "0.8"))),
        timeframe_minutes=timeframe_minutes,
        trail_atr_multiplier=float(os.getenv("TRAIL_ATR_MULTIPLIER", "1.5")),
        max_bars_in_trade=int(os.getenv("MAX_BARS_IN_TRADE", "12")),
        long_adx_threshold=_env_optional_float("LONG_ADX_THRESHOLD"),
        short_adx_threshold=_env_optional_float("SHORT_ADX_THRESHOLD"),
        long_atr_max_pct=_env_optional_float("LONG_ATR_MAX_PCT"),
        short_atr_max_pct=_env_optional_float("SHORT_ATR_MAX_PCT"),
        long_volume_min_multiplier=_env_optional_float("LONG_VOLUME_MIN_MULTIPLIER"),
        short_volume_min_multiplier=_env_optional_float("SHORT_VOLUME_MIN_MULTIPLIER"),
        min_sma_spread_atr_mult=float(os.getenv("MIN_SMA_SPREAD_ATR_MULT", "0")),
        min_sma_spread_pct=float(os.getenv("MIN_SMA_SPREAD_PCT", "0")),
        use_vwap_filter=_env_flag("USE_VWAP_FILTER", False),
        min_price_distance_from_vwap_pct=float(os.getenv("MIN_PRICE_DISTANCE_FROM_VWAP_PCT", "0")),
        use_session_open_filter=_env_flag("USE_SESSION_OPEN_FILTER", False),
        min_price_distance_from_open_pct=float(os.getenv("MIN_PRICE_DISTANCE_FROM_OPEN_PCT", "0")),
        entry_windows=parse_entry_windows(os.getenv("ENTRY_WINDOWS"), default_windows),
        long_entry_windows=parse_entry_windows(os.getenv("LONG_ENTRY_WINDOWS"), default_windows),
        short_entry_windows=parse_entry_windows(os.getenv("SHORT_ENTRY_WINDOWS"), default_windows),
        long_trail_atr_multiplier=_env_optional_float("LONG_TRAIL_ATR_MULTIPLIER"),
        short_trail_atr_multiplier=_env_optional_float("SHORT_TRAIL_ATR_MULTIPLIER"),
        long_max_bars_in_trade=_env_optional_int("LONG_MAX_BARS_IN_TRADE"),
        short_max_bars_in_trade=_env_optional_int("SHORT_MAX_BARS_IN_TRADE"),
        enable_breakeven_stop=_env_flag("ENABLE_BREAKEVEN_STOP", False),
        breakeven_after_atr_multiple=float(os.getenv("BREAKEVEN_AFTER_ATR_MULTIPLE", "1.0")),
        enable_profit_lock=_env_flag("ENABLE_PROFIT_LOCK", False),
        profit_lock_after_atr_multiple=float(os.getenv("PROFIT_LOCK_AFTER_ATR_MULTIPLE", "2.0")),
        profit_lock_atr_multiple=float(os.getenv("PROFIT_LOCK_ATR_MULTIPLE", "0.5")),
    )


def compute_qty(mode: str, base_qty: int, equity: float, price: float, atr_value: float | None) -> int:
    max_position_notional_pct = float(os.getenv("MAX_POSITION_NOTIONAL_PCT", "0.02"))
    if mode == "fixed":
        return base_qty
    cap_notional_pct = min(float(os.getenv("TARGET_POSITION_NOTIONAL_PCT", str(max_position_notional_pct))), max_position_notional_pct)
    capped_qty = math.floor((equity * cap_notional_pct) / price) if price > 0 else 0
    if mode == "notional_cap":
        return max(0, capped_qty)
    risk_pct = float(os.getenv("ATR_RISK_PER_TRADE_PCT", "0.0025"))
    if atr_value is None or atr_value <= 0:
        return 0
    atr_qty = math.floor((equity * risk_pct) / atr_value)
    return max(0, min(atr_qty, capped_qty))


def apply_slippage(price: float, side: str, slippage_per_share: float) -> float:
    if side == "buy":
        return price + slippage_per_share
    return price - slippage_per_share


def run_replay(bars: pd.DataFrame, cfg: StrategyConfig, sizing_mode: str, base_qty: int, starting_equity: float) -> tuple[pd.DataFrame, list[dict]]:
    commission = float(os.getenv("RESEARCH_COMMISSION_PER_TRADE", "0"))
    slippage = float(os.getenv("RESEARCH_SLIPPAGE_PER_SHARE", "0.01"))
    reversal_signal_strength_min = float(os.getenv("REVERSAL_SIGNAL_STRENGTH_MIN", "0"))
    bars2 = compute_indicators(bars, cfg)

    equity = starting_equity
    position: ReplayPosition | None = None
    equity_rows: list[dict] = []
    trades: list[dict] = []

    for i in range(len(bars2)):
        slice_df = bars2.iloc[: i + 1]
        signal, metrics, reasons = generate_signal(slice_df, cfg)
        row = bars2.iloc[i]
        ts = bars2.index[i]
        price = float(metrics.get("price") or row["close"])
        atr_value = metrics.get("atr")
        signal_strength = float(metrics.get("signal_strength") or 0.0)

        if position is not None:
            position.high_water = max(position.high_water, price)
            position.low_water = min(position.low_water, price)
            trail_mult = cfg.trail_atr_multiplier_for(position.side)
            if position.side == "long":
                stop_price = position.high_water - (trail_mult * float(atr_value or 0.0))
                if cfg.enable_breakeven_stop and atr_value is not None and price >= position.entry_price + (cfg.breakeven_after_atr_multiple * atr_value):
                    stop_price = max(stop_price, position.entry_price)
                if cfg.enable_profit_lock and atr_value is not None and price >= position.entry_price + (cfg.profit_lock_after_atr_multiple * atr_value):
                    stop_price = max(stop_price, position.entry_price + (cfg.profit_lock_atr_multiple * atr_value))
                bars_held = int((bars2.index[: i + 1] > position.entry_ts).sum())
                should_exit = price < stop_price
                if not should_exit and bars_held >= cfg.max_bars_in_trade_for("long") and price <= position.entry_price:
                    should_exit = True
                if not should_exit and signal == "SHORT" and signal_strength >= reversal_signal_strength_min:
                    should_exit = True
                if should_exit:
                    fill_price = apply_slippage(price, "sell", slippage)
                    pnl = (fill_price - position.entry_price) * position.qty - (2 * commission)
                    equity += pnl
                    trades.append(
                        {
                            "entry_ts": str(position.entry_ts),
                            "exit_ts": str(ts),
                            "side": position.side,
                            "qty": position.qty,
                            "entry_price": position.entry_price,
                            "exit_price": fill_price,
                            "pnl": pnl,
                            "return_pct": (fill_price - position.entry_price) / position.entry_price,
                            "entry_signal_side": position.entry_metrics.get("entry_signal_side"),
                            "entry_adx": position.entry_metrics.get("adx"),
                            "entry_atr_pct": position.entry_metrics.get("atr_pct"),
                            "entry_volume_ratio": position.entry_metrics.get("volume_ratio"),
                            "entry_sma_spread_pct": position.entry_metrics.get("sma_spread_pct"),
                            "entry_window_bucket": position.entry_metrics.get("entry_window_bucket"),
                            "hold_seconds": (ts - position.entry_ts).total_seconds(),
                            "realized_slippage_estimate": abs(fill_price - float(position.entry_metrics.get("decision_price") or price)),
                        }
                    )
                    position = None
            else:
                stop_price = position.low_water + (trail_mult * float(atr_value or 0.0))
                if cfg.enable_breakeven_stop and atr_value is not None and price <= position.entry_price - (cfg.breakeven_after_atr_multiple * atr_value):
                    stop_price = min(stop_price, position.entry_price)
                if cfg.enable_profit_lock and atr_value is not None and price <= position.entry_price - (cfg.profit_lock_after_atr_multiple * atr_value):
                    stop_price = min(stop_price, position.entry_price - (cfg.profit_lock_atr_multiple * atr_value))
                bars_held = int((bars2.index[: i + 1] > position.entry_ts).sum())
                should_exit = price > stop_price
                if not should_exit and bars_held >= cfg.max_bars_in_trade_for("short") and price >= position.entry_price:
                    should_exit = True
                if not should_exit and signal == "LONG" and signal_strength >= reversal_signal_strength_min:
                    should_exit = True
                if should_exit:
                    fill_price = apply_slippage(price, "buy", slippage)
                    pnl = (position.entry_price - fill_price) * position.qty - (2 * commission)
                    equity += pnl
                    trades.append(
                        {
                            "entry_ts": str(position.entry_ts),
                            "exit_ts": str(ts),
                            "side": position.side,
                            "qty": position.qty,
                            "entry_price": position.entry_price,
                            "exit_price": fill_price,
                            "pnl": pnl,
                            "return_pct": (position.entry_price - fill_price) / position.entry_price,
                            "entry_signal_side": position.entry_metrics.get("entry_signal_side"),
                            "entry_adx": position.entry_metrics.get("adx"),
                            "entry_atr_pct": position.entry_metrics.get("atr_pct"),
                            "entry_volume_ratio": position.entry_metrics.get("volume_ratio"),
                            "entry_sma_spread_pct": position.entry_metrics.get("sma_spread_pct"),
                            "entry_window_bucket": position.entry_metrics.get("entry_window_bucket"),
                            "hold_seconds": (ts - position.entry_ts).total_seconds(),
                            "realized_slippage_estimate": abs(fill_price - float(position.entry_metrics.get("decision_price") or price)),
                        }
                    )
                    position = None

        if position is None and signal in {"LONG", "SHORT"}:
            qty = compute_qty(sizing_mode, base_qty, equity, price, atr_value)
            if qty > 0:
                fill_side = "buy" if signal == "LONG" else "sell"
                fill_price = apply_slippage(price, fill_side, slippage)
                position = ReplayPosition(
                    side="long" if signal == "LONG" else "short",
                    qty=qty,
                    entry_price=fill_price,
                    entry_ts=ts,
                    high_water=price,
                    low_water=price,
                    entry_metrics={
                        "entry_signal_side": "long" if signal == "LONG" else "short",
                        "adx": metrics.get("adx"),
                        "atr_pct": metrics.get("atr_pct"),
                        "volume_ratio": metrics.get("volume_ratio"),
                        "sma_spread_pct": metrics.get("sma_spread_pct"),
                        "entry_window_bucket": metrics.get("entry_window_bucket"),
                        "signal_strength": metrics.get("signal_strength"),
                        "decision_price": price,
                    },
                )
                equity -= commission

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

    for title, rows in (
        ("## By Month", payload["by_month"]),
        ("## By Hour", payload["by_hour"]),
        ("## By Side", payload["by_side"]),
        ("## By Session", payload["by_session"]),
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
    payload = {
        "symbol": symbol,
        "timeframe_minutes": timeframe_minutes,
        "sizing_mode": sizing_mode,
        "strategy_config": asdict(cfg),
        "full_summary": full_summary,
        "walk_forward": walk_rows,
        "by_month": summarize_by_group(trades_df, "entry_month"),
        "by_hour": summarize_by_group(trades_df, "entry_hour"),
        "by_side": summarize_by_group(trades_df, "entry_signal_side"),
        "by_session": summarize_by_group(trades_df, "session_bucket"),
        "best_conditions": best_worst_conditions(
            trades_df,
            ["session_bucket", "entry_signal_side", "adx_bucket", "atr_bucket", "volume_ratio_bucket", "hold_bucket"],
        )[0],
        "worst_conditions": best_worst_conditions(
            trades_df,
            ["session_bucket", "entry_signal_side", "adx_bucket", "atr_bucket", "volume_ratio_bucket", "hold_bucket"],
        )[1],
    }

    stem = os.getenv("RESEARCH_OUTPUT_STEM", "research_latest").strip() or "research_latest"
    write_report(REPORTS_DIR / f"{stem}.md", REPORTS_DIR / f"{stem}.json", payload)
    print(f"Wrote {REPORTS_DIR / f'{stem}.md'}")
    print(f"Wrote {REPORTS_DIR / f'{stem}.json'}")


if __name__ == "__main__":
    main()
