import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")

def make_clients() -> tuple[TradingClient, StockHistoricalDataClient]:
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    paper = _env_bool("ALPACA_PAPER", True)

    trading = TradingClient(api_key=key, secret_key=secret, paper=paper)
    data = StockHistoricalDataClient(api_key=key, secret_key=secret)
    return trading, data

def get_recent_bars(data_client: StockHistoricalDataClient, symbol: str, timeframe_minutes: int, limit: int = 200) -> pd.DataFrame:
    """
    Fetch recent bars. For SMA(50) on 5-min bars, 200 bars is plenty for warmup.
    """
    tf = TimeFrame(timeframe_minutes, TimeFrameUnit.Minute)

    # always use IEX feed (works on free accounts and avoids SIP-only errors)
    feed = DataFeed.IEX

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)  # generous window to ensure enough bars even with market closures

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=tf,
        start=start,
        end=end,
        limit=limit,
        adjustment="raw",
        feed=feed,
    )
    try:
        bars = data_client.get_stock_bars(req).df
    except APIError as e:
        # If the account does not have access to the requested feed (e.g. SIP),
        # surface a warning and return an empty frame so the bot can fall back
        # to the "no_bars" path instead of crashing.
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

def place_market_order(trading: TradingClient, symbol: str, side: str, qty: int):
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")

    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    return trading.submit_order(order_data=order)

def get_order(trading: TradingClient, order_id: str):
    return trading.get_order_by_id(order_id)