# Trading Bot

Trend-following trading bot for Alpaca, supporting both intraday equities (SPY) and 24/7 crypto (BTC/USD). Uses a trend-confirmed moving-average strategy with ADX, ATR, volume, momentum, and regime filters, layered risk controls, and multiple exit mechanisms.

It currently:

- pulls bars from Alpaca for stocks (IEX feed) or crypto (CryptoHistoricalDataClient)
- generates trend-following signals on a configurable timeframe
- applies layered risk checks before entering trades (daily drawdown, consecutive losses, cooldown, hard stop)
- manages exits via trailing stop, hard stop, breakeven stop, profit lock, time stop, and trend reversal
- records runs, orders, events, and closed trades in SQLite
- writes daily and monitoring reports to `reports/`

## What It Uses

- Python 3.11
- Alpaca API
- SQLite
- Docker / Docker Compose

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
| `HARD_STOP_ATR_MULT` | `0` | Hard stop distance in ATR units from entry (0 = disabled) |
| `ENABLE_BREAKEVEN_STOP` | `false` | Move stop to breakeven after N ATR of profit |
| `ENABLE_PROFIT_LOCK` | `false` | Lock in partial profit after N ATR move |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.01` | Halt entries after this % daily loss |
| `MAX_DAILY_LOSS` | `0` | Dollar daily loss cap (0 = disabled) |
| `ALLOW_OVERNIGHT_HOLDING` | `false` | Keep positions overnight (set `true` for crypto) |
| `FLATTEN_BEFORE_CLOSE_MINUTES` | `5` | Force flat this many minutes before 4 PM ET (set `0` for crypto) |

## Profiles

Pre-built configs live in `config/`:

| File | Symbol | Use |
|---|---|---|
| `config/paper_spy.env` | SPY | Paper trading equities |
| `config/live_spy.env` | SPY | Live trading equities |
| `config/paper_btc.env` | BTC/USD | Paper trading Bitcoin (24/7, fractional) |
| `config/live_btc.env` | BTC/USD | Live trading Bitcoin (24/7, fractional) |

Load a profile by setting `BOT_PROFILE` / `BOT_MARKET`, by sourcing the file before running, or with the profile runner:

```powershell
python -m bot.profile_runner paper trade btc
python -m bot.profile_runner live trade btc
```

## Run

```bash
# Paper trade (BTC config, Alpaca paper account)
docker compose run --rm paper

# Live trade (BTC config, Alpaca live account)
docker compose run --rm trade

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

Run the research / replay report:

```powershell
docker compose run --rm research
```

Run the walk-forward optimizer:

