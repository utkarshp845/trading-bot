#!/usr/bin/env bash
set -Eeuo pipefail

python -m pytest -q
python -m bot.validate_runtime
python -m bot.profile_runner paper validate
python -m bot.profile_runner live validate
python -m bot.profile_runner paper validate btc
python -m bot.profile_runner live validate btc
