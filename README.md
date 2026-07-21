# Judgy Reachy — DGX Spark deployment

The DGX Spark runs every model locally: YOLO26m phone detection, MediaPipe face
tracking, and OmniVoice TTS. Reachy Mini only runs its onboard hardware daemon.
The Reachy Mini Control app is not needed.

## Setup on DGX Spark (Ubuntu/Linux ARM64)

```bash
cd /path/to/judgy_reachy_no_phone

sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential pkg-config \
  gobject-introspection libgirepository1.0-dev libglib2.0-dev \
  gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-tools

python3 -m venv .venv
source .venv/bin/activate
./bootstrap_local.sh
cp .env.example .env
```

## Configure local voice

Copy a 5–15 second WAV of a voice you recorded or are permitted to clone to:

```text
judgy_reachy_no_phone/assets/voice_reference.wav
```

Edit `.env` and set `OMNIVOICE_REFERENCE_TEXT` to the exact words spoken in the
sample. OmniVoice downloads its model once, then performs all TTS inference on
the DGX. Set `OMNIVOICE_MODEL_PATH` to the downloaded local folder for fully
offline restarts. YOLO likewise downloads `yolo26m.pt` once on its first run;
keep that file beside the app for offline restarts.

## Start Reachy without the controller app

From the DGX, ensure the robot is reachable and start its daemon:

```bash
./wake_reachy.sh
```

This uses `pollen@192.168.50.200` by default; change `REACHY_MINI_HOST` in
`.env` if needed. The daemon is enabled at robot boot and wakes the robot using
its service defaults.

## Run the app

```bash
source .venv/bin/activate
./run_local.sh
```

Open `http://127.0.0.1:8042` on the DGX, or `http://<DGX-IP>:8042` from another
machine on the same network.

## Local components

- YOLO26m + ByteTrack: phone detection and tracking
- MediaPipe: face detection and Reachy head following
- OmniVoice: local, voice-cloned WAV generation
- Pre-written personality lines: no cloud LLM
