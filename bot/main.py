from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import json
import os
import time
import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from bot.broker_alpaca import (
    get_account_snapshot,
    get_order,
    get_position_snapshot,
    get_recent_bars,
    is_market_open,
    make_clients,
    normalize_order_status,
    place_market_order,
)
from bot.io_log import setup_logger
from bot.paths import LOGS_DIR, ensure_runtime_dirs
from bot.risk import RiskConfig, evaluate_entry_risk, parse_ts
from bot.store import (
    clear_position_state,
    connect,
    get_orders_requiring_sync,
    get_position_state,
    get_state,
    has_pending_orders,
    increment_trades_today,
    init_db,
    mark_order_processed,
    record_closed_trade,
    record_event,
    record_order_submission,
    record_run,
    set_consecutive_losses,
    set_last_trade,
    upsert_position_state,
    update_order_status,
)
from bot.strategy_ma import StrategyConfig, compute_indicators, generate_signal


TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}


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
    last_trade = parse_ts(last_trade_ts)
    if last_trade is None:
        return None
    return int((bars.index > last_trade).sum())


def bars_in_trade(entry_ts: str | None, bars: pd.DataFrame) -> int | None:
    if not entry_ts or bars.empty:
        return None
    entry_dt = parse_ts(entry_ts)
    if entry_dt is None:
        return None
    return int((bars.index > entry_dt).sum())


def append_csv(path: Path, header: list[str], row: list[object]) -> None:
    ensure_runtime_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8") as handle:
        if needs_header:
            handle.write(",".join(header) + "\n")
        handle.write(",".join("" if value is None else str(value) for value in row) + "\n")


def _safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_iso(value: object) -> str | None:
    dt = parse_ts(value)
    return dt.isoformat() if dt is not None else None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def emit_event(conn, ts: str, level: str, event_type: str, symbol: str | None, message: str | None, payload: dict | None = None) -> None:
    record_event(conn, ts, level, event_type, symbol=symbol, message=message, payload=payload)


def semantic_action_type(position_qty: float, action: str) -> str | None:
    if action == "BUY":
        if position_qty < 0:
            return "close_short"
        if position_qty == 0:
            return "open_long"
    if action == "SELL":
        if position_qty > 0:
            return "close_long"
        if position_qty == 0:
            return "open_short"
    return None


def refresh_order_fill_snapshot(trading, order_id: str, logger):
    latest_order = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(0.75)
        try:
            latest_order = get_order(trading, order_id)
        except Exception as exc:
            logger.warning(f"Could not fetch order status for {order_id}: {exc}")
            continue
        status = normalize_order_status(getattr(latest_order, "status", None))
        filled_avg = _safe_float(getattr(latest_order, "filled_avg_price", None))
        filled_qty = _safe_float(getattr(latest_order, "filled_qty", None))
        filled_at = _to_iso(getattr(latest_order, "filled_at", None))
        if filled_avg is not None or filled_qty is not None or status == "FILLED":
            return status, filled_avg, filled_qty, filled_at

    if latest_order is None:
        return None, None, None, None

    return (
        normalize_order_status(getattr(latest_order, "status", None)),
        _safe_float(getattr(latest_order, "filled_avg_price", None)),
        _safe_float(getattr(latest_order, "filled_qty", None)),
        _to_iso(getattr(latest_order, "filled_at", None)),
    )


def sync_position_state_with_broker(conn, trading, symbol: str, logger):
    broker_position = get_position_snapshot(trading, symbol)
    pos_state = get_position_state(conn, symbol)

    if broker_position is None:
        if pos_state.entry_price is not None and not has_pending_orders(conn, symbol):
            clear_position_state(conn, symbol)
            logger.info("Cleared local position state because broker reports no open position.")
        return get_position_state(conn, symbol), 0.0

    entry_price = broker_position["avg_entry_price"]
    if entry_price is None:
        entry_price = pos_state.entry_price

    entry_ts = pos_state.entry_ts or utc_iso_now()
    highest_price = pos_state.highest_price
    lowest_price = pos_state.lowest_price

    if broker_position["side"] == "long":
        seed = entry_price if entry_price is not None else broker_position["current_price"]
        if highest_price is None:
            highest_price = seed
    else:
        seed = entry_price if entry_price is not None else broker_position["current_price"]
        if lowest_price is None:
            lowest_price = seed

    upsert_position_state(
        conn,
        symbol,
        broker_position["side"],
        entry_price,
        entry_ts,
        highest_price,
        lowest_price,
    )

    return get_position_state(conn, symbol), float(broker_position["qty"])


