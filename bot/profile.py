from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from bot.paths import APP_ROOT


DEFAULT_MARKET = "spy"
SUPPORTED_PROFILES = {"paper", "live"}
SUPPORTED_MARKETS = {"spy", "btc"}


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


def _load_profile_env(profile: str, market: str) -> None:
    profile_env = APP_ROOT / "config" / f"{profile}_{market}.env"
    if profile_env.exists():
        # Match docker-compose env_file precedence: profile-specific values
        # should override the base .env for that profile's runtime.
        load_dotenv(profile_env, override=True)


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
    os.environ.setdefault("BOT_DATA_DIR", str(runtime_root / "data"))
    os.environ.setdefault("BOT_LOGS_DIR", str(runtime_root / "logs"))
    os.environ.setdefault("BOT_REPORTS_DIR", str(runtime_root / "reports"))


def _set_market_defaults(market: str) -> None:
    os.environ["BOT_MARKET"] = market

    if market == "spy":
        os.environ.setdefault("SYMBOL", "SPY")
        os.environ.setdefault("IS_CRYPTO", "false")
        os.environ.setdefault("ALLOW_OVERNIGHT_HOLDING", "false")
        os.environ.setdefault("FLATTEN_BEFORE_CLOSE_MINUTES", "5")
        return

    if market == "btc":
        os.environ.setdefault("SYMBOL", "BTC/USD")
        os.environ.setdefault("IS_CRYPTO", "true")
        os.environ.setdefault("ALLOW_OVERNIGHT_HOLDING", "true")
        os.environ.setdefault("FLATTEN_BEFORE_CLOSE_MINUTES", "0")
        return

    raise ValueError(f"Unsupported bot market: {market}")


def _set_profile_defaults(profile: str, market: str) -> None:
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


def load_profile(profile: str, market: str | None = None) -> None:
    normalized_profile, normalized_market = _normalize_profile_market(profile, market)
    _load_base_env()
    _load_profile_env(normalized_profile, normalized_market)
    _bind_profile_credentials(normalized_profile)
    _set_runtime_dirs(normalized_profile, normalized_market)
    _set_profile_defaults(normalized_profile, normalized_market)


def profile_runtime_root(profile: str, market: str | None = None) -> Path:
    normalized_profile, normalized_market = _normalize_profile_market(profile, market)
    return _runtime_root(normalized_profile, normalized_market)
