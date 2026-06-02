from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
from zoneinfo import ZoneInfo

from bot.metrics import add_condition_buckets, best_worst_conditions, closed_trade_summary, load_table_df, summarize_by_group
from bot.paths import DATA_DIR, REPORTS_DIR, ensure_runtime_dirs


ET = ZoneInfo("America/New_York")

REJECTION_REASONS = [
    "regime_filter_failed",
    "adx_below_threshold",
    "atr_too_high",
    "volume_below_threshold",
    "outside_time_window",
    "sma_spread_below_atr_threshold",
    "sma_spread_below_pct_threshold",
    "trend_ema_filter_failed",
    "pullback_depth_out_of_range",
    "reaccel_not_confirmed",
    "momentum_filter_failed",
    "adx_not_accelerating",
    "spike_bar_blocked",
    "trend_up_no_entry",
    "trend_down_no_entry",
    "trend_neutral",
    "short_signal_diagnostic_only",
    "cooldown",
    "max_trades_hit",
    "max_entry_failures_hit",
    "daily_loss_limit_hit",
    "daily_drawdown_limit_hit",
    "position_notional_limit_hit",
    "stale_bar_data",
    "position_sizing_blocked",
]

NEAR_MISS_IGNORED_REASONS = {
    "trend_up_no_entry",
    "trend_down_no_entry",
    "trend_neutral",
    "short_signal_diagnostic_only",
    "cooldown_overridden_stronger_signal",
}

LATEST_METRIC_KEYS = [
    "regime_side",
    "regime_slope_pct",
    "regime_adx",
    "momentum_pct",
    "pullback_depth_atr",
    "pullback_depth_bucket",
    "bar_range_atr",
    "bar_body_atr",
    "volume_ratio",
    "signal_strength",
]


def _to_dt_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _fmt_money(value) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"${float(value):,.2f}"


def _fmt_pct(value) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _fmt_num(value, digits: int = 4) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _fmt_ts(value) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return str(value)
    return str(ts.tz_convert(ET))


def _table(conn: sqlite3.Connection, name: str) -> pd.DataFrame:
    return load_table_df(conn, name)


def _condition_lines(df: pd.DataFrame, column: str, prefix: str) -> list[str]:
    lines: list[str] = []
    for row in summarize_by_group(df, column)[:4]:
        lines.append(
            f"- {prefix} {row['bucket']}: trades={row['trade_count']} net={_fmt_money(row['net_pnl'])} avg={_fmt_money(row['avg_pnl'])}"
        )
    return lines


def _split_reason_tokens(*values: object) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        for raw in str(value).split(";"):
            token = raw.strip()
            if token:
                tokens.append(token)
    return tokens


def _reason_base(token: str) -> str:
    return token.split("(", 1)[0].strip()


def _count_reason_matches(df: pd.DataFrame, target_reasons: list[str]) -> dict[str, int]:
    counts = {reason: 0 for reason in target_reasons}
    if df.empty:
        return counts

    for row in df.itertuples():
        row_tokens = _split_reason_tokens(getattr(row, "reasons", None), getattr(row, "note", None))
        row_bases = {_reason_base(token) for token in row_tokens}
        for target in target_reasons:
            if target in row_bases:
                counts[target] += 1
    return counts


def _reason_count_sections(runs: pd.DataFrame) -> dict[str, dict[str, int]]:
    if runs.empty or "ts" not in runs.columns:
        return {
            "last_24h": _count_reason_matches(pd.DataFrame(), REJECTION_REASONS),
            "last_7d": _count_reason_matches(pd.DataFrame(), REJECTION_REASONS),
        }

    now = pd.Timestamp(datetime.now(timezone.utc))
    last_24h = runs[runs["ts"] >= now - pd.Timedelta(hours=24)]
    last_7d = runs[runs["ts"] >= now - pd.Timedelta(days=7)]
    return {
        "last_24h": _count_reason_matches(last_24h, REJECTION_REASONS),
        "last_7d": _count_reason_matches(last_7d, REJECTION_REASONS),
    }


