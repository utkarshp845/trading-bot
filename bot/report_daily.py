from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.metrics import add_condition_buckets, best_worst_conditions, closed_trade_summary, load_table_df, max_drawdown, summarize_by_group
from bot.paths import DATA_DIR, LOGS_DIR, REPORTS_DIR, ensure_runtime_dirs


ET = ZoneInfo("America/New_York")
ACTION_TYPES = ["open_long", "close_long", "open_short", "close_short"]
REJECTION_REASONS = [
    "adx_below_threshold",
    "atr_too_high",
    "volume_below_threshold",
    "outside_time_window",
    "cooldown",
    "stale_bar_data",
]


def _read_csv(path: Path, header: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=header)

    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
    except Exception:
        return pd.DataFrame(columns=header)

    try:
        if first_line.split(",")[0] == header[0]:
            return pd.read_csv(path)
        return pd.read_csv(path, names=header, header=None)
    except Exception:
        return pd.DataFrame(columns=header)


def _to_dt_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _fmt_money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def _fmt_num(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:.2f}"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None or pd.isna(seconds):
        return "n/a"
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _append_group_section(lines: list[str], title: str, rows: list[dict]) -> None:
    lines.append("")
    lines.append(title)
    if not rows:
        lines.append("- No data yet.")
        return
    for row in rows[:5]:
        pf = "n/a" if row["profit_factor"] is None else f"{row['profit_factor']:.2f}"
        win_rate = "n/a" if row["win_rate"] is None else f"{row['win_rate'] * 100:.1f}%"
        lines.append(
            f"- {row['bucket']}: trades={row['trade_count']} net={_fmt_money(row['net_pnl'])} avg={_fmt_money(row['avg_pnl'])} win={win_rate} pf={pf}"
        )


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


def _count_reason_matches(df: pd.DataFrame, target_reasons: list[str]) -> dict[str, int]:
    counts = {reason: 0 for reason in target_reasons}
    if df.empty:
        return counts

    for row in df.itertuples():
        row_tokens = _split_reason_tokens(getattr(row, "reasons", None), getattr(row, "note", None))
        for target in target_reasons:
            if any(token == target or token.startswith(f"{target}(") for token in row_tokens):
                counts[target] += 1
    return counts


