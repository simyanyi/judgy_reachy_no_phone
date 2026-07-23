#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  printf 'Missing .venv. Run ./bootstrap_local.sh first.\n' >&2
  exit 1
fi
if [ ! -f .env ]; then
  printf 'Missing .env. Run ./bootstrap_local.sh first.\n' >&2
  exit 1
fi
REACHY_MINI_HOST=$(.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("REACHY_MINI_HOST", ""))')
JUDGY_APP_PORT=$(.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("JUDGY_APP_PORT", "8042"))')
HF_HOME=$(.venv/bin/python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("HF_HOME", ".cache/huggingface"))')
if [ -z "$REACHY_MINI_HOST" ]; then
  printf 'REACHY_MINI_HOST is missing from .env.\n' >&2
  exit 1
fi
export REACHY_MINI_HOST JUDGY_APP_PORT HF_HOME
# Hugging Face Xet stalled on macOS in testing; standard HTTP resumes partial
# downloads reliably. Kokoro uses MPS for supported ops and CPU fallback for
# the remaining ones.
export HF_HUB_DISABLE_XET=1
export PYTORCH_ENABLE_MPS_FALLBACK=1
APP_PORT="$JUDGY_APP_PORT"
if command -v lsof >/dev/null 2>&1 && \
   lsof -nP -iTCP:"$APP_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  printf 'Port %s is already in use. Stop the previous app or choose JUDGY_APP_PORT in .env.\n' "$APP_PORT" >&2
  lsof -nP -iTCP:"$APP_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

.venv/bin/python -m judgy_reachy_no_phone.wait_for_reachy "$REACHY_MINI_HOST"

if [ "$(uname -s)" = "Darwin" ]; then
  site_packages=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
  gstreamer_library_path=""
  gstreamer_typelib_path=""
  gstreamer_plugin_path=""
  mkdir -p .venv/.cache/gstreamer-1.0 .venv/.cache/matplotlib
  for library_dir in "$site_packages"/gstreamer_*/lib; do
    [ -d "$library_dir" ] || continue
    gstreamer_library_path="${gstreamer_library_path:+$gstreamer_library_path:}$library_dir"
    if [ -d "$library_dir/girepository-1.0" ]; then
      gstreamer_typelib_path="${gstreamer_typelib_path:+$gstreamer_typelib_path:}$library_dir/girepository-1.0"
    fi
    # libgstpython requires a shared libpython that Python.org's macOS build
    # does not provide. Reachy does not use Python-authored GStreamer plugins,
    # so exclude that one plugin directory to avoid repeated slow rescans.
    if [ -d "$library_dir/gstreamer-1.0" ] && \
       [ "$library_dir" != "$site_packages/gstreamer_python/lib" ]; then
      gstreamer_plugin_path="${gstreamer_plugin_path:+$gstreamer_plugin_path:}$library_dir/gstreamer-1.0"
    fi
  done
  gstreamer_root="$site_packages/gstreamer_libs"
  env \
    GI_TYPELIB_PATH="$gstreamer_typelib_path${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}" \
    DYLD_LIBRARY_PATH="$gstreamer_library_path${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}" \
    GST_PLUGIN_PATH_1_0="$gstreamer_plugin_path${GST_PLUGIN_PATH_1_0:+:$GST_PLUGIN_PATH_1_0}" \
    GST_PLUGIN_SYSTEM_PATH_1_0="$gstreamer_plugin_path" \
    GST_REGISTRY_1_0="$PWD/.venv/.cache/gstreamer-1.0/registry-macos.bin" \
    GST_PLUGIN_SCANNER="$gstreamer_root/libexec/gstreamer-1.0/gst-plugin-scanner" \
    GST_PLUGIN_SCANNER_1_0="$gstreamer_root/libexec/gstreamer-1.0/gst-plugin-scanner" \
    MPLCONFIGDIR="$PWD/.venv/.cache/matplotlib" \
    .venv/bin/python -m judgy_reachy_no_phone.main
else
  .venv/bin/python -m judgy_reachy_no_phone.main
fi
