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

Both `spy` (QQQ) and `btc` research runs now use $150 starting equity to match the real account.

Outputs:

- `reports/research_latest.md`
- `reports/research_latest.json`

Run the parameter optimizer:

```powershell
docker compose run --rm optimize
```

BTC live tuning through the profile runner:

```powershell
python -m bot.profile_runner live optimize btc
```

If you want a quicker sanity check first:

```powershell
$env:OPT_MAX_CANDIDATES="10"
docker compose run --rm optimize
```

Outputs:

- `reports/optimize_latest.md`
- `reports/optimize_latest.json`

The BTC live optimizer report includes the loaded live baseline, top candidates, acceptance checks, and 2x-slippage stress. Do not treat a candidate as deployable unless it is marked accepted in the report.

Useful optional env controls:

- `OPT_MAX_CANDIDATES` to cap grid size. Default is `50`.
- `OPT_PROGRESS_EVERY` to control how often progress lines print
- `OPT_REPORT_TOP_N` to control how many ranked setups are shown
- `OPT_*_VALUES` to narrow or widen the search grid for individual parameters

## Normal Bot Run

Run one bot cycle (equity profile, the default live deployment target):

```powershell
docker compose run --rm paper
docker compose run --rm trade
```

BTC variants:

```powershell
docker compose run --rm paper-btc
docker compose run --rm trade-btc
```

Direct profile runner equivalents:

```powershell
python -m bot.profile_runner paper trade spy
python -m bot.profile_runner live trade spy
python -m bot.profile_runner paper trade btc
python -m bot.profile_runner live trade btc
```

Profile-specific validation:

```powershell
python -m bot.profile_runner paper validate spy
python -m bot.profile_runner live validate spy
python -m bot.profile_runner paper validate btc
python -m bot.profile_runner live validate btc
```

Validate actual paper broker and market-data access without placing an order:

```bash
python -m bot.profile_runner paper connectivity btc
```

EC2 deployment runs this connectivity check before installing cron. Invalid or expired credentials therefore fail deployment instead of producing a validation-only database that looks healthy.

The paper BTC cron installation creates three jobs by default:

- trading cycle every 5 minutes
- monitor report hourly at minute 17
- research replay daily at 00:42 ET

Override these with `CRON_SCHEDULE`, `MONITOR_CRON_SCHEDULE`, and `RESEARCH_CRON_SCHEDULE` when installing cron.

Current runtime defaults:

- entry stale-bar blocking is disabled unless `ENABLE_STALE_BAR_CHECK=1`
- bot startup waits `20` seconds by default before broker/data checks; override with `STARTUP_DELAY_SECONDS`
- overnight carrying is disabled by default; override with `ALLOW_OVERNIGHT_HOLDING=true`
- end-of-day flattening starts `5` minutes before the close by default; override with `FLATTEN_BEFORE_CLOSE_MINUTES`
- `paper` and `trade` select separate Alpaca key pairs from `.env` when `ALPACA_PAPER_*` and `ALPACA_LIVE_*` variables are set
- BTC paper writes runtime artifacts under `runtime/paper_btc`, BTC live under `runtime/live_btc`
- `config/paper_btc.env` is an exact mirror of `config/live_btc.env` (same sizing, same filters) so paper fills validate the strategy that actually runs live
- `reports/monitor_latest.md` includes 24h/7d rejection counts, near-miss entry bars, and latest filter metrics for diagnosing quiet BTC live periods
- `config/live_spy.env` trades QQQ on hourly bars (fractional, long-only, ~90% notional) and is the recommended default live profile for a small account; see `docs/strategy_revamp_2026-07.md` for the replay evidence. Replay any change to it before relying on it live.

## Notes

- Start Docker Desktop before using the commands above.
- Keep real Alpaca keys only in the local untracked `.env`.
- Alpaca's paper account balance still needs to be adjusted in the dashboard. The repo now mirrors that target by using `$150` as the paper research starting equity (see `RESEARCH_STARTING_EQUITY` in `config/paper_spy.env` and `config/paper_btc.env`).
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
