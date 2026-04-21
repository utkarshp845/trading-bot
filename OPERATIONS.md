# Operations

## Validation

Run the non-trading runtime validation inside Docker:

```powershell
docker compose run --rm validate
```

What it checks:

- runtime directories
- database schema and migrations
- indicator and signal generation on synthetic bars
- risk evaluation on a healthy sample
- report generation
- monitor report generation

Optional live broker validation:

```powershell
$env:VALIDATE_BROKER="1"
docker compose run --rm validate
```

This only validates broker connectivity and account snapshot retrieval. It does not place trades.

## Monitoring

Generate the latest monitoring artifacts:

```powershell
docker compose run --rm monitor
```

Profile-specific monitors:

```powershell
docker compose run --rm paper-monitor
docker compose run --rm live-monitor
```

Outputs:

- `reports/monitor_latest.md`
- `reports/monitor_latest.json`

These summarize:

- latest run and action
- recent events
- bot state and loss streak
- pending orders
- open position state
- closed-trade summary
- P&L by exit hour
- recent closed trades

## Research

Run the historical replay / walk-forward report:

```powershell
docker compose run --rm research
```

Small-account BTC paper rehearsal:

```powershell
python -m bot.profile_runner paper research btc
```

Outputs:

- `reports/research_latest.md`
- `reports/research_latest.json`

Run the parameter optimizer:

```powershell
docker compose run --rm optimize
```

If you want a quicker sanity check first:

```powershell
$env:OPT_MAX_CANDIDATES="10"
docker compose run --rm optimize
```

Outputs:

- `reports/optimize_latest.md`
- `reports/optimize_latest.json`

Useful optional env controls:

- `OPT_MAX_CANDIDATES` to cap grid size. Default is `50`.
- `OPT_PROGRESS_EVERY` to control how often progress lines print
- `OPT_REPORT_TOP_N` to control how many ranked setups are shown
- `OPT_*_VALUES` to narrow or widen the search grid for individual parameters

## Normal Bot Run

Run one BTC bot cycle:

```powershell
docker compose run --rm paper
docker compose run --rm trade
```

Direct profile runner equivalents:

```powershell
python -m bot.profile_runner paper trade btc
python -m bot.profile_runner live trade btc
```

Profile-specific validation:

```powershell
docker compose run --rm paper-validate
docker compose run --rm live-validate
```

Current runtime defaults:

- entry stale-bar blocking is disabled unless `ENABLE_STALE_BAR_CHECK=1`
- bot startup waits `20` seconds by default before broker/data checks; override with `STARTUP_DELAY_SECONDS`
- overnight carrying is disabled by default; override with `ALLOW_OVERNIGHT_HOLDING=true`
- end-of-day flattening starts `5` minutes before the close by default; override with `FLATTEN_BEFORE_CLOSE_MINUTES`
- `paper` and `trade` select separate Alpaca key pairs from `.env` when `ALPACA_PAPER_*` and `ALPACA_LIVE_*` variables are set
- BTC paper writes runtime artifacts under `runtime/paper_btc`, BTC live under `runtime/live_btc`
- The BTC paper profile mirrors the live BTC strategy so paper runs are a closer dress rehearsal

## Notes

- Start Docker Desktop before using the commands above.
- Keep real Alpaca keys only in the local untracked `.env`.
- Alpaca's paper account balance still needs to be adjusted in the dashboard. The repo now mirrors that target by using roughly `$250` as the paper research starting equity.
- Review `reports/monitor_latest.md` after each trading session if you want a concise explanation of what the bot did and why.

## EC2 Deploy

The repo includes a GitHub Actions workflow for EC2 deploys:

- `workflow: .github/workflows/deploy-ec2.yml`
- `docs: docs/github_actions_ec2.md`

The deployment path syncs the repo to EC2, uploads the server `.env`, validates the selected profile, and installs a weekday cron schedule for repeated runs.

## Validation Script

For the full local validation pass, run:

```powershell
./scripts/validate.ps1
```

This runs pytest plus the base, paper, and live runtime validators.
