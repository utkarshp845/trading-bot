from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd


def load_table_df(conn: sqlite3.Connection, table: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(f"SELECT * FROM {table};", conn)
    except Exception:
        return pd.DataFrame()


def closed_trade_summary(df: pd.DataFrame) -> dict[str, Optional[float]]:
    if df.empty or "pnl" not in df.columns:
        return {
            "trade_count": 0,
            "win_rate": None,
            "gross_profit": None,
            "gross_loss": None,
            "profit_factor": None,
            "avg_pnl": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "net_pnl": None,
        }

    pnl = pd.to_numeric(df["pnl"], errors="coerce").dropna()
    if pnl.empty:
        return {
            "trade_count": 0,
            "win_rate": None,
            "gross_profit": None,
            "gross_loss": None,
            "profit_factor": None,
            "avg_pnl": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "net_pnl": None,
        }

    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = float(losses.sum()) if not losses.empty else 0.0
    profit_factor = None
    if gross_loss != 0:
        profit_factor = abs(gross_profit / gross_loss)

    return {
        "trade_count": int(len(pnl)),
        "win_rate": float((pnl > 0).mean()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_pnl": float(pnl.mean()),
        "avg_win": float(wins.mean()) if not wins.empty else None,
        "avg_loss": float(losses.mean()) if not losses.empty else None,
        "expectancy": float(pnl.mean()),
        "net_pnl": float(pnl.sum()),
    }


def max_drawdown(equity: pd.Series) -> float | None:
    eq = pd.to_numeric(equity, errors="coerce").dropna()
    if eq.empty:
        return None
    peak = eq.cummax()
    dd = (eq - peak) / peak
    return float(dd.min())
