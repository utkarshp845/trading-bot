# Trading Bot Audit

Date: 2026-03-24

## Executive Summary

This repository is currently a single-symbol intraday trading bot built around Alpaca paper trading, a moving-average trend strategy, SQLite-backed state, CSV logging, and a daily Markdown reporting script. The bot is materially stronger than it was at the start of this audit: execution tracking is now fill-aware, reporting is more trustworthy, local/runtime paths are more robust, and basic risk guardrails exist.

It is still best classified as a paper-trading research bot rather than a production-ready alpha engine. The next gains are most likely to come from better research automation, richer trade analytics, multi-symbol selection, and stricter execution validation rather than from simply turning up size.

## Repo Inventory

Top-level layout:

- `bot/`: trading logic, broker integration, persistence, risk, and reporting code
- `data/`: SQLite database storage
- `logs/`: bot logs, equity snapshots, and trade submission logs
- `reports/`: generated daily reports
- `Dockerfile`: container image definition
- `docker-compose.yml`: container runtime wiring
- `.env`: local runtime config with placeholder keys
- `.env.example`: safe env template

Primary bot modules:

- [`bot/main.py`](/C:/Users/UtkarshPandey/trading-bot/bot/main.py): runtime entrypoint, signal evaluation, risk gating, order submission, reconciliation, and logging
- [`bot/strategy_ma.py`](/C:/Users/UtkarshPandey/trading-bot/bot/strategy_ma.py): indicator calculation and signal generation
- [`bot/broker_alpaca.py`](/C:/Users/UtkarshPandey/trading-bot/bot/broker_alpaca.py): Alpaca market data and trading client helpers
- [`bot/store.py`](/C:/Users/UtkarshPandey/trading-bot/bot/store.py): SQLite schema, migrations, position state, order state, and closed-trade storage
- [`bot/risk.py`](/C:/Users/UtkarshPandey/trading-bot/bot/risk.py): entry-risk evaluation and timestamp utilities
- [`bot/report_daily.py`](/C:/Users/UtkarshPandey/trading-bot/bot/report_daily.py): daily report generation
- [`bot/metrics.py`](/C:/Users/UtkarshPandey/trading-bot/bot/metrics.py): trade-summary and drawdown calculations
- [`bot/paths.py`](/C:/Users/UtkarshPandey/trading-bot/bot/paths.py): runtime path normalization for Docker and local execution
- [`bot/io_log.py`](/C:/Users/UtkarshPandey/trading-bot/bot/io_log.py): logger setup

## Current Strategy

The strategy is a single-symbol intraday trend-following system using:

- Fast SMA: `20`
- Slow SMA: `50`
- ADX period: `14`
- ADX threshold: `25`
- ATR period: `14`
- ATR max percent of price: `0.0035`
- Volume moving average period: `20`
- Trailing stop: `1.5 x ATR`
- Max bars in trade: `12`
- Cooldown: `2` bars
- Max trades per day: `5`

Source of truth:

- [`bot/strategy_ma.py`](/C:/Users/UtkarshPandey/trading-bot/bot/strategy_ma.py)
- [`.env`](/C:/Users/UtkarshPandey/trading-bot/.env)

Entry logic:

- Long when `SMA_FAST > SMA_SLOW`
- Short when `SMA_FAST < SMA_SLOW`
- ADX must be above threshold
- ATR as a percent of price must be below threshold
- Volume must exceed `0.8 x volume_ma`
- Entry must fall inside allowed ET windows:
  - `09:40` to `11:30`
  - `14:00` to `15:45`

Exit logic:

- ATR trailing stop
- Time stop if the trade stagnates past `MAX_BARS_IN_TRADE`
- Trend reversal exit when the opposite signal appears

Assessment:

- The strategy is coherent and safer than a naive MA crossover.
- It is still narrow in scope and likely too simple to support meaningful scaling without more research.
- It does not yet include regime detection, event/news filters, slippage modeling, portfolio construction, or cross-symbol ranking.

