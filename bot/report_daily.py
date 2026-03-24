from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from zoneinfo import ZoneInfo

from bot.metrics import closed_trade_summary, load_table_df, max_drawdown
from bot.paths import DATA_DIR, LOGS_DIR, REPORTS_DIR, ensure_runtime_dirs


ET = ZoneInfo("America/New_York")


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


def main():
    ensure_runtime_dirs()
    symbol = os.getenv("SYMBOL", "SPY").strip().upper()

    equity_path = LOGS_DIR / "equity.csv"
    trades_path = LOGS_DIR / "trades.csv"
    db_path = DATA_DIR / "bot.db"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    eq = _read_csv(equity_path, ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"])
    tr = _read_csv(
        trades_path,
        ["ts_utc", "symbol", "side", "qty", "order_id", "status", "filled_avg_price", "filled_qty", "intent", "note"],
    )

    conn = sqlite3.connect(db_path) if db_path.exists() else None
    closed = load_table_df(conn, "closed_trades") if conn is not None else pd.DataFrame()

    now_et = datetime.now(ET)
    report_date_et = now_et.date().isoformat()
    out_path = REPORTS_DIR / f"daily_{report_date_et}.md"

    lines: list[str] = [f"# Daily Report ({report_date_et} ET)", "", f"**Symbol:** {symbol}", ""]

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

    trades_count = 0
    buys = 0
    sells = 0
    last_order_ts_et = None
    if not tr.empty and "ts_utc" in tr.columns:
        tr["ts"] = _to_dt_utc(tr["ts_utc"])
        tr = tr.dropna(subset=["ts"]).sort_values("ts")
        tr["ts_et"] = tr["ts"].dt.tz_convert(ET)
        tr["date_et"] = tr["ts_et"].dt.date.astype(str)
        todays = tr[tr["date_et"] == report_date_et]
        trades_count = int(len(todays))
        buys = int((todays["side"].astype(str).str.lower() == "buy").sum())
        sells = int((todays["side"].astype(str).str.lower() == "sell").sum())
        if not todays.empty:
            last_order_ts_et = str(todays["ts_et"].iloc[-1])

    today_closed = pd.DataFrame()
    overall_summary = closed_trade_summary(closed)
    today_summary = closed_trade_summary(today_closed)
    if not closed.empty and "exit_ts" in closed.columns:
        closed["exit_ts"] = _to_dt_utc(closed["exit_ts"])
        closed = closed.dropna(subset=["exit_ts"]).sort_values("exit_ts")
        closed["exit_ts_et"] = closed["exit_ts"].dt.tz_convert(ET)
        closed["date_et"] = closed["exit_ts_et"].dt.date.astype(str)
        today_closed = closed[closed["date_et"] == report_date_et]
        today_summary = closed_trade_summary(today_closed)
        overall_summary = closed_trade_summary(closed)

    lines.append("## Today")
    lines.append(f"- Start equity: {_fmt_money(start_eq)}")
    lines.append(f"- End equity: {_fmt_money(end_eq)}")
    lines.append(f"- Day P&L (equity): {_fmt_money(day_pnl)}")
    lines.append(f"- Orders submitted: {trades_count} (buys: {buys}, sells: {sells})")
    lines.append(f"- Closed trades: {today_summary['trade_count']}")
    lines.append(f"- Closed-trade P&L: {_fmt_money(today_summary['net_pnl'])}")
    lines.append(f"- Win rate: {_fmt_pct(today_summary['win_rate'])}")
    lines.append(f"- Profit factor: {_fmt_num(today_summary['profit_factor'])}")
    if last_order_ts_et:
        lines.append(f"- Last order time (ET): {last_order_ts_et}")

    lines.append("")
    lines.append("## Overall")
    lines.append(f"- First recorded equity: {_fmt_money(first_equity)}")
    lines.append(f"- Latest equity: {_fmt_money(last_equity)}")
    lines.append(f"- Total P&L (equity): {_fmt_money(total_pnl)}")
    lines.append(f"- Max drawdown: {_fmt_pct(mdd)}")
    lines.append(f"- Closed trades: {overall_summary['trade_count']}")
    lines.append(f"- Net closed-trade P&L: {_fmt_money(overall_summary['net_pnl'])}")
    lines.append(f"- Win rate: {_fmt_pct(overall_summary['win_rate'])}")
    lines.append(f"- Avg trade: {_fmt_money(overall_summary['avg_pnl'])}")
    lines.append(f"- Avg win: {_fmt_money(overall_summary['avg_win'])}")
    lines.append(f"- Avg loss: {_fmt_money(overall_summary['avg_loss'])}")
    lines.append(f"- Profit factor: {_fmt_num(overall_summary['profit_factor'])}")

    if not today_closed.empty:
        lines.append("")
        lines.append("## Closed Trades Today")
        for row in today_closed.tail(10).itertuples():
            lines.append(
                f"- {row.side} qty={row.qty} entry={_fmt_money(row.entry_price)} exit={_fmt_money(row.exit_price)} pnl={_fmt_money(row.pnl)} reason={row.exit_reason or 'n/a'}"
            )

    lines.append("")
    lines.append("## Notes")
    lines.append("- Orders are counted as trades only after broker-confirmed fills are reconciled.")
    lines.append("- Equity and closed-trade summaries can differ intraday because open P&L remains unrealized until a position is closed.")
    lines.append("- This report supports both legacy headerless CSV logs and the new headered format.")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")

    if conn is not None:
        conn.close()


if __name__ == "__main__":
    main()
