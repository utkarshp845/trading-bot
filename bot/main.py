import os
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.io_log import setup_logger
from bot.store import (
    connect, init_db, get_state,
    record_run, record_order,
    set_last_trade, increment_trades_today,
    get_position_state, upsert_position_state, clear_position_state
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
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)

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
    return int((bars.index > last_trade).sum())


def bars_in_trade(entry_ts: str | None, bars: pd.DataFrame) -> int | None:
    if not entry_ts or bars.empty:
        return None
    try:
        entry_dt = datetime.fromisoformat(entry_ts)
    except Exception:
        return None
    return int((bars.index > entry_dt).sum())


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

    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        if not os.getenv(k):
            raise RuntimeError(f"Missing env var: {k}")

    symbol = os.getenv("SYMBOL", "SPY").strip().upper()
    qty = int(os.getenv("QTY", "1"))
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "5"))

    sma_fast = int(os.getenv("SMA_FAST", "20"))
    sma_slow = int(os.getenv("SMA_SLOW", "50"))

    adx_period = int(os.getenv("ADX_PERIOD", "14"))
    adx_threshold = float(os.getenv("ADX_THRESHOLD", "25"))

    atr_period = int(os.getenv("ATR_PERIOD", "14"))
    atr_max_pct = float(os.getenv("ATR_MAX_PCT", "0.0035"))

    volume_ma_period = int(os.getenv("VOLUME_MA_PERIOD", "20"))

    trail_atr_multiplier = float(os.getenv("TRAIL_ATR_MULTIPLIER", "1.5"))
    max_bars_in_trade = int(os.getenv("MAX_BARS_IN_TRADE", "12"))

    cooldown_bars = int(os.getenv("COOLDOWN_BARS", "2"))
    max_trades_per_day = int(os.getenv("MAX_TRADES_PER_DAY", "5"))

    ts = utc_iso_now()
    logger.info(f"Run start ts={ts} symbol={symbol} tf={timeframe_minutes}m qty={qty}")

    trading, data = make_clients()

    conn = connect()
    init_db(conn)
    state = get_state(conn)
    pos_state = get_position_state(conn, symbol)

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

    cfg = StrategyConfig(
        sma_fast=sma_fast,
        sma_slow=sma_slow,
        adx_period=adx_period,
        adx_threshold=adx_threshold,
        atr_period=atr_period,
        atr_max_pct=atr_max_pct,
        volume_ma_period=volume_ma_period,
        trail_atr_multiplier=trail_atr_multiplier,
        max_bars_in_trade=max_bars_in_trade,
    )

    bars2 = compute_indicators(bars, cfg)
    signal, metrics, reasons = generate_signal(bars2, cfg)

    last_price = metrics.get("price")
    v_fast = metrics.get("sma_fast")
    v_slow = metrics.get("sma_slow")
    v_adx = metrics.get("adx")
    v_atr = metrics.get("atr")
    v_atr_pct = metrics.get("atr_pct")
    v_volume = metrics.get("volume")
    v_volume_ma = metrics.get("volume_ma")

    pos_qty = get_position_qty(trading, symbol)
    equity, cash = get_account_snapshot(trading)

    # Bootstrap position state if already long but no state exists
    if pos_qty > 0 and pos_state.entry_price is None:
        bootstrap_price = last_price
        bootstrap_ts = ts
        bootstrap_high = last_price
        upsert_position_state(conn, symbol, bootstrap_price, bootstrap_ts, bootstrap_high)
        pos_state = get_position_state(conn, symbol)
        logger.info("Bootstrapped position state for existing position.")

    # Keep highest price updated while long
    if pos_qty > 0 and last_price is not None:
        current_high = pos_state.highest_price if pos_state.highest_price is not None else last_price
        new_high = max(float(current_high), float(last_price))
        upsert_position_state(conn, symbol, pos_state.entry_price, pos_state.entry_ts, new_high)
        pos_state = get_position_state(conn, symbol)

    desired_action = "HOLD"
    note_parts = list(reasons)

    # Entry if flat
    if pos_qty <= 0:
        if signal == "BUY":
            desired_action = "BUY"

    # Exit logic if long
    elif pos_qty > 0:
        exit_reason = None

        # trailing stop
        if (
            last_price is not None
            and v_atr is not None
            and pos_state.highest_price is not None
        ):
            trailing_stop = float(pos_state.highest_price) - (trail_atr_multiplier * float(v_atr))
            if float(last_price) < trailing_stop:
                desired_action = "SELL"
                exit_reason = f"trailing_stop_hit({last_price}<{trailing_stop})"

        # time stop
        if desired_action == "HOLD":
            trade_bars = bars_in_trade(pos_state.entry_ts, bars2)
            if (
                trade_bars is not None
                and trade_bars >= max_bars_in_trade
                and pos_state.entry_price is not None
                and last_price is not None
                and float(last_price) <= float(pos_state.entry_price)
            ):
                desired_action = "SELL"
                exit_reason = f"time_stop_hit({trade_bars}>={max_bars_in_trade})"

        # crossover exit
        if desired_action == "HOLD" and signal == "SELL":
            desired_action = "SELL"
            exit_reason = "trend_reversal"

        if exit_reason:
            note_parts.append(exit_reason)

    action = desired_action

    # Apply guardrails only for actual trades
    if desired_action in ("BUY", "SELL"):
        if state.trades_today >= max_trades_per_day:
            action = "HOLD"
            note_parts.append("max_trades_hit")

        since = bars_since(state.last_trade_ts, bars2)
        if since is not None and since < cooldown_bars:
            action = "HOLD"
            note_parts.append(f"cooldown({since}<{cooldown_bars})")

    note = ";".join(note_parts) if note_parts else None

    logger.info(
        f"price={last_price} sma_fast={v_fast} sma_slow={v_slow} "
        f"adx={v_adx} atr={v_atr} atr_pct={v_atr_pct} volume={v_volume} volume_ma={v_volume_ma} "
        f"signal={signal} pos_qty={pos_qty} desired_action={desired_action} "
        f"action={action} trades_today={state.trades_today} note={note}"
    )

    order_info = None
    executed_qty = None

    if action == "BUY":
        order_info = place_market_order(trading, symbol, "buy", qty)
        executed_qty = qty

        # optimistic state update for paper-trading simplicity
        if last_price is not None:
            upsert_position_state(conn, symbol, float(last_price), ts, float(last_price))

    elif action == "SELL":
        sell_qty = int(float(pos_qty))
        if sell_qty > 0:
            order_info = place_market_order(trading, symbol, "sell", sell_qty)
            executed_qty = sell_qty
            clear_position_state(conn, symbol)
        else:
            logger.info("Position qty not positive; skipping sell.")
            action = "HOLD"
            note = "sell_skipped_no_position" if note is None else f"{note};sell_skipped_no_position"

    record_run(conn, ts, symbol, last_price, v_fast, v_slow, action, pos_qty, equity, cash, note)

    append_csv(
        "/app/logs/equity.csv",
        ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
        [ts, symbol, equity, cash, pos_qty, last_price]
    )

    if order_info is not None:
        order_id = str(order_info.id)
        status = None
        filled_avg = None
        filled_qty = None

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