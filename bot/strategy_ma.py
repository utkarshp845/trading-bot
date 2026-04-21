from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
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

    timeframe_minutes: int
    trail_atr_multiplier: float
    max_bars_in_trade: int

    long_adx_threshold: float | None = None
    short_adx_threshold: float | None = None
    long_atr_max_pct: float | None = None
    short_atr_max_pct: float | None = None
    long_volume_min_multiplier: float | None = None
    short_volume_min_multiplier: float | None = None
    min_sma_spread_atr_mult: float = 0.0
    min_sma_spread_pct: float = 0.0
    use_vwap_filter: bool = False
    min_price_distance_from_vwap_pct: float = 0.0
    use_session_open_filter: bool = False
    min_price_distance_from_open_pct: float = 0.0
    entry_windows: tuple[tuple[int, int], ...] = field(default_factory=lambda: ((940, 1130), (1400, 1545)))
    long_entry_windows: tuple[tuple[int, int], ...] | None = None
    short_entry_windows: tuple[tuple[int, int], ...] | None = None
    long_trail_atr_multiplier: float | None = None
    short_trail_atr_multiplier: float | None = None
    long_max_bars_in_trade: int | None = None
    short_max_bars_in_trade: int | None = None
    enable_breakeven_stop: bool = False
    breakeven_after_atr_multiple: float = 1.0
    enable_profit_lock: bool = False
    profit_lock_after_atr_multiple: float = 2.0
    profit_lock_atr_multiple: float = 0.5
    trend_ema_period: int = 0
    min_trend_ema_distance_pct: float = 0.0
    long_min_trend_ema_distance_pct: float | None = None
    short_min_trend_ema_distance_pct: float | None = None
    momentum_lookback_bars: int = 0
    min_momentum_pct: float = 0.0
    long_min_momentum_pct: float | None = None
    short_min_momentum_pct: float | None = None
    min_adx_delta: float = 0.0

    def adx_threshold_for(self, side: str) -> float:
        if side == "long" and self.long_adx_threshold is not None:
            return self.long_adx_threshold
        if side == "short" and self.short_adx_threshold is not None:
            return self.short_adx_threshold
        return self.adx_threshold

    def atr_max_pct_for(self, side: str) -> float:
        if side == "long" and self.long_atr_max_pct is not None:
            return self.long_atr_max_pct
        if side == "short" and self.short_atr_max_pct is not None:
            return self.short_atr_max_pct
        return self.atr_max_pct

    def volume_min_multiplier_for(self, side: str) -> float:
        if side == "long" and self.long_volume_min_multiplier is not None:
            return self.long_volume_min_multiplier
        if side == "short" and self.short_volume_min_multiplier is not None:
            return self.short_volume_min_multiplier
        return self.volume_min_multiplier

    def entry_windows_for(self, side: str) -> tuple[tuple[int, int], ...]:
        if side == "long" and self.long_entry_windows is not None:
            return self.long_entry_windows
        if side == "short" and self.short_entry_windows is not None:
            return self.short_entry_windows
        return self.entry_windows

    def trail_atr_multiplier_for(self, side: str) -> float:
        if side == "long" and self.long_trail_atr_multiplier is not None:
            return self.long_trail_atr_multiplier
        if side == "short" and self.short_trail_atr_multiplier is not None:
            return self.short_trail_atr_multiplier
        return self.trail_atr_multiplier

    def max_bars_in_trade_for(self, side: str) -> int:
        if side == "long" and self.long_max_bars_in_trade is not None:
            return self.long_max_bars_in_trade
        if side == "short" and self.short_max_bars_in_trade is not None:
            return self.short_max_bars_in_trade
        return self.max_bars_in_trade

    def min_trend_ema_distance_pct_for(self, side: str) -> float:
        if side == "long" and self.long_min_trend_ema_distance_pct is not None:
            return self.long_min_trend_ema_distance_pct
        if side == "short" and self.short_min_trend_ema_distance_pct is not None:
            return self.short_min_trend_ema_distance_pct
        return self.min_trend_ema_distance_pct

    def min_momentum_pct_for(self, side: str) -> float:
        if side == "long" and self.long_min_momentum_pct is not None:
            return self.long_min_momentum_pct
        if side == "short" and self.short_min_momentum_pct is not None:
            return self.short_min_momentum_pct
        return self.min_momentum_pct


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def parse_entry_windows(raw: str | None, fallback: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    if raw is None or not raw.strip():
        return fallback

    windows: list[tuple[int, int]] = []
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token:
            continue
        if "-" not in token:
            continue
        start_text, end_text = token.split("-", 1)
        try:
            windows.append((int(start_text.replace(":", "")), int(end_text.replace(":", ""))))
        except ValueError:
            continue

    return tuple(windows) if windows else fallback


def build_strategy_config_from_env(timeframe_minutes: int) -> StrategyConfig:
    default_windows = ((940, 1130), (1400, 1545))
    return StrategyConfig(
        sma_fast=int(os.getenv("SMA_FAST", "20")),
        sma_slow=int(os.getenv("SMA_SLOW", "50")),
        adx_period=int(os.getenv("ADX_PERIOD", "14")),
        adx_threshold=float(os.getenv("ADX_THRESHOLD", "20")),
        atr_period=int(os.getenv("ATR_PERIOD", "14")),
        atr_max_pct=float(os.getenv("ATR_MAX_PCT", "0.0045")),
        volume_ma_period=int(os.getenv("VOLUME_MA_PERIOD", "20")),
        volume_min_multiplier=float(os.getenv("VOLUME_MIN_MULTIPLIER", os.getenv("VOLUME_THRESHOLD_MULTIPLIER", "0.8"))),
        timeframe_minutes=timeframe_minutes,
        trail_atr_multiplier=float(os.getenv("TRAIL_ATR_MULTIPLIER", "1.5")),
        max_bars_in_trade=int(os.getenv("MAX_BARS_IN_TRADE", "12")),
        long_adx_threshold=_env_optional_float("LONG_ADX_THRESHOLD"),
        short_adx_threshold=_env_optional_float("SHORT_ADX_THRESHOLD"),
        long_atr_max_pct=_env_optional_float("LONG_ATR_MAX_PCT"),
        short_atr_max_pct=_env_optional_float("SHORT_ATR_MAX_PCT"),
        long_volume_min_multiplier=_env_optional_float("LONG_VOLUME_MIN_MULTIPLIER"),
        short_volume_min_multiplier=_env_optional_float("SHORT_VOLUME_MIN_MULTIPLIER"),
        min_sma_spread_atr_mult=float(os.getenv("MIN_SMA_SPREAD_ATR_MULT", "0")),
        min_sma_spread_pct=float(os.getenv("MIN_SMA_SPREAD_PCT", "0")),
        use_vwap_filter=_env_flag("USE_VWAP_FILTER", False),
        min_price_distance_from_vwap_pct=float(os.getenv("MIN_PRICE_DISTANCE_FROM_VWAP_PCT", "0")),
        use_session_open_filter=_env_flag("USE_SESSION_OPEN_FILTER", False),
        min_price_distance_from_open_pct=float(os.getenv("MIN_PRICE_DISTANCE_FROM_OPEN_PCT", "0")),
        entry_windows=parse_entry_windows(os.getenv("ENTRY_WINDOWS"), default_windows),
        long_entry_windows=parse_entry_windows(os.getenv("LONG_ENTRY_WINDOWS"), default_windows),
        short_entry_windows=parse_entry_windows(os.getenv("SHORT_ENTRY_WINDOWS"), default_windows),
        long_trail_atr_multiplier=_env_optional_float("LONG_TRAIL_ATR_MULTIPLIER"),
        short_trail_atr_multiplier=_env_optional_float("SHORT_TRAIL_ATR_MULTIPLIER"),
        long_max_bars_in_trade=_env_optional_int("LONG_MAX_BARS_IN_TRADE"),
        short_max_bars_in_trade=_env_optional_int("SHORT_MAX_BARS_IN_TRADE"),
        enable_breakeven_stop=_env_flag("ENABLE_BREAKEVEN_STOP", False),
        breakeven_after_atr_multiple=float(os.getenv("BREAKEVEN_AFTER_ATR_MULTIPLE", "1.0")),
        enable_profit_lock=_env_flag("ENABLE_PROFIT_LOCK", False),
        profit_lock_after_atr_multiple=float(os.getenv("PROFIT_LOCK_AFTER_ATR_MULTIPLE", "2.0")),
        profit_lock_atr_multiple=float(os.getenv("PROFIT_LOCK_ATR_MULTIPLE", "0.5")),
        trend_ema_period=int(os.getenv("TREND_EMA_PERIOD", "0")),
        min_trend_ema_distance_pct=float(os.getenv("MIN_TREND_EMA_DISTANCE_PCT", "0")),
        long_min_trend_ema_distance_pct=_env_optional_float("LONG_MIN_TREND_EMA_DISTANCE_PCT"),
        short_min_trend_ema_distance_pct=_env_optional_float("SHORT_MIN_TREND_EMA_DISTANCE_PCT"),
        momentum_lookback_bars=int(os.getenv("MOMENTUM_LOOKBACK_BARS", "0")),
        min_momentum_pct=float(os.getenv("MIN_MOMENTUM_PCT", "0")),
        long_min_momentum_pct=_env_optional_float("LONG_MIN_MOMENTUM_PCT"),
        short_min_momentum_pct=_env_optional_float("SHORT_MIN_MOMENTUM_PCT"),
        min_adx_delta=float(os.getenv("MIN_ADX_DELTA", "0")),
    )


def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()

    out["sma_fast"] = out["close"].rolling(cfg.sma_fast).mean()
    out["sma_slow"] = out["close"].rolling(cfg.sma_slow).mean()
    if cfg.trend_ema_period > 0:
        out["trend_ema"] = out["close"].ewm(span=cfg.trend_ema_period, adjust=False).mean()
    else:
        out["trend_ema"] = pd.NA

    adx = ADXIndicator(
        high=out["high"],
        low=out["low"],
        close=out["close"],
        window=cfg.adx_period,
    )
    out["adx"] = adx.adx()
    out["adx_delta"] = out["adx"].diff()

    atr = AverageTrueRange(
        high=out["high"],
        low=out["low"],
        close=out["close"],
        window=cfg.atr_period,
    )
    out["atr"] = atr.average_true_range()
    out["atr_pct"] = out["atr"] / out["close"]

    out["volume_ma"] = out["volume"].rolling(cfg.volume_ma_period).mean()
    out["volume_ratio"] = out["volume"] / out["volume_ma"]
    out["sma_spread"] = out["sma_fast"] - out["sma_slow"]
    out["sma_spread_pct"] = out["sma_spread"].abs() / out["close"]
    out["sma_spread_atr"] = out["sma_spread"].abs() / out["atr"].replace(0, pd.NA)
    if cfg.momentum_lookback_bars > 0:
        out["momentum_pct"] = out["close"].pct_change(cfg.momentum_lookback_bars)
    else:
        out["momentum_pct"] = 0.0

    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")

    ts_et = out.index.tz_convert(ET)
    session_dates = pd.Series(ts_et.date, index=out.index)
    out["session_open"] = out.groupby(session_dates)["open"].transform("first")
    cum_pv = (out["close"] * out["volume"]).groupby(session_dates).cumsum()
    cum_volume = out["volume"].groupby(session_dates).cumsum().replace(0, pd.NA)
    out["vwap"] = cum_pv / cum_volume
    out["price_distance_from_vwap_pct"] = (out["close"] - out["vwap"]).abs() / out["close"]
    out["price_distance_from_open_pct"] = (out["close"] - out["session_open"]).abs() / out["close"]
    out["price_distance_from_trend_ema_pct"] = (out["close"] - out["trend_ema"]).abs() / out["close"]

    return out


def _window_label(hhmm_start: int, hhmm_end: int) -> str:
    return f"{hhmm_start // 100:02d}:{hhmm_start % 100:02d}-{hhmm_end // 100:02d}:{hhmm_end % 100:02d}"


def _normalize_ts(ts):
    if ts is None:
        return None
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts


def _bar_close_ts(ts, timeframe_minutes: int):
    normalized = _normalize_ts(ts)
    if normalized is None:
        return None
    return normalized + timedelta(minutes=timeframe_minutes)


def classify_time_window_et(ts, timeframe_minutes: int, windows: tuple[tuple[int, int], ...] | None = None) -> str | None:
    bar_close_ts = _bar_close_ts(ts, timeframe_minutes)
    if bar_close_ts is None:
        return None
    ts_et = bar_close_ts.tz_convert(ET)
    hhmm = ts_et.hour * 100 + ts_et.minute
    use_windows = windows or ((940, 1130), (1400, 1545))
    for start_hhmm, end_hhmm in use_windows:
        if start_hhmm <= hhmm <= end_hhmm:
            return _window_label(start_hhmm, end_hhmm)
    return None


def _in_valid_trade_window_et(ts, timeframe_minutes: int, windows: tuple[tuple[int, int], ...] | None = None) -> bool:
    return classify_time_window_et(ts, timeframe_minutes, windows) is not None


def build_signal_metrics(last: pd.Series, last_ts, cfg: StrategyConfig) -> dict:
    normalized_last_ts = _normalize_ts(last_ts)
    bar_close_ts = _bar_close_ts(normalized_last_ts, cfg.timeframe_minutes)
    bar_close_ts_et = bar_close_ts.tz_convert(ET) if bar_close_ts is not None else None
    return {
        "price": float(last["close"]) if pd.notna(last["close"]) else None,
        "sma_fast": float(last["sma_fast"]) if pd.notna(last["sma_fast"]) else None,
        "sma_slow": float(last["sma_slow"]) if pd.notna(last["sma_slow"]) else None,
        "adx": float(last["adx"]) if pd.notna(last["adx"]) else None,
        "atr": float(last["atr"]) if pd.notna(last["atr"]) else None,
        "atr_pct": float(last["atr_pct"]) if pd.notna(last["atr_pct"]) else None,
        "volume": float(last["volume"]) if pd.notna(last["volume"]) else None,
        "volume_ma": float(last["volume_ma"]) if pd.notna(last["volume_ma"]) else None,
        "volume_ratio": float(last["volume_ratio"]) if pd.notna(last.get("volume_ratio")) else None,
        "sma_spread": float(last["sma_spread"]) if pd.notna(last.get("sma_spread")) else None,
        "sma_spread_pct": float(last["sma_spread_pct"]) if pd.notna(last.get("sma_spread_pct")) else None,
        "sma_spread_atr": float(last["sma_spread_atr"]) if pd.notna(last.get("sma_spread_atr")) else None,
        "trend_ema": float(last["trend_ema"]) if pd.notna(last.get("trend_ema")) else None,
        "price_distance_from_trend_ema_pct": float(last["price_distance_from_trend_ema_pct"]) if pd.notna(last.get("price_distance_from_trend_ema_pct")) else None,
        "momentum_pct": float(last["momentum_pct"]) if pd.notna(last.get("momentum_pct")) else None,
        "adx_delta": float(last["adx_delta"]) if pd.notna(last.get("adx_delta")) else None,
        "vwap": float(last["vwap"]) if pd.notna(last.get("vwap")) else None,
        "session_open": float(last["session_open"]) if pd.notna(last.get("session_open")) else None,
        "price_distance_from_vwap_pct": float(last["price_distance_from_vwap_pct"]) if pd.notna(last.get("price_distance_from_vwap_pct")) else None,
        "price_distance_from_open_pct": float(last["price_distance_from_open_pct"]) if pd.notna(last.get("price_distance_from_open_pct")) else None,
        "bar_ts": str(normalized_last_ts) if normalized_last_ts is not None else None,
        "bar_close_ts": str(bar_close_ts) if bar_close_ts is not None else None,
        "bar_close_ts_et": str(bar_close_ts_et) if bar_close_ts_et is not None else None,
        "long_time_window": classify_time_window_et(normalized_last_ts, cfg.timeframe_minutes, cfg.entry_windows_for("long")),
        "short_time_window": classify_time_window_et(normalized_last_ts, cfg.timeframe_minutes, cfg.entry_windows_for("short")),
    }


def _side_specific_checks(side: str, metrics: dict, cfg: StrategyConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    adx_ok = metrics["adx"] is not None and metrics["adx"] > cfg.adx_threshold_for(side)
    atr_ok = metrics["atr_pct"] is not None and metrics["atr_pct"] <= cfg.atr_max_pct_for(side)
    volume_ok = metrics["volume"] is not None and metrics["volume_ma"] is not None and metrics["volume"] > (
        cfg.volume_min_multiplier_for(side) * metrics["volume_ma"]
    )

    time_window_key = "long_time_window" if side == "long" else "short_time_window"
    time_window_ok = metrics.get(time_window_key) is not None

    spread_atr_ok = cfg.min_sma_spread_atr_mult <= 0 or (
        metrics["sma_spread_atr"] is not None and metrics["sma_spread_atr"] >= cfg.min_sma_spread_atr_mult
    )
    spread_pct_ok = cfg.min_sma_spread_pct <= 0 or (
        metrics["sma_spread_pct"] is not None and metrics["sma_spread_pct"] >= cfg.min_sma_spread_pct
    )
    vwap_ok = not cfg.use_vwap_filter or (
        metrics["price_distance_from_vwap_pct"] is not None
        and metrics["price_distance_from_vwap_pct"] >= cfg.min_price_distance_from_vwap_pct
    )
    open_ok = not cfg.use_session_open_filter or (
        metrics["price_distance_from_open_pct"] is not None
        and metrics["price_distance_from_open_pct"] >= cfg.min_price_distance_from_open_pct
    )
    trend_ema_distance_min = cfg.min_trend_ema_distance_pct_for(side)
    trend_ema_ok = True
    if cfg.trend_ema_period > 0:
        if side == "long":
            trend_ema_ok = (
                metrics["trend_ema"] is not None
                and metrics["price"] is not None
                and metrics["sma_fast"] is not None
                and metrics["price"] > metrics["trend_ema"]
                and metrics["sma_fast"] >= metrics["trend_ema"]
            )
        else:
            trend_ema_ok = (
                metrics["trend_ema"] is not None
                and metrics["price"] is not None
                and metrics["sma_fast"] is not None
                and metrics["price"] < metrics["trend_ema"]
                and metrics["sma_fast"] <= metrics["trend_ema"]
            )
        if trend_ema_ok and trend_ema_distance_min > 0:
            trend_ema_ok = (
                metrics["price_distance_from_trend_ema_pct"] is not None
                and metrics["price_distance_from_trend_ema_pct"] >= trend_ema_distance_min
            )

    min_momentum_pct = cfg.min_momentum_pct_for(side)
    momentum_ok = True
    if cfg.momentum_lookback_bars > 0 and min_momentum_pct > 0:
        if side == "long":
            momentum_ok = metrics["momentum_pct"] is not None and metrics["momentum_pct"] >= min_momentum_pct
        else:
            momentum_ok = metrics["momentum_pct"] is not None and metrics["momentum_pct"] <= (-1.0 * min_momentum_pct)

    adx_delta_ok = cfg.min_adx_delta <= 0 or (
        metrics["adx_delta"] is not None and metrics["adx_delta"] >= cfg.min_adx_delta
    )

    if not adx_ok:
        reasons.append("adx_below_threshold")
    if not atr_ok:
        reasons.append("atr_too_high")
    if not volume_ok:
        reasons.append("volume_below_threshold")
    if not time_window_ok:
        reasons.append("outside_time_window")
    if not spread_atr_ok:
        reasons.append("sma_spread_below_atr_threshold")
    if not spread_pct_ok:
        reasons.append("sma_spread_below_pct_threshold")
    if not vwap_ok:
        reasons.append("too_close_to_vwap")
    if not open_ok:
        reasons.append("too_close_to_session_open")
    if not trend_ema_ok:
        reasons.append("trend_ema_filter_failed")
    if not momentum_ok:
        reasons.append("momentum_filter_failed")
    if not adx_delta_ok:
        reasons.append("adx_not_accelerating")

    return all(
        [adx_ok, atr_ok, volume_ok, time_window_ok, spread_atr_ok, spread_pct_ok, vwap_ok, open_ok, trend_ema_ok, momentum_ok, adx_delta_ok]
    ), reasons


def evaluate_signal_from_metrics(metrics: dict, cfg: StrategyConfig) -> tuple[str, list[str]]:
    required = ["price", "sma_fast", "sma_slow", "adx", "atr", "atr_pct", "volume", "volume_ma"]
    if any(metrics.get(key) is None for key in required):
        return "HOLD", ["indicators_not_ready"]

    trend_up = metrics["sma_fast"] > metrics["sma_slow"]
    trend_down = metrics["sma_fast"] < metrics["sma_slow"]
    long_ok, long_reasons = _side_specific_checks("long", metrics, cfg)
    short_ok, short_reasons = _side_specific_checks("short", metrics, cfg)

    strength_terms = [
        metrics.get("adx") or 0.0,
        (metrics.get("sma_spread_atr") or 0.0) * 10.0,
        (metrics.get("volume_ratio") or 0.0) * 5.0,
        abs(metrics.get("momentum_pct") or 0.0) * 1000.0,
    ]
    metrics["signal_strength"] = round(sum(strength_terms), 6)

    if trend_up and long_ok:
        metrics["time_window_ok"] = True
        metrics["entry_window_bucket"] = metrics.get("long_time_window")
        return "LONG", ["long_entry_filters_passed"]

    if trend_down and short_ok:
        metrics["time_window_ok"] = True
        metrics["entry_window_bucket"] = metrics.get("short_time_window")
        return "SHORT", ["short_entry_filters_passed"]

    reasons = []
    if trend_up:
        reasons.extend(long_reasons)
        reasons.append("trend_up_no_entry")
        metrics["entry_window_bucket"] = metrics.get("long_time_window")
        metrics["time_window_ok"] = metrics.get("long_time_window") is not None
    elif trend_down:
        reasons.extend(short_reasons)
        reasons.append("trend_down_no_entry")
        metrics["entry_window_bucket"] = metrics.get("short_time_window")
        metrics["time_window_ok"] = metrics.get("short_time_window") is not None
    else:
        reasons.extend(sorted(set(long_reasons + short_reasons)))
        reasons.append("trend_neutral")
        metrics["entry_window_bucket"] = None
        metrics["time_window_ok"] = False

    return "HOLD", list(dict.fromkeys(reasons))


def generate_signal(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[str, dict, list[str]]:
    if df.empty:
        return "HOLD", {}, ["no_data"]

    last = df.iloc[-1]
    last_ts = df.index[-1] if len(df.index) > 0 else None
    metrics = build_signal_metrics(last, last_ts, cfg)
    signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
    return signal, metrics, reasons
