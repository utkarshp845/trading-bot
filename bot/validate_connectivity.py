from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from bot.broker_alpaca import get_recent_bars, make_clients


@dataclass(frozen=True)
class ConnectivityResult:
    symbol: str
    paper: bool
    equity: float
    bar_count: int
    latest_bar_ts: str


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def validate_connectivity() -> ConnectivityResult:
    """Verify broker authentication and market-data access without placing an order."""
    symbol = os.getenv("SYMBOL", "SPY").strip().upper()
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "5"))
    paper = _env_flag("ALPACA_PAPER", True)

    trading, data = make_clients()
    try:
        account = trading.get_account()
        equity = float(account.equity)
    except Exception as exc:
        raise RuntimeError(
            f"Alpaca {'paper' if paper else 'live'} trading authentication failed for {symbol}. "
            "Refresh the profile credentials before installing or trusting the schedule."
        ) from exc

    bars = get_recent_bars(data, symbol, timeframe_minutes, limit=3)
    if bars.empty:
        raise RuntimeError(
            f"Alpaca market-data connectivity failed for {symbol}; no recent bars were returned. "
            "Check the credentials, symbol, and data entitlement."
        )

    return ConnectivityResult(
        symbol=symbol,
        paper=paper,
        equity=equity,
        bar_count=len(bars),
        latest_bar_ts=str(bars.index[-1]),
    )


def main() -> int:
    try:
        result = validate_connectivity()
    except RuntimeError as exc:
        print(f"connectivity failed: {exc}", file=sys.stderr)
        return 1
    mode = "paper" if result.paper else "live"
    print(
        f"connectivity ok: mode={mode} symbol={result.symbol} equity={result.equity:.2f} "
        f"bars={result.bar_count} latest_bar={result.latest_bar_ts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
