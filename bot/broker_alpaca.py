import math
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _as_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_order_status(status) -> str | None:
    if status is None:
        return None

    raw = getattr(status, "value", status)
    text = str(raw).strip()
    if not text:
        return None
    if "." in text:
        text = text.split(".")[-1]
    return text.upper()

def make_clients() -> tuple[TradingClient, StockHistoricalDataClient | CryptoHistoricalDataClient]:
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    paper = _env_bool("ALPACA_PAPER", True)
    is_crypto = _env_bool("IS_CRYPTO", False)

    trading = TradingClient(api_key=key, secret_key=secret, paper=paper)
    if is_crypto:
        data = CryptoHistoricalDataClient(api_key=key, secret_key=secret)
    else:
        data = StockHistoricalDataClient(api_key=key, secret_key=secret)
    return trading, data

def get_historical_bars(
    data_client: StockHistoricalDataClient | CryptoHistoricalDataClient,
    symbol: str,
    timeframe_minutes: int,
    start: datetime,
    end: datetime,
    limit: int | None = None,
) -> pd.DataFrame:
    tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)
    is_crypto = isinstance(data_client, CryptoHistoricalDataClient)

    try:
        if is_crypto:
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                limit=limit,
            )
            bars = data_client.get_crypto_bars(req).df
        else:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                limit=limit,
                adjustment="raw",
                feed=DataFeed.IEX,
            )
            bars = data_client.get_stock_bars(req).df
    except APIError as e:
        msg = str(e)
        if "401" in msg or "Authorization" in msg:
            print("[alpaca] 401 Unauthorized — check ALPACA_API_KEY and ALPACA_SECRET_KEY in .env")
        else:
            print(f"[alpaca] failed to fetch bars: {e}")
        return pd.DataFrame()
    if bars.empty:
        return bars

    # bars index is multi-index: (symbol, timestamp)
    if isinstance(bars.index, pd.MultiIndex):
        bars = bars.reset_index()
        bars = bars[bars["symbol"] == symbol].copy()
        bars = bars.set_index("timestamp")

    bars = bars.sort_index()
    return bars


def get_recent_bars(data_client: StockHistoricalDataClient | CryptoHistoricalDataClient, symbol: str, timeframe_minutes: int, limit: int = 200) -> pd.DataFrame:
    """
    Fetch recent bars, looking back far enough to actually cover `limit` bars.

    Crypto trades around the clock, but equities only print bars for ~6.5
    session hours on ~5 of 7 days, so the calendar window must be much wider
    than `limit * timeframe` or higher-timeframe regime indicators never warm up.
    """
    end = datetime.now(timezone.utc)
    minutes_needed = timeframe_minutes * limit
    if isinstance(data_client, CryptoHistoricalDataClient):
        lookback_days = math.ceil(minutes_needed / (60 * 24)) + 2
    else:
        session_days = minutes_needed / (60 * 6.5)
        lookback_days = math.ceil(session_days * 7 / 5) + 5
    start = end - timedelta(days=max(7, lookback_days))
    bars = get_historical_bars(data_client, symbol, timeframe_minutes, start=start, end=end, limit=None)
    if bars.empty:
        return bars
    return bars.sort_index().tail(limit)

def get_position_qty(trading: TradingClient, symbol: str) -> float:
    try:
        pos = trading.get_open_position(symbol)
        return float(pos.qty)
    except Exception:
        return 0.0

def get_account_snapshot(trading: TradingClient) -> tuple[float | None, float | None]:
    try:
        acct = trading.get_account()
        equity = float(acct.equity)
        cash = float(acct.cash)
        return equity, cash
    except Exception:
        return None, None

def place_market_order(trading: TradingClient, symbol: str, side: str, qty: float):
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")

    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.GTC if _env_bool("IS_CRYPTO", False) else TimeInForce.DAY,
    )
    return trading.submit_order(order_data=order)

def get_order(trading: TradingClient, order_id: str):
    return trading.get_order_by_id(order_id)


def get_position_snapshot(trading: TradingClient, symbol: str) -> dict | None:
    try:
        pos = trading.get_open_position(symbol)
    except Exception:
        return None

    qty = _as_float(getattr(pos, "qty", None))
    if qty is None or qty == 0:
        return None

    avg_entry_price = _as_float(getattr(pos, "avg_entry_price", None))
    current_price = _as_float(getattr(pos, "current_price", None))
    return {
        "symbol": symbol,
        "qty": qty,
        "side": "long" if qty > 0 else "short",
        "avg_entry_price": avg_entry_price,
        "current_price": current_price,
    }


def is_market_open(trading: TradingClient) -> bool | None:
    try:
        clock = trading.get_clock()
        return bool(getattr(clock, "is_open", None))
    except Exception:
        return None
