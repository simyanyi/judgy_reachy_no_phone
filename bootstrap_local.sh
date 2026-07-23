#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e .

if [ ! -f .env ]; then
  cp .env.example .env
fi

printf '\nSetup complete. Set the Reachy host in .env, then run ./run_local.sh.\n'
