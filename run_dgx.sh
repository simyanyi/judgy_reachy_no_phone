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
export REACHY_MINI_HOST
ROBOT_HOST="$REACHY_MINI_HOST"

./wake_reachy.sh

printf 'Waiting for Reachy daemon at %s:8000...\n' "$ROBOT_HOST"
ready=false
for _ in $(seq 1 30); do
  if curl --silent --show-error --connect-timeout 1 \
      --output /dev/null "http://${ROBOT_HOST}:8000/" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  printf 'Reachy daemon did not become reachable at %s:8000.\n' "$ROBOT_HOST" >&2
  exit 1
fi

exec ./run_local.sh
