from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from bot import paths as paths_module
from bot.paths import APP_ROOT


DEFAULT_MARKET = "spy"
SUPPORTED_PROFILES = {"paper", "live"}
SUPPORTED_MARKETS = {"spy", "btc"}

LIVE_BTC_SAFETY_ENV = {
    "ALLOW_SHORTS": "false",
    "POSITION_SIZING_MODE": "atr_risk",
    "ATR_RISK_PER_TRADE_PCT": "0.0075",
    "MAX_POSITION_NOTIONAL_PCT": "0.35",
    "TARGET_POSITION_NOTIONAL_PCT": "0.30",
    "MIN_ORDER_NOTIONAL": "1.0",
    "HARD_STOP_ATR_MULT": "2.0",
    "ENABLE_STALE_BAR_CHECK": "true",
    "MAX_BAR_AGE_SECONDS": "600",
    "MAX_DAILY_DRAWDOWN_PCT": "0.025",
    "MAX_DAILY_LOSS": "3",
    "MAX_CONSECUTIVE_LOSSES": "2",
    "MAX_TRADES_PER_DAY": "3",
    "MAX_CONSECUTIVE_ENTRY_FAILURES_PER_DAY": "1",
}


def _load_base_env() -> None:
    default_env = APP_ROOT / ".env"
    if default_env.exists():
        load_dotenv(default_env)


def _normalize_profile_market(profile: str, market: str | None = None) -> tuple[str, str]:
    raw_profile = profile.strip().lower().replace("_", "-")
    raw_market = (market or os.getenv("BOT_MARKET", DEFAULT_MARKET)).strip().lower().replace("_", "-")

    if "-" in raw_profile:
        profile_part, market_part = raw_profile.split("-", 1)
        raw_profile = profile_part
        raw_market = market_part

    if raw_profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported bot profile: {profile}")
    if raw_market not in SUPPORTED_MARKETS:
        raise ValueError(f"Unsupported bot market: {market or raw_market}")

    return raw_profile, raw_market


def _load_profile_env(profile: str, market: str) -> set[str]:
    profile_env = APP_ROOT / "config" / f"{profile}_{market}.env"
    if profile_env.exists():
        # Match docker-compose env_file precedence: profile-specific values
        # should override the base .env for that profile's runtime.
        load_dotenv(profile_env, override=True)
        return {key for key in dotenv_values(profile_env).keys() if key is not None}
    return set()


def _bind_profile_credentials(profile: str) -> None:
    profile_key = os.getenv(f"ALPACA_{profile.upper()}_API_KEY")
    profile_secret = os.getenv(f"ALPACA_{profile.upper()}_SECRET_KEY")

    if profile_key:
        os.environ["ALPACA_API_KEY"] = profile_key
    if profile_secret:
        os.environ["ALPACA_SECRET_KEY"] = profile_secret

def _runtime_root(profile: str, market: str) -> Path:
    if market == DEFAULT_MARKET:
        return APP_ROOT / "runtime" / profile
    return APP_ROOT / "runtime" / f"{profile}_{market}"


def _set_runtime_dirs(profile: str, market: str) -> None:
    runtime_root = _runtime_root(profile, market)
    os.environ["BOT_DATA_DIR"] = str(runtime_root / "data")
    os.environ["BOT_LOGS_DIR"] = str(runtime_root / "logs")
    os.environ["BOT_REPORTS_DIR"] = str(runtime_root / "reports")
    paths_module.refresh_runtime_dirs()


def _set_market_defaults(market: str) -> None:
    os.environ["BOT_MARKET"] = market

    if market == "spy":
        os.environ["SYMBOL"] = "SPY"
        os.environ["IS_CRYPTO"] = "false"
        os.environ["ALLOW_OVERNIGHT_HOLDING"] = "false"
        os.environ["FLATTEN_BEFORE_CLOSE_MINUTES"] = "5"
        return

    if market == "btc":
        os.environ["SYMBOL"] = "BTC/USD"
        os.environ["IS_CRYPTO"] = "true"
        os.environ["ALLOW_OVERNIGHT_HOLDING"] = "true"
        os.environ["FLATTEN_BEFORE_CLOSE_MINUTES"] = "0"
        return

    raise ValueError(f"Unsupported bot market: {market}")


def _set_profile_defaults(profile: str, market: str, profile_env_keys: set[str] | None = None) -> None:
    profile_env_keys = profile_env_keys or set()
    os.environ["BOT_PROFILE"] = profile
    _set_market_defaults(market)

    if profile == "paper":
        os.environ["ALPACA_PAPER"] = "true"
        os.environ.setdefault("RESEARCH_STARTING_EQUITY", "250")
        os.environ.setdefault("RESEARCH_OUTPUT_STEM", f"research_paper_{market}")
        os.environ.setdefault("STRATEGY_VERSION", f"v2-paper-{market}")
    elif profile == "live":
        os.environ["ALPACA_PAPER"] = "false"
        os.environ.setdefault("RESEARCH_OUTPUT_STEM", f"research_live_{market}")
        os.environ.setdefault("STRATEGY_VERSION", f"v2-live-{market}")
    else:
        raise ValueError(f"Unsupported bot profile: {profile}")

    if profile == "live" and market == "btc":
        for key, value in LIVE_BTC_SAFETY_ENV.items():
            if key not in profile_env_keys:
                os.environ[key] = value


def load_profile(profile: str, market: str | None = None) -> None:
    normalized_profile, normalized_market = _normalize_profile_market(profile, market)
    _load_base_env()
    profile_env_keys = _load_profile_env(normalized_profile, normalized_market)
    _bind_profile_credentials(normalized_profile)
    _set_runtime_dirs(normalized_profile, normalized_market)
    _set_profile_defaults(normalized_profile, normalized_market, profile_env_keys)


def profile_runtime_root(profile: str, market: str | None = None) -> Path:
    normalized_profile, normalized_market = _normalize_profile_market(profile, market)
    return _runtime_root(normalized_profile, normalized_market)