def _top_reason_lines(counts: dict[str, int], limit: int = 8) -> list[str]:
    top = [(reason, count) for reason, count in counts.items() if count > 0]
    top.sort(key=lambda item: (-item[1], item[0]))
    if not top:
        return ["- No rejection reasons recorded in this window."]
    return [f"- {reason}: {count}" for reason, count in top[:limit]]


def _parse_metrics(value: object) -> dict:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_metric_lines(latest_run) -> list[str]:
    if latest_run is None:
        return []
    metrics = _parse_metrics(latest_run.get("metrics_json"))
    if not metrics:
        return ["- Strategy metrics: n/a"]

    parts: list[str] = []
    for key in LATEST_METRIC_KEYS:
        value = metrics.get(key)
        if isinstance(value, float):
            formatted = _fmt_num(value, 6 if key.endswith("_pct") else 4)
        else:
            formatted = "n/a" if value is None else str(value)
        parts.append(f"{key}={formatted}")
    return [f"- Strategy metrics: {'; '.join(parts)}"]


def _near_miss_rows(runs: pd.DataFrame, limit: int = 10) -> list[dict]:
    if runs.empty:
        return []

    near_misses: list[dict] = []
    for row in runs.tail(500).itertuples():
        signal = str(getattr(row, "signal", "") or "")
        action = str(getattr(row, "desired_action", "") or "")
        try:
            position_qty = float(getattr(row, "position_qty", 0.0) or 0.0)
        except (TypeError, ValueError):
            position_qty = 0.0
        if signal != "HOLD" or action != "HOLD" or position_qty != 0.0:
            continue

        tokens = _split_reason_tokens(getattr(row, "reasons", None), getattr(row, "note", None))
        blockers: list[str] = []
        for token in tokens:
            base = _reason_base(token)
            if base in NEAR_MISS_IGNORED_REASONS:
                continue
            if base not in blockers:
                blockers.append(base)
        if not 1 <= len(blockers) <= 2:
            continue

        metrics = _parse_metrics(getattr(row, "metrics_json", None))
        near_misses.append(
            {
                "ts": getattr(row, "ts", None),
                "price": getattr(row, "price", None),
                "blockers": blockers,
                "regime_side": metrics.get("regime_side"),
                "momentum_pct": metrics.get("momentum_pct"),
                "pullback_depth_atr": metrics.get("pullback_depth_atr"),
                "bar_range_atr": metrics.get("bar_range_atr"),
                "volume_ratio": metrics.get("volume_ratio"),
                "signal_strength": metrics.get("signal_strength"),
            }
        )

    return near_misses[-limit:]


def _near_miss_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return ["- No near-miss entry bars found in the recent run window."]

    lines: list[str] = []
    for row in rows:
        lines.append(
            f"- {_fmt_ts(row['ts'])} price={_fmt_money(row.get('price'))} blockers={';'.join(row['blockers'])} "
            f"regime={row.get('regime_side') or 'n/a'} momentum={_fmt_num(row.get('momentum_pct'), 6)} "
            f"pullback={_fmt_num(row.get('pullback_depth_atr'), 3)} range_atr={_fmt_num(row.get('bar_range_atr'), 3)} "
            f"volume_ratio={_fmt_num(row.get('volume_ratio'), 3)} strength={_fmt_num(row.get('signal_strength'), 3)}"
        )
    return lines


