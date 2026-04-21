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
    set_last_entry_signal,
    set_last_trade,
    upsert_position_state,
    update_order_status,
)
from bot.strategy_ma import StrategyConfig, compute_indicators, generate_signal, parse_entry_windows
from bot.trade_controls import bars_since, compute_entry_qty, should_allow_reentry_during_cooldown
from bot.trade_controls import evaluate_session_exit


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


def _env_optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _entry_metrics_payload(signal: str, metrics: dict) -> dict:
    return {
        "signal": signal,
        "bar_ts": metrics.get("bar_ts"),
        "bar_close_ts": metrics.get("bar_close_ts"),
        "entry_signal_side": "long" if signal == "LONG" else "short" if signal == "SHORT" else None,
        "adx": metrics.get("adx"),
        "atr_pct": metrics.get("atr_pct"),
        "volume_ratio": metrics.get("volume_ratio"),
        "sma_spread_pct": metrics.get("sma_spread_pct"),
        "entry_window_bucket": metrics.get("entry_window_bucket"),
        "signal_strength": metrics.get("signal_strength"),
        "decision_price": metrics.get("price"),
    }


def _entry_meta_from_order(order, filled_avg: float | None) -> dict:
    try:
        payload = json.loads(order.entry_metrics_json) if order.entry_metrics_json else {}
    except json.JSONDecodeError:
        payload = {}

    decision_price = _safe_float(payload.get("decision_price"))
    slippage = None
    if decision_price is not None and filled_avg is not None:
        if payload.get("entry_signal_side") == "short":
            slippage = filled_avg - decision_price
        else:
            slippage = decision_price - filled_avg
    payload["realized_slippage_estimate"] = slippage
    return payload


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

    entry_ts = pos_state.entry_ts
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
        entry_bar_ts=pos_state.entry_bar_ts,
        entry_signal_side=pos_state.entry_signal_side,
        entry_adx=pos_state.entry_adx,
        entry_atr_pct=pos_state.entry_atr_pct,
        entry_volume_ratio=pos_state.entry_volume_ratio,
        entry_sma_spread_pct=pos_state.entry_sma_spread_pct,
        entry_window_bucket=pos_state.entry_window_bucket,
        entry_signal_strength=pos_state.entry_signal_strength,
        realized_slippage_estimate=pos_state.realized_slippage_estimate,
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
            fill_ts = filled_at or order.ts
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

            if order.intent == "entry":
                entry_meta = _entry_meta_from_order(order, filled_avg)
                upsert_position_state(
                    conn,
                    symbol,
                    "long" if order.side.lower() == "buy" else "short",
                    filled_avg if filled_avg is not None else position_before.entry_price,
                    fill_ts,
                    filled_avg if order.side.lower() == "buy" else position_before.highest_price,
                    filled_avg if order.side.lower() == "sell" else position_before.lowest_price,
                    entry_bar_ts=entry_meta.get("bar_close_ts") or entry_meta.get("bar_ts"),
                    entry_signal_side=entry_meta.get("entry_signal_side"),
                    entry_adx=_safe_float(entry_meta.get("adx")),
                    entry_atr_pct=_safe_float(entry_meta.get("atr_pct")),
                    entry_volume_ratio=_safe_float(entry_meta.get("volume_ratio")),
                    entry_sma_spread_pct=_safe_float(entry_meta.get("sma_spread_pct")),
                    entry_window_bucket=entry_meta.get("entry_window_bucket"),
                    entry_signal_strength=_safe_float(entry_meta.get("signal_strength")),
                    realized_slippage_estimate=_safe_float(entry_meta.get("realized_slippage_estimate")),
                )
                set_last_entry_signal(
                    conn,
                    _safe_float(entry_meta.get("signal_strength")),
                    entry_meta.get("entry_signal_side"),
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

                    hold_seconds = None
                    if position_before.entry_ts:
                        entry_dt = parse_ts(position_before.entry_ts)
                        exit_dt = parse_ts(fill_ts)
                        if entry_dt is not None and exit_dt is not None:
                            hold_seconds = (exit_dt - entry_dt).total_seconds()

                    record_closed_trade(
                        conn,
                        symbol,
                        position_before.side,
                        position_before.entry_ts,
                        fill_ts,
                        float(position_before.entry_price),
                        exit_price,
                        float(realized_qty),
                        float(pnl),
                        return_pct,
                        None,
                        order.notes,
                        None,
                        entry_bar_ts=position_before.entry_bar_ts,
                        exit_bar_ts=fill_ts,
                        entry_signal_side=position_before.entry_signal_side,
                        entry_adx=position_before.entry_adx,
                        entry_atr_pct=position_before.entry_atr_pct,
                        entry_volume_ratio=position_before.entry_volume_ratio,
                        entry_sma_spread_pct=position_before.entry_sma_spread_pct,
                        entry_window_bucket=position_before.entry_window_bucket,
                        hold_seconds=hold_seconds,
                        realized_slippage_estimate=position_before.realized_slippage_estimate,
                    )

                    state_after_trade = get_state(conn)
                    if pnl > 0:
                        set_consecutive_losses(conn, 0)
                    else:
                        set_consecutive_losses(conn, state_after_trade.consecutive_losses + 1)

            sync_position_state_with_broker(conn, trading, symbol, logger)
            set_last_trade(conn, fill_ts)
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
    sizing_mode = os.getenv("POSITION_SIZING_MODE", "fixed").strip().lower() or "fixed"
    reversal_signal_strength_min = float(os.getenv("REVERSAL_SIGNAL_STRENGTH_MIN", "0"))
    allow_overnight_holding = _env_flag("ALLOW_OVERNIGHT_HOLDING", False)
    flatten_before_close_minutes = max(0, int(os.getenv("FLATTEN_BEFORE_CLOSE_MINUTES", "5")))
    is_crypto = _env_flag("IS_CRYPTO", False)

    ts = utc_iso_now()
    logger.info(
        f"Run start ts={ts} symbol={symbol} tf={timeframe_minutes}m qty={qty} "
        f"strategy_version={strategy_version} stale_bar_check={'enabled' if stale_bar_checks_enabled else 'disabled'} "
        f"max_bar_age_seconds={max_bar_age_seconds} startup_delay={startup_delay_seconds}s sizing_mode={sizing_mode} "
        f"allow_overnight_holding={allow_overnight_holding} flatten_before_close_minutes={flatten_before_close_minutes}"
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
            "position_sizing_mode": sizing_mode,
            "allow_overnight_holding": allow_overnight_holding,
            "flatten_before_close_minutes": flatten_before_close_minutes,
        },
    )

    if startup_delay_seconds > 0:
        logger.info(f"Sleeping {startup_delay_seconds}s before broker and data checks to let the latest bar settle.")
        time.sleep(startup_delay_seconds)

    reconcile_submitted_orders(conn, trading, symbol, logger)

    equity, cash = get_account_snapshot(trading)
    state = get_state(conn, equity)
    pos_state, pos_qty = sync_position_state_with_broker(conn, trading, symbol, logger)
    session_exit = evaluate_session_exit(
        pos_qty,
        pos_state.entry_ts,
        allow_overnight_holding=allow_overnight_holding,
        flatten_before_close_minutes=flatten_before_close_minutes,
    )

    used_et_market_fallback = False
    if is_crypto:
        market_open = True
    else:
        market_open = is_market_open(trading)
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
        pending_orders = has_pending_orders(conn, symbol)
        if session_exit.should_exit and not pending_orders and pos_qty != 0:
            forced_action = "SELL" if pos_qty > 0 else "BUY"
            forced_intent = "exit"
            forced_note = session_exit.reason
            executed_qty = int(abs(float(pos_qty)))
            order_info = place_market_order(trading, symbol, forced_action.lower(), executed_qty)
            status = normalize_order_status(getattr(order_info, "status", None))
            filled_avg = _safe_float(getattr(order_info, "filled_avg_price", None))
            filled_qty = _safe_float(getattr(order_info, "filled_qty", None))
            filled_at = _to_iso(getattr(order_info, "filled_at", None))
            record_run(
                conn,
                ts,
                symbol,
                None,
                None,
                None,
                "HOLD",
                forced_action,
                pos_qty,
                equity,
                cash,
                forced_note,
                strategy_version=strategy_version,
            )
            record_order_submission(
                conn,
                ts,
                symbol,
                forced_action.lower(),
                float(executed_qty),
                str(order_info.id),
                status,
                filled_avg,
                filled_qty,
                forced_intent,
                semantic_action_type(pos_qty, forced_action),
                forced_note,
                pos_qty,
                filled_at,
                decision_signal="HOLD",
            )
            logger.warning(f"No bars returned, but submitted forced session exit due to {forced_note}.")
            emit_event(
                conn,
                ts,
                "WARN",
                "session_exit_without_bars",
                symbol,
                "No bars returned; forced session exit order was still submitted.",
                {"reason": forced_note, "position_qty": pos_qty, "order_id": str(order_info.id)},
            )
            logger.info("Run complete.")
            return
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
    logger.info(
        f"bars_loaded count={len(bars)} first_bar_ts={bars.index[0].isoformat()} last_bar_ts={bars.index[-1].isoformat()}"
    )

    default_windows = ((940, 1130), (1400, 1545))
    cfg = StrategyConfig(
        sma_fast=int(os.getenv("SMA_FAST", "20")),
        sma_slow=int(os.getenv("SMA_SLOW", "50")),
        adx_period=int(os.getenv("ADX_PERIOD", "14")),
        adx_threshold=float(os.getenv("ADX_THRESHOLD", "20")),
        atr_period=int(os.getenv("ATR_PERIOD", "14")),
        atr_max_pct=float(os.getenv("ATR_MAX_PCT", "0.0045")),
        volume_ma_period=int(os.getenv("VOLUME_MA_PERIOD", "20")),
        volume_min_multiplier=float(os.getenv("VOLUME_MIN_MULTIPLIER", os.getenv("VOLUME_THRESHOLD_MULTIPLIER", "0.8"))),
        timeframe_minutes=timeframe_minutes,
        trail_atr_multiplier=float(os.getenv("TRAIL_ATR_MULTIPLIER", "1.5")),
        max_bars_in_trade=int(os.getenv("MAX_BARS_IN_TRADE", "12")),
        long_adx_threshold=_env_optional_float("LONG_ADX_THRESHOLD"),
        short_adx_threshold=_env_optional_float("SHORT_ADX_THRESHOLD"),
        long_atr_max_pct=_env_optional_float("LONG_ATR_MAX_PCT"),
        short_atr_max_pct=_env_optional_float("SHORT_ATR_MAX_PCT"),
        long_volume_min_multiplier=_env_optional_float("LONG_VOLUME_MIN_MULTIPLIER"),
        short_volume_min_multiplier=_env_optional_float("SHORT_VOLUME_MIN_MULTIPLIER"),
        min_sma_spread_atr_mult=float(os.getenv("MIN_SMA_SPREAD_ATR_MULT", "0")),
        min_sma_spread_pct=float(os.getenv("MIN_SMA_SPREAD_PCT", "0")),
        use_vwap_filter=_env_flag("USE_VWAP_FILTER", False),
        min_price_distance_from_vwap_pct=float(os.getenv("MIN_PRICE_DISTANCE_FROM_VWAP_PCT", "0")),
        use_session_open_filter=_env_flag("USE_SESSION_OPEN_FILTER", False),
        min_price_distance_from_open_pct=float(os.getenv("MIN_PRICE_DISTANCE_FROM_OPEN_PCT", "0")),
        entry_windows=parse_entry_windows(os.getenv("ENTRY_WINDOWS"), default_windows),
        long_entry_windows=parse_entry_windows(os.getenv("LONG_ENTRY_WINDOWS"), default_windows),
        short_entry_windows=parse_entry_windows(os.getenv("SHORT_ENTRY_WINDOWS"), default_windows),
        long_trail_atr_multiplier=_env_optional_float("LONG_TRAIL_ATR_MULTIPLIER"),
        short_trail_atr_multiplier=_env_optional_float("SHORT_TRAIL_ATR_MULTIPLIER"),
        long_max_bars_in_trade=_env_optional_int("LONG_MAX_BARS_IN_TRADE"),
        short_max_bars_in_trade=_env_optional_int("SHORT_MAX_BARS_IN_TRADE"),
        enable_breakeven_stop=_env_flag("ENABLE_BREAKEVEN_STOP", False),
        breakeven_after_atr_multiple=float(os.getenv("BREAKEVEN_AFTER_ATR_MULTIPLE", "1.0")),
        enable_profit_lock=_env_flag("ENABLE_PROFIT_LOCK", False),
        profit_lock_after_atr_multiple=float(os.getenv("PROFIT_LOCK_AFTER_ATR_MULTIPLE", "2.0")),
        profit_lock_atr_multiple=float(os.getenv("PROFIT_LOCK_ATR_MULTIPLE", "0.5")),
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
    bar_close_ts = metrics.get("bar_close_ts")
    signal_strength = metrics.get("signal_strength")

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
            entry_bar_ts=pos_state.entry_bar_ts,
            entry_signal_side=pos_state.entry_signal_side,
            entry_adx=pos_state.entry_adx,
            entry_atr_pct=pos_state.entry_atr_pct,
            entry_volume_ratio=pos_state.entry_volume_ratio,
            entry_sma_spread_pct=pos_state.entry_sma_spread_pct,
            entry_window_bucket=pos_state.entry_window_bucket,
            entry_signal_strength=pos_state.entry_signal_strength,
            realized_slippage_estimate=pos_state.realized_slippage_estimate,
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
            entry_bar_ts=pos_state.entry_bar_ts,
            entry_signal_side=pos_state.entry_signal_side,
            entry_adx=pos_state.entry_adx,
            entry_atr_pct=pos_state.entry_atr_pct,
            entry_volume_ratio=pos_state.entry_volume_ratio,
            entry_sma_spread_pct=pos_state.entry_sma_spread_pct,
            entry_window_bucket=pos_state.entry_window_bucket,
            entry_signal_strength=pos_state.entry_signal_strength,
            realized_slippage_estimate=pos_state.realized_slippage_estimate,
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
        trail_atr_multiplier = cfg.trail_atr_multiplier_for("long")
        max_bars_in_trade = cfg.max_bars_in_trade_for("long")
        hard_stop_atr_mult = float(os.getenv("HARD_STOP_ATR_MULT", "0"))

        if (
            hard_stop_atr_mult > 0
            and last_price is not None
            and v_atr is not None
            and pos_state.entry_price is not None
            and float(last_price) < float(pos_state.entry_price) - (hard_stop_atr_mult * float(v_atr))
        ):
            desired_action = "SELL"
            exit_reason = f"long_hard_stop_hit({last_price}<{float(pos_state.entry_price) - hard_stop_atr_mult * float(v_atr):.4f})"

        if desired_action == "HOLD" and last_price is not None and v_atr is not None and pos_state.highest_price is not None:
            trailing_stop = float(pos_state.highest_price) - (trail_atr_multiplier * float(v_atr))
            if cfg.enable_breakeven_stop and pos_state.entry_price is not None and float(last_price) >= (
                float(pos_state.entry_price) + (cfg.breakeven_after_atr_multiple * float(v_atr))
            ):
                trailing_stop = max(trailing_stop, float(pos_state.entry_price))
            if cfg.enable_profit_lock and pos_state.entry_price is not None and float(last_price) >= (
                float(pos_state.entry_price) + (cfg.profit_lock_after_atr_multiple * float(v_atr))
            ):
                trailing_stop = max(trailing_stop, float(pos_state.entry_price) + (cfg.profit_lock_atr_multiple * float(v_atr)))
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

        if desired_action == "HOLD" and signal == "SHORT" and float(signal_strength or 0.0) >= reversal_signal_strength_min:
            desired_action = "SELL"
            exit_reason = "long_trend_reversal"

        if exit_reason:
            note_parts.append(exit_reason)
            intent = "exit"
    elif pos_qty < 0:
        exit_reason = None
        trail_atr_multiplier = cfg.trail_atr_multiplier_for("short")
        max_bars_in_trade = cfg.max_bars_in_trade_for("short")
        hard_stop_atr_mult = float(os.getenv("HARD_STOP_ATR_MULT", "0"))

        if (
            hard_stop_atr_mult > 0
            and last_price is not None
            and v_atr is not None
            and pos_state.entry_price is not None
            and float(last_price) > float(pos_state.entry_price) + (hard_stop_atr_mult * float(v_atr))
        ):
            desired_action = "BUY"
            exit_reason = f"short_hard_stop_hit({last_price}>{float(pos_state.entry_price) + hard_stop_atr_mult * float(v_atr):.4f})"

        if desired_action == "HOLD" and last_price is not None and v_atr is not None and pos_state.lowest_price is not None:
            trailing_stop = float(pos_state.lowest_price) + (trail_atr_multiplier * float(v_atr))
            if cfg.enable_breakeven_stop and pos_state.entry_price is not None and float(last_price) <= (
                float(pos_state.entry_price) - (cfg.breakeven_after_atr_multiple * float(v_atr))
            ):
                trailing_stop = min(trailing_stop, float(pos_state.entry_price))
            if cfg.enable_profit_lock and pos_state.entry_price is not None and float(last_price) <= (
                float(pos_state.entry_price) - (cfg.profit_lock_after_atr_multiple * float(v_atr))
            ):
                trailing_stop = min(trailing_stop, float(pos_state.entry_price) - (cfg.profit_lock_atr_multiple * float(v_atr)))
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

        if desired_action == "HOLD" and signal == "LONG" and float(signal_strength or 0.0) >= reversal_signal_strength_min:
            desired_action = "BUY"
            exit_reason = "short_trend_reversal"

        if exit_reason:
            note_parts.append(exit_reason)
            intent = "exit"

    if session_exit.should_exit and desired_action == "HOLD":
        desired_action = "SELL" if pos_qty > 0 else "BUY"
        intent = "exit"
        note_parts.append(session_exit.reason)
        emit_event(
            conn,
            ts,
            "WARN",
            "session_exit_triggered",
            symbol,
            "Position exit triggered by session policy.",
            {"reason": session_exit.reason, "position_qty": pos_qty, "entry_ts": pos_state.entry_ts},
        )

    action = desired_action
    pending_orders = has_pending_orders(conn, symbol)
    order_qty = qty

    entering_long = pos_qty == 0 and desired_action == "BUY" and signal == "LONG"
    entering_short = pos_qty == 0 and desired_action == "SELL" and signal == "SHORT"
    if entering_long or entering_short:
        order_qty = compute_entry_qty(
            sizing_mode,
            qty,
            equity,
            _safe_float(last_price),
            _safe_float(v_atr),
            max_position_notional_pct,
            target_position_notional_pct=float(os.getenv("TARGET_POSITION_NOTIONAL_PCT", str(max_position_notional_pct))),
            atr_risk_per_trade_pct=float(os.getenv("ATR_RISK_PER_TRADE_PCT", "0.0025")),
            fractional=is_crypto,
        )
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
            last_bar_ts=bar_close_ts or bar_ts,
            position_notional=(float(last_price) * order_qty) if last_price is not None else None,
            stale_bar_timestamp_basis="bar_close" if bar_close_ts else "bar_open",
        )
        logger.info(
            "stale_bar_check "
            f"current_runtime_timestamp={risk_eval.stale_bar_details.get('current_runtime_timestamp')} "
            f"latest_bar_timestamp={risk_eval.stale_bar_details.get('latest_bar_timestamp')} "
            f"computed_bar_age_seconds={risk_eval.stale_bar_details.get('computed_bar_age_seconds')} "
            f"allowed_max_age={risk_eval.stale_bar_details.get('allowed_max_age')} "
            f"timestamp_basis={risk_eval.stale_bar_details.get('timestamp_basis')}"
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
                {
                    "risk_reasons": risk_eval.reasons,
                    "signal": signal,
                    "stale_bar_details": risk_eval.stale_bar_details,
                },
            )

        if order_qty <= 0:
            action = "HOLD"
            note_parts.append("position_sizing_blocked")

        since = bars_since(state.last_trade_ts, bars2)
        if since is not None and since < cooldown_bars:
            if should_allow_reentry_during_cooldown(
                state,
                signal,
                _safe_float(signal_strength),
                require_signal_strength_improvement=_env_flag("REENTRY_REQUIRES_SIGNAL_STRENGTH_IMPROVEMENT", False),
                min_signal_strength_delta=float(os.getenv("REENTRY_MIN_SIGNAL_STRENGTH_DELTA", "0.0")),
            ):
                note_parts.append("cooldown_overridden_stronger_signal")
            else:
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
        f"signal={signal} signal_strength={signal_strength} pos_qty={pos_qty} desired_action={desired_action} "
        f"action={action} action_type={action_type} trades_today={state.trades_today} "
        f"consecutive_losses={state.consecutive_losses} note={note}"
    )

    order_info = None
    executed_qty = None
    order_side_for_log = None
    order_intent = intent
    entry_metrics_payload = _entry_metrics_payload(signal, metrics) if intent == "entry" else None

    if action == "BUY":
        if pos_qty < 0:
            cover_qty = abs(float(pos_qty)) if is_crypto else int(abs(float(pos_qty)))
            if cover_qty > 0:
                order_info = place_market_order(trading, symbol, "buy", cover_qty)
                executed_qty = cover_qty
                order_side_for_log = "buy"
                order_intent = "exit"
        elif pos_qty == 0 and order_qty > 0:
            order_info = place_market_order(trading, symbol, "buy", order_qty)
            executed_qty = order_qty
            order_side_for_log = "buy"
            order_intent = "entry"
    elif action == "SELL":
        if pos_qty > 0:
            sell_qty = float(pos_qty) if is_crypto else int(float(pos_qty))
            if sell_qty > 0:
                order_info = place_market_order(trading, symbol, "sell", sell_qty)
                executed_qty = sell_qty
                order_side_for_log = "sell"
                order_intent = "exit"
        elif pos_qty == 0 and order_qty > 0:
            order_info = place_market_order(trading, symbol, "sell", order_qty)
            executed_qty = order_qty
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
            decision_signal=signal,
            entry_metrics_json=json.dumps(entry_metrics_payload, sort_keys=True) if entry_metrics_payload is not None else None,
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
                "signal_strength": signal_strength,
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
