#!/usr/bin/env bash

set -Eeuo pipefail

profile="${1:-live}"
app_dir="${2:-${APP_DIR:-/opt/trading-bot/app}}"
cron_tz="${CRON_TZ:-America/New_York}"
schedule="${CRON_SCHEDULE:-*/5 * * * *}"
monitor_schedule="${MONITOR_CRON_SCHEDULE:-17 * * * *}"
research_schedule="${RESEARCH_CRON_SCHEDULE:-42 0 * * *}"
docker_bin="${DOCKER_BIN:-$(command -v docker || true)}"
market="${DEPLOY_MARKET:-btc}"
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

trade_job="${schedule} cd ${app_dir} && ${docker_bin} compose run --rm ${compose_service} >> ${app_dir}/logs/${profile}_cron.log 2>&1 ${job_marker}"
monitor_job="${monitor_schedule} cd ${app_dir} && ${docker_bin} compose run --rm --entrypoint python ${compose_service} -m bot.profile_runner ${profile} monitor ${market} >> ${app_dir}/logs/${profile}_monitor_cron.log 2>&1 ${job_marker}"
current_crontab="$(crontab -l 2>/dev/null || true)"

{
  echo "CRON_TZ=${cron_tz}"
  printf '%s\n' "$current_crontab" | grep -v '^CRON_TZ=' | grep -v -F "$job_marker" || true
  echo "$trade_job"
  echo "$monitor_job"
  if [[ "$profile" == "paper" ]]; then
    echo "${research_schedule} cd ${app_dir} && ${docker_bin} compose run --rm --entrypoint python ${compose_service} -m bot.profile_runner ${profile} research ${market} >> ${app_dir}/logs/${profile}_research_cron.log 2>&1 ${job_marker}"
  fi
} | sed '/^[[:space:]]*$/d' | crontab -

echo "Installed ${profile} trade schedule: ${schedule} (${cron_tz})"
echo "Installed ${profile} monitor schedule: ${monitor_schedule} (${cron_tz})"
if [[ "$profile" == "paper" ]]; then
  echo "Installed ${profile} research schedule: ${research_schedule} (${cron_tz})"
fi
