#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

ROBOT_HOST="${REACHY_MINI_HOST:-192.168.50.200}"
ssh "pollen@${ROBOT_HOST}" 'sudo systemctl enable --now reachy-mini-daemon && sudo systemctl status reachy-mini-daemon --no-pager'
