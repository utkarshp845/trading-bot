from __future__ import annotations

import os
import sys

from dotenv import dotenv_values

from bot import profile as profile_module


PROFILE_CONTRACT_KEYS = (
    "SYMBOL",
    "IS_CRYPTO",
    "POSITION_SIZING_MODE",
    "ENABLE_STALE_BAR_CHECK",
    "MAX_DAILY_LOSS",
    "MAX_TRADES_PER_DAY",
)


def validate_profile_env(profile: str, market: str) -> dict[str, str | None]:
    config_path = profile_module.APP_ROOT / "config" / f"{profile}_{market}.env"
    if not config_path.exists():
        raise FileNotFoundError(f"Profile config not found: {config_path}")

    expected = dotenv_values(config_path)
    profile_module.load_profile(profile, market)

    failures: list[str] = []
    resolved: dict[str, str | None] = {}
    for key in PROFILE_CONTRACT_KEYS:
        expected_value = expected.get(key)
        actual_value = os.getenv(key)
        resolved[key] = actual_value
        if expected_value is None:
            failures.append(f"{key}: missing from {config_path.name}")
        elif actual_value != expected_value:
            failures.append(f"{key}: expected {expected_value!r}, got {actual_value!r}")

    if failures:
        raise AssertionError("; ".join(failures))

    return resolved


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    profile = args[0] if len(args) >= 1 else os.getenv("DEPLOY_PROFILE", "live")
    market = args[1] if len(args) >= 2 else os.getenv("DEPLOY_MARKET", "btc")

    resolved = validate_profile_env(profile, market)
    print(
        "remote profile ok:",
        os.getenv("BOT_PROFILE"),
        os.getenv("BOT_MARKET"),
        resolved["SYMBOL"],
        resolved["POSITION_SIZING_MODE"],
        resolved["ENABLE_STALE_BAR_CHECK"],
        resolved["MAX_DAILY_LOSS"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
