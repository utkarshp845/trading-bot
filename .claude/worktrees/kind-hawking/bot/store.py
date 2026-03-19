import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DB_PATH = "/app/data/bot.db"


@dataclass
class BotState:
    last_trade_ts: Optional[str]
    trades_today: int
    trades_today_date: str


@dataclass
class PositionState:
    symbol: str
    entry_price: Optional[float]
    entry_ts: Optional[str]
    highest_price: Optional[float]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect() -> sqlite3.Connection:
    os.makedirs("/app/data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_trade_ts TEXT,
        trades_today INTEGER NOT NULL,
        trades_today_date TEXT NOT NULL
    );
    """)

    conn.execute("""
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
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty REAL NOT NULL,
        order_id TEXT NOT NULL,
        status TEXT,
        filled_avg_price REAL,
        filled_qty REAL
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS position_state (
        symbol TEXT PRIMARY KEY,
        entry_price REAL,
        entry_ts TEXT,
        highest_price REAL
    );
    """)

    conn.commit()


def get_state(conn: sqlite3.Connection) -> BotState:
    today = _utc_now().date().isoformat()
    cur = conn.execute("SELECT last_trade_ts, trades_today, trades_today_date FROM state WHERE id=1;")
    row = cur.fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO state (id, last_trade_ts, trades_today, trades_today_date) VALUES (1, NULL, 0, ?);",
            (today,)
        )
        conn.commit()
        return BotState(last_trade_ts=None, trades_today=0, trades_today_date=today)

    last_trade_ts, trades_today, trades_today_date = row

    if trades_today_date != today:
        trades_today = 0
        trades_today_date = today
        conn.execute(
            "UPDATE state SET trades_today=?, trades_today_date=? WHERE id=1;",
            (trades_today, trades_today_date)
        )
        conn.commit()

    return BotState(last_trade_ts=last_trade_ts, trades_today=trades_today, trades_today_date=trades_today_date)


def set_last_trade(conn: sqlite3.Connection, ts_iso_utc: str) -> None:
    conn.execute("UPDATE state SET last_trade_ts=? WHERE id=1;", (ts_iso_utc,))
    conn.commit()


def increment_trades_today(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE state SET trades_today = trades_today + 1 WHERE id=1;")
    conn.commit()


def get_position_state(conn: sqlite3.Connection, symbol: str) -> PositionState:
    cur = conn.execute(
        "SELECT symbol, entry_price, entry_ts, highest_price FROM position_state WHERE symbol=?;",
        (symbol,)
    )
    row = cur.fetchone()

    if row is None:
        return PositionState(symbol=symbol, entry_price=None, entry_ts=None, highest_price=None)

    return PositionState(
        symbol=row[0],
        entry_price=row[1],
        entry_ts=row[2],
        highest_price=row[3],
    )


def upsert_position_state(
    conn: sqlite3.Connection,
    symbol: str,
    entry_price: Optional[float],
    entry_ts: Optional[str],
    highest_price: Optional[float]
) -> None:
    conn.execute(
        """
        INSERT INTO position_state (symbol, entry_price, entry_ts, highest_price)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            entry_price=excluded.entry_price,
            entry_ts=excluded.entry_ts,
            highest_price=excluded.highest_price;
        """,
        (symbol, entry_price, entry_ts, highest_price)
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
    note: str | None
) -> None:
    conn.execute(
        """INSERT INTO runs (ts, symbol, price, sma_fast, sma_slow, signal, position_qty, equity, cash, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        (ts, symbol, price, sma_fast, sma_slow, signal, position_qty, equity, cash, note)
    )
    conn.commit()


def record_order(
    conn: sqlite3.Connection,
    ts: str,
    symbol: str,
    side: str,
    qty: float,
    order_id: str,
    status: str | None,
    filled_avg_price: float | None,
    filled_qty: float | None
) -> None:
    conn.execute(
        """INSERT INTO orders (ts, symbol, side, qty, order_id, status, filled_avg_price, filled_qty)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
        (ts, symbol, side, qty, order_id, status, filled_avg_price, filled_qty)
    )
    conn.commit()