def reconcile_submitted_orders(conn, trading, symbol: str, logger) -> None:
    tracked_orders = get_orders_requiring_sync(conn, symbol)
    if not tracked_orders:
        return

    for order in tracked_orders:
        try:
            remote_order = get_order(trading, order.order_id)
        except Exception as exc:
            logger.warning(f"Could not refresh order {order.order_id}: {exc}")
            continue

        status = normalize_order_status(getattr(remote_order, "status", None))
        filled_avg = _safe_float(getattr(remote_order, "filled_avg_price", None))
        filled_qty = _safe_float(getattr(remote_order, "filled_qty", None))
        filled_at = _to_iso(getattr(remote_order, "filled_at", None))
        update_order_status(conn, order.order_id, status, filled_avg, filled_qty, filled_at)

        if status == "FILLED" and order.processed_at is None:
            processed_at = utc_iso_now()
            position_before = get_position_state(conn, symbol)
            exit_ts = filled_at or order.ts
            emit_event(
                conn,
                processed_at,
                "INFO",
                "order_filled",
                symbol,
                f"Order {order.order_id} filled.",
                {
                    "order_id": order.order_id,
                    "side": order.side,
                    "intent": order.intent,
                    "action_type": order.action_type,
                    "filled_avg_price": filled_avg,
                    "filled_qty": filled_qty,
                    "filled_at": filled_at,
                },
            )

            if order.intent == "exit":
                exit_price = filled_avg
                realized_qty = filled_qty if filled_qty is not None else order.qty
                if (
                    position_before.entry_price is not None
                    and exit_price is not None
                    and position_before.side in {"long", "short"}
                ):
                    if position_before.side == "long":
                        pnl = (exit_price - float(position_before.entry_price)) * float(realized_qty)
                        return_pct = (exit_price - float(position_before.entry_price)) / float(position_before.entry_price)
                    else:
                        pnl = (float(position_before.entry_price) - exit_price) * float(realized_qty)
                        return_pct = (float(position_before.entry_price) - exit_price) / float(position_before.entry_price)

                    record_closed_trade(
                        conn,
                        symbol,
                        position_before.side,
                        position_before.entry_ts,
                        exit_ts,
                        float(position_before.entry_price),
                        exit_price,
                        float(realized_qty),
                        float(pnl),
                        return_pct,
                        None,
                        order.notes,
                        None,
                    )

                    state_after_trade = get_state(conn)
                    if pnl > 0:
                        set_consecutive_losses(conn, 0)
                    else:
                        set_consecutive_losses(conn, state_after_trade.consecutive_losses + 1)

            sync_position_state_with_broker(conn, trading, symbol, logger)
            set_last_trade(conn, exit_ts)
            if order.intent == "entry":
                increment_trades_today(conn)
            mark_order_processed(conn, order.order_id, processed_at)
            logger.info(
                f"Order fill processed order_id={order.order_id} intent={order.intent} "
                f"action_type={order.action_type} status={status}"
            )
            continue

        if status in TERMINAL_ORDER_STATUSES and order.processed_at is None:
            mark_order_processed(conn, order.order_id, utc_iso_now())
            emit_event(
                conn,
                utc_iso_now(),
                "WARN" if status != "FILLED" else "INFO",
                "order_terminal",
                symbol,
                f"Order {order.order_id} reached terminal status {status}.",
                {"order_id": order.order_id, "status": status, "intent": order.intent, "action_type": order.action_type},
            )
            logger.info(f"Marked terminal non-fill order as processed order_id={order.order_id} status={status}")


