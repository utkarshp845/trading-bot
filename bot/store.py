import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DB_PATH = "/app/data/bot.db"

@dataclass
class BotState:
    last_trade_ts: Optional[str]  # ISO8601 UTC
    trades_today: int
    trades_today_date: str        # YYYY-MM-DD (UTC)

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

    # reset daily counter on date change (UTC)
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