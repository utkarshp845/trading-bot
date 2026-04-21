from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bot.paths import DATA_DIR, ensure_runtime_dirs
from bot.risk import trading_day_et


DB_PATH = DATA_DIR / "bot.db"


@dataclass
class BotState:
    last_trade_ts: Optional[str]
    trades_today: int
    trades_today_date: str
    consecutive_losses: int
    daily_start_equity: Optional[float]
    daily_start_equity_date: Optional[str]
    last_entry_signal_strength: Optional[float]
    last_entry_signal_side: Optional[str]
    entry_failures_today: int
    entry_failures_day_utc: str


@dataclass
class PositionState:
    symbol: str
    side: Optional[str]
    entry_price: Optional[float]
    entry_ts: Optional[str]
    highest_price: Optional[float]
    lowest_price: Optional[float]
    entry_bar_ts: Optional[str]
    entry_signal_side: Optional[str]
    entry_adx: Optional[float]
    entry_atr_pct: Optional[float]
    entry_volume_ratio: Optional[float]
    entry_sma_spread_pct: Optional[float]
    entry_window_bucket: Optional[str]
    entry_signal_strength: Optional[float]
    realized_slippage_estimate: Optional[float]


@dataclass
class OrderRecord:
    id: int
    ts: str
    symbol: str
    side: str
    qty: float
    order_id: str
    status: Optional[str]
    filled_avg_price: Optional[float]
    filled_qty: Optional[float]
    intent: Optional[str]
    action_type: Optional[str]
    notes: Optional[str]
    submitted_position_qty: Optional[float]
    processed_at: Optional[str]
    filled_at: Optional[str]
    decision_signal: Optional[str]
    entry_metrics_json: Optional[str]