def main():
    load_dotenv()
    ensure_runtime_dirs()
    logger = setup_logger()

    for key in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        value = os.getenv(key, "").strip()
        if not value or value.startswith("YOUR_"):
            raise RuntimeError(f"Missing env var: {key}")

    symbol = os.getenv("SYMBOL", "SPY").strip().upper()
    qty = int(os.getenv("QTY", "1"))
    timeframe_minutes = int(os.getenv("TIMEFRAME_MINUTES", "5"))

    sma_fast = int(os.getenv("SMA_FAST", "20"))
    sma_slow = int(os.getenv("SMA_SLOW", "50"))
    adx_period = int(os.getenv("ADX_PERIOD", "14"))
    adx_threshold = float(os.getenv("ADX_THRESHOLD", "20"))
    atr_period = int(os.getenv("ATR_PERIOD", "14"))
    atr_max_pct = float(os.getenv("ATR_MAX_PCT", "0.0045"))
    volume_ma_period = int(os.getenv("VOLUME_MA_PERIOD", "20"))
    volume_min_multiplier = float(os.getenv("VOLUME_MIN_MULTIPLIER", os.getenv("VOLUME_THRESHOLD_MULTIPLIER", "0.8")))
    trail_atr_multiplier = float(os.getenv("TRAIL_ATR_MULTIPLIER", "1.5"))
    max_bars_in_trade = int(os.getenv("MAX_BARS_IN_TRADE", "12"))
    cooldown_bars = int(os.getenv("COOLDOWN_BARS", "2"))
    max_trades_per_day = int(os.getenv("MAX_TRADES_PER_DAY", "5"))
    max_daily_drawdown_pct = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.01"))
    max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "0"))
    max_consecutive_losses = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
    strategy_version = os.getenv("STRATEGY_VERSION", "v1").strip() or "v1"
    stale_bar_checks_enabled = _env_flag("ENABLE_STALE_BAR_CHECK", False)
    max_bar_age_seconds = (
        int(
            os.getenv(
                "MAX_BAR_AGE_SECONDS",
                str(int(os.getenv("STALE_BAR_MAX_MINUTES", str(max(15, timeframe_minutes * 3)))) * 60),
            )
        )
        if stale_bar_checks_enabled
        else 0
    )
    max_position_notional_pct = float(os.getenv("MAX_POSITION_NOTIONAL_PCT", "0.02"))
    startup_delay_seconds = max(0, int(os.getenv("STARTUP_DELAY_SECONDS", "20")))

    ts = utc_iso_now()
    logger.info(
        f"Run start ts={ts} symbol={symbol} tf={timeframe_minutes}m qty={qty} "
        f"strategy_version={strategy_version} stale_bar_check={'enabled' if stale_bar_checks_enabled else 'disabled'} "
        f"max_bar_age_seconds={max_bar_age_seconds} startup_delay={startup_delay_seconds}s"
    )

    trading, data = make_clients()
    conn = connect()
    init_db(conn)
    emit_event(
        conn,
        ts,
        "INFO",
        "run_start",
        symbol,
        "Bot run started.",
        {
            "timeframe_minutes": timeframe_minutes,
            "qty": qty,
            "strategy_version": strategy_version,
            "stale_bar_check_enabled": stale_bar_checks_enabled,
            "max_bar_age_seconds": max_bar_age_seconds,
            "startup_delay_seconds": startup_delay_seconds,
        },
    )

    if startup_delay_seconds > 0:
        logger.info(f"Sleeping {startup_delay_seconds}s before broker and data checks to let the latest bar settle.")
        time.sleep(startup_delay_seconds)

    reconcile_submitted_orders(conn, trading, symbol, logger)

    equity, cash = get_account_snapshot(trading)
    state = get_state(conn, equity)
    pos_state, pos_qty = sync_position_state_with_broker(conn, trading, symbol, logger)

    market_open = is_market_open(trading)
    used_et_market_fallback = False
    if market_open is None and _env_flag("ALLOW_ET_MARKET_CLOCK_FALLBACK", False):
        market_open = is_market_open_now_et()
        used_et_market_fallback = True

    if market_open is None or not market_open:
        note = "market_clock_unavailable" if market_open is None else "market_closed"
        message = (
            "Market clock unavailable; holding to avoid trading outside the session."
            if market_open is None
            else "Market is closed; holding."
        )
        logger.info(
            "Market clock unavailable. Recording HOLD and exiting."
            if market_open is None
            else "Market closed. Recording HOLD and exiting."
        )
        emit_event(
            conn,
            ts,
            "WARN" if market_open is None else "INFO",
            "market_clock_unavailable" if market_open is None else "market_closed",
            symbol,
            message,
            {"et_fallback_used": used_et_market_fallback} if market_open is None else None,
        )
        record_run(conn, ts, symbol, None, None, None, "HOLD", "HOLD", pos_qty, equity, cash, note, strategy_version=strategy_version)
        append_csv(
            LOGS_DIR / "equity.csv",
            ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
            [ts, symbol, equity, cash, pos_qty, None],
        )
        logger.info("Run complete.")
        return

    bars = get_recent_bars(data, symbol, timeframe_minutes, limit=220)
    if bars.empty:
        note = "no_bars"
        logger.warning("No bars returned. Recording run as HOLD.")
        emit_event(conn, ts, "WARN", "no_bars", symbol, "No bars returned from market data client.", None)
        record_run(conn, ts, symbol, None, None, None, "HOLD", "HOLD", pos_qty, equity, cash, note, strategy_version=strategy_version)
        append_csv(
            LOGS_DIR / "equity.csv",
            ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
            [ts, symbol, equity, cash, pos_qty, None],
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
        volume_min_multiplier=volume_min_multiplier,
        timeframe_minutes=timeframe_minutes,
        trail_atr_multiplier=trail_atr_multiplier,
        max_bars_in_trade=max_bars_in_trade,
    )

    bars2 = compute_indicators(bars, cfg)
    signal, metrics, reasons = generate_signal(bars2, cfg)
    emit_event(
        conn,
        ts,
        "INFO",
        "signal_evaluated",
        symbol,
        f"Signal evaluated as {signal}.",
        {"signal": signal, "metrics": metrics, "reasons": reasons},
    )

    last_price = metrics.get("price")
    v_fast = metrics.get("sma_fast")
    v_slow = metrics.get("sma_slow")
    v_adx = metrics.get("adx")
    v_atr = metrics.get("atr")
    v_atr_pct = metrics.get("atr_pct")
    v_volume = metrics.get("volume")
    v_volume_ma = metrics.get("volume_ma")
    bar_ts = metrics.get("bar_ts")

    if pos_qty > 0 and last_price is not None:
        current_high = pos_state.highest_price if pos_state.highest_price is not None else last_price
        new_high = max(float(current_high), float(last_price))
        upsert_position_state(
            conn,
            symbol,
            "long",
            pos_state.entry_price,
            pos_state.entry_ts,
            new_high,
            pos_state.lowest_price,
        )
        pos_state = get_position_state(conn, symbol)
    elif pos_qty < 0 and last_price is not None:
        current_low = pos_state.lowest_price if pos_state.lowest_price is not None else last_price
        new_low = min(float(current_low), float(last_price))
        upsert_position_state(
            conn,
            symbol,
            "short",
            pos_state.entry_price,
            pos_state.entry_ts,
            pos_state.highest_price,
            new_low,
        )
        pos_state = get_position_state(conn, symbol)

    desired_action = "HOLD"
    note_parts = list(reasons)
    intent = None

    if pos_qty == 0:
        if signal == "LONG":
            desired_action = "BUY"
            intent = "entry"
        elif signal == "SHORT":
            desired_action = "SELL"
            intent = "entry"
    elif pos_qty > 0:
        exit_reason = None

        if last_price is not None and v_atr is not None and pos_state.highest_price is not None:
            trailing_stop = float(pos_state.highest_price) - (trail_atr_multiplier * float(v_atr))
            if float(last_price) < trailing_stop:
                desired_action = "SELL"
                exit_reason = f"long_trailing_stop_hit({last_price}<{trailing_stop})"

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
                exit_reason = f"long_time_stop_hit({trade_bars}>={max_bars_in_trade})"

        if desired_action == "HOLD" and signal == "SHORT":
            desired_action = "SELL"
            exit_reason = "long_trend_reversal"

        if exit_reason:
            note_parts.append(exit_reason)
            intent = "exit"
    elif pos_qty < 0:
        exit_reason = None

        if last_price is not None and v_atr is not None and pos_state.lowest_price is not None:
            trailing_stop = float(pos_state.lowest_price) + (trail_atr_multiplier * float(v_atr))
            if float(last_price) > trailing_stop:
                desired_action = "BUY"
                exit_reason = f"short_trailing_stop_hit({last_price}>{trailing_stop})"

        if desired_action == "HOLD":
            trade_bars = bars_in_trade(pos_state.entry_ts, bars2)
            if (
                trade_bars is not None
                and trade_bars >= max_bars_in_trade
                and pos_state.entry_price is not None
                and last_price is not None
                and float(last_price) >= float(pos_state.entry_price)
            ):
                desired_action = "BUY"
                exit_reason = f"short_time_stop_hit({trade_bars}>={max_bars_in_trade})"

        if desired_action == "HOLD" and signal == "LONG":
            desired_action = "BUY"
            exit_reason = "short_trend_reversal"

        if exit_reason:
            note_parts.append(exit_reason)
            intent = "exit"

    action = desired_action
    pending_orders = has_pending_orders(conn, symbol)

    entering_long = pos_qty == 0 and desired_action == "BUY" and signal == "LONG"
    entering_short = pos_qty == 0 and desired_action == "SELL" and signal == "SHORT"
    if entering_long or entering_short:
        risk_eval = evaluate_entry_risk(
            RiskConfig(
                max_trades_per_day=max_trades_per_day,
                max_daily_drawdown_pct=max_daily_drawdown_pct,
                max_daily_loss=max_daily_loss,
                max_consecutive_losses=max_consecutive_losses,
                max_bar_age_seconds=max_bar_age_seconds,
                max_position_notional_pct=max_position_notional_pct,
            ),
            trades_today=state.trades_today,
            consecutive_losses=state.consecutive_losses,
            daily_start_equity=state.daily_start_equity,
            current_equity=equity,
            last_bar_ts=bar_ts,
            position_notional=(float(last_price) * qty) if last_price is not None else None,
        )
        if not risk_eval.allow_entries:
            action = "HOLD"
            note_parts.extend(risk_eval.reasons)
            emit_event(
                conn,
                ts,
                "WARN",
                "entry_blocked_risk",
                symbol,
                "Entry blocked by risk guardrails.",
                {"risk_reasons": risk_eval.reasons, "signal": signal},
            )

        since = bars_since(state.last_trade_ts, bars2)
        if since is not None and since < cooldown_bars:
            action = "HOLD"
            note_parts.append("cooldown")
            emit_event(
                conn,
                ts,
                "WARN",
                "entry_blocked_cooldown",
                symbol,
                "Entry blocked by cooldown.",
                {"bars_since_last_trade": since, "cooldown_bars": cooldown_bars, "signal": signal},
            )

    if pending_orders:
        action = "HOLD"
        note_parts.append("pending_order_in_flight")
        emit_event(
            conn,
            ts,
            "WARN",
            "entry_blocked_pending_order",
            symbol,
            "Entry blocked because an order is already in flight.",
            None,
        )

    note = ";".join(dict.fromkeys(note_parts)) if note_parts else None
    metrics_json = json.dumps(metrics, sort_keys=True)
    reasons_text = ";".join(reasons) if reasons else None
    action_type = semantic_action_type(pos_qty, action)

    logger.info(
        f"price={last_price} sma_fast={v_fast} sma_slow={v_slow} "
        f"adx={v_adx} atr={v_atr} atr_pct={v_atr_pct} volume={v_volume} volume_ma={v_volume_ma} "
        f"signal={signal} pos_qty={pos_qty} desired_action={desired_action} "
        f"action={action} action_type={action_type} trades_today={state.trades_today} "
        f"consecutive_losses={state.consecutive_losses} note={note}"
    )

    order_info = None
    executed_qty = None
    order_side_for_log = None
    order_intent = intent

    if action == "BUY":
        if pos_qty < 0:
            cover_qty = int(abs(float(pos_qty)))
            if cover_qty > 0:
                order_info = place_market_order(trading, symbol, "buy", cover_qty)
                executed_qty = cover_qty
                order_side_for_log = "buy"
                order_intent = "exit"
        elif pos_qty == 0:
            order_info = place_market_order(trading, symbol, "buy", qty)
            executed_qty = qty
            order_side_for_log = "buy"
            order_intent = "entry"
    elif action == "SELL":
        if pos_qty > 0:
            sell_qty = int(float(pos_qty))
            if sell_qty > 0:
                order_info = place_market_order(trading, symbol, "sell", sell_qty)
                executed_qty = sell_qty
                order_side_for_log = "sell"
                order_intent = "exit"
        elif pos_qty == 0:
            order_info = place_market_order(trading, symbol, "sell", qty)
            executed_qty = qty
            order_side_for_log = "sell"
            order_intent = "entry"

    record_run(
        conn,
        ts,
        symbol,
        last_price,
        v_fast,
        v_slow,
        signal,
        action,
        pos_qty,
        equity,
        cash,
        note,
        reasons=reasons_text,
        metrics_json=metrics_json,
        bar_ts=bar_ts,
        strategy_version=strategy_version,
    )

    append_csv(
        LOGS_DIR / "equity.csv",
        ["ts_utc", "symbol", "equity", "cash", "position_qty", "last_price"],
        [ts, symbol, equity, cash, pos_qty, last_price],
    )

    if order_info is not None:
        order_id = str(order_info.id)
        status = normalize_order_status(getattr(order_info, "status", None))
        filled_avg = _safe_float(getattr(order_info, "filled_avg_price", None))
        filled_qty = _safe_float(getattr(order_info, "filled_qty", None))
        filled_at = _to_iso(getattr(order_info, "filled_at", None))
        refreshed_status, refreshed_avg, refreshed_qty, refreshed_filled_at = refresh_order_fill_snapshot(trading, order_id, logger)
        status = refreshed_status or status
        filled_avg = refreshed_avg if refreshed_avg is not None else filled_avg
        filled_qty = refreshed_qty if refreshed_qty is not None else filled_qty
        filled_at = refreshed_filled_at or filled_at

        record_order_submission(
            conn,
            ts,
            symbol,
            order_side_for_log if order_side_for_log is not None else action.lower(),
            float(executed_qty if executed_qty is not None else qty),
            order_id,
            status,
            filled_avg,
            filled_qty,
            order_intent,
            action_type,
            note,
            pos_qty,
            filled_at,
        )
        emit_event(
            conn,
            ts,
            "INFO",
            "order_submitted",
            symbol,
            f"Submitted {order_intent or 'unknown'} order {order_id}.",
            {
                "order_id": order_id,
                "status": status,
                "intent": order_intent,
                "action_type": action_type,
                "side": order_side_for_log,
                "qty": executed_qty if executed_qty is not None else qty,
                "filled_avg_price": filled_avg,
                "filled_qty": filled_qty,
            },
        )

        append_csv(
            LOGS_DIR / "trades.csv",
            ["ts_utc", "symbol", "side", "qty", "order_id", "status", "filled_avg_price", "filled_qty", "intent", "note"],
            [
                ts,
                symbol,
                order_side_for_log if order_side_for_log is not None else action.lower(),
                executed_qty if executed_qty is not None else qty,
                order_id,
                status,
                filled_avg,
                filled_qty,
                action_type if action_type is not None else order_intent,
                note,
            ],
        )

        logger.info(
            f"Order submitted order_id={order_id} status={status} side={order_side_for_log} "
            f"intent={order_intent} action_type={action_type}"
        )

    emit_event(conn, utc_iso_now(), "INFO", "run_complete", symbol, "Bot run complete.", {"final_action": action, "note": note})
    logger.info("Run complete.")


if __name__ == "__main__":
    main()
