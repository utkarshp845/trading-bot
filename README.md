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

Main outputs:

- `reports/monitor_latest.md`
- `reports/monitor_latest.json`
- `reports/daily_YYYY-MM-DD.md`
- `reports/research_latest.md`
- `reports/research_latest.json`

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

## Notes

- This repo is set up for paper trading, not production deployment.
- Keep real API keys only in your local `.env`.
- By default, the bot will hold if Alpaca market clock data is unavailable.
- `OPERATIONS.md` has a few extra day-to-day commands.