```powershell
docker compose run --rm optimize
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

## Deployment

The bot can be deployed to AWS EC2 using GitHub Actions.

### Prerequisites

1. AWS EC2 instance with Docker installed
2. AWS ECR repository for the Docker images
3. IAM user with ECR and EC2 access
4. SSH key pair for EC2 access

### Setup

1. **Create AWS ECR Repository:**
   ```bash
   aws ecr create-repository --repository-name trading-bot --region your-region
   ```

2. **Configure GitHub Secrets:**
   Add the following secrets to your GitHub repository:
   - `AWS_ACCESS_KEY_ID`: Your AWS access key
   - `AWS_SECRET_ACCESS_KEY`: Your AWS secret key
   - `AWS_ACCOUNT_ID`: Your AWS account ID
   - `AWS_REGION`: AWS region (e.g., us-east-1)
   - `EC2_HOST`: EC2 instance public IP or DNS
   - `EC2_USER`: SSH username (usually 'ec2-user' or 'ubuntu')
   - `EC2_SSH_KEY`: Private SSH key for EC2 access

4. **Setup EC2 Instance:**
   - Install Docker and AWS CLI
   - Configure AWS CLI with credentials that have ECR pull permissions
   - Clone your repository
   - Copy `.env` file with your Alpaca credentials to the project directory
   - Ensure the data, logs, and reports directories exist and are writable
   - Make sure Docker daemon is running
   - **Set up cron job for periodic execution:**
     ```bash
     # Edit crontab
     crontab -e
     
     # Add line to run every 5 minutes during market hours (9:30 AM - 4:00 PM ET, Monday-Friday)
     # Note: Adjust timezone as needed
     */5 9-15 * * 1-5 docker run --rm --env-file /path/to/trading-bot/.env -v /path/to/trading-bot/data:/app/data -v /path/to/trading-bot/logs:/app/logs -v /path/to/trading-bot/reports:/app/reports trading-bot:latest
     ```
     
     Or create a simple run script:
     ```bash
     #!/bin/bash
     docker run --rm \
       --env-file .env \
       -v $(pwd)/data:/app/data \
       -v $(pwd)/logs:/app/logs \
       -v $(pwd)/reports:/app/reports \
       trading-bot:latest
     ```
     
     Use the provided `run.sh` script for this purpose.

5. **Manual Deployment:**
   ```bash
   # On EC2 instance
   ./deploy.sh
   ```

### Automatic Deployment

Push to the `main` branch to trigger automatic deployment via GitHub Actions.

### Security Notes

- Use IAM roles on EC2 instead of access keys when possible
- Restrict EC2 security groups to only allow SSH from your IP
- Generate a dedicated SSH key pair for deployment
- Store sensitive credentials only in GitHub secrets and the .env file on EC2
- Regularly rotate API keys and SSH keys

- `bot/` - trading logic, broker integration, storage, reporting
- `data/` - SQLite database
- `logs/` - runtime logs and CSV snapshots
- `reports/` - generated reports

## Risk Controls

The bot has multiple independent safety layers:

| Control | Config Key | Default |
|---|---|---|
| Hard stop loss | `HARD_STOP_ATR_MULT` | disabled |
| Trailing stop | `TRAIL_ATR_MULTIPLIER` | 1.5x ATR |
| Breakeven stop | `ENABLE_BREAKEVEN_STOP` | false |
| Profit lock | `ENABLE_PROFIT_LOCK` | false |
| Time-based stop | `MAX_BARS_IN_TRADE` | 12 bars |
| Trend reversal exit | `REVERSAL_SIGNAL_STRENGTH_MIN` | 35 |
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
- Cron schedule should run every 5 minutes around the clock

For BTC with a small account, recommended settings (already in `config/paper_btc.env` and `config/live_btc.env`):
- `POSITION_SIZING_MODE=atr_risk` with `ATR_RISK_PER_TRADE_PCT=0.0025`
- `HARD_STOP_ATR_MULT=3.0`
- `TRAIL_ATR_MULTIPLIER=2.0` (BTC needs more room than equities)
- `MAX_DAILY_LOSS=10` (hard dollar cap)
- `TREND_EMA_PERIOD=55` with minimum EMA-distance confirmation to filter weak mean-reversion noise
- `MOMENTUM_LOOKBACK_BARS=3` plus `MIN_ADX_DELTA=0.25` to prefer strengthening breakouts over flat crossovers

## Notes

- Keep real API keys only in your local `.env` — never commit them.
- By default, the bot behaves as an intraday system: it flattens inherited overnight positions on the next session and exits before market close. Set `ALLOW_OVERNIGHT_HOLDING=true` and `FLATTEN_BEFORE_CLOSE_MINUTES=0` for crypto.
- The SPY runners write to `runtime/paper` and `runtime/live`; BTC runners write to `runtime/paper_btc` and `runtime/live_btc`.
- Use the optimizer to rank parameter sets on walk-forward windows before going live.
- `OPERATIONS.md` has day-to-day commands.
- `docs/github_actions_ec2.md` covers the GitHub Actions EC2 deployment path.
- Run `scripts/validate.ps1` (Windows) or `scripts/validate.sh` (Unix) for a full local validation pass.
