from __future__ import annotations

import sys

from bot.profile import load_profile


def _usage() -> int:
    print("Usage: python -m bot.profile_runner <paper|live> <trade|monitor|research|validate>")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        return _usage()

    profile, action = args
    load_profile(profile)

    if action == "trade":
        from bot.main import main as trade_main

        trade_main()
        return 0

    if action == "monitor":
        from bot.report_monitor import main as monitor_main

        monitor_main()
        return 0

    if action == "research":
        from bot.research import main as research_main

        research_main()
        return 0

    if action == "validate":
        from bot.validate_runtime import main as validate_main

        return int(validate_main())

    return _usage()


if __name__ == "__main__":
    raise SystemExit(main())
