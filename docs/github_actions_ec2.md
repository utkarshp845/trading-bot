# GitHub Actions EC2 Deploy

## Overview

This document describes the GitHub Actions workflow for deploying the trading bot to an EC2 instance.

## Deployment Path

The application is deployed to `/home/ubuntu/trading-bot` on the EC2 instance.

## Verify on EC2

Check the installed cron entries:

```bash
crontab -l
```

Check the last deploy logs from GitHub Actions in the Actions tab.

Check runtime output on the server:

```bash
ls -la /home/ubuntu/trading-bot/logs
tail -n 50 /home/ubuntu/trading-bot/logs/live_cron.log
tail -n 50 /home/ubuntu/trading-bot/logs/paper_cron.log
```

If you want to run one profile manually on the server after a deploy:

```bash
cd /home/ubuntu/trading-bot
docker compose run --rm live-spy
docker compose run --rm paper-spy
```

If you want a different schedule, set `CRON_SCHEDULE` before running `deploy/ec2/install_cron.sh`, or edit the script default.

Current default schedule:

- every 5 minutes
- weekdays only
- `America/New_York` timezone

Because the bot already guards on market hours internally, off-session runs should exit safely.