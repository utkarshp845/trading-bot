from __future__ import annotations

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


@dataclass
class PositionState:
    symbol: str
    side: Optional[str]
    entry_price: Optional[float]
    entry_ts: Optional[str]
    highest_price: Optional[float]
    lowest_price: Optional[float]


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
    notes: Optional[str]
    submitted_position_qty: Optional[float]
    processed_at: Optional[str]
    filled_at: Optional[str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
            daily_start_equity_date TEXT
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
            position_qty REAL,
            equity REAL,
            cash REAL,
            note TEXT
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
            notes TEXT,
            submitted_position_qty REAL,
            processed_at TEXT,
            filled_at TEXT
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
            lowest_price REAL
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
            bars_held INTEGER
        );
        """
    )

    position_cols = {row[1] for row in conn.execute("PRAGMA table_info(position_state);")}
    if "side" not in position_cols:
        conn.execute("ALTER TABLE position_state ADD COLUMN side TEXT;")
    if "lowest_price" not in position_cols:
        conn.execute("ALTER TABLE position_state ADD COLUMN lowest_price REAL;")

    state_cols = {row[1] for row in conn.execute("PRAGMA table_info(state);")}
    if "consecutive_losses" not in state_cols:
        conn.execute("ALTER TABLE state ADD COLUMN consecutive_losses INTEGER NOT NULL DEFAULT 0;")
    if "daily_start_equity" not in state_cols:
        conn.execute("ALTER TABLE state ADD COLUMN daily_start_equity REAL;")
    if "daily_start_equity_date" not in state_cols:
        conn.execute("ALTER TABLE state ADD COLUMN daily_start_equity_date TEXT;")

    order_cols = {row[1] for row in conn.execute("PRAGMA table_info(orders);")}
    if "intent" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN intent TEXT;")
    if "notes" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN notes TEXT;")
    if "submitted_position_qty" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN submitted_position_qty REAL;")
    if "processed_at" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN processed_at TEXT;")
    if "filled_at" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN filled_at TEXT;")

    conn.commit()


def get_state(conn: sqlite3.Connection, current_equity: float | None = None) -> BotState:
    today = trading_day_et()
    row = conn.execute(
        """
        SELECT last_trade_ts, trades_today, trades_today_date, consecutive_losses, daily_start_equity, daily_start_equity_date
        FROM state
        WHERE id=1;
        """
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO state (
                id, last_trade_ts, trades_today, trades_today_date, consecutive_losses, daily_start_equity, daily_start_equity_date
            )
            VALUES (1, NULL, 0, ?, 0, ?, ?);
            """,
            (today, current_equity, today if current_equity is not None else None),
        )
        conn.commit()
        return BotState(None, 0, today, 0, current_equity, today if current_equity is not None else None)

    last_trade_ts = row["last_trade_ts"]
    trades_today = row["trades_today"]
    trades_today_date = row["trades_today_date"]
    consecutive_losses = row["consecutive_losses"]
    daily_start_equity = row["daily_start_equity"]
    daily_start_equity_date = row["daily_start_equity_date"]

    if trades_today_date != today:
        trades_today = 0
        trades_today_date = today
        daily_start_equity = current_equity
        daily_start_equity_date = today
        conn.execute(
            "UPDATE state SET trades_today=?, trades_today_date=?, daily_start_equity=?, daily_start_equity_date=? WHERE id=1;",
            (trades_today, trades_today_date, daily_start_equity, daily_start_equity_date),
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

    return BotState(
        last_trade_ts=last_trade_ts,
        trades_today=trades_today,
        trades_today_date=trades_today_date,
        consecutive_losses=consecutive_losses,
        daily_start_equity=daily_start_equity,
        daily_start_equity_date=daily_start_equity_date,
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


def get_position_state(conn: sqlite3.Connection, symbol: str) -> PositionState:
    row = conn.execute(
        "SELECT symbol, side, entry_price, entry_ts, highest_price, lowest_price FROM position_state WHERE symbol=?;",
        (symbol,),
    ).fetchone()

    if row is None:
        return PositionState(symbol, None, None, None, None, None)

    return PositionState(
        symbol=row["symbol"],
        side=row["side"],
        entry_price=row["entry_price"],
        entry_ts=row["entry_ts"],
        highest_price=row["highest_price"],
        lowest_price=row["lowest_price"],
    )


def upsert_position_state(
    conn: sqlite3.Connection,
    symbol: str,
    side: Optional[str],
    entry_price: Optional[float],
    entry_ts: Optional[str],
    highest_price: Optional[float],
    lowest_price: Optional[float],
) -> None:
    conn.execute(
        """
        INSERT INTO position_state (symbol, side, entry_price, entry_ts, highest_price, lowest_price)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            side=excluded.side,
            entry_price=excluded.entry_price,
            entry_ts=excluded.entry_ts,
            highest_price=excluded.highest_price,
            lowest_price=excluded.lowest_price;
        """,
        (symbol, side, entry_price, entry_ts, highest_price, lowest_price),
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
    position_qty: float | None,
    equity: float | None,
    cash: float | None,
    note: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (ts, symbol, price, sma_fast, sma_slow, signal, position_qty, equity, cash, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (ts, symbol, price, sma_fast, sma_slow, signal, position_qty, equity, cash, note),
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
    notes: str | None,
    submitted_position_qty: float | None,
    filled_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO orders (
            ts, symbol, side, qty, order_id, status, filled_avg_price, filled_qty, intent, notes, submitted_position_qty, processed_at, filled_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?);
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
            notes,
            submitted_position_qty,
            filled_at,
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
        SELECT id, ts, symbol, side, qty, order_id, status, filled_avg_price, filled_qty, intent, notes, submitted_position_qty, processed_at, filled_at
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
            notes=row["notes"],
            submitted_position_qty=row["submitted_position_qty"],
            processed_at=row["processed_at"],
            filled_at=row["filled_at"],
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
) -> None:
    conn.execute(
        """
        INSERT INTO closed_trades (
            symbol, side, entry_ts, exit_ts, entry_price, exit_price, qty, pnl, return_pct, entry_reason, exit_reason, bars_held
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (symbol, side, entry_ts, exit_ts, entry_price, exit_price, qty, pnl, return_pct, entry_reason, exit_reason, bars_held),
    )
    conn.commit()