@dataclass
class EventRecord:
    ts: str
    level: str
    event_type: str
    symbol: Optional[str]
    message: Optional[str]
    payload_json: Optional[str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_day() -> str:
    return _utc_now().date().isoformat()


def connect() -> sqlite3.Connection:
    ensure_runtime_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_trade_ts TEXT,
            trades_today INTEGER NOT NULL,
            trades_today_date TEXT NOT NULL,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            daily_start_equity REAL,
            daily_start_equity_date TEXT,
            last_entry_signal_strength REAL,
            last_entry_signal_side TEXT,
            entry_failures_today INTEGER NOT NULL DEFAULT 0,
            entry_failures_day_utc TEXT NOT NULL DEFAULT ''
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            sma_fast REAL,
            sma_slow REAL,
            signal TEXT NOT NULL,
            desired_action TEXT,
            position_qty REAL,
            equity REAL,
            cash REAL,
            note TEXT,
            reasons TEXT,
            metrics_json TEXT,
            bar_ts TEXT,
            strategy_version TEXT
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            order_id TEXT NOT NULL,
            status TEXT,
            filled_avg_price REAL,
            filled_qty REAL,
            intent TEXT,
            action_type TEXT,
            notes TEXT,
            submitted_position_qty REAL,
            processed_at TEXT,
            filled_at TEXT,
            decision_signal TEXT,
            entry_metrics_json TEXT
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS position_state (
            symbol TEXT PRIMARY KEY,
            side TEXT,
            entry_price REAL,
            entry_ts TEXT,
            highest_price REAL,
            lowest_price REAL,
            entry_bar_ts TEXT,
            entry_signal_side TEXT,
            entry_adx REAL,
            entry_atr_pct REAL,
            entry_volume_ratio REAL,
            entry_sma_spread_pct REAL,
            entry_window_bucket TEXT,
            entry_signal_strength REAL,
            realized_slippage_estimate REAL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_ts TEXT,
            exit_ts TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            qty REAL NOT NULL,
            pnl REAL NOT NULL,
            return_pct REAL,
            entry_reason TEXT,
            exit_reason TEXT,
            bars_held INTEGER,
            entry_bar_ts TEXT,
            exit_bar_ts TEXT,
            entry_signal_side TEXT,
            entry_adx REAL,
            entry_atr_pct REAL,
            entry_volume_ratio REAL,
            entry_sma_spread_pct REAL,
            entry_window_bucket TEXT,
            hold_seconds REAL,
            realized_slippage_estimate REAL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            symbol TEXT,
            message TEXT,
            payload_json TEXT
        );
        """
    )

    position_cols = {row[1] for row in conn.execute("PRAGMA table_info(position_state);")}
    for name, sql_type in (
        ("side", "TEXT"),
        ("lowest_price", "REAL"),
        ("entry_bar_ts", "TEXT"),
        ("entry_signal_side", "TEXT"),
        ("entry_adx", "REAL"),
        ("entry_atr_pct", "REAL"),
        ("entry_volume_ratio", "REAL"),
        ("entry_sma_spread_pct", "REAL"),
        ("entry_window_bucket", "TEXT"),
        ("entry_signal_strength", "REAL"),
        ("realized_slippage_estimate", "REAL"),
    ):
        if name not in position_cols:
            conn.execute(f"ALTER TABLE position_state ADD COLUMN {name} {sql_type};")

    state_cols = {row[1] for row in conn.execute("PRAGMA table_info(state);")}
    for name, sql_type in (
        ("consecutive_losses", "INTEGER NOT NULL DEFAULT 0"),
        ("daily_start_equity", "REAL"),
        ("daily_start_equity_date", "TEXT"),
        ("last_entry_signal_strength", "REAL"),
        ("last_entry_signal_side", "TEXT"),
        ("entry_failures_today", "INTEGER NOT NULL DEFAULT 0"),
        ("entry_failures_day_utc", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in state_cols:
            conn.execute(f"ALTER TABLE state ADD COLUMN {name} {sql_type};")

    order_cols = {row[1] for row in conn.execute("PRAGMA table_info(orders);")}
    for name, sql_type in (
        ("intent", "TEXT"),
        ("notes", "TEXT"),
        ("action_type", "TEXT"),
        ("submitted_position_qty", "REAL"),
        ("processed_at", "TEXT"),
        ("filled_at", "TEXT"),
        ("decision_signal", "TEXT"),
        ("entry_metrics_json", "TEXT"),
    ):
        if name not in order_cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {sql_type};")

    run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs);")}
    for name, sql_type in (
        ("desired_action", "TEXT"),
        ("reasons", "TEXT"),
        ("metrics_json", "TEXT"),
        ("bar_ts", "TEXT"),
        ("strategy_version", "TEXT"),
    ):
        if name not in run_cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {sql_type};")

    closed_cols = {row[1] for row in conn.execute("PRAGMA table_info(closed_trades);")}
    for name, sql_type in (
        ("entry_bar_ts", "TEXT"),
        ("exit_bar_ts", "TEXT"),
        ("entry_signal_side", "TEXT"),
        ("entry_adx", "REAL"),
        ("entry_atr_pct", "REAL"),
        ("entry_volume_ratio", "REAL"),
        ("entry_sma_spread_pct", "REAL"),
        ("entry_window_bucket", "TEXT"),
        ("hold_seconds", "REAL"),
        ("realized_slippage_estimate", "REAL"),
    ):
        if name not in closed_cols:
            conn.execute(f"ALTER TABLE closed_trades ADD COLUMN {name} {sql_type};")

    conn.commit()


def get_state(conn: sqlite3.Connection, current_equity: float | None = None) -> BotState:
    today = trading_day_et()
    today_utc = _utc_day()
    row = conn.execute(
        """
        SELECT last_trade_ts, trades_today, trades_today_date, consecutive_losses, daily_start_equity, daily_start_equity_date,
               last_entry_signal_strength, last_entry_signal_side, entry_failures_today, entry_failures_day_utc
        FROM state
        WHERE id=1;
        """
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO state (
                id, last_trade_ts, trades_today, trades_today_date, consecutive_losses, daily_start_equity, daily_start_equity_date,
                last_entry_signal_strength, last_entry_signal_side, entry_failures_today, entry_failures_day_utc
            )
            VALUES (1, NULL, 0, ?, 0, ?, ?, NULL, NULL, 0, ?);
            """,
            (today, current_equity, today if current_equity is not None else None, today_utc),
        )
        conn.commit()
        return BotState(None, 0, today, 0, current_equity, today if current_equity is not None else None, None, None, 0, today_utc)

    last_trade_ts = row["last_trade_ts"]
    trades_today = row["trades_today"]
    trades_today_date = row["trades_today_date"]
    consecutive_losses = row["consecutive_losses"]
    daily_start_equity = row["daily_start_equity"]
    daily_start_equity_date = row["daily_start_equity_date"]
    last_entry_signal_strength = row["last_entry_signal_strength"]
    last_entry_signal_side = row["last_entry_signal_side"]
    entry_failures_today = row["entry_failures_today"] if row["entry_failures_today"] is not None else 0
    entry_failures_day_utc = row["entry_failures_day_utc"] or today_utc

    if trades_today_date != today:
        trades_today = 0
        trades_today_date = today
        consecutive_losses = 0
        daily_start_equity = current_equity
        daily_start_equity_date = today
        conn.execute(
            """
            UPDATE state
            SET trades_today=?, trades_today_date=?, consecutive_losses=?, daily_start_equity=?, daily_start_equity_date=?
            WHERE id=1;
            """,
            (trades_today, trades_today_date, consecutive_losses, daily_start_equity, daily_start_equity_date),
        )
        conn.commit()
    elif daily_start_equity is None and current_equity is not None:
        daily_start_equity = current_equity
        daily_start_equity_date = today
        conn.execute(
            "UPDATE state SET daily_start_equity=?, daily_start_equity_date=? WHERE id=1;",
            (daily_start_equity, daily_start_equity_date),
        )
        conn.commit()

    if entry_failures_day_utc != today_utc:
        entry_failures_today = 0
        entry_failures_day_utc = today_utc
        conn.execute(
            "UPDATE state SET entry_failures_today=?, entry_failures_day_utc=? WHERE id=1;",
            (entry_failures_today, entry_failures_day_utc),
        )
        conn.commit()

    return BotState(
        last_trade_ts=last_trade_ts,
        trades_today=trades_today,
        trades_today_date=trades_today_date,
        consecutive_losses=consecutive_losses,
        daily_start_equity=daily_start_equity,
        daily_start_equity_date=daily_start_equity_date,
        last_entry_signal_strength=last_entry_signal_strength,
        last_entry_signal_side=last_entry_signal_side,
        entry_failures_today=entry_failures_today,
        entry_failures_day_utc=entry_failures_day_utc,
    )


