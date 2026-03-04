from dataclasses import dataclass
import pandas as pd

@dataclass
class StrategyConfig:
    sma_fast: int
    sma_slow: int

def compute_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    out["sma_fast"] = out["close"].rolling(cfg.sma_fast).mean()
    out["sma_slow"] = out["close"].rolling(cfg.sma_slow).mean()
    return out

def generate_signal(df: pd.DataFrame) -> tuple[str, float | None, float | None, float | None]:
    """
    Returns: (signal, last_price, sma_fast, sma_slow)
      signal in {"BUY", "SELL", "HOLD"}
    """
    if df.empty:
        return ("HOLD", None, None, None)

    last = df.iloc[-1]
    last_price = float(last["close"])
    sma_fast = last.get("sma_fast")
    sma_slow = last.get("sma_slow")

    if pd.isna(sma_fast) or pd.isna(sma_slow):
        return ("HOLD", last_price, None, None)

    sma_fast = float(sma_fast)
    sma_slow = float(sma_slow)

    if sma_fast > sma_slow:
        return ("BUY", last_price, sma_fast, sma_slow)
    if sma_fast < sma_slow:
        return ("SELL", last_price, sma_fast, sma_slow)

    return ("HOLD", last_price, sma_fast, sma_slow)