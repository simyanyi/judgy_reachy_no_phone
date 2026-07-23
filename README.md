# Judgy Reachy — macOS and DGX Spark

## Setup on macOS

The macOS installation uses the GStreamer wheel bundle, so Homebrew GStreamer
is not required:

```bash
cd /path/to/judgy_reachy_no_phone
./bootstrap_local.sh
cp .env.example .env  # only if .env does not already exist
```

Start Reachy Mini Control and wake the robot. Set the robot IP and a UI port
that does not conflict with Control in `.env`:

```dotenv
REACHY_MINI_HOST=<current-robot-ip-or-hostname>
JUDGY_APP_PORT=8043
```

Then run:

```bash
./run_local.sh
```

Open `http://localhost:8043`. The Control app may remain open on port 8042;
this app connects directly to the robot daemon at `REACHY_MINI_HOST`.

The DGX Spark runs every model locally: YOLO26m phone detection, YOLO face
tracking, and Kokoro-82M TTS. Reachy Mini only runs its onboard hardware daemon.
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

Kokoro uses its packaged voices rather than a reference recording. The default
American English voice is configured in `.env`:

```dotenv
KOKORO_PRELOAD=true
KOKORO_MODEL_ID=hexgrad/Kokoro-82M
KOKORO_LANGUAGE=a
KOKORO_VOICE=af_heart
KOKORO_SPEED=1.0
HF_HOME=.cache/huggingface
```

The model and selected voice download and warm up in the background when the app
starts. The camera and web UI remain available during the first download; speech
triggered before it finishes waits for the same preload. Later launches use the
project-local `.cache/huggingface` directory and run locally. YOLO likewise downloads `yolo26m.pt` once on
its first run; keep that file beside the app for offline restarts.

## Run on DGX without the controller app

Set the robot address in `.env`. Port 8042 is available on DGX:

```dotenv
REACHY_MINI_HOST=<current-robot-ip-or-hostname>
JUDGY_APP_PORT=8042
```

Then use the combined launcher:

```bash
source .venv/bin/activate
./run_dgx.sh
```

It connects to `pollen@REACHY_MINI_HOST`, enables and starts
`reachy-mini-daemon`, waits up to 30 seconds for port 8000, and launches the
same application through `run_local.sh`.

Open `http://127.0.0.1:8042` on the DGX, or `http://<DGX-IP>:8042` from another
machine on the same network.

## Local components

- YOLO26m + ByteTrack: phone detection and tracking
- YOLOv11n-face: isolated face detection and calibrated head following
- Kokoro-82M: lightweight local WAV speech generation
- Pre-written personality lines: no cloud LLM