def set_last_trade(conn: sqlite3.Connection, ts_iso_utc: str) -> None:
    conn.execute("UPDATE state SET last_trade_ts=? WHERE id=1;", (ts_iso_utc,))
    conn.commit()


def increment_trades_today(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE state SET trades_today = trades_today + 1 WHERE id=1;")
    conn.commit()


def set_consecutive_losses(conn: sqlite3.Connection, consecutive_losses: int) -> None:
    conn.execute("UPDATE state SET consecutive_losses=? WHERE id=1;", (consecutive_losses,))
    conn.commit()


def set_last_entry_signal(conn: sqlite3.Connection, signal_strength: float | None, signal_side: str | None) -> None:
    conn.execute(
        "UPDATE state SET last_entry_signal_strength=?, last_entry_signal_side=? WHERE id=1;",
        (signal_strength, signal_side),
    )
    conn.commit()


def increment_entry_failures_today(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE state SET entry_failures_today = entry_failures_today + 1, entry_failures_day_utc=? WHERE id=1;",
        (_utc_day(),),
    )
    conn.commit()


def get_position_state(conn: sqlite3.Connection, symbol: str) -> PositionState:
    row = conn.execute(
        """
        SELECT symbol, side, entry_price, entry_ts, highest_price, lowest_price, entry_bar_ts, entry_signal_side,
               entry_adx, entry_atr_pct, entry_volume_ratio, entry_sma_spread_pct, entry_window_bucket,
               entry_signal_strength, realized_slippage_estimate
        FROM position_state
        WHERE symbol=?;
        """,
        (symbol,),
    ).fetchone()

    if row is None:
        return PositionState(symbol, None, None, None, None, None, None, None, None, None, None, None, None, None, None)

    return PositionState(
        symbol=row["symbol"],
        side=row["side"],
        entry_price=row["entry_price"],
        entry_ts=row["entry_ts"],
        highest_price=row["highest_price"],
        lowest_price=row["lowest_price"],
        entry_bar_ts=row["entry_bar_ts"],
        entry_signal_side=row["entry_signal_side"],
        entry_adx=row["entry_adx"],
        entry_atr_pct=row["entry_atr_pct"],
        entry_volume_ratio=row["entry_volume_ratio"],
        entry_sma_spread_pct=row["entry_sma_spread_pct"],
        entry_window_bucket=row["entry_window_bucket"],
        entry_signal_strength=row["entry_signal_strength"],
        realized_slippage_estimate=row["realized_slippage_estimate"],
    )


def upsert_position_state(
    conn: sqlite3.Connection,
    symbol: str,
    side: Optional[str],
    entry_price: Optional[float],
    entry_ts: Optional[str],
    highest_price: Optional[float],
    lowest_price: Optional[float],
    entry_bar_ts: Optional[str] = None,
    entry_signal_side: Optional[str] = None,
    entry_adx: Optional[float] = None,
    entry_atr_pct: Optional[float] = None,
    entry_volume_ratio: Optional[float] = None,
    entry_sma_spread_pct: Optional[float] = None,
    entry_window_bucket: Optional[str] = None,
    entry_signal_strength: Optional[float] = None,
    realized_slippage_estimate: Optional[float] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO position_state (
            symbol, side, entry_price, entry_ts, highest_price, lowest_price, entry_bar_ts, entry_signal_side,
            entry_adx, entry_atr_pct, entry_volume_ratio, entry_sma_spread_pct, entry_window_bucket, entry_signal_strength,
            realized_slippage_estimate
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            side=excluded.side,
            entry_price=excluded.entry_price,
            entry_ts=excluded.entry_ts,
            highest_price=excluded.highest_price,
            lowest_price=excluded.lowest_price,
            entry_bar_ts=COALESCE(excluded.entry_bar_ts, position_state.entry_bar_ts),
            entry_signal_side=COALESCE(excluded.entry_signal_side, position_state.entry_signal_side),
            entry_adx=COALESCE(excluded.entry_adx, position_state.entry_adx),
            entry_atr_pct=COALESCE(excluded.entry_atr_pct, position_state.entry_atr_pct),
            entry_volume_ratio=COALESCE(excluded.entry_volume_ratio, position_state.entry_volume_ratio),
            entry_sma_spread_pct=COALESCE(excluded.entry_sma_spread_pct, position_state.entry_sma_spread_pct),
            entry_window_bucket=COALESCE(excluded.entry_window_bucket, position_state.entry_window_bucket),
            entry_signal_strength=COALESCE(excluded.entry_signal_strength, position_state.entry_signal_strength),
            realized_slippage_estimate=COALESCE(excluded.realized_slippage_estimate, position_state.realized_slippage_estimate);
        """,
        (
            symbol,
            side,
            entry_price,
            entry_ts,
            highest_price,
            lowest_price,
            entry_bar_ts,
            entry_signal_side,
            entry_adx,
            entry_atr_pct,
            entry_volume_ratio,
            entry_sma_spread_pct,
            entry_window_bucket,
            entry_signal_strength,
            realized_slippage_estimate,
        ),
    )
    conn.commit()


def clear_position_state(conn: sqlite3.Connection, symbol: str) -> None:
    conn.execute("DELETE FROM position_state WHERE symbol=?;", (symbol,))
    conn.commit()


def record_run(
    conn: sqlite3.Connection,
    ts: str,
    symbol: str,
    price: float | None,
    sma_fast: float | None,
    sma_slow: float | None,
    signal: str,
    desired_action: str | None,
    position_qty: float | None,
    equity: float | None,
    cash: float | None,
    note: str | None,
    reasons: str | None = None,
    metrics_json: str | None = None,
    bar_ts: str | None = None,
    strategy_version: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            ts, symbol, price, sma_fast, sma_slow, signal, desired_action, position_qty, equity, cash, note, reasons, metrics_json, bar_ts, strategy_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            ts,
            symbol,
            price,
            sma_fast,
            sma_slow,
            signal,
            desired_action,
            position_qty,
            equity,
            cash,
            note,
            reasons,
            metrics_json,
            bar_ts,
            strategy_version,
        ),
    )
    conn.commit()


def record_order_submission(
    conn: sqlite3.Connection,
    ts: str,
    symbol: str,
    side: str,
    qty: float,
    order_id: str,
    status: str | None,
    filled_avg_price: float | None,
    filled_qty: float | None,
    intent: str | None,
    action_type: str | None,
    notes: str | None,
    submitted_position_qty: float | None,
    filled_at: str | None = None,
    decision_signal: str | None = None,
    entry_metrics_json: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO orders (
            ts, symbol, side, qty, order_id, status, filled_avg_price, filled_qty, intent, action_type, notes, submitted_position_qty, processed_at, filled_at, decision_signal, entry_metrics_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?);
        """,
        (
            ts,
            symbol,
            side,
            qty,
            order_id,
            status,
            filled_avg_price,
            filled_qty,
            intent,
            action_type,
            notes,
            submitted_position_qty,
            filled_at,
            decision_signal,
            entry_metrics_json,
        ),
    )
    conn.commit()


def update_order_status(
    conn: sqlite3.Connection,
    order_id: str,
    status: str | None,
    filled_avg_price: float | None,
    filled_qty: float | None,
    filled_at: str | None,
) -> None:
    conn.execute(
        """
        UPDATE orders
        SET status=?, filled_avg_price=?, filled_qty=?, filled_at=COALESCE(?, filled_at)
        WHERE order_id=?;
        """,
        (status, filled_avg_price, filled_qty, filled_at, order_id),
    )
    conn.commit()


def mark_order_processed(conn: sqlite3.Connection, order_id: str, processed_at: str) -> None:
    conn.execute("UPDATE orders SET processed_at=? WHERE order_id=?;", (processed_at, order_id))
    conn.commit()


def get_orders_requiring_sync(conn: sqlite3.Connection, symbol: str) -> list[OrderRecord]:
    rows = conn.execute(
        """
        SELECT id, ts, symbol, side, qty, order_id, status, filled_avg_price, filled_qty, intent, action_type, notes,
               submitted_position_qty, processed_at, filled_at, decision_signal, entry_metrics_json
        FROM orders
        WHERE symbol=?
          AND (
            status IS NULL
            OR UPPER(status) NOT IN ('FILLED', 'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED')
            OR (processed_at IS NULL AND UPPER(status)='FILLED')
          )
        ORDER BY id ASC;
        """,
        (symbol,),
    ).fetchall()

    return [
        OrderRecord(
            id=row["id"],
            ts=row["ts"],
            symbol=row["symbol"],
            side=row["side"],
            qty=row["qty"],
            order_id=row["order_id"],
            status=row["status"],
            filled_avg_price=row["filled_avg_price"],
            filled_qty=row["filled_qty"],
            intent=row["intent"],
            action_type=row["action_type"],
            notes=row["notes"],
            submitted_position_qty=row["submitted_position_qty"],
            processed_at=row["processed_at"],
            filled_at=row["filled_at"],
            decision_signal=row["decision_signal"],
            entry_metrics_json=row["entry_metrics_json"],
        )
        for row in rows
    ]


def has_pending_orders(conn: sqlite3.Connection, symbol: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM orders
        WHERE symbol=?
          AND (
            status IS NULL
            OR UPPER(status) NOT IN ('FILLED', 'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED')
          );
        """,
        (symbol,),
    ).fetchone()
    return bool(row[0])


def record_closed_trade(
    conn: sqlite3.Connection,
    symbol: str,
    side: str,
    entry_ts: str | None,
    exit_ts: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    pnl: float,
    return_pct: float | None,
    entry_reason: str | None,
    exit_reason: str | None,
    bars_held: int | None,
    entry_bar_ts: str | None = None,
    exit_bar_ts: str | None = None,
    entry_signal_side: str | None = None,
    entry_adx: float | None = None,
    entry_atr_pct: float | None = None,
    entry_volume_ratio: float | None = None,
    entry_sma_spread_pct: float | None = None,
    entry_window_bucket: str | None = None,
    hold_seconds: float | None = None,
    realized_slippage_estimate: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO closed_trades (
            symbol, side, entry_ts, exit_ts, entry_price, exit_price, qty, pnl, return_pct, entry_reason, exit_reason, bars_held,
            entry_bar_ts, exit_bar_ts, entry_signal_side, entry_adx, entry_atr_pct, entry_volume_ratio, entry_sma_spread_pct,
            entry_window_bucket, hold_seconds, realized_slippage_estimate
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            symbol,
            side,
            entry_ts,
            exit_ts,
            entry_price,
            exit_price,
            qty,
            pnl,
            return_pct,
            entry_reason,
            exit_reason,
            bars_held,
            entry_bar_ts,
            exit_bar_ts,
            entry_signal_side,
            entry_adx,
            entry_atr_pct,
            entry_volume_ratio,
            entry_sma_spread_pct,
            entry_window_bucket,
            hold_seconds,
            realized_slippage_estimate,
        ),
    )
    conn.commit()


def record_event(
    conn: sqlite3.Connection,
    ts: str,
    level: str,
    event_type: str,
    symbol: str | None = None,
    message: str | None = None,
    payload: dict | None = None,
) -> None:
    payload_json = json.dumps(payload, sort_keys=True) if payload is not None else None
    conn.execute(
        """
        INSERT INTO events (ts, level, event_type, symbol, message, payload_json)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (ts, level, event_type, symbol, message, payload_json),
    )
    conn.commit()
