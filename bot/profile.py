from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from bot.paths import APP_ROOT


def _load_base_env() -> None:
    default_env = APP_ROOT / ".env"
    if default_env.exists():
        load_dotenv(default_env)


def _load_profile_env(profile: str) -> None:
    profile_env = APP_ROOT / "config" / f"{profile}_spy.env"
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


def _set_runtime_dirs(profile: str) -> None:
    runtime_root = APP_ROOT / "runtime" / profile
    os.environ.setdefault("BOT_DATA_DIR", str(runtime_root / "data"))
    os.environ.setdefault("BOT_LOGS_DIR", str(runtime_root / "logs"))
    os.environ.setdefault("BOT_REPORTS_DIR", str(runtime_root / "reports"))


def _set_profile_defaults(profile: str) -> None:
    os.environ["BOT_PROFILE"] = profile
    os.environ.setdefault("SYMBOL", "SPY")
    os.environ.setdefault("ALLOW_OVERNIGHT_HOLDING", "false")
    os.environ.setdefault("FLATTEN_BEFORE_CLOSE_MINUTES", "5")

    if profile == "paper":
        os.environ["ALPACA_PAPER"] = "true"
        os.environ.setdefault("RESEARCH_STARTING_EQUITY", "250")
        os.environ.setdefault("RESEARCH_OUTPUT_STEM", "research_paper_spy")
        os.environ.setdefault("STRATEGY_VERSION", "v1-paper-spy")
    elif profile == "live":
        os.environ.setdefault("ALPACA_PAPER", "false")
        os.environ.setdefault("RESEARCH_OUTPUT_STEM", "research_live_spy")
        os.environ.setdefault("STRATEGY_VERSION", "v1-live-spy")
    else:
        raise ValueError(f"Unsupported bot profile: {profile}")


def load_profile(profile: str) -> None:
    normalized = profile.strip().lower()
    _load_base_env()
    _load_profile_env(normalized)
    _bind_profile_credentials(normalized)
    _set_runtime_dirs(normalized)
    _set_profile_defaults(normalized)


def profile_runtime_root(profile: str) -> Path:
    return APP_ROOT / "runtime" / profile.strip().lower()