def main():
    load_dotenv()
    ensure_runtime_dirs()
    symbol = os.getenv("SYMBOL", "SPY").strip().upper()

    equity_path = LOGS_DIR / "equity.csv"
    db_path = DATA_DIR / "bot.db"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    eq = _read_csv(equity_path, ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"])
    conn = sqlite3.connect(db_path) if db_path.exists() else None
    runs = load_table_df(conn, "runs") if conn is not None else pd.DataFrame()
    orders = load_table_df(conn, "orders") if conn is not None else pd.DataFrame()
    closed = load_table_df(conn, "closed_trades") if conn is not None else pd.DataFrame()

    now_et = datetime.now(ET)
    report_date_et = now_et.date().isoformat()
    out_path = REPORTS_DIR / f"daily_{report_date_et}.md"

    lines: list[str] = [f"# Daily Report ({report_date_et} ET)", "", f"**Symbol:** {symbol}"]

    strategy_version = os.getenv("STRATEGY_VERSION", "").strip() or None
    if not runs.empty and "ts" in runs.columns:
        runs["ts"] = _to_dt_utc(runs["ts"])
        runs = runs.dropna(subset=["ts"]).sort_values("ts")
        runs["ts_et"] = runs["ts"].dt.tz_convert(ET)
        runs["date_et"] = runs["ts_et"].dt.date.astype(str)
        latest_strategy = runs["strategy_version"].dropna().iloc[-1] if "strategy_version" in runs.columns and not runs["strategy_version"].dropna().empty else None
        strategy_version = latest_strategy or strategy_version
    else:
        runs = pd.DataFrame()

    if strategy_version:
        lines.extend(["", f"**Strategy version:** {strategy_version}"])

    if not eq.empty and "ts_utc" in eq.columns:
        eq["ts"] = _to_dt_utc(eq["ts_utc"])
        eq = eq.dropna(subset=["ts"]).sort_values("ts")
        eq["ts_et"] = eq["ts"].dt.tz_convert(ET)
        eq["date_et"] = eq["ts_et"].dt.date.astype(str)

        daily = (
            eq.dropna(subset=["equity"])
            .groupby("date_et", as_index=False)
            .agg(
                start_equity=("equity", "first"),
                end_equity=("equity", "last"),
                start_cash=("cash", "first"),
                end_cash=("cash", "last"),
            )
        )
        daily["day_pnl"] = daily["end_equity"] - daily["start_equity"]

        first_equity = float(pd.to_numeric(eq["equity"], errors="coerce").dropna().iloc[0])
        last_equity = float(pd.to_numeric(eq["equity"], errors="coerce").dropna().iloc[-1])
        total_pnl = last_equity - first_equity
        mdd = max_drawdown(eq["equity"])

        today_row = daily[daily["date_et"] == report_date_et]
        if not today_row.empty:
            start_eq = float(today_row["start_equity"].iloc[0])
            end_eq = float(today_row["end_equity"].iloc[0])
            day_pnl = float(today_row["day_pnl"].iloc[0])
        else:
            start_eq = end_eq = day_pnl = None
    else:
        first_equity = last_equity = total_pnl = mdd = None
        start_eq = end_eq = day_pnl = None

    if not orders.empty and "ts" in orders.columns:
        orders["ts"] = _to_dt_utc(orders["ts"])
        orders = orders.dropna(subset=["ts"]).sort_values("ts")
        orders["ts_et"] = orders["ts"].dt.tz_convert(ET)
        orders["date_et"] = orders["ts_et"].dt.date.astype(str)
        today_orders = orders[orders["date_et"] == report_date_et]
        last_order_ts_et = str(today_orders["ts_et"].iloc[-1]) if not today_orders.empty else None
        action_counts = {
            action: int((today_orders.get("action_type", pd.Series(dtype="object")).astype(str) == action).sum())
            for action in ACTION_TYPES
        }
        buys = int((today_orders.get("side", pd.Series(dtype="object")).astype(str).str.lower() == "buy").sum())
        sells = int((today_orders.get("side", pd.Series(dtype="object")).astype(str).str.lower() == "sell").sum())
        orders_count = int(len(today_orders))
    else:
        today_orders = pd.DataFrame()
        last_order_ts_et = None
        action_counts = {action: 0 for action in ACTION_TYPES}
        buys = 0
        sells = 0
        orders_count = 0

    if not closed.empty and "exit_ts" in closed.columns:
        closed["exit_ts"] = _to_dt_utc(closed["exit_ts"])
        closed["entry_ts"] = _to_dt_utc(closed["entry_ts"]) if "entry_ts" in closed.columns else pd.NaT
        closed = closed.dropna(subset=["exit_ts"]).sort_values("exit_ts")
        closed["exit_ts_et"] = closed["exit_ts"].dt.tz_convert(ET)
        closed["date_et"] = closed["exit_ts_et"].dt.date.astype(str)
        if "hold_seconds" not in closed.columns:
            closed["hold_seconds"] = (closed["exit_ts"] - closed["entry_ts"]).dt.total_seconds()
        today_closed = closed[closed["date_et"] == report_date_et]
        today_closed = add_condition_buckets(today_closed)
        closed = add_condition_buckets(closed)
        today_summary = closed_trade_summary(today_closed)
        overall_summary = closed_trade_summary(closed)
        avg_hold_seconds = float(today_closed["hold_seconds"].dropna().mean()) if not today_closed.empty and not today_closed["hold_seconds"].dropna().empty else None
    else:
        today_closed = pd.DataFrame()
        today_summary = closed_trade_summary(today_closed)
        overall_summary = closed_trade_summary(pd.DataFrame())
        avg_hold_seconds = None

    today_runs = runs[runs["date_et"] == report_date_et] if not runs.empty else pd.DataFrame()
    rejection_counts = _count_reason_matches(today_runs, REJECTION_REASONS)

    lines.extend(
        [
            "",
            "## Today",
            f"- Start equity: {_fmt_money(start_eq)}",
            f"- End equity: {_fmt_money(end_eq)}",
            f"- Day P&L (equity): {_fmt_money(day_pnl)}",
            f"- Orders submitted: {orders_count} (buys: {buys}, sells: {sells})",
            f"- Action counts: open_long={action_counts['open_long']}, close_long={action_counts['close_long']}, open_short={action_counts['open_short']}, close_short={action_counts['close_short']}",
            f"- Closed trades: {today_summary['trade_count']}",
            f"- Closed-trade P&L: {_fmt_money(today_summary['net_pnl'])}",
            f"- Win rate: {_fmt_pct(today_summary['win_rate'])}",
            f"- Profit factor: {_fmt_num(today_summary['profit_factor'])}",
            f"- Avg hold time (closed trades): {_fmt_duration(avg_hold_seconds)}",
        ]
    )
    if last_order_ts_et:
        lines.append(f"- Last order time (ET): {last_order_ts_et}")

    lines.extend(
        [
            "",
            "## Rejections",
            *(f"- {reason}: {rejection_counts[reason]}" for reason in REJECTION_REASONS),
            "",
            "## Overall",
            f"- First recorded equity: {_fmt_money(first_equity)}",
            f"- Latest equity: {_fmt_money(last_equity)}",
            f"- Total P&L (equity): {_fmt_money(total_pnl)}",
            f"- Max drawdown: {_fmt_pct(mdd)}",
            f"- Closed trades: {overall_summary['trade_count']}",
            f"- Net closed-trade P&L: {_fmt_money(overall_summary['net_pnl'])}",
            f"- Win rate: {_fmt_pct(overall_summary['win_rate'])}",
            f"- Avg trade: {_fmt_money(overall_summary['avg_pnl'])}",
            f"- Avg win: {_fmt_money(overall_summary['avg_win'])}",
            f"- Avg loss: {_fmt_money(overall_summary['avg_loss'])}",
            f"- Profit factor: {_fmt_num(overall_summary['profit_factor'])}",
        ]
    )

    if not today_closed.empty:
        lines.append("")
        lines.append("## Closed Trades Today")
        for row in today_closed.tail(10).itertuples():
            lines.append(
                f"- {row.side} qty={row.qty} entry={_fmt_money(row.entry_price)} exit={_fmt_money(row.exit_price)} pnl={_fmt_money(row.pnl)} hold={_fmt_duration(getattr(row, 'hold_seconds', None))} reason={row.exit_reason or 'n/a'}"
            )

    if not today_closed.empty:
        _append_group_section(lines, "## Performance By Session", summarize_by_group(today_closed, "session_bucket"))
        _append_group_section(lines, "## Performance By Side", summarize_by_group(today_closed, "entry_signal_side"))
        _append_group_section(lines, "## Performance By ADX Bucket", summarize_by_group(today_closed, "adx_bucket"))
        _append_group_section(lines, "## Performance By ATR Bucket", summarize_by_group(today_closed, "atr_bucket"))
        _append_group_section(lines, "## Performance By Volume Ratio Bucket", summarize_by_group(today_closed, "volume_ratio_bucket"))
        _append_group_section(lines, "## Performance By Hold Bucket", summarize_by_group(today_closed, "hold_bucket"))
        best_lines, worst_lines = best_worst_conditions(
            today_closed,
            ["session_bucket", "entry_signal_side", "adx_bucket", "atr_bucket", "volume_ratio_bucket", "hold_bucket"],
        )
        lines.append("")
        lines.append("## Best Conditions")
        lines.extend(best_lines if best_lines else ["- No repeatable condition winners yet."])
        lines.append("")
        lines.append("## Worst Conditions")
        lines.extend(worst_lines if worst_lines else ["- No repeatable condition losers yet."])

    lines.extend(
        [
            "",
            "## Notes",
            "- Action counts come from semantic order action types captured at submission time.",
            "- Rejection counts come from run-level reasons and notes for the trading day.",
            "- Equity and closed-trade summaries can differ intraday because open P&L remains unrealized until a position is closed.",
            "",
        ]
    )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")

    if conn is not None:
        conn.close()


if __name__ == "__main__":
    main()