## Tickers and Market Scope

Configured ticker:

- `SYMBOL=SPY`

Current market scope:

- One ticker only
- One timeframe only: `5` minute bars
- One broker/data source: Alpaca
- One market data feed path in code: IEX

Assessment:

- Using `SPY` is a reasonable starting point for a liquid paper-trading prototype.
- Staying locked to one symbol severely limits opportunity discovery and makes it difficult to distinguish strategy edge from symbol-specific behavior.
- A future profit-focused version should likely scan a watchlist and rank candidates rather than force trades only in SPY.

## Data and Storage

Data sources:

- Alpaca historical stock bars via `StockHistoricalDataClient`
- Alpaca position/account/order state via `TradingClient`

Local persistence:

- SQLite database at `data/bot.db`
- Equity snapshots at `logs/equity.csv`
- Trade submission log at `logs/trades.csv`
- Application logs at `logs/bot.log`
- Generated reports under `reports/`

Current persistence model:

- `runs`: each bot decision cycle
- `orders`: broker order submission and reconciliation state
- `position_state`: local trailing-stop state
- `state`: daily counters and loss streaks
- `closed_trades`: realized trade outcomes

Assessment:

- The repo now has a much better measurement foundation than before.
- Closed-trade analytics are now possible, but historical legacy data is limited and not yet rich enough to support serious expectancy analysis.
- There is still no full backtest dataset, no research dataset store, and no feature history store for systematic optimization.

## Execution and Broker Audit

What is now improved:

- Orders are no longer treated as trades merely because they were submitted.
- The bot now refreshes tracked orders against Alpaca and processes fills before updating trade counters and realized P&L.
- Position bootstrap now uses broker position state and average entry price when available.
- Pending orders suppress duplicate in-flight submissions.

What still needs work:

- There is no partial-fill-specific execution model beyond basic filled quantity handling.
- There is no explicit order timeout or cancel/replace workflow.
- There is no reconciliation loop outside normal bot runs.
- There is no persistent broker/account mismatch alerting layer.

Assessment:

- Execution correctness is significantly better than it was.
- The next step is continuous reconciliation and exception handling, not more strategy complexity.

## Risk Controls

Current risk controls:

- Market-hours gate
- Cooldown bars between entries
- Maximum trades per day
- ATR volatility filter
- Entry time windows
- Max daily drawdown percent
- Max consecutive losses
- Max position notional percent of equity
- Stale-bar protection
- Trailing stop and time stop

Assessment:

- These are good baseline protections for paper trading.
- There is still no portfolio-level exposure control because the bot only trades one symbol.
- There is no broker kill switch, no holiday/early-close calendar logic, and no alerting when risk rules trigger repeatedly.

## Reporting and Analytics

Current reporting:

- Daily Markdown report generated by [`bot/report_daily.py`](/C:/Users/UtkarshPandey/trading-bot/bot/report_daily.py)
- Supports both legacy headerless CSVs and newer headered CSVs
- Summarizes:
  - Daily and total equity P&L
  - Max drawdown
  - Order counts
  - Closed-trade count
  - Win rate
  - Profit factor
  - Average trade / win / loss

Assessment:

- Reporting is much more trustworthy now than before.
- It is still mostly operational reporting, not research-grade analysis.
- Missing high-value analytics include:
  - MAE/MFE per trade
  - Hold-time distribution
  - P&L by hour of day
  - P&L by signal subtype
  - Slippage and fill-quality analysis
  - Rolling Sharpe / drawdown / profit factor windows

## Config Audit

Current env-config surface:

- Broker credentials
- Symbol and order quantity
- Timeframe
- Strategy thresholds
- Entry frequency limits
- Risk guardrails

Files:

- [`.env`](/C:/Users/UtkarshPandey/trading-bot/.env)
- [`.env.example`](/C:/Users/UtkarshPandey/trading-bot/.env.example)

Assessment:

