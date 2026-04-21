#!/usr/bin/env bash

set -Eeuo pipefail

profile="${1:-live}"
app_dir="${2:-${APP_DIR:-/opt/trading-bot/app}}"
cron_tz="${CRON_TZ:-America/New_York}"
schedule="${CRON_SCHEDULE:-*/5 * * * *}"
docker_bin="${DOCKER_BIN:-$(command -v docker || true)}"
job_marker="# trading-bot-${profile}"

case "$profile" in
  live|paper) ;;
  *)
    echo "Unsupported profile: $profile" >&2
    exit 1
    ;;
esac

case "$profile" in
  live) compose_service="trade" ;;
  paper) compose_service="paper" ;;
esac

if [[ -z "$docker_bin" ]]; then
  echo "docker is required to install the cron job" >&2
  exit 1
fi

mkdir -p "$app_dir/logs"

job_line="${schedule} cd ${app_dir} && ${docker_bin} compose run --rm ${compose_service} >> ${app_dir}/logs/${profile}_cron.log 2>&1 ${job_marker}"
current_crontab="$(crontab -l 2>/dev/null || true)"

{
  echo "CRON_TZ=${cron_tz}"
  printf '%s\n' "$current_crontab" | grep -v '^CRON_TZ=' | grep -v -F "$job_marker" || true
  echo "$job_line"
} | sed '/^[[:space:]]*$/d' | crontab -

echo "Installed cron entry for ${profile}: ${schedule} (${cron_tz})"
