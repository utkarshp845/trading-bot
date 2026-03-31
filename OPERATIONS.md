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

Outputs:

- `reports/research_latest.md`
- `reports/research_latest.json`

## Normal Bot Run

Run one bot cycle:

```powershell
docker compose run --rm bot
```

Current runtime defaults:

- entry stale-bar blocking is disabled unless `ENABLE_STALE_BAR_CHECK=1`
- bot startup waits `20` seconds by default before broker/data checks; override with `STARTUP_DELAY_SECONDS`

## Notes

- Start Docker Desktop before using the commands above.
- Keep real Alpaca keys only in the local untracked `.env`.
- Review `reports/monitor_latest.md` after each trading session if you want a concise explanation of what the bot did and why.