- Secret hygiene is improved because committed credentials were replaced with placeholders and a safe template was added.
- The live keys still need to remain only in the user’s local untracked `.env`.
- Config is still fully manual; there is no parameter profile system, no environment promotion workflow, and no validation for invalid combinations.

## Docker and Runtime Audit

Container/runtime setup:

- Python 3.11 slim image
- `docker-compose.yml` mounts `data/`, `logs/`, and `reports/`
- Runtime paths now normalize cleanly via [`bot/paths.py`](/C:/Users/UtkarshPandey/trading-bot/bot/paths.py)

Validation status:

- `docker compose config` succeeded
- Full runtime/container validation could not be completed during this session because Docker Desktop’s Linux engine was not running

Assessment:

- Runtime structure is sound
- Operational validation is incomplete until the container can actually be started and the bot run end-to-end

## Existing Historical State

Observed artifacts in the repo indicate:

- Existing equity log data in `logs/equity.csv`
- Existing trade submission rows in `logs/trades.csv`
- Existing SQLite state in `data/bot.db`
- Limited historical sample size

Observed practical meaning:

- The bot has run before and captured some paper-trading state
- The sample is far too small to infer durable profitability
- Existing legacy logs are useful for continuity, but not enough for model selection or robust parameter tuning

## What Was Fixed During This Audit

- Added path abstraction to support Docker and local execution
- Reworked order handling so fills are reconciled before state is trusted
- Added closed-trade storage
- Added daily starting equity and loss-streak state
- Added entry risk guardrails for drawdown, stale bars, loss streak, and notional size
- Improved daily reporting and legacy CSV compatibility
- Replaced committed key material with placeholders
- Added `.env.example`

## Remaining Gaps

High-priority remaining gaps:

- No automated backtesting engine
- No walk-forward or out-of-sample validation
- No symbol universe selection
- No research notebook or experiment tracking workflow
- No alerting/notification system
- No live health monitoring
- No holiday and early-close market calendar handling
- No slippage/commission modeling
- No unit or integration test suite
- No CI pipeline

## Best Automation Opportunities

Highest-value automation to increase odds of making more money:

- Automated reconciliation job:
  Continuously verify order state, broker position state, and local state even between signal runs.

- Automated daily trade journal:
  Write a root-cause style report of every filled trade, including entry filter state, exit reason, and realized P&L.

- Automated backtest sweeps:
  Evaluate parameter combinations over rolling time windows, with out-of-sample scoring.

- Automated symbol scanner:
  Rank a liquid watchlist by trend quality, volatility fit, and volume quality before choosing what to trade.

- Automated regime filter:
  Trade only in market conditions that match historically profitable environments.

- Automated risk pause:
  Suspend entries after repeated losses, suspicious broker-state mismatches, stale data, or abnormal intraday drawdown.

- Automated weekly research report:
  Summarize edge decay, parameter drift, and which filters are helping versus hurting.

## Recommended Roadmap

1. Get runtime validation fully working.
   Start Docker Desktop and run end-to-end paper-trading and reporting checks.

2. Add tests.
   Begin with signal generation, risk evaluation, store migrations, and order reconciliation paths.

3. Build research automation.
   Add backtests, parameter sweeps, and walk-forward validation.

4. Expand opportunity set.
   Move from fixed-SPY to a ranked watchlist.

5. Improve analytics.
   Add trade decomposition, hour-of-day analysis, and fill-quality tracking.

6. Add monitoring.
   Alert on failures, pending orders, broker/local mismatches, and repeated risk-rule triggers.

## Final Assessment

This repo now has a much safer and more credible paper-trading foundation than it had before the audit. It has working structure, clearer state management, and better reporting discipline. It does not yet have enough validated edge, enough historical evidence, or enough automation to justify scaling for real money optimization.

The most profitable next move is not to make the current SPY strategy more aggressive. It is to build the research and automation layer that can prove which strategy variants, symbols, and market regimes deserve capital.
