import os
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.io_log import setup_logger
from bot.store import (
    connect, init_db, get_state,
    record_run, record_order,
    set_last_trade, increment_trades_today
)
from bot.strategy_ma import StrategyConfig, compute_indicators, generate_signal
from bot.broker_alpaca import (
    make_clients, get_recent_bars,
    get_position_qty, get_account_snapshot,
    place_market_order, get_order
)


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_market_open_now_et() -> bool:
    """
    Regular US equity session: 09:30–16:00 America/New_York, Mon–Fri.
    Notes:
      - This does NOT account for US market holidays or early closes.
      - Fine for a simple first trading bot.
    """
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)

    # Mon=0 ... Sun=6
    if now.weekday() >= 5:
        return False

    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_time <= now < close_time


def bars_since(last_trade_ts: str | None, bars: pd.DataFrame) -> int | None:
    if not last_trade_ts or bars.empty:
        return None
    try:
        last_trade = datetime.fromisoformat(last_trade_ts)
    except Exception:
        return None

    # count bars strictly after last_trade
    return int((bars.index > last_trade).sum())


def append_csv(path: str, header: list[str], row: list):
    exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if not exists:
            f.write(",".join(header) + "\n")
        f.write(",".join("" if v is None else str(v) for v in row) + "\n")


def main():
    load_dotenv()

    logger = setup_logger()

    # required envs
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        if not os.getenv(k):
            raise RuntimeError(f"Missing env var: {k}")

    symbol = os.getenv("SYMBOL", "SPY").strip().upper()
    qty = int(os.getenv("QTY", "1"))
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "5"))

    sma_fast = int(os.getenv("SMA_FAST", "20"))
    sma_slow = int(os.getenv("SMA_SLOW", "50"))
    cooldown_bars = int(os.getenv("COOLDOWN_BARS", "2"))
    max_trades_per_day = int(os.getenv("MAX_TRADES_PER_DAY", "5"))

    ts = utc_iso_now()
    logger.info(f"Run start ts={ts} symbol={symbol} tf={timeframe_minutes}m qty={qty}")

    # Alpaca clients
    trading, data = make_clients()

    # DB + state
    conn = connect()
    init_db(conn)
    state = get_state(conn)

    # Market-hours guard
    if not is_market_open_now_et():
        note = "market_closed"
        logger.info("Market closed (ET). Recording HOLD and exiting.")
        equity, cash = get_account_snapshot(trading)
        pos_qty = get_position_qty(trading, symbol)

        record_run(conn, ts, symbol, None, None, None, "HOLD", pos_qty, equity, cash, note)

        append_csv(
            "/app/logs/equity.csv",
            ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
            [ts, symbol, equity, cash, pos_qty, None]
        )
        logger.info("Run complete.")
        return

    # Pull bars
    bars = get_recent_bars(data, symbol, timeframe_minutes, limit=220)
    if bars.empty:
        note = "no_bars"
        logger.warning("No bars returned. Recording run as HOLD.")
        equity, cash = get_account_snapshot(trading)
        pos_qty = get_position_qty(trading, symbol)

        record_run(conn, ts, symbol, None, None, None, "HOLD", pos_qty, equity, cash, note)

        append_csv(
            "/app/logs/equity.csv",
            ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
            [ts, symbol, equity, cash, pos_qty, None]
        )
        logger.info("Run complete.")
        return

    # Compute indicators & signal
    cfg = StrategyConfig(sma_fast=sma_fast, sma_slow=sma_slow)
    bars2 = compute_indicators(bars, cfg)
    signal, last_price, v_fast, v_slow = generate_signal(bars2)

    pos_qty = get_position_qty(trading, symbol)
    equity, cash = get_account_snapshot(trading)

    # Convert signal into desired action based on current position
    desired_action = "HOLD"
    if signal == "BUY" and pos_qty <= 0:
        desired_action = "BUY"
    elif signal == "SELL" and pos_qty > 0:
        desired_action = "SELL"

    action = desired_action
    note_parts = []

    # Apply guardrails ONLY if we're actually about to trade
    if desired_action in ("BUY", "SELL"):
        if state.trades_today >= max_trades_per_day:
            action = "HOLD"
            note_parts.append("max_trades_hit")

        since = bars_since(state.last_trade_ts, bars2)
        if since is not None and since < cooldown_bars:
            action = "HOLD"
            note_parts.append(f"cooldown({since}<{cooldown_bars})")
    else:
        since = None  # useful for debugging consistency

    note = ";".join(note_parts) if note_parts else None

    logger.info(
        f"price={last_price} sma_fast={v_fast} sma_slow={v_slow} "
        f"signal={signal} pos_qty={pos_qty} desired_action={desired_action} "
        f"action={action} trades_today={state.trades_today} note={note}"
    )

    # Execute
    order_info = None
    executed_qty = None

    if action == "BUY":
        order_info = place_market_order(trading, symbol, "buy", qty)
        executed_qty = qty

    elif action == "SELL":
        # sell existing qty (rounding down to int shares for simplicity)
        sell_qty = int(float(pos_qty))
        if sell_qty > 0:
            order_info = place_market_order(trading, symbol, "sell", sell_qty)
            executed_qty = sell_qty
        else:
            logger.info("Position qty not positive; skipping sell.")
            action = "HOLD"
            note = "sell_skipped_no_position" if note is None else f"{note};sell_skipped_no_position"

    # Record run
    record_run(conn, ts, symbol, last_price, v_fast, v_slow, action, pos_qty, equity, cash, note)

    # Record equity curve
    append_csv(
        "/app/logs/equity.csv",
        ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
        [ts, symbol, equity, cash, pos_qty, last_price]
    )

    # Record order if one was submitted
    if order_info is not None:
        order_id = str(order_info.id)
        status = None
        filled_avg = None
        filled_qty = None

        # Quick status check (best-effort)
        try:
            o = get_order(trading, order_id)
            status = getattr(o, "status", None)
            filled_avg = getattr(o, "filled_avg_price", None)
            filled_qty = getattr(o, "filled_qty", None)
        except Exception as e:
            logger.warning(f"Could not fetch order status: {e}")

        record_order(
            conn,
            ts,
            symbol,
            action.lower(),
            float(executed_qty if executed_qty is not None else qty),
            order_id,
            status,
            filled_avg,
            filled_qty
        )

        append_csv(
            "/app/logs/trades.csv",
            ["ts_utc", "symbol", "side", "qty", "order_id", "status", "filled_avg_price", "filled_qty"],
            [ts, symbol, action.lower(), executed_qty if executed_qty is not None else qty, order_id, status, filled_avg, filled_qty]
        )

        set_last_trade(conn, ts)
        increment_trades_today(conn)
        logger.info(f"Order submitted order_id={order_id} status={status}")

    logger.info("Run complete.")


if __name__ == "__main__":
    main()