def main() -> None:
    ensure_runtime_dirs()
    db_path = DATA_DIR / "bot.db"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        out = REPORTS_DIR / "monitor_latest.md"
        out.write_text("# Monitor Report\n\nNo database found yet.\n", encoding="utf-8")
        return

    conn = sqlite3.connect(db_path)
    runs = _table(conn, "runs")
    orders = _table(conn, "orders")
    events = _table(conn, "events")
    closed = _table(conn, "closed_trades")
    state = _table(conn, "state")
    positions = _table(conn, "position_state")

    if not runs.empty and "ts" in runs.columns:
        runs["ts"] = _to_dt_utc(runs["ts"])
        runs = runs.dropna(subset=["ts"]).sort_values("ts")
        runs["ts_et"] = runs["ts"].dt.tz_convert(ET)

    if not orders.empty and "ts" in orders.columns:
        orders["ts"] = _to_dt_utc(orders["ts"])
        orders = orders.dropna(subset=["ts"]).sort_values("ts")
        if "filled_at" in orders.columns:
            orders["filled_at"] = _to_dt_utc(orders["filled_at"])

    if not events.empty and "ts" in events.columns:
        events["ts"] = _to_dt_utc(events["ts"])
        events = events.dropna(subset=["ts"]).sort_values("ts")

    if not closed.empty and "exit_ts" in closed.columns:
        closed["exit_ts"] = _to_dt_utc(closed["exit_ts"])
        closed = closed.dropna(subset=["exit_ts"]).sort_values("exit_ts")
        closed["exit_hour_et"] = closed["exit_ts"].dt.tz_convert(ET).dt.hour
        closed = add_condition_buckets(closed)

    latest_run = runs.iloc[-1] if not runs.empty else None
    latest_events = events.tail(20) if not events.empty else pd.DataFrame()
    pending_orders = orders[orders["processed_at"].isna()] if not orders.empty and "processed_at" in orders.columns else pd.DataFrame()
    summary = closed_trade_summary(closed)
    rejection_counts = _reason_count_sections(runs)
    near_misses = _near_miss_rows(runs)

    pnl_by_hour_lines: list[str] = []
    if not closed.empty and "pnl" in closed.columns:
        by_hour = (
            closed.groupby("exit_hour_et", as_index=False)["pnl"]
            .agg(["count", "sum", "mean"])
            .reset_index()
            .sort_values("exit_hour_et")
        )
        for row in by_hour.itertuples():
            pnl_by_hour_lines.append(
                f"- Hour {int(row.exit_hour_et):02d}: trades={int(row.count)} net={_fmt_money(row.sum)} avg={_fmt_money(row.mean)}"
            )

    recent_trade_lines: list[str] = []
    if not closed.empty:
        for row in closed.tail(10).itertuples():
            recent_trade_lines.append(
                f"- {_fmt_ts(row.exit_ts)} side={row.side} qty={row.qty} pnl={_fmt_money(row.pnl)} return={_fmt_pct(row.return_pct)} exit_reason={row.exit_reason or 'n/a'}"
            )

    recent_event_lines: list[str] = []
    if not latest_events.empty:
        for row in latest_events.itertuples():
            recent_event_lines.append(
                f"- {_fmt_ts(row.ts)} [{row.level}] {row.event_type}: {row.message or ''}".rstrip()
            )

    condition_lines = []
    if not closed.empty:
        condition_lines.extend(_condition_lines(closed, "session_bucket", "session"))
        condition_lines.extend(_condition_lines(closed, "entry_signal_side", "side"))
        condition_lines.extend(_condition_lines(closed, "adx_bucket", "adx"))
        best_lines, worst_lines = best_worst_conditions(
            closed,
            ["session_bucket", "entry_signal_side", "adx_bucket", "atr_bucket", "volume_ratio_bucket", "hold_bucket"],
        )
    else:
        best_lines = []
        worst_lines = []

    state_lines: list[str] = []
    if not state.empty:
        row = state.iloc[0]
        state_lines.extend(
            [
                f"- Trades today: {int(row.get('trades_today', 0))}",
                f"- Last trade ts: {_fmt_ts(row.get('last_trade_ts'))}",
                f"- Consecutive losses: {int(row.get('consecutive_losses', 0))}",
                f"- Daily start equity: {_fmt_money(row.get('daily_start_equity'))}",
            ]
        )

    position_lines: list[str] = []
    if not positions.empty:
        for row in positions.itertuples():
            position_lines.append(
                f"- {row.symbol} side={row.side or 'flat'} entry={_fmt_money(row.entry_price)} entry_ts={_fmt_ts(row.entry_ts)} high={_fmt_money(row.highest_price)} low={_fmt_money(row.lowest_price)}"
            )
    else:
        position_lines.append("- No tracked open position state.")

    pending_lines: list[str] = []
    if not pending_orders.empty:
        for row in pending_orders.itertuples():
            pending_lines.append(
                f"- order_id={row.order_id} side={row.side} intent={row.intent or 'n/a'} status={row.status or 'n/a'} submitted={_fmt_ts(row.ts)}"
            )
    else:
        pending_lines.append("- No pending orders.")

    latest_run_lines: list[str] = []
    if latest_run is not None:
        latest_run_lines.extend(
            [
                f"- Run time: {_fmt_ts(latest_run['ts'])}",
                f"- Symbol: {latest_run.get('symbol')}",
                f"- Signal: {latest_run.get('signal')}",
                f"- Recorded action: {latest_run.get('desired_action') or 'n/a'}",
                f"- Position qty: {latest_run.get('position_qty')}",
                f"- Equity: {_fmt_money(latest_run.get('equity'))}",
                f"- Note: {latest_run.get('note') or 'n/a'}",
                f"- Reasons: {latest_run.get('reasons') or 'n/a'}",
            ]
        )
        latest_run_lines.extend(_latest_metric_lines(latest_run))

    profit_factor_text = "n/a" if summary["profit_factor"] is None else f"{summary['profit_factor']:.2f}"
    lines = [
        "# Monitor Report",
        "",
        f"Generated: {datetime.now(timezone.utc).astimezone(ET)}",
        "",
        "## Latest Run",
    ]
    lines.extend(latest_run_lines if latest_run_lines else ["- No runs recorded yet."])
    lines.extend(
        [
            "",
            "## Bot State",
        ]
    )
    lines.extend(state_lines if state_lines else ["- No state row found yet."])
    lines.extend(
        [
            "",
            "## Open Position State",
            *position_lines,
            "",
            "## Pending Orders",
            *pending_lines,
            "",
            "## Rejection Counts",
            "Last 24h:",
            *_top_reason_lines(rejection_counts["last_24h"]),
            "",
            "Last 7d:",
            *_top_reason_lines(rejection_counts["last_7d"]),
            "",
            "## Near-Miss Entry Bars",
            *_near_miss_lines(near_misses),
            "",
            "## Closed Trade Summary",
            f"- Trades: {summary['trade_count']}",
            f"- Net P&L: {_fmt_money(summary['net_pnl'])}",
            f"- Win rate: {_fmt_pct(summary['win_rate'])}",
            f"- Avg trade: {_fmt_money(summary['avg_pnl'])}",
            f"- Profit factor: {profit_factor_text}",
            "",
            "## P&L By Exit Hour (ET)",
        ]
    )
    lines.extend(pnl_by_hour_lines if pnl_by_hour_lines else ["- No closed trades yet."])
    lines.extend(
        [
            "",
            "## Recent Closed Trades",
        ]
    )
    lines.extend(recent_trade_lines if recent_trade_lines else ["- No closed trades yet."])
    lines.extend(
        [
            "",
            "## Condition Breakdown",
        ]
    )
    lines.extend(condition_lines if condition_lines else ["- No condition data yet."])
    lines.extend(
        [
            "",
            "## Best Conditions",
        ]
    )
    lines.extend(best_lines if best_lines else ["- No repeatable winners yet."])
    lines.extend(
        [
            "",
            "## Worst Conditions",
        ]
    )
    lines.extend(worst_lines if worst_lines else ["- No repeatable losers yet."])
    lines.extend(
        [
            "",
            "## Recent Events",
        ]
    )
    lines.extend(recent_event_lines if recent_event_lines else ["- No events recorded yet."])
    lines.append("")

    monitor_md = REPORTS_DIR / "monitor_latest.md"
    monitor_json = REPORTS_DIR / "monitor_latest.json"
    monitor_md.write_text("\n".join(lines), encoding="utf-8")
    monitor_json.write_text(
        json.dumps(
            {
                "generated_at_et": str(datetime.now(timezone.utc).astimezone(ET)),
                "latest_run": None if latest_run is None else latest_run.to_dict(),
                "state": [] if state.empty else state.to_dict(orient="records"),
                "positions": [] if positions.empty else positions.to_dict(orient="records"),
                "pending_orders": [] if pending_orders.empty else pending_orders.to_dict(orient="records"),
                "rejection_counts": rejection_counts,
                "near_misses": near_misses,
                "closed_trade_summary": summary,
            },
            default=str,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {monitor_md}")
    print(f"Wrote {monitor_json}")
    conn.close()


if __name__ == "__main__":
    main()
