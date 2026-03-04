import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd


ET = ZoneInfo("America/New_York")


def _read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _to_dt_utc(series: pd.Series) -> pd.Series:
    # equity.csv uses ISO timestamps like 2026-03-04T15:45:03.207802+00:00
    return pd.to_datetime(series, utc=True, errors="coerce")


def _max_drawdown(equity: pd.Series) -> float | None:
    eq = pd.to_numeric(equity, errors="coerce").dropna()
    if eq.empty:
        return None
    peak = eq.cummax()
    dd = (eq - peak) / peak
    return float(dd.min())


def _fmt_money(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"${x:,.2f}"


def _fmt_pct(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x*100:.2f}%"


def main():
    symbol = os.getenv("SYMBOL", "SPY").strip().upper()

    equity_path = "/app/logs/equity.csv"
    trades_path = "/app/logs/trades.csv"
    out_dir = "/app/reports"
    os.makedirs(out_dir, exist_ok=True)

    eq = _read_csv(equity_path)
    tr = _read_csv(trades_path)

    now_et = datetime.now(ET)
    report_date_et = now_et.date().isoformat()
    out_path = os.path.join(out_dir, f"daily_{report_date_et}.md")

    if eq.empty or "ts_utc" not in eq.columns:
        content = f"# Daily Report ({report_date_et} ET)\n\nNo equity data found yet.\n"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Wrote {out_path}")
        return

    eq["ts"] = _to_dt_utc(eq["ts_utc"])
    eq = eq.dropna(subset=["ts"]).sort_values("ts")

    # Convert timestamps to ET and derive ET date for grouping
    eq["ts_et"] = eq["ts"].dt.tz_convert(ET)
    eq["date_et"] = eq["ts_et"].dt.date.astype(str)

    # Daily start/end equity
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

    # Totals
    first_equity = float(pd.to_numeric(eq["equity"], errors="coerce").dropna().iloc[0])
    last_equity = float(pd.to_numeric(eq["equity"], errors="coerce").dropna().iloc[-1])
    total_pnl = last_equity - first_equity
    mdd = _max_drawdown(eq["equity"])

    # Today's row (ET)
    today_row = daily[daily["date_et"] == report_date_et]
    if not today_row.empty:
        start_eq = float(today_row["start_equity"].iloc[0])
        end_eq = float(today_row["end_equity"].iloc[0])
        day_pnl = float(today_row["day_pnl"].iloc[0])
    else:
        start_eq = end_eq = day_pnl = None

    # Trades summary for today (ET)
    trades_today = tr.copy()
    trades_count = 0
    buys = sells = 0
    last_order_ts_et = None

    if not trades_today.empty and "ts_utc" in trades_today.columns:
        trades_today["ts"] = pd.to_datetime(trades_today["ts_utc"], utc=True, errors="coerce")
        trades_today = trades_today.dropna(subset=["ts"]).sort_values("ts")
        trades_today["ts_et"] = trades_today["ts"].dt.tz_convert(ET)
        trades_today["date_et"] = trades_today["ts_et"].dt.date.astype(str)

        todays = trades_today[trades_today["date_et"] == report_date_et]
        trades_count = int(len(todays))
        if "side" in todays.columns:
            buys = int((todays["side"].astype(str).str.lower() == "buy").sum())
            sells = int((todays["side"].astype(str).str.lower() == "sell").sum())
        if not todays.empty:
            last_order_ts_et = str(todays["ts_et"].iloc[-1])

    lines = []
    lines.append(f"# Daily Report ({report_date_et} ET)")
    lines.append("")
    lines.append(f"**Symbol:** {symbol}")
    lines.append("")
    lines.append("## Today")
    lines.append(f"- Start equity: {_fmt_money(start_eq)}")
    lines.append(f"- End equity: {_fmt_money(end_eq)}")
    lines.append(f"- Day P&L: {_fmt_money(day_pnl)}")
    lines.append(f"- Trades: {trades_count} (buys: {buys}, sells: {sells})")
    if last_order_ts_et:
        lines.append(f"- Last order time (ET): {last_order_ts_et}")
    lines.append("")
    lines.append("## Overall")
    lines.append(f"- First recorded equity: {_fmt_money(first_equity)}")
    lines.append(f"- Latest equity: {_fmt_money(last_equity)}")
    lines.append(f"- Total P&L: {_fmt_money(total_pnl)}")
    lines.append(f"- Max drawdown (from equity series): {_fmt_pct(mdd)}")
    lines.append("")
    lines.append("## Notes")
    lines.append("- This report uses regular session timing (ET) and does not account for market holidays/early closes yet.")
    lines.append("- P&L is based on account equity snapshots written by the bot each run.")
    lines.append("")

    content = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()