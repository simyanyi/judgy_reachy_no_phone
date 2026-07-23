#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")"
if [ ! -f .env ]; then
  printf 'Missing .env. Run ./bootstrap_local.sh first.\n' >&2
  exit 1
fi
REACHY_MINI_HOST=$(.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("REACHY_MINI_HOST", ""))')
if [ -z "$REACHY_MINI_HOST" ]; then
  printf 'REACHY_MINI_HOST is missing from .env.\n' >&2
  exit 1
fi
ROBOT_HOST="$REACHY_MINI_HOST"
ssh "pollen@reachy-mini.local" 'sudo systemctl enable --now reachy-mini-daemon && sudo systemctl status reachy-mini-daemon --no-pager'
