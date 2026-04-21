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

    trade_count = int(len(pnl))
    return {
        "trade_count": trade_count,
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


def add_condition_buckets(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    out = df.copy()
    for numeric in ("entry_adx", "entry_atr_pct", "entry_volume_ratio", "hold_seconds", "entry_sma_spread_pct"):
        if numeric in out.columns:
            out[numeric] = pd.to_numeric(out[numeric], errors="coerce")

    if "entry_window_bucket" in out.columns:
        out["session_bucket"] = out["entry_window_bucket"].map(
            lambda value: "morning" if isinstance(value, str) and value.startswith(("09", "10", "11")) else "afternoon" if isinstance(value, str) else "unknown"
        )
    if "entry_regime_on" in out.columns:
        out["regime_bucket"] = out["entry_regime_on"].map(
            lambda value: "regime_on" if value is True else "regime_off" if value is False else "unknown"
        )
    if "entry_pullback_depth_bucket" in out.columns:
        out["pullback_depth_bucket"] = out["entry_pullback_depth_bucket"].fillna("n/a")
    if "entry_after_prior_loss" in out.columns:
        out["prior_loss_bucket"] = out["entry_after_prior_loss"].map(
            lambda value: "after_loss" if value is True else "not_after_loss" if value is False else "unknown"
        )
    if "entry_adx" in out.columns:
        out["adx_bucket"] = pd.cut(
            out["entry_adx"],
            bins=[-float("inf"), 20, 25, 35, float("inf")],
            labels=["<20", "20-25", "25-35", "35+"],
        ).astype("object")
    if "entry_atr_pct" in out.columns:
        out["atr_bucket"] = pd.cut(
            out["entry_atr_pct"],
            bins=[-float("inf"), 0.0015, 0.0030, 0.0050, float("inf")],
            labels=["<0.15%", "0.15-0.30%", "0.30-0.50%", "0.50%+"],
        ).astype("object")
    if "entry_volume_ratio" in out.columns:
        out["volume_ratio_bucket"] = pd.cut(
            out["entry_volume_ratio"],
            bins=[-float("inf"), 0.8, 1.0, 1.5, float("inf")],
            labels=["<0.8", "0.8-1.0", "1.0-1.5", "1.5+"],
        ).astype("object")
    if "hold_seconds" in out.columns:
        out["hold_bucket"] = pd.cut(
            out["hold_seconds"],
            bins=[-float("inf"), 1800, 3600, 7200, float("inf")],
            labels=["<30m", "30-60m", "60-120m", "120m+"],
        ).astype("object")
    return out


def summarize_by_group(df: pd.DataFrame, column: str) -> list[dict]:
    if df.empty or column not in df.columns:
        return []

    results: list[dict] = []
    grouped = df.groupby(column, dropna=False)
    for key, subset in grouped:
        summary = closed_trade_summary(subset)
        label = "n/a" if key is None or (isinstance(key, float) and pd.isna(key)) else str(key)
        results.append(
            {
                "bucket": label,
                "trade_count": summary["trade_count"],
                "net_pnl": summary["net_pnl"],
                "avg_pnl": summary["avg_pnl"],
                "win_rate": summary["win_rate"],
                "profit_factor": summary["profit_factor"],
                "expectancy": summary["expectancy"],
            }
        )

    results.sort(key=lambda row: (row["net_pnl"] is None, -(row["net_pnl"] or 0)))
    return results


def best_worst_conditions(df: pd.DataFrame, columns: list[str], min_trades: int = 2) -> tuple[list[str], list[str]]:
    enriched = add_condition_buckets(df)
    scored: list[tuple[str, float, int]] = []
    for column in columns:
        for row in summarize_by_group(enriched, column):
            if row["trade_count"] >= min_trades and row["avg_pnl"] is not None:
                scored.append((f"{column}={row['bucket']}", float(row["avg_pnl"]), int(row["trade_count"])))

    scored.sort(key=lambda item: item[1], reverse=True)
    best = [f"- {label}: avg={avg:,.2f} trades={count}" for label, avg, count in scored[:3]]
    worst = [f"- {label}: avg={avg:,.2f} trades={count}" for label, avg, count in scored[-3:]]
    return best, worst
