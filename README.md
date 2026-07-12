# Trading Bot

[![CI](https://github.com/utkarshp845/trading-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/utkarshp845/trading-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)

A systematic trading bot for Alpaca, built and run against a real ~$150 live
account. This repo is as much a record of the mistakes and the evidence that
corrected them as it is working code — see [What I've Learned](#what-ive-learned)
before the code itself.

**Not financial advice.** This is a personal project for learning and
experimentation with a small amount of real money. Nothing here is a
recommendation to trade, and past replay/backtest performance does not
predict future results. See [Disclaimer](#disclaimer).

## Contents

- [What This Is](#what-this-is)
- [What I've Learned](#what-ives-learned)
- [Current Strategy & Results](#current-strategy--results)
- [How It Works](#how-it-works)
- [What It Uses](#what-it-uses)
- [Setup](#setup)
- [Profiles](#profiles)
- [Run / Validate / Monitor](#run)
- [Deployment](#deployment)
- [Risk Controls](#risk-controls)
- [Crypto (BTC/USD)](#crypto-btcusd)
- [Small Equity Accounts](#small-equity-accounts)
- [Docs](#docs)
- [Disclaimer](#disclaimer)

## What This Is

A trend-following bot that pulls bars from Alpaca, evaluates a rules-based
signal (moving averages, ADX, ATR, volume, and a higher-timeframe regime
filter), sizes and places orders through Alpaca's API, and tracks everything
— fills, P&L, risk-limit hits, rejection reasons — in SQLite so it can be
audited after the fact instead of trusted blindly.

It currently:

- pulls bars from Alpaca for stocks (IEX feed) or crypto (CryptoHistoricalDataClient)
- generates trend-following signals on a configurable timeframe
- applies layered risk checks before entering trades (daily drawdown, consecutive losses, cooldown, hard stop)
- manages exits via trailing stop, hard stop, breakeven stop, profit lock, time stop, and trend reversal
- records runs, orders, events, and closed trades in SQLite
- writes daily and monitoring reports to `reports/`

## What I've Learned

The interesting part of this project isn't the code, it's what running it
live with real (small) money exposed:

- **A strategy that looks reasonable on paper can be structurally unable to
  make money at a given account size.** The original live setup traded
  BTC/USD on 5-minute bars with $150 of capital. Over 120 days it made
  exactly 2 trades — the entry filter stack required ~14 conditions to align
  at once. Position sizes were capped around $45, and Alpaca's crypto
  round-trip friction (~0.6%, taker fee + spread) was often larger than the
  trade's entire expected profit. It wasn't unlucky — it was unwinnable by
  construction.
- **"Trade more, smaller" made it worse, not better — I tested it instead of
  assuming.** A loosened, higher-frequency BTC variant made 184 trades over a
  year and lost $95, of which ~$99 was pure fee friction. On a small crypto
  account, activity itself is the cost. This only became obvious by replaying
  it against real historical data rather than reasoning about it in the
  abstract.
- **The venue mattered more than the signal.** The same trend logic, run on
  equities (near-zero commission) instead of crypto (~0.6% round trip),
  turned from a loser into a net-positive strategy in replay — without
  changing the core idea, just the friction it had to overcome.
- **Backtests can lie quietly through infrastructure bugs, not just overfitting.**
  While diagnosing this, I found the replay harness never reset its
  consecutive-loss counter on day rollover the way the live code does — so
  any backtest that hit its loss-streak limit silently stopped trading for
  the rest of the test period. Every prior research report in this repo was
  undercounting trades because of it. Fixed in
  [`bot/trade_controls.py`](bot/trade_controls.py); full writeup in
  [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).
- **Cron intervals need to match the strategy's timeframe, not just "run
  often."** Moving from 5-minute to hourly bars and leaving the deploy cron
  at every 5 minutes would have meant ~12x redundant invocations per hour —
  harmless (guarded by cooldown checks) but a real signal that deploy
  config and strategy config can silently drift apart if nobody checks.

Full investigation with numbers: [`docs/strategy_revamp_2026-07.md`](docs/strategy_revamp_2026-07.md).
Change-by-change history: [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## Current Strategy & Results

The default live/paper deployment is an **hourly QQQ trend-following
strategy** sized for a small account: long-only, fractional, ~90% notional
per position, a daily-EMA regime filter, and wide trailing exits that hold
winning trends for days rather than minutes. A defensive BTC/USD profile is
also available — it only trades confirmed multi-hour uptrends and is
designed to sit out downtrends entirely rather than force trades.

Replay results, `$150` starting capital, real historical bars, realistic
slippage assumptions (see the strategy doc for methodology — **these are
backtest results, not live account performance**):

| Profile | Period | Net P&L | Profit Factor | Max Drawdown | Trades |
|---|---|---|---|---|---|
| QQQ hourly trend (live default) | 2023-08 → 2026-07 (~3 yrs) | +$55.3 (+37%) | 1.76 | 6.7% | 74 (~2/mo) |
| BTC hourly, strict uptrend gate | 2025-26 (bear year, BTC −46%) | $0.00 | — | 0% | 0 (stayed flat) |
| BTC hourly, strict uptrend gate | 2024-25 (bull year) | +$3.1 | 1.14 | 10.6% | 12 |
| *(retired)* BTC 5m scalp, live config | 2026-03 → 2026-07 (real bars) | −$0.85 | 0.0 | 0.6% | 2 |

## How It Works

```
Alpaca bars → indicators (SMA/EMA/ADX/ATR/volume) → regime filter (higher timeframe)
           → signal (LONG/HOLD) → risk gate (drawdown, loss streak, cooldown, staleness)
           → position sizing → order submission → SQLite (runs, orders, closed trades, events)
           → reports (daily / monitor / research / optimize)
```

- **Signal:** [`bot/strategy_ma.py`](bot/strategy_ma.py) — SMA trend cross confirmed by ADX, ATR-bounded volatility, a trend EMA, and a higher-timeframe regime filter (e.g. daily EMA for the equity profile).
- **Risk:** [`bot/risk.py`](bot/risk.py), [`bot/trade_controls.py`](bot/trade_controls.py) — daily drawdown/loss caps, consecutive-loss halt, entry cooldown, stale-data rejection, position-notional caps.
- **Execution:** [`bot/broker_alpaca.py`](bot/broker_alpaca.py), [`bot/main.py`](bot/main.py) — order submission, fill reconciliation, broker position sync.
- **Persistence:** [`bot/store.py`](bot/store.py) — SQLite schema for runs, orders, position state, closed trades, and events, so every decision (including ones that resulted in *no* trade) is auditable later.
- **Research:** [`bot/research.py`](bot/research.py), [`bot/optimize_strategy.py`](bot/optimize_strategy.py) — replay/backtest harness and walk-forward parameter search, used to generate the results above.

## What It Uses

- Python 3.11
- Alpaca API (`alpaca-py`)
- SQLite
- Docker / Docker Compose
- GitHub Actions (CI + EC2 deploy)

## Setup

1. Copy `.env.example` to `.env`
2. Add your Alpaca keys (paper or live)
3. Choose a profile config from `config/` or adjust settings directly in `.env`

Key env values:

| Variable | Default | Description |
|---|---|---|
| `ALPACA_API_KEY` | — | Alpaca API key |
| `ALPACA_SECRET_KEY` | — | Alpaca secret key |
| `ALPACA_PAPER` | `true` | Set `false` for live trading |
| `SYMBOL` | `SPY` | Any equity or `BTC/USD` for crypto |
| `IS_CRYPTO` | `false` | Set `true` to enable crypto mode (24/7 market, fractional qty, GTC orders) |
| `TIMEFRAME_MINUTES` | `5` | Bar timeframe in minutes |
| `POSITION_SIZING_MODE` | `fixed` | `fixed`, `notional_cap`, or `atr_risk` |
| `ALLOW_FRACTIONAL_EQUITIES` | `false` | Allow fractional equity quantities for small live stock accounts |
| `MIN_ORDER_NOTIONAL` | `1.0` | Minimum dollar exposure for fractional stock or crypto entries |
| `HARD_STOP_ATR_MULT` | `0` | Hard stop distance in ATR units from entry (0 = disabled) |
| `ENABLE_BREAKEVEN_STOP` | `false` | Move stop to breakeven after N ATR of profit |
| `ENABLE_PROFIT_LOCK` | `false` | Lock in partial profit after N ATR move |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.01` | Halt entries after this % daily loss |
| `MAX_DAILY_LOSS` | `0` | Dollar daily loss cap (0 = disabled) |
| `ALLOW_OVERNIGHT_HOLDING` | `false` | Keep positions overnight (set `true` for crypto) |
| `FLATTEN_BEFORE_CLOSE_MINUTES` | `5` | Force flat this many minutes before 4 PM ET (set `0` for crypto) |

These are the raw code-level defaults (used only if you run `bot.main`
directly without a profile). Every shipped profile in `config/` overrides
the relevant ones — see [Profiles](#profiles).

## Profiles

Pre-built configs live in `config/`:

| File | Symbol | Use |
|---|---|---|
| `config/paper_spy.env` | QQQ | Paper trading equities (hourly trend, multi-day holds) |
| `config/live_spy.env` | QQQ | Small-account live equities — the recommended live profile |
| `config/paper_btc.env` | BTC/USD | Paper trading Bitcoin (defensive uptrend-only) |
| `config/live_btc.env` | BTC/USD | Live Bitcoin — defensive, dormant outside confirmed uptrends |

See `docs/strategy_revamp_2026-07.md` for the replay evidence behind these profiles.

Load a profile by setting `BOT_PROFILE` / `BOT_MARKET`, by sourcing the file before running, or with the profile runner:

```powershell
python -m bot.profile_runner paper trade spy
python -m bot.profile_runner live trade spy
```

## Run

```bash
# Paper trade (equity config, Alpaca paper account)
docker compose run --rm paper

# Live trade (equity config, Alpaca live account)
docker compose run --rm trade

# BTC variants
docker compose run --rm paper-btc
docker compose run --rm trade-btc

# Generate monitor report
docker compose run --rm monitor

# Generate paper BTC monitor report
docker compose run --rm paper-monitor

# Validate the full build and runtime setup
docker compose run --rm validate
```

## Validate

Run the built-in runtime validation:

```powershell
docker compose run --rm validate
```

This checks:

- runtime directories
- database setup
- signal generation
- risk evaluation
- report generation

## Monitor

Generate the latest monitor report:

```powershell
docker compose run --rm monitor
```

Or use the profile-specific monitors:

```powershell
docker compose run --rm paper-monitor
docker compose run --rm live-monitor
```

The monitor report includes recent rejection counts, near-miss entry bars, and the latest strategy metrics (`regime_side`, `momentum_pct`, `pullback_depth_atr`, `bar_range_atr`, `volume_ratio`, and `signal_strength`) so quiet live periods can be diagnosed from recorded bot state.

Run the research / replay report:

```powershell
docker compose run --rm research
```

Run the walk-forward optimizer:

```powershell
docker compose run --rm optimize
```

BTC equivalent:

```powershell
python -m bot.profile_runner live optimize btc
```

The optimizer now logs progress while it runs. For a quick smoke test, cap the search first:

```powershell
$env:OPT_MAX_CANDIDATES="10"
docker compose run --rm optimize
```

Main outputs:

- `reports/monitor_latest.md`
- `reports/monitor_latest.json`
- `reports/daily_YYYY-MM-DD.md`
- `reports/research_latest.md`
- `reports/research_latest.json`
- `reports/optimize_latest.md`
- `reports/optimize_latest.json`

The optimizer compares candidates against the loaded live baseline over the same bars, for either market. A candidate is marked accepted only if it clears the full replay, walk-forward, baseline-improvement, and 2x-slippage checks in the report.

## Deployment

The bot deploys to a plain EC2 instance via GitHub Actions: the workflow at
`.github/workflows/deploy-ec2.yml` rsyncs the repo over SSH, builds the
Docker image on the instance, and installs a cron job — no container
registry or AWS IAM setup involved. Full setup steps, required secrets, and
verification commands are in `docs/github_actions_ec2.md`.

It triggers automatically on pushes to `master`, or manually from the
Actions tab with a chosen profile (`live`/`paper`) and market
(`spy`/`btc`, defaults to `spy`).

### Security Notes

- Restrict EC2 security groups to only allow SSH from your IP
- Generate a dedicated SSH key pair for deployment
- Store sensitive credentials only in GitHub secrets and the `.env` file on EC2
- Regularly rotate API keys and SSH keys
- Never commit `.env`, `.pem`, or any real API key — `.gitignore` blocks the common patterns, but double-check before pushing

## Project Structure

- `bot/` - trading logic, broker integration, storage, reporting
- `config/` - per-profile env files (`live_spy`, `paper_spy`, `live_btc`, `paper_btc`)
- `data/` - SQLite database
- `logs/` - runtime logs and CSV snapshots
- `reports/` - generated reports
- `docs/` - strategy audits, build log, and deployment guides

## Risk Controls

The bot has multiple independent safety layers:

| Control | Config Key | Default |
|---|---|---|
| Hard stop loss | `HARD_STOP_ATR_MULT` | disabled |
| Trailing stop | `TRAIL_ATR_MULTIPLIER` | 1.5x ATR |
| Breakeven stop | `ENABLE_BREAKEVEN_STOP` | false |
| Profit lock | `ENABLE_PROFIT_LOCK` | false |
| Time-based stop | `MAX_BARS_IN_TRADE` | 12 bars |
| Regime-invalidation exit | `EXIT_ON_REGIME_INVALIDATION` | true |
| Daily drawdown halt | `MAX_DAILY_DRAWDOWN_PCT` | 1% |
| Dollar loss cap | `MAX_DAILY_LOSS` | disabled |
| Consecutive loss halt | `MAX_CONSECUTIVE_LOSSES` | 3 |
| Max trades per day | `MAX_TRADES_PER_DAY` | 5 |
| Entry cooldown | `COOLDOWN_BARS` | 2 bars |
| Stale data check | `ENABLE_STALE_BAR_CHECK` | false |

## Crypto (BTC/USD)

Set `IS_CRYPTO=true` (or use `config/paper_btc.env` / `config/live_btc.env`) to enable crypto mode:

- Uses `CryptoHistoricalDataClient` for bar data (no IEX feed requirement)
- Bypasses NYSE market-hours check — trades 24/7
- Orders use `TimeInForce.GTC` instead of `DAY`
- Position sizing returns fractional quantities (e.g. `0.0005 BTC`)
- Trades hourly bars like the equity profile; cron runs once an hour around the clock (see `docs/github_actions_ec2.md`)

Important for small accounts: Alpaca crypto costs ~0.25% taker fee + spread per
side (~0.6% per round trip). Replay evidence in `docs/strategy_revamp_2026-07.md`
shows that at ~$150 of equity this friction exceeds any repeatable intraday
edge — every high-frequency BTC variant tested lost money after fees. The
shipped BTC profiles are therefore deliberately defensive: hourly bars, a
strict 4h uptrend regime gate, near-full-notional single positions, and wide
trailing exits. Expect them to be dormant in downtrends. Prefer the equity
profile for growth.

## Small Equity Accounts

For a roughly `$150` account, one whole share of most ETFs is too large a
chunk of the account to size or diversify sensibly. `config/live_spy.env`
(despite the filename, it trades `QQQ`) already sets this up:

- `ALLOW_FRACTIONAL_EQUITIES=true`
- `ALLOW_SHORTS=false`
- `POSITION_SIZING_MODE=notional_cap` with `TARGET_POSITION_NOTIONAL_PCT=0.90` — near-full-notional single positions, since a $150 account can't usefully diversify anyway
- `MAX_POSITION_NOTIONAL_PCT` above the target as a hard ceiling

## Docs

- [`docs/strategy_revamp_2026-07.md`](docs/strategy_revamp_2026-07.md) — the investigation and evidence behind the current strategy
- [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) — running log of strategy and infrastructure changes
- [`docs/github_actions_ec2.md`](docs/github_actions_ec2.md) — EC2 deployment setup
- [`OPERATIONS.md`](OPERATIONS.md) — day-to-day commands (run, monitor, research, optimize)
- `docs/strategy_audit_current.md`, `docs/live_account_path_100usd.md` — earlier, now-superseded audits, kept as historical record

## Notes

- Keep real API keys only in your local `.env` — never commit them.
- Without a profile, the raw `bot.main` default is an intraday equity system: it flattens inherited overnight positions on the next session and exits before market close. Both shipped profiles override this — `config/live_spy.env` and `config/live_btc.env` both set `ALLOW_OVERNIGHT_HOLDING=true` and `FLATTEN_BEFORE_CLOSE_MINUTES=0`, since the current strategy is a multi-day trend hold, not an intraday one.
- The `spy`-market runners write to `runtime/paper` and `runtime/live`; `btc`-market runners write to `runtime/paper_btc` and `runtime/live_btc`.
- Use the optimizer to rank parameter sets on walk-forward windows before going live.
- Run `scripts/validate.ps1` (Windows) or `scripts/validate.sh` (Unix) for a full local validation pass.

## Disclaimer

This is a personal, educational project. It is not investment advice, and
nothing in this repository — including the replay results above — is a
recommendation to buy, sell, or hold any security or asset. Backtest and
replay performance do not guarantee future results; they use simplified
fill/slippage assumptions and a limited historical window. Trading involves
risk of loss, including total loss of capital. The author is not a
registered investment advisor. Use at your own risk, and never trade with
money you cannot afford to lose.
