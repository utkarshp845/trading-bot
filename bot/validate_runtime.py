from __future__ import annotations

from datetime import datetime, timedelta, timezone

import os
import sqlite3
import sys

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.metrics import closed_trade_summary
from bot.paths import DATA_DIR, LOGS_DIR, REPORTS_DIR, ensure_runtime_dirs
from bot.report_daily import main as report_daily_main
from bot.report_monitor import main as report_monitor_main
from bot.risk import RiskConfig, evaluate_entry_risk
from bot.store import connect, init_db, record_event, record_run
from bot.strategy_ma import StrategyConfig, compute_indicators, generate_signal


ET = ZoneInfo("America/New_York")


def check(condition: bool, ok: str, fail: str, failures: list[str]) -> None:
    if condition:
        print(f"[ok] {ok}")
    else:
        print(f"[fail] {fail}")
        failures.append(fail)


def build_synthetic_bars() -> pd.DataFrame:
    start = datetime.now(timezone.utc) - timedelta(minutes=5 * 260)
    index = pd.date_range(start=start, periods=260, freq="5min", tz="UTC")
    closes = [100 + (i * 0.08) for i in range(260)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [x + 0.12 for x in closes],
            "low": [x - 0.12 for x in closes],
            "close": closes,
            "volume": [10000 + (i % 20) * 25 for i in range(260)],
        },
        index=index,
    )


def main() -> int:
    load_dotenv()
    ensure_runtime_dirs()
    failures: list[str] = []

    check(DATA_DIR.exists(), "Data directory exists.", "Data directory missing.", failures)
    check(LOGS_DIR.exists(), "Logs directory exists.", "Logs directory missing.", failures)
    check(REPORTS_DIR.exists(), "Reports directory exists.", "Reports directory missing.", failures)

    conn = connect()
    init_db(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    }
    for required in {"state", "runs", "orders", "position_state", "closed_trades", "events"}:
        check(required in tables, f"Table {required} is available.", f"Missing required table {required}.", failures)

    bars = build_synthetic_bars()
    cfg = StrategyConfig(
        sma_fast=20,
        sma_slow=50,
        adx_period=14,
        adx_threshold=10,
        atr_period=14,
        atr_max_pct=0.01,
        volume_ma_period=20,
        volume_min_multiplier=0.8,
        timeframe_minutes=5,
        trail_atr_multiplier=1.5,
        max_bars_in_trade=12,
    )
    bars2 = compute_indicators(bars, cfg)
    signal, metrics, reasons = generate_signal(bars2, cfg)
    check(not bars2.empty, "Indicator pipeline produced output.", "Indicator pipeline returned empty output.", failures)
    check(signal in {"LONG", "SHORT", "HOLD"}, "Signal generation returned a valid action.", "Signal generation returned an invalid action.", failures)
    check("price" in metrics, "Signal metrics payload exists.", "Signal metrics payload missing required fields.", failures)

    risk = evaluate_entry_risk(
        RiskConfig(
            max_trades_per_day=5,
            max_daily_drawdown_pct=0.01,
            max_daily_loss=500.0,
            max_consecutive_losses=3,
            max_bar_age_seconds=15 * 60,
            max_position_notional_pct=0.10,
        ),
        trades_today=0,
        consecutive_losses=0,
        daily_start_equity=100000,
        current_equity=100000,
        last_bar_ts=metrics.get("bar_ts"),
        position_notional=1000,
    )
    check(risk.allow_entries, "Risk evaluation allows a healthy sample entry.", "Risk evaluation unexpectedly blocked a healthy sample entry.", failures)

    ts = datetime.now(timezone.utc).isoformat()
    record_run(
        conn,
        ts,
        os.getenv("SYMBOL", "SPY"),
        metrics.get("price"),
        metrics.get("sma_fast"),
        metrics.get("sma_slow"),
        signal,
        "HOLD",
        0.0,
        100000.0,
        100000.0,
        "runtime_validation_sample",
        reasons=";".join(reasons),
        metrics_json="{}",
        bar_ts=metrics.get("bar_ts"),
        strategy_version=os.getenv("STRATEGY_VERSION", "validate"),
    )
    record_event(
        conn,
        ts,
        "INFO",
        "runtime_validation",
        os.getenv("SYMBOL", "SPY"),
        "Validation sample run recorded.",
        {"signal": signal},
    )
    conn.close()

    report_daily_main()
    report_monitor_main()

    check((REPORTS_DIR / "monitor_latest.md").exists(), "Monitor report generated.", "Monitor report was not generated.", failures)
    today_name = f"daily_{datetime.now(ET).date().isoformat()}.md"
    check((REPORTS_DIR / today_name).exists(), "Daily report generated.", "Daily report was not generated.", failures)

    summary = closed_trade_summary(pd.DataFrame())
    check(summary["trade_count"] == 0, "Closed-trade summary handles empty inputs.", "Closed-trade summary failed on empty input.", failures)

    if os.getenv("VALIDATE_BROKER", "0").strip() in {"1", "true", "TRUE"}:
        from bot.broker_alpaca import get_account_snapshot, make_clients

        key = os.getenv("ALPACA_API_KEY", "")
        secret = os.getenv("ALPACA_SECRET_KEY", "")
        check(bool(key and secret and not key.startswith("YOUR_")), "Broker credentials provided for optional validation.", "Optional broker validation requested but credentials are placeholders.", failures)
        if key and secret and not key.startswith("YOUR_"):
            try:
                trading, _ = make_clients()
                equity, cash = get_account_snapshot(trading)
                check(equity is not None and cash is not None, "Broker account snapshot retrieved.", "Broker account snapshot failed.", failures)
            except Exception as exc:
                failures.append(f"Optional broker validation failed: {exc}")
                print(f"[fail] Optional broker validation failed: {exc}")

    if failures:
        print("")
        print("Runtime validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("")
    print("Runtime validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
