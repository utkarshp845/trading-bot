#!/usr/bin/env bash

set -Eeuo pipefail

profile="${1:-live}"
app_dir="${APP_DIR:-$(pwd)}"
install_cron="${INSTALL_CRON:-true}"
run_after_deploy="${RUN_AFTER_DEPLOY:-true}"
docker_bin="${DOCKER_BIN:-$(command -v docker || true)}"

case "$profile" in
  live|paper) ;;
  *)
    echo "Unsupported profile: $profile" >&2
    exit 1
    ;;
esac

if [[ -z "$docker_bin" ]]; then
  echo "docker is required on the EC2 host" >&2
  exit 1
fi

cd "$app_dir"

if [[ ! -f .env ]]; then
  echo "Missing $app_dir/.env" >&2
  exit 1
fi

mkdir -p data logs reports runtime

echo "Building trading-bot image in $app_dir"
"$docker_bin" compose build

echo "Running ${profile} validation"
"$docker_bin" compose run --rm "${profile}-validate"

if [[ "$install_cron" == "true" || "$install_cron" == "1" ]]; then
  echo "Installing weekday cron schedule for ${profile}"
  bash deploy/ec2/install_cron.sh "$profile" "$app_dir"
fi

if [[ "$run_after_deploy" == "true" || "$run_after_deploy" == "1" ]]; then
  echo "Running immediate ${profile} trading cycle"
  "$docker_bin" compose run --rm "${profile}-spy"
fi

echo "Deployment finished for ${profile}"
