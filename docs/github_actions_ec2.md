# GitHub Actions EC2 Deploy

This repo now includes an EC2 deployment workflow at `.github/workflows/deploy-ec2.yml`.

What it does:

- triggers automatically on pushes to `master`
- can also be run manually from the GitHub Actions tab
- connects to your EC2 instance over SSH
- syncs the repo to the server
- uploads the runtime `.env`
- builds the Docker image on EC2
- runs `python -m bot.profile_runner <paper|live> validate btc` on EC2
- installs a cron job that runs the chosen BTC profile every 5 minutes in `America/New_York`

## Required GitHub Secrets

Create these repository or environment secrets:

- `EC2_HOST`: public DNS name or public IP of the instance
- `EC2_USER`: SSH user, usually `ubuntu`
- `EC2_SSH_KEY`: private key used by GitHub Actions to SSH into EC2
- `EC2_ENV_FILE`: full contents of the server-side `.env`

Example `EC2_ENV_FILE` source:

- start from `.env.example`
- fill in your Alpaca keys
- keep `ALPACA_PAPER_*` and `ALPACA_LIVE_*` values there
- keep only real secrets in this GitHub secret, not in the repo

Optional repository variables:

- `EC2_APP_DIR`: defaults to `/home/ubuntu/trading-bot`
- `EC2_PORT`: defaults to `22`

## One-Time EC2 Setup

Use Ubuntu 22.04 or 24.04 on EC2.

1. SSH into the instance.
2. Install Docker, the Compose plugin, and cron:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin cron
sudo systemctl enable --now docker
sudo systemctl enable --now cron
sudo usermod -aG docker "$USER"
newgrp docker
```

3. Create the app directory:

```bash
mkdir -p /home/ubuntu/trading-bot
```

4. Make sure the SSH user from `EC2_USER` can log in non-interactively from GitHub Actions.

Recommended path:

- generate a dedicated SSH key pair for deployment
- add the public key to `~/.ssh/authorized_keys` on EC2
- store the private key in the `EC2_SSH_KEY` GitHub secret

5. Confirm the instance user can run Docker without `sudo`:

```bash
docker version
docker compose version
```

## First Deployment

1. Push this repo to GitHub with the new workflow files.
2. Add the secrets listed above.
3. Open `Actions` in GitHub.
4. Run `Deploy To EC2`.
5. Choose `live` or `paper`.
6. Leave `install_cron` enabled unless you want a code-only deploy.

On every later push to `master`, the workflow will auto-deploy the `live` profile.

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
docker compose run --rm trade
docker compose run --rm paper
```

If you want a different schedule, set `CRON_SCHEDULE` before running `deploy/ec2/install_cron.sh`, or edit the script default.

Current default schedule:

- every 5 minutes
- every day
- `America/New_York` timezone

Because the deployed profile is BTC, 24/7 scheduling is the correct default. If you later switch the deployment target back to equities, override `CRON_SCHEDULE` before running the installer.
