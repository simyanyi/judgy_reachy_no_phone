#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")"
if [ ! -f .env ]; then
  printf 'Missing .env. Run ./bootstrap_local.sh first.\n' >&2
  exit 1
fi
if [ ! -f judgy_reachy_no_phone/assets/voice_reference.wav ]; then
  printf 'Missing judgy_reachy_no_phone/assets/voice_reference.wav. See assets/README.md.\n' >&2
  exit 1
fi

.venv/bin/python -m judgy_reachy_no_phone.main
