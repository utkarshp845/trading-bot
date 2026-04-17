# Current Strategy Audit

Date: 2026-04-14

References:
- `reports/research_latest.md`
- `README.md`
- `bot/strategy_ma.py`
- `bot/main.py`
- `bot/research.py`

## Executive Summary

This bot is currently a research-grade intraday trend-following system for Alpaca paper trading, not a real-money-ready strategy.

The strongest evidence in the repo is mixed at best:
- Full-sample replay is slightly negative: 177 trades, net P&L `-3.005`, profit factor `0.975`, win rate `35.59%`, expectancy `-0.01698`.
- Performance is uneven by regime. Morning trades are positive, afternoon trades are decisively negative.
- Shorts slightly outperform longs, while long trades are net negative.

Operationally, the bot is in better shape than the strategy edge. Broker reconciliation, stale-bar blocking, pending-order checks, and intraday flatten behavior are all moving in the right direction. The strategy itself still does not show a strong enough, stable enough edge to justify going live in its current all-day configuration.

Verdict: no-go for real money in current form.

## Strategy Description

The current strategy is a 5-minute SMA trend-following setup centered on the latest bar from Alpaca data.

Core entry logic:
- Trend signal comes from `sma_fast > sma_slow` for long and `sma_fast < sma_slow` for short.
- Entries are gated by ADX threshold, ATR percentage cap, volume relative to rolling volume average, and entry time windows in ET.
- Optional filters exist for VWAP distance, session-open distance, SMA spread thresholds, and side-specific thresholds.

Current defaults from the repo:
- Symbol: `SPY`
- Timeframe: `5m`
- Sizing mode: `fixed`
- Base quantity: `1`
- Entry windows: `0930-1600` for both long and short

Exit logic:
- ATR-based trailing stop
- Time stop after max bars in trade, but only when price has not improved beyond entry
- Trend reversal exit when the opposite signal is strong enough
- Session handling now includes intraday flatten behavior and overnight protection via `ALLOW_OVERNIGHT_HOLDING=false` and `FLATTEN_BEFORE_CLOSE_MINUTES=5`

Replay model:
- Uses fixed per-share slippage from env, currently `0.01`
- Uses commission from env, currently `0`
- Does not model partial fills, queue position, order rejection patterns, or spread widening in stressed periods

## Current Config Snapshot

Important current settings from `.env`:
- `SYMBOL=SPY`
- `TIMEFRAME_MINUTES=5`
- `QTY=1`
- `POSITION_SIZING_MODE=fixed`
- `MAX_TRADES_PER_DAY=5`
- `COOLDOWN_BARS=2`
- `MAX_DAILY_DRAWDOWN_PCT=0.01`
- `MAX_CONSECUTIVE_LOSSES=3`
- `ENABLE_STALE_BAR_CHECK=true`
- `MAX_BAR_AGE_SECONDS=900`
- `MAX_POSITION_NOTIONAL_PCT=0.02`
- `ALLOW_ET_MARKET_CLOCK_FALLBACK=true`
- `ALLOW_OVERNIGHT_HOLDING=false`
- `FLATTEN_BEFORE_CLOSE_MINUTES=5`

Operational note:
- Live assumptions are still centered on `SPY` and integer share quantity.
- That is workable for paper and research, but it is not a clean fit for a `$100` starting account.

## Evidence From Repo Outputs

Latest research summary from `reports/research_latest.md`:
- Trades: `177`
- Net P&L: `-3.004999999996812`
- Profit factor: `0.975182722880647`
- Win rate: `0.3559322033898305`
- Avg trade / expectancy: `-0.016977401129925494`
- Max drawdown: `-0.00025362686922957874`
- Trades per day: `3.2181818181818183`

Walk-forward slices:
- Window 1 test: net `-7.655`, PF `0.593`, trades `27`
- Window 2 test: net `-2.79`, PF `0.867`, trades `25`
- Window 3 test: net `11.285`, PF `1.625`, trades `23`

By side:
- Short: 105 trades, net `2.115`, avg `0.0201`, PF `1.028`
- Long: 72 trades, net `-5.12`, avg `-0.0711`, PF `0.887`

By session:
- Morning: 116 trades, net `16.675`, avg `0.14375`, PF `1.222`
- Afternoon: 61 trades, net `-19.68`, avg `-0.3226`, PF `0.571`

By hour:
- 10 ET: strong, net `19.62`, PF `1.835`
- 13 ET: positive, but only 8 trades
- 15 ET: worst bucket, net `-22.09`, PF `0.242`

Condition notes:
- Best average holds are longer holds, especially `60-120m` and `120m+`
- Worst bucket is short holds under `30m`
- Weak volume ratio bucket `0.8-1.0` is negative

## Strengths

- Signal generation and replay tooling are already structured and readable.
- Strategy code cleanly separates signal construction, risk evaluation, and replay execution.
- Live loop has meaningful operational safeguards:
  - stale-bar protection
  - pending-order blocking
  - broker order sync
  - broker position reconciliation
  - intraday flatten / overnight handling
- Research workflow already includes walk-forward reporting instead of only in-sample summary.

## Weaknesses And Risks

- The replay is slightly negative overall. That is the most important fact in the repo.
- Afternoon performance is materially weak and drags down the otherwise better morning behavior.
- Long performance is negative overall, which is a problem for any future small-account live profile that will likely need to be long-only.
- Current live assumptions are not realistic for a `$100` account:
  - `SPY` as the default live symbol
  - integer share sizing
  - current structure still shaped around a larger paper account
- Replay fill assumptions are simplified:
  - fixed slippage
  - no spread-aware logic
  - no partial fills
  - no live microstructure effects
- The strategy is likely overtrading weak regimes instead of concentrating on its best regime.

## Real-Money Readiness Assessment

Verdict: not ready in current form.

Why:
- Paper readiness is mainly about whether the bot can run safely and consistently.
- Real-money readiness requires a strategy edge that survives slippage, weaker fills, and small-account constraints.
- This repo shows improving operational safety, but the strategy edge is not yet convincing enough in the current all-day version.
- A `$100` live account is stricter than a `$100,000` paper account. Small trade sizes magnify slippage, symbol-fit issues, and structural constraints around shorting and whole-share sizing.

The bot can continue as a paper-trading and research platform. It should not be treated as a ready production strategy yet.

## Recommended Immediate Improvement

First strategy change to test next: restrict new entries to the stronger morning regime.

Reason:
- Morning session results are clearly positive in the current replay.
- Afternoon results are clearly negative.
- The cleanest improvement path is to remove the weak regime before adding more indicators or complexity.

Recommended first test:
- Keep exits active all day so open trades can still be managed safely.
- Restrict new entries to `0930-1130 ET`.
- Evaluate the result again in replay and walk-forward before making any live-account decisions.
