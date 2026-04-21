from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import timedelta
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
    allow_shorts: bool = True
    regime_timeframe_minutes: int = 60
    regime_ema_period: int = 96
    regime_adx_period: int = 14
    regime_adx_min: float = 18.0
    regime_slope_lookback_bars: int = 3
    regime_min_slope_pct: float = 0.002
    regime_atr_max_pct: float = 0.02
    pullback_lookback_bars: int = 6
    pullback_min_depth_atr: float = 0.4
    pullback_max_depth_atr: float = 1.2
    reaccel_min_bar_body_atr: float = 0.2
    spike_bar_max_range_atr: float = 1.8
    min_volume_ratio: float = 1.1
    trail_after_atr_multiple: float = 1.5
    exit_on_regime_invalidation: bool = True

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

    def volume_ratio_threshold_for(self, side: str) -> float:
        return max(self.volume_min_multiplier_for(side), self.min_volume_ratio)

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
        if not token or "-" not in token:
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
        trend_ema_period=int(os.getenv("EXEC_TREND_EMA_PERIOD", os.getenv("TREND_EMA_PERIOD", "55"))),
        min_trend_ema_distance_pct=float(os.getenv("MIN_TREND_EMA_DISTANCE_PCT", "0")),
        long_min_trend_ema_distance_pct=_env_optional_float("LONG_MIN_TREND_EMA_DISTANCE_PCT"),
        short_min_trend_ema_distance_pct=_env_optional_float("SHORT_MIN_TREND_EMA_DISTANCE_PCT"),
        momentum_lookback_bars=int(os.getenv("MOMENTUM_LOOKBACK_BARS", "0")),
        min_momentum_pct=float(os.getenv("MIN_MOMENTUM_PCT", "0")),
        long_min_momentum_pct=_env_optional_float("LONG_MIN_MOMENTUM_PCT"),
        short_min_momentum_pct=_env_optional_float("SHORT_MIN_MOMENTUM_PCT"),
        min_adx_delta=float(os.getenv("MIN_ADX_DELTA", "0")),
        allow_shorts=_env_flag("ALLOW_SHORTS", True),
        regime_timeframe_minutes=int(os.getenv("REGIME_TIMEFRAME_MINUTES", "60")),
        regime_ema_period=int(os.getenv("REGIME_EMA_PERIOD", "96")),
        regime_adx_period=int(os.getenv("REGIME_ADX_PERIOD", "14")),
        regime_adx_min=float(os.getenv("REGIME_ADX_MIN", "18")),
        regime_slope_lookback_bars=int(os.getenv("REGIME_SLOPE_LOOKBACK_BARS", "3")),
        regime_min_slope_pct=float(os.getenv("REGIME_MIN_SLOPE_PCT", "0.002")),
        regime_atr_max_pct=float(os.getenv("REGIME_ATR_MAX_PCT", "0.02")),
        pullback_lookback_bars=int(os.getenv("PULLBACK_LOOKBACK_BARS", "6")),
        pullback_min_depth_atr=float(os.getenv("PULLBACK_MIN_DEPTH_ATR", "0.4")),
        pullback_max_depth_atr=float(os.getenv("PULLBACK_MAX_DEPTH_ATR", "1.2")),
        reaccel_min_bar_body_atr=float(os.getenv("REACCEL_MIN_BAR_BODY_ATR", "0.2")),
        spike_bar_max_range_atr=float(os.getenv("SPIKE_BAR_MAX_RANGE_ATR", "1.8")),
        min_volume_ratio=float(os.getenv("MIN_VOLUME_RATIO", os.getenv("VOLUME_MIN_MULTIPLIER", "1.1"))),
        trail_after_atr_multiple=float(os.getenv("TRAIL_AFTER_ATR_MULTIPLE", "1.5")),
        exit_on_regime_invalidation=_env_flag("EXIT_ON_REGIME_INVALIDATION", True),
    )


def required_history_bars(cfg: StrategyConfig) -> int:
    exec_need = max(
        cfg.sma_slow,
        cfg.volume_ma_period,
        cfg.atr_period * 3,
        cfg.adx_period * 3,
        cfg.trend_ema_period,
        cfg.momentum_lookback_bars + 1,
        cfg.pullback_lookback_bars + 2,
    )
    regime_ratio = max(1, math.ceil(cfg.regime_timeframe_minutes / max(1, cfg.timeframe_minutes)))
    regime_need = max(
        cfg.regime_ema_period,
        cfg.regime_adx_period * 3,
        cfg.atr_period * 3,
        cfg.regime_slope_lookback_bars + 1,
    )
    return max(exec_need, (regime_need * regime_ratio) + cfg.pullback_lookback_bars + 25)


def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().sort_index()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    return out


def _regime_resample(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    regime = df.copy()
    regime.index = regime.index + timedelta(minutes=cfg.timeframe_minutes)
    regime = regime.resample(
        f"{cfg.regime_timeframe_minutes}min",
        label="right",
        closed="right",
    ).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    regime = regime.dropna(subset=["open", "high", "low", "close"])
    if regime.empty:
        return regime

    min_regime_bars = max(
        cfg.regime_adx_period + 1,
        (cfg.regime_adx_period * 2),
        cfg.atr_period + 1,
        cfg.regime_slope_lookback_bars + 1,
    )
    if len(regime) < min_regime_bars:
        regime["regime_ema"] = regime["close"].ewm(span=cfg.regime_ema_period, adjust=False).mean()
        regime["regime_adx"] = pd.NA
        regime["regime_atr"] = pd.NA
        regime["regime_atr_pct"] = pd.NA
        regime["regime_slope_pct"] = pd.NA
        regime["regime_on"] = pd.NA
        regime["regime_bearish"] = pd.NA
        regime["regime_side"] = pd.NA
        regime["regime_bar_close_ts"] = regime.index
        return regime

    regime["regime_ema"] = regime["close"].ewm(span=cfg.regime_ema_period, adjust=False).mean()
    regime_adx = ADXIndicator(
        high=regime["high"],
        low=regime["low"],
        close=regime["close"],
        window=cfg.regime_adx_period,
    )
    regime["regime_adx"] = regime_adx.adx()

    regime_atr = AverageTrueRange(
        high=regime["high"],
        low=regime["low"],
        close=regime["close"],
        window=cfg.atr_period,
    )
    regime["regime_atr"] = regime_atr.average_true_range()
    regime["regime_atr_pct"] = regime["regime_atr"] / regime["close"]
    regime["regime_slope_pct"] = regime["regime_ema"].pct_change(cfg.regime_slope_lookback_bars)
    regime["regime_on"] = (
        (regime["close"] > regime["regime_ema"])
        & (regime["regime_slope_pct"] >= cfg.regime_min_slope_pct)
        & (regime["regime_adx"] >= cfg.regime_adx_min)
        & (regime["regime_atr_pct"] <= cfg.regime_atr_max_pct)
    )
    regime["regime_bearish"] = (
        (regime["close"] < regime["regime_ema"])
        & (regime["regime_slope_pct"] <= (-1.0 * cfg.regime_min_slope_pct))
        & (regime["regime_adx"] >= cfg.regime_adx_min)
        & (regime["regime_atr_pct"] <= cfg.regime_atr_max_pct)
    )
    regime["regime_side"] = regime["regime_on"].map(lambda value: "bullish" if value else None)
    regime.loc[regime["regime_bearish"], "regime_side"] = "bearish"
    regime["regime_bar_close_ts"] = regime.index
    return regime


def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = _normalize_index(df)

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

    ts_et = out.index.tz_convert(ET)
    session_dates = pd.Series(ts_et.date, index=out.index)
    out["session_open"] = out.groupby(session_dates)["open"].transform("first")
    cum_pv = (out["close"] * out["volume"]).groupby(session_dates).cumsum()
    cum_volume = out["volume"].groupby(session_dates).cumsum().replace(0, pd.NA)
    out["vwap"] = cum_pv / cum_volume
    out["price_distance_from_vwap_pct"] = (out["close"] - out["vwap"]).abs() / out["close"]
    out["price_distance_from_open_pct"] = (out["close"] - out["session_open"]).abs() / out["close"]
    out["price_distance_from_trend_ema_pct"] = (out["close"] - out["trend_ema"]).abs() / out["close"]

    out["bar_range_atr"] = (out["high"] - out["low"]) / out["atr"].replace(0, pd.NA)
    out["bar_body_atr"] = (out["close"] - out["open"]).abs() / out["atr"].replace(0, pd.NA)
    out["prior_close"] = out["close"].shift(1)
    out["prior_high"] = out["high"].shift(1)
    out["prior_low"] = out["low"].shift(1)
    out["bullish_bar"] = out["close"] > out["open"]
    out["bearish_bar"] = out["close"] < out["open"]

    long_anchor_high = out["high"].rolling(cfg.pullback_lookback_bars).max().shift(1)
    long_pullback_low = out["low"].rolling(cfg.pullback_lookback_bars).min()
    short_anchor_low = out["low"].rolling(cfg.pullback_lookback_bars).min().shift(1)
    short_rally_high = out["high"].rolling(cfg.pullback_lookback_bars).max()

    out["long_pullback_depth_atr"] = (long_anchor_high - long_pullback_low) / out["atr"].replace(0, pd.NA)
    out["short_pullback_depth_atr"] = (short_rally_high - short_anchor_low) / out["atr"].replace(0, pd.NA)
    out["long_reaccel_ok"] = (
        out["bullish_bar"]
        & (out["bar_body_atr"] >= cfg.reaccel_min_bar_body_atr)
        & (out["close"] > out["prior_close"])
    )
    out["short_reaccel_ok"] = (
        out["bearish_bar"]
        & (out["bar_body_atr"] >= cfg.reaccel_min_bar_body_atr)
        & (out["close"] < out["prior_close"])
    )
    out["spike_bar"] = out["bar_range_atr"] > cfg.spike_bar_max_range_atr

    regime = _regime_resample(out[["open", "high", "low", "close", "volume"]], cfg)
    if not regime.empty:
        enriched = out.copy()
        enriched["bar_ts"] = enriched.index
        enriched["bar_close_ts"] = enriched.index + timedelta(minutes=cfg.timeframe_minutes)
        regime_metrics = regime[
            [
                "regime_ema",
                "regime_adx",
                "regime_atr",
                "regime_atr_pct",
                "regime_slope_pct",
                "regime_on",
                "regime_bearish",
                "regime_side",
                "regime_bar_close_ts",
            ]
        ].copy()
        merged = pd.merge_asof(
            enriched.reset_index(drop=True).sort_values("bar_close_ts"),
            regime_metrics.reset_index(drop=True).sort_values("regime_bar_close_ts"),
            left_on="bar_close_ts",
            right_on="regime_bar_close_ts",
            direction="backward",
        )
        merged = merged.set_index("bar_ts")
        merged.index.name = out.index.name
        out = merged
    else:
        out["bar_close_ts"] = out.index + timedelta(minutes=cfg.timeframe_minutes)
        out["regime_ema"] = pd.NA
        out["regime_adx"] = pd.NA
        out["regime_atr"] = pd.NA
        out["regime_atr_pct"] = pd.NA
        out["regime_slope_pct"] = pd.NA
        out["regime_on"] = pd.NA
        out["regime_bearish"] = pd.NA
        out["regime_side"] = pd.NA
        out["regime_bar_close_ts"] = pd.NA

    return out.sort_index()


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


def _safe_float(value: object) -> float | None:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    return bool(value)


def _pullback_bucket(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 0.4:
        return "<0.4 ATR"
    if value < 0.8:
        return "0.4-0.8 ATR"
    if value < 1.2:
        return "0.8-1.2 ATR"
    return "1.2+ ATR"


def build_signal_metrics(last: pd.Series, last_ts, cfg: StrategyConfig) -> dict:
    normalized_last_ts = _normalize_ts(last_ts)
    bar_close_ts = _bar_close_ts(normalized_last_ts, cfg.timeframe_minutes)
    bar_close_ts_et = bar_close_ts.tz_convert(ET) if bar_close_ts is not None else None
    long_pullback_depth = _safe_float(last.get("long_pullback_depth_atr"))
    short_pullback_depth = _safe_float(last.get("short_pullback_depth_atr"))
    return {
        "price": _safe_float(last.get("close")),
        "sma_fast": _safe_float(last.get("sma_fast")),
        "sma_slow": _safe_float(last.get("sma_slow")),
        "adx": _safe_float(last.get("adx")),
        "atr": _safe_float(last.get("atr")),
        "atr_pct": _safe_float(last.get("atr_pct")),
        "volume": _safe_float(last.get("volume")),
        "volume_ma": _safe_float(last.get("volume_ma")),
        "volume_ratio": _safe_float(last.get("volume_ratio")),
        "sma_spread": _safe_float(last.get("sma_spread")),
        "sma_spread_pct": _safe_float(last.get("sma_spread_pct")),
        "sma_spread_atr": _safe_float(last.get("sma_spread_atr")),
        "trend_ema": _safe_float(last.get("trend_ema")),
        "price_distance_from_trend_ema_pct": _safe_float(last.get("price_distance_from_trend_ema_pct")),
        "momentum_pct": _safe_float(last.get("momentum_pct")),
        "adx_delta": _safe_float(last.get("adx_delta")),
        "vwap": _safe_float(last.get("vwap")),
        "session_open": _safe_float(last.get("session_open")),
        "price_distance_from_vwap_pct": _safe_float(last.get("price_distance_from_vwap_pct")),
        "price_distance_from_open_pct": _safe_float(last.get("price_distance_from_open_pct")),
        "bar_range_atr": _safe_float(last.get("bar_range_atr")),
        "bar_body_atr": _safe_float(last.get("bar_body_atr")),
        "long_pullback_depth_atr": long_pullback_depth,
        "short_pullback_depth_atr": short_pullback_depth,
        "pullback_depth_bucket_long": _pullback_bucket(long_pullback_depth),
        "pullback_depth_bucket_short": _pullback_bucket(short_pullback_depth),
        "long_reaccel_ok": _safe_bool(last.get("long_reaccel_ok")),
        "short_reaccel_ok": _safe_bool(last.get("short_reaccel_ok")),
        "spike_bar": _safe_bool(last.get("spike_bar")),
        "regime_ema": _safe_float(last.get("regime_ema")),
        "regime_adx": _safe_float(last.get("regime_adx")),
        "regime_atr_pct": _safe_float(last.get("regime_atr_pct")),
        "regime_slope_pct": _safe_float(last.get("regime_slope_pct")),
        "regime_on": _safe_bool(last.get("regime_on")),
        "regime_bearish": _safe_bool(last.get("regime_bearish")),
        "regime_side": last.get("regime_side"),
        "regime_bar_close_ts": str(last.get("regime_bar_close_ts")) if not pd.isna(last.get("regime_bar_close_ts")) else None,
        "bar_ts": str(normalized_last_ts) if normalized_last_ts is not None else None,
        "bar_close_ts": str(bar_close_ts) if bar_close_ts is not None else None,
        "bar_close_ts_et": str(bar_close_ts_et) if bar_close_ts_et is not None else None,
        "long_time_window": classify_time_window_et(normalized_last_ts, cfg.timeframe_minutes, cfg.entry_windows_for("long")),
        "short_time_window": classify_time_window_et(normalized_last_ts, cfg.timeframe_minutes, cfg.entry_windows_for("short")),
        "short_entries_allowed": cfg.allow_shorts,
    }


def _side_specific_checks(side: str, metrics: dict, cfg: StrategyConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    adx_ok = metrics["adx"] is not None and metrics["adx"] >= cfg.adx_threshold_for(side)
    atr_ok = metrics["atr_pct"] is not None and metrics["atr_pct"] <= cfg.atr_max_pct_for(side)
    volume_ok = metrics["volume_ratio"] is not None and metrics["volume_ratio"] >= cfg.volume_ratio_threshold_for(side)

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
    if side == "long":
        price_trend_ok = (
            metrics["trend_ema"] is not None
            and metrics["price"] is not None
            and metrics["sma_fast"] is not None
            and metrics["sma_slow"] is not None
            and metrics["price"] > metrics["trend_ema"]
            and metrics["sma_fast"] > metrics["sma_slow"]
            and metrics["sma_fast"] >= metrics["trend_ema"]
        )
        regime_ok = metrics["regime_on"] is True
        pullback_depth = metrics.get("long_pullback_depth_atr")
        pullback_ok = (
            pullback_depth is not None
            and pullback_depth >= cfg.pullback_min_depth_atr
            and pullback_depth <= cfg.pullback_max_depth_atr
        )
        reaccel_ok = metrics.get("long_reaccel_ok") is True
    else:
        price_trend_ok = (
            metrics["trend_ema"] is not None
            and metrics["price"] is not None
            and metrics["sma_fast"] is not None
            and metrics["sma_slow"] is not None
            and metrics["price"] < metrics["trend_ema"]
            and metrics["sma_fast"] < metrics["sma_slow"]
            and metrics["sma_fast"] <= metrics["trend_ema"]
        )
        regime_ok = metrics["regime_bearish"] is True
        pullback_depth = metrics.get("short_pullback_depth_atr")
        pullback_ok = (
            pullback_depth is not None
            and pullback_depth >= cfg.pullback_min_depth_atr
            and pullback_depth <= cfg.pullback_max_depth_atr
        )
        reaccel_ok = metrics.get("short_reaccel_ok") is True

    trend_ema_ok = price_trend_ok
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
    spike_ok = metrics.get("spike_bar") is not True

    if not regime_ok:
        reasons.append("regime_filter_failed")
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
    if not pullback_ok:
        reasons.append("pullback_depth_out_of_range")
    if not reaccel_ok:
        reasons.append("reaccel_not_confirmed")
    if not momentum_ok:
        reasons.append("momentum_filter_failed")
    if not adx_delta_ok:
        reasons.append("adx_not_accelerating")
    if not spike_ok:
        reasons.append("spike_bar_blocked")

    return all(
        [
            regime_ok,
            adx_ok,
            atr_ok,
            volume_ok,
            time_window_ok,
            spread_atr_ok,
            spread_pct_ok,
            vwap_ok,
            open_ok,
            trend_ema_ok,
            pullback_ok,
            reaccel_ok,
            momentum_ok,
            adx_delta_ok,
            spike_ok,
        ]
    ), reasons


def evaluate_signal_from_metrics(metrics: dict, cfg: StrategyConfig) -> tuple[str, list[str]]:
    required = [
        "price",
        "sma_fast",
        "sma_slow",
        "adx",
        "atr",
        "atr_pct",
        "volume",
        "volume_ma",
        "regime_ema",
        "regime_adx",
        "regime_atr_pct",
        "regime_slope_pct",
    ]
    if any(metrics.get(key) is None for key in required):
        return "HOLD", ["indicators_not_ready"]

    trend_up = metrics["sma_fast"] > metrics["sma_slow"]
    trend_down = metrics["sma_fast"] < metrics["sma_slow"]
    long_ok, long_reasons = _side_specific_checks("long", metrics, cfg)
    short_ok, short_reasons = _side_specific_checks("short", metrics, cfg)

    strength_terms = [
        metrics.get("adx") or 0.0,
        (metrics.get("regime_adx") or 0.0) * 0.5,
        (metrics.get("sma_spread_atr") or 0.0) * 8.0,
        (metrics.get("volume_ratio") or 0.0) * 5.0,
        abs(metrics.get("momentum_pct") or 0.0) * 1000.0,
        (metrics.get("bar_body_atr") or 0.0) * 8.0,
    ]
    metrics["signal_strength"] = round(sum(strength_terms), 6)
    metrics["time_window_ok"] = False
    metrics["pullback_depth_atr"] = None
    metrics["pullback_depth_bucket"] = None

    if trend_up and long_ok:
        metrics["time_window_ok"] = True
        metrics["entry_window_bucket"] = metrics.get("long_time_window")
        metrics["pullback_depth_atr"] = metrics.get("long_pullback_depth_atr")
        metrics["pullback_depth_bucket"] = metrics.get("pullback_depth_bucket_long")
        return "LONG", ["long_entry_filters_passed"]

    if trend_down and short_ok:
        metrics["time_window_ok"] = True
        metrics["entry_window_bucket"] = metrics.get("short_time_window")
        metrics["pullback_depth_atr"] = metrics.get("short_pullback_depth_atr")
        metrics["pullback_depth_bucket"] = metrics.get("pullback_depth_bucket_short")
        return "SHORT", ["short_entry_filters_passed"]

    reasons: list[str] = []
    if trend_up:
        reasons.extend(long_reasons)
        reasons.append("trend_up_no_entry")
        metrics["entry_window_bucket"] = metrics.get("long_time_window")
        metrics["time_window_ok"] = metrics.get("long_time_window") is not None
        metrics["pullback_depth_atr"] = metrics.get("long_pullback_depth_atr")
        metrics["pullback_depth_bucket"] = metrics.get("pullback_depth_bucket_long")
    elif trend_down:
        reasons.extend(short_reasons)
        reasons.append("trend_down_no_entry")
        metrics["entry_window_bucket"] = metrics.get("short_time_window")
        metrics["time_window_ok"] = metrics.get("short_time_window") is not None
        metrics["pullback_depth_atr"] = metrics.get("short_pullback_depth_atr")
        metrics["pullback_depth_bucket"] = metrics.get("pullback_depth_bucket_short")
    else:
        reasons.extend(sorted(set(long_reasons + short_reasons)))
        reasons.append("trend_neutral")
        metrics["entry_window_bucket"] = None

    return "HOLD", list(dict.fromkeys(reasons))


def generate_signal(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[str, dict, list[str]]:
    if df.empty:
        return "HOLD", {}, ["no_data"]

    last = df.iloc[-1]
    last_ts = df.index[-1] if len(df.index) > 0 else None
    metrics = build_signal_metrics(last, last_ts, cfg)
    signal, reasons = evaluate_signal_from_metrics(metrics, cfg)
    return signal, metrics, reasons
