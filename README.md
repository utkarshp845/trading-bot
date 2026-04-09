# Trading Bot

Simple intraday trading bot for Alpaca paper trading.

It currently:

- pulls recent stock bars from Alpaca
- generates trend-following signals on a configurable timeframe
- applies basic risk checks before entering trades
- records runs, orders, events, and closed trades in SQLite
- writes daily and monitoring reports to `reports/`

## What It Uses

- Python 3.11
- Alpaca API
- SQLite
- Docker / Docker Compose

## Setup

1. Copy `.env.example` to `.env`
2. Add your Alpaca paper trading keys
3. Adjust any strategy or risk settings you want

Important env values:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `ALPACA_PAPER=true`
- `SYMBOL=SPY`
- `TIMEFRAME_MINUTES=5`
- `QTY=1`

## Run

Run one bot cycle:

```powershell
docker compose run --rm bot
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

Run the research / replay report:

```powershell
docker compose run --rm research
```

Run the walk-forward optimizer:

```powershell
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

## Repo Layout

- `bot/` - trading logic, broker integration, storage, reporting
- `data/` - SQLite database
- `logs/` - runtime logs and CSV snapshots
- `reports/` - generated reports

## Notes

- This repo is set up for paper trading, not production deployment.
- Keep real API keys only in your local `.env`.
- By default, the bot will hold if Alpaca market clock data is unavailable.
- Use the optimizer to rank parameter sets on walk-forward windows before copying new values into `.env`.
- `OPERATIONS.md` has a few extra day-to-day commands.
