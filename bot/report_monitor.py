from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd
from zoneinfo import ZoneInfo

from bot.metrics import add_condition_buckets, best_worst_conditions, closed_trade_summary, load_table_df, summarize_by_group
from bot.paths import DATA_DIR, REPORTS_DIR, ensure_runtime_dirs


ET = ZoneInfo("America/New_York")


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
