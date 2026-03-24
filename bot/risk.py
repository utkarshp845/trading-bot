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
    max_consecutive_losses: int
    stale_bar_max_minutes: int
    max_position_notional_pct: float


@dataclass
class RiskEvaluation:
    allow_entries: bool
    reasons: list[str]


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


def is_stale_bar(last_bar_ts: object, max_age_minutes: int, now_utc: Optional[datetime] = None) -> bool:
    bar_dt = parse_ts(last_bar_ts)
    if bar_dt is None:
        return True

    current = now_utc or datetime.now(timezone.utc)
    age_seconds = (current - bar_dt).total_seconds()
    return age_seconds > (max_age_minutes * 60)


def evaluate_entry_risk(
    cfg: RiskConfig,
    trades_today: int,
    consecutive_losses: int,
    daily_start_equity: float | None,
    current_equity: float | None,
    last_bar_ts: object,
    position_notional: float | None,
    now_utc: Optional[datetime] = None,
) -> RiskEvaluation:
    reasons: list[str] = []

    if trades_today >= cfg.max_trades_per_day:
        reasons.append("max_trades_hit")

    if cfg.max_consecutive_losses > 0 and consecutive_losses >= cfg.max_consecutive_losses:
        reasons.append("loss_streak_limit_hit")

    if (
        daily_start_equity is not None
        and current_equity is not None
        and daily_start_equity > 0
        and cfg.max_daily_drawdown_pct > 0
    ):
        drawdown = (current_equity - daily_start_equity) / daily_start_equity
        if drawdown <= (-1.0 * cfg.max_daily_drawdown_pct):
            reasons.append(f"daily_drawdown_limit_hit({drawdown:.4f})")

    if cfg.stale_bar_max_minutes > 0 and is_stale_bar(last_bar_ts, cfg.stale_bar_max_minutes, now_utc):
        reasons.append("stale_bar_data")

    if (
        cfg.max_position_notional_pct > 0
        and current_equity is not None
        and current_equity > 0
        and position_notional is not None
        and position_notional > (current_equity * cfg.max_position_notional_pct)
    ):
        reasons.append("position_notional_limit_hit")

    return RiskEvaluation(allow_entries=not reasons, reasons=reasons)
