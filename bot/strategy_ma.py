from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from ta.trend import ADXIndicator
from ta.volatility import AverageTrueRange


ET = ZoneInfo("America/New_York")


@dataclass
class StrategyConfig:
    sma_fast: int
    sma_slow: int

    adx_period: int
    adx_threshold: float

    atr_period: int
    atr_max_pct: float

    volume_ma_period: int
    volume_min_multiplier: float

    trail_atr_multiplier: float
    max_bars_in_trade: int


def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()

    out["sma_fast"] = out["close"].rolling(cfg.sma_fast).mean()
    out["sma_slow"] = out["close"].rolling(cfg.sma_slow).mean()

    adx = ADXIndicator(
        high=out["high"],
        low=out["low"],
        close=out["close"],
        window=cfg.adx_period
    )
    out["adx"] = adx.adx()

    atr = AverageTrueRange(
        high=out["high"],
        low=out["low"],
        close=out["close"],
        window=cfg.atr_period
    )
    out["atr"] = atr.average_true_range()
    out["atr_pct"] = out["atr"] / out["close"]

    out["volume_ma"] = out["volume"].rolling(cfg.volume_ma_period).mean()

    return out


def _in_valid_trade_window_et(ts) -> bool:
    """
    Valid entry windows:
      - 09:40 to 11:30 ET
      - 14:00 to 15:45 ET
    """
    if ts is None:
        return False

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")

    ts_et = ts.tz_convert(ET)
    hour = ts_et.hour
    minute = ts_et.minute
    hhmm = hour * 100 + minute

    in_morning = 940 <= hhmm <= 1130
    in_afternoon = 1400 <= hhmm <= 1545
    return in_morning or in_afternoon


def generate_signal(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[str, dict, list[str]]:
    """
    Returns:
      signal: "LONG" | "SHORT" | "HOLD"
      metrics: dict
      reasons: list[str]
    """
    if df.empty:
        return "HOLD", {}, ["no_data"]

    last = df.iloc[-1]
    last_ts = df.index[-1] if len(df.index) > 0 else None

    metrics = {
        "price": float(last["close"]) if pd.notna(last["close"]) else None,
        "sma_fast": float(last["sma_fast"]) if pd.notna(last["sma_fast"]) else None,
        "sma_slow": float(last["sma_slow"]) if pd.notna(last["sma_slow"]) else None,
        "adx": float(last["adx"]) if pd.notna(last["adx"]) else None,
        "atr": float(last["atr"]) if pd.notna(last["atr"]) else None,
        "atr_pct": float(last["atr_pct"]) if pd.notna(last["atr_pct"]) else None,
        "volume": float(last["volume"]) if pd.notna(last["volume"]) else None,
        "volume_ma": float(last["volume_ma"]) if pd.notna(last["volume_ma"]) else None,
        "bar_ts": str(last_ts) if last_ts is not None else None,
        "time_window_ok": _in_valid_trade_window_et(last_ts),
    }

    required = ["price", "sma_fast", "sma_slow", "adx", "atr", "atr_pct", "volume", "volume_ma"]
    if any(metrics[k] is None for k in required):
        return "HOLD", metrics, ["indicators_not_ready"]

    trend_up = metrics["sma_fast"] > metrics["sma_slow"]
    trend_down = metrics["sma_fast"] < metrics["sma_slow"]
    adx_ok = metrics["adx"] > cfg.adx_threshold
    atr_ok = metrics["atr_pct"] <= cfg.atr_max_pct
    volume_ok = metrics["volume"] > (cfg.volume_min_multiplier * metrics["volume_ma"])
    time_window_ok = metrics["time_window_ok"]

    bullish_ok = trend_up and adx_ok and atr_ok and volume_ok and time_window_ok
    bearish_ok = trend_down and adx_ok and atr_ok and volume_ok and time_window_ok

    if bullish_ok:
        return "LONG", metrics, ["long_entry_filters_passed"]

    if bearish_ok:
        return "SHORT", metrics, ["short_entry_filters_passed"]

    reasons = []
    if not adx_ok:
        reasons.append("adx_below_threshold")
    if not atr_ok:
        reasons.append("atr_too_high")
    if not volume_ok:
        reasons.append("volume_below_threshold")
    if not time_window_ok:
        reasons.append("outside_time_window")
    if trend_up:
        reasons.append("trend_up_no_entry")
    elif trend_down:
        reasons.append("trend_down_no_entry")
    else:
        reasons.append("trend_neutral")

    return "HOLD", metrics, reasons
