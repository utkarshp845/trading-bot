from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


@dataclass
class RiskConfig:
    max_trades_per_day: int
    max_daily_drawdown_pct: float
    max_daily_loss: float
    max_consecutive_losses: int
    max_bar_age_seconds: int
    max_position_notional_pct: float


@dataclass
class RiskEvaluation:
    allow_entries: bool
    reasons: list[str]
    stale_bar_details: dict[str, object | None]


def trading_day_et(ts: Optional[datetime] = None) -> str:
    now = ts.astimezone(ET) if ts is not None else datetime.now(ET)
    return now.date().isoformat()


def parse_ts(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        parsed = pd.to_datetime(text, utc=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime()

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def stale_bar_details(
    last_bar_ts: object,
    max_age_seconds: int,
    now_utc: Optional[datetime] = None,
    timestamp_basis: str = "bar_open",
) -> dict[str, object | None]:
    bar_dt = parse_ts(last_bar_ts)
    current = now_utc or datetime.now(timezone.utc)
    age_seconds = (current - bar_dt).total_seconds() if bar_dt is not None else None
    is_stale = True if bar_dt is None else age_seconds > max_age_seconds
    return {
        "current_runtime_timestamp": current.isoformat(),
        "latest_bar_timestamp": bar_dt.isoformat() if bar_dt is not None else None,
        "computed_bar_age_seconds": round(age_seconds, 6) if age_seconds is not None else None,
        "allowed_max_age": max_age_seconds,
        "timestamp_basis": timestamp_basis,
        "is_stale": is_stale,
    }


def is_stale_bar(last_bar_ts: object, max_age_seconds: int, now_utc: Optional[datetime] = None) -> bool:
    details = stale_bar_details(last_bar_ts, max_age_seconds, now_utc=now_utc)
    return bool(details["is_stale"])


def evaluate_entry_risk(
    cfg: RiskConfig,
    trades_today: int,
    consecutive_losses: int,
    daily_start_equity: float | None,
    current_equity: float | None,
    last_bar_ts: object,
    position_notional: float | None,
    now_utc: Optional[datetime] = None,
    stale_bar_timestamp_basis: str = "bar_open",
) -> RiskEvaluation:
    reasons: list[str] = []
    parsed_bar_ts = parse_ts(last_bar_ts)
    current_runtime = now_utc or datetime.now(timezone.utc)
    stale_details = {
        "current_runtime_timestamp": current_runtime.isoformat(),
        "latest_bar_timestamp": parsed_bar_ts.isoformat() if parsed_bar_ts is not None else None,
        "computed_bar_age_seconds": None,
        "allowed_max_age": cfg.max_bar_age_seconds,
        "timestamp_basis": stale_bar_timestamp_basis,
        "is_stale": None,
    }

    if trades_today >= cfg.max_trades_per_day:
        reasons.append("max_trades_hit")

    if cfg.max_consecutive_losses > 0 and consecutive_losses >= cfg.max_consecutive_losses:
        reasons.append("max_consecutive_losses_hit")

    if cfg.max_daily_loss > 0 and daily_start_equity is not None and current_equity is not None:
        daily_pnl = current_equity - daily_start_equity
        if daily_pnl <= (-1.0 * cfg.max_daily_loss):
            reasons.append("max_daily_loss_hit")

    if (
        daily_start_equity is not None
        and current_equity is not None
        and daily_start_equity > 0
        and cfg.max_daily_drawdown_pct > 0
    ):
        drawdown = (current_equity - daily_start_equity) / daily_start_equity
        if drawdown <= (-1.0 * cfg.max_daily_drawdown_pct):
            reasons.append(f"daily_drawdown_limit_hit({drawdown:.4f})")

    if cfg.max_bar_age_seconds > 0:
        stale_details = stale_bar_details(
            last_bar_ts,
            cfg.max_bar_age_seconds,
            now_utc=now_utc,
            timestamp_basis=stale_bar_timestamp_basis,
        )
        if stale_details["is_stale"]:
            reasons.append("stale_bar_data")

    if (
        cfg.max_position_notional_pct > 0
        and current_equity is not None
        and current_equity > 0
        and position_notional is not None
        and position_notional > (current_equity * cfg.max_position_notional_pct)
    ):
        reasons.append("position_notional_limit_hit")

    return RiskEvaluation(allow_entries=not reasons, reasons=reasons, stale_bar_details=stale_details)
