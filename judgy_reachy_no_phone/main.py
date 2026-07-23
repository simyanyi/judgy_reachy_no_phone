"""
Judgy Reachy No Phone - Get off your phone! 📱🤖

A Reachy Mini app that detects when you pick up your phone
and shames you with snarky comments.
"""
import os
import sys
import time
import threading
import logging
import asyncio
import base64
from collections import deque

if __package__ is None and __spec__ is None:
    package_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(package_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    __package__ = os.path.basename(package_dir)

import cv2
from dotenv import load_dotenv

from reachy_mini import ReachyMini, ReachyMiniApp
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from .config import Config, PERSONALITIES, get_random_personality
from .detection import PhoneDetector
from .audio import LLMResponder, TextToSpeech
from .face_tracking import FaceTracker
from .animations import (
    play_sound_safe,
    get_animation_for_count,
    disappointed_shake,
    approving_nod,
    idle_breathing
)
from reachy_mini.motion.recorded_move import RecordedMoves

logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
APP_PORT = int(os.getenv("JUDGY_APP_PORT", "8042"))
ROBOT_HOST = os.getenv("REACHY_MINI_HOST", "").strip()
if not ROBOT_HOST:
    raise RuntimeError("REACHY_MINI_HOST is missing from .env")

configured_personality = os.getenv("DEFAULT_PERSONALITY", "random").strip().lower()
if configured_personality in {"", "random"}:
    DEFAULT_PERSONALITY = get_random_personality()
elif configured_personality in PERSONALITIES:
    DEFAULT_PERSONALITY = configured_personality
else:
    raise RuntimeError(
        f"Invalid DEFAULT_PERSONALITY={configured_personality!r}. "
        f"Valid values: random, {', '.join(PERSONALITIES)}"
    )
logger.info("Default personality selected: %s", DEFAULT_PERSONALITY)


class JudgyReachyNoPhone(ReachyMiniApp):
    """Judgy Reachy No Phone - Get off your phone! 📱🤖"""

    custom_app_url: str | None = f"http://0.0.0.0:{APP_PORT}"
    dont_start_webserver: bool = False
    request_media_backend: str | None = "default"

    def __init__(self):
        super().__init__()
        configured_host = ROBOT_HOST.lower()
        if configured_host and configured_host not in {
            "localhost", "127.0.0.1", "::1",
        }:
            # ReachyMiniApp otherwise ignores the explicit host whenever any
            # process happens to answer on the Mac's localhost:8000.
            self.daemon_on_localhost = False
            logger.info("Using explicitly configured remote daemon: %s", configured_host)
        self.config = Config()

        # Loading state tracking (like demo.js)
        self.model_loading_status = "idle"  # idle, loading, ready, error
        self.model_loading_message = ""
        self.camera_loading_status = "idle"  # idle, connecting, ready, error
        self.camera_loading_message = "Waiting for camera connection..."
        self.tts_loading_status = "idle"  # idle, loading, ready, error, disabled
        self.tts_loading_message = "Waiting to preload Kokoro..."
        self.detector = PhoneDetector(
            confidence=self.config.DETECTION_CONFIDENCE,
            loading_callback=self._on_model_loading
        )
        self.llm = LLMResponder(api_key=self.config.GROQ_API_KEY, personality=DEFAULT_PERSONALITY)
        self.tts = TextToSpeech(
            personality=DEFAULT_PERSONALITY
        )
        self.face_tracker = FaceTracker(
            enabled=self.config.FACE_TRACKING_ENABLED,
            confidence=self.config.FACE_TRACKING_CONFIDENCE,
        )
        # Load Reachy's emotion library for Pure Reachy mode
        try:
            self.emotions = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
            logger.info("Loaded Reachy emotions library")
        except Exception as e:
            logger.warning(f"Failed to load emotions library: {e}")
            self.emotions = None

        # State
        self.is_running = False
        self.is_monitoring = False
        self.praise_enabled = True
        self.has_previous_session = False  # Track if there's data to continue from
        self._lock = threading.Lock()

        # Stats
        self.session_start = None
        self.total_shames = 0
        self.longest_streak = 0
        self.current_streak_start = None
        self.frozen_streak = 0  # Stores streak when monitoring is stopped
        self.frozen_phone_count = 0  # Store phone count when stopped

        # Camera thread state
        self.latest_frame = None
        self.latest_frame_jpeg = None  # JPEG encoded frame for web display
        self.latest_frame_at = 0.0
        self.camera_running = False
        self.camera_fps = 0
        self.detection_event_queue = deque(maxlen=16)
        self._frame_condition = threading.Condition()

        # This endpoint must exist before model loading callbacks or browser requests.
        @self.settings_app.get("/api/personalities")
        def get_personalities():
            personalities_list = []
            for key, data in PERSONALITIES.items():
                eleven_voice_data = data.get(
                    "default_eleven_voices", data.get("default_eleven_voice", "")
                )
                default_eleven = (
                    eleven_voice_data[0] if isinstance(eleven_voice_data, list)
                    and eleven_voice_data else eleven_voice_data
                )
                personalities_list.append({
                    "id": key,
                    "name": data["name"],
                    "voice": data["voice"],
                    "default_voice": data.get("default_voice", ""),
                    "default_eleven_voice": default_eleven or "",
                })
            return {
                "personalities": personalities_list,
                "default_personality": DEFAULT_PERSONALITY,
            }

    def _on_model_loading(self, status: str, message: str):
        """Callback for model loading progress (like demo.js)."""
        self.model_loading_status = status
        self.model_loading_message = message
        logger.info(f"Model loading: {status} - {message}")

    def _preload_tts(self):
        """Warm Kokoro in the background so the first reaction is immediate."""
        self.tts_loading_status = "loading"
        self.tts_loading_message = "Downloading/loading local Kokoro model..."
        logger.info("Preloading local Kokoro-82M model in the background...")
        try:
            self.tts.preload()
        except Exception as exc:
            self.tts_loading_status = "error"
            self.tts_loading_message = f"Kokoro preload failed: {exc}"
            logger.exception("Kokoro preload failed")
        else:
            self.tts_loading_status = "ready"
            self.tts_loading_message = "Kokoro ready"
            logger.info("Local Kokoro-82M model is ready")

    def _camera_thread(self, webcam, stop_event: threading.Event):
        """Fast camera capture and encoding thread (for laptop webcam in simulation)."""
        fps_counter = 0
        fps_start = time.time()

        logger.info("Laptop camera thread started (simulation mode)")

        try:
            while not stop_event.is_set() and self.camera_running:
                ret, frame = webcam.read()
                if not ret:
                    time.sleep(0.01)
                    continue

                # Calculate FPS
                fps_counter += 1
                if time.time() - fps_start >= 1.0:
                    self.camera_fps = fps_counter
                    fps_counter = 0
                    fps_start = time.time()

                # Camera frames may be read-only views. Draw only on a writable
                # display copy and retain the original for inference.
                frame_with_boxes = self.detector.draw_detections(frame.copy())
                frame_with_boxes = self.face_tracker.draw(frame_with_boxes)
                self._publish_frame(frame, frame_with_boxes)

        finally:
            logger.info("Laptop camera thread stopped")

    def _robot_camera_thread(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        """Camera thread using robot's media system (for real robot)."""
        fps_counter = 0
        fps_start = time.time()
        logger.info("Robot camera thread started")

        try:
            while not stop_event.is_set() and self.camera_running:
                try:
                    frame = reachy_mini.media.get_frame()
                except Exception as exc:
                    self.camera_loading_status = "connecting"
                    self.camera_loading_message = f"Waiting for robot camera: {exc}"
                    logger.warning("Robot camera read failed: %s", exc)
                    time.sleep(0.25)
                    continue
                if frame is None:
                    self.camera_loading_status = "connecting"
                    self.camera_loading_message = "Waiting for the first robot camera frame..."
                    time.sleep(0.02)
                    continue

                # Calculate FPS
                fps_counter += 1
                if time.time() - fps_start >= 1.0:
                    self.camera_fps = fps_counter
                    fps_counter = 0
                    fps_start = time.time()

                # WebRTC supplies a read-only NumPy view on some backends.
                # OpenCV drawing requires its own writable array.
                frame_with_boxes = self.detector.draw_detections(frame.copy())
                frame_with_boxes = self.face_tracker.draw(frame_with_boxes)
                self._publish_frame(frame, frame_with_boxes)

        finally:
            logger.info("Robot camera thread stopped")

    def _publish_frame(self, frame, display_frame):
        """Publish the newest frame without letting slow clients block capture."""
        # Keep inference on the original frame, but avoid encoding and sending a
        # needlessly large browser preview over Wi-Fi.
        try:
            preview_width = max(320, int(os.getenv("CAMERA_PREVIEW_WIDTH", "960")))
            jpeg_quality = max(40, min(95, int(os.getenv("CAMERA_JPEG_QUALITY", "70"))))
        except ValueError:
            preview_width, jpeg_quality = 960, 70
        height, width = display_frame.shape[:2]
        if width > preview_width:
            display_frame = cv2.resize(
                display_frame,
                (preview_width, round(height * preview_width / width)),
                interpolation=cv2.INTER_AREA,
            )
        encoded, buffer = cv2.imencode(
            ".jpg", display_frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        if not encoded:
            return
        with self._frame_condition:
            self.latest_frame = frame.copy()
            self.latest_frame_jpeg = buffer.tobytes()
            self.latest_frame_at = time.monotonic()
            self.camera_loading_status = "ready"
            self.camera_loading_message = "Camera streaming"
            self._frame_condition.notify_all()

    def _detection_thread(self, stop_event: threading.Event):
        """Run expensive inference independently so it cannot freeze video capture."""
        last_frame_at = 0.0
        while not stop_event.is_set() and self.camera_running:
            if not self.is_monitoring or self.model_loading_status != "ready":
                stop_event.wait(0.1)
                continue
            with self._frame_condition:
                self._frame_condition.wait_for(
                    lambda: self.latest_frame_at > last_frame_at or stop_event.is_set(),
                    timeout=0.5,
                )
                if stop_event.is_set() or self.latest_frame is None:
                    continue
                frame = self.latest_frame.copy()
                last_frame_at = self.latest_frame_at
            try:
                event = self.detector.process_frame(
                    frame,
                    pickup_threshold=self.config.PICKUP_THRESHOLD,
                    putdown_threshold=self.config.PUTDOWN_THRESHOLD,
                    cooldown=self.config.COOLDOWN_SECONDS,
                )
                if event:
                    self.detection_event_queue.append(event)
            except Exception as exc:
                logger.error("Detection error: %s", exc)

    def _face_tracking_thread(self, stop_event: threading.Event):
        """Track faces from the newest frame without delaying camera capture."""
        last_frame_at = 0.0
        while not stop_event.is_set() and self.camera_running:
            with self._frame_condition:
                self._frame_condition.wait_for(
                    lambda: self.latest_frame_at > last_frame_at
                    or stop_event.is_set(),
                    timeout=0.5,
                )
                if stop_event.is_set() or self.latest_frame is None:
                    continue
                frame = self.latest_frame.copy()
                last_frame_at = self.latest_frame_at
            self.face_tracker.process(frame)

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        """Main loop."""

        # Register all routes before lengthy model/camera initialization begins.
        self._run_ui(reachy_mini, stop_event)
        logger.info("App ready: open http://localhost:%s", APP_PORT)

        # Model loading/downloads can take minutes on first run. Keep camera startup
        # independent so users get live video immediately.
        logger.info("Initializing YOLO model in the background...")
        model_thread = threading.Thread(
            target=self.detector.initialize,
            name="phone-detector-loader",
            daemon=True,
        )
        model_thread.start()

        preload_tts = os.getenv("KOKORO_PRELOAD", "true").strip().lower()
        if preload_tts in {"1", "true", "yes", "on"}:
            threading.Thread(
                target=self._preload_tts,
                name="kokoro-loader",
                daemon=True,
            ).start()
        else:
            self.tts_loading_status = "disabled"
            self.tts_loading_message = "Kokoro startup preload disabled"

        # Auto-detect: Use laptop webcam in simulation, robot camera otherwise
        is_simulation = reachy_mini.client.get_status().simulation_enabled
        webcam = None

        if is_simulation:
            logger.info("Simulation mode detected - using laptop webcam...")
            self.camera_loading_status = "connecting"
            self.camera_loading_message = "Opening laptop webcam..."

            webcam = cv2.VideoCapture(0)
            if not webcam.isOpened():
                logger.error("Failed to open laptop webcam!")
                self.camera_loading_status = "error"
                self.camera_loading_message = "Failed to open webcam"
                webcam = None
            else:
                logger.info("Laptop webcam opened successfully!")
                self.camera_loading_status = "connecting"
                self.camera_loading_message = "Waiting for the first camera frame..."
                self.camera_running = True

                # Start fast camera thread
                camera_thread = threading.Thread(
                    target=self._camera_thread,
                    args=(webcam, stop_event),
                    daemon=True
                )
                camera_thread.start()
        else:
            logger.info("Real robot detected - using robot camera...")
            self.camera_loading_status = "connecting"
            self.camera_loading_message = "Connecting to robot camera..."

            self.camera_running = True
            # Start camera thread with robot's media system
            camera_thread = threading.Thread(
                target=self._robot_camera_thread,
                args=(reachy_mini, stop_event),
                daemon=True
            )
            camera_thread.start()

        if self.camera_running:
            face_tracking_thread = threading.Thread(
                target=self._face_tracking_thread,
                args=(stop_event,),
                name="face-tracking",
                daemon=True,
            )
            face_tracking_thread.start()

            detection_thread = threading.Thread(
                target=self._detection_thread,
                args=(stop_event,),
                daemon=True,
            )
            detection_thread.start()

        # Detection and robot control loop (separate from camera display)
        breath_counter = 0
        BREATH_INTERVAL = 8
        last_tick = time.time()

        try:
            while not stop_event.is_set():
                current_time = time.time()
                delta = current_time - last_tick
                last_tick = current_time

                # Process detection events from camera thread
                while self.detection_event_queue:
                    event = self.detection_event_queue.popleft()
                    try:
                        if event == "picked_up":
                            self._handle_phone_pickup(reachy_mini)
                        elif event == "put_down" and self.praise_enabled:
                            self._handle_phone_putdown(reachy_mini)
                    except Exception as e:
                        logger.error(f"Event handling error: {e}")

                # Idle breathing when not reacting - only if no pending events
                if not self.detection_event_queue:
                    self.face_tracker.follow(reachy_mini)
                breath_counter += delta
                if breath_counter >= BREATH_INTERVAL:
                    breath_counter = 0
                    # Only do idle breathing if no events pending (to avoid blocking)
                    if self.is_monitoring and not self.detector.phone_visible and len(self.detection_event_queue) == 0:
                        try:
                            # Pass callback to check for events during breathing
                            idle_breathing(reachy_mini, should_stop=lambda: len(self.detection_event_queue) > 0)
                        except:
                            pass

                # Faster loop for responsive event processing
                time.sleep(0.05)  # 20 FPS = 50ms max delay

        finally:
            # Stop camera thread
            self.camera_running = False
            self.face_tracker.close()
            if webcam is not None:
                webcam.release()
                logger.info("Webcam released")

        # Cleanup
        self.is_monitoring = False

    def _handle_phone_pickup(self, reachy: ReachyMini):
        """Handle phone pickup event."""
        count = self.detector.phone_count
        self.total_shames += 1

        # Reset streak
        if self.current_streak_start:
            streak_duration = time.time() - self.current_streak_start
            if streak_duration > self.longest_streak:
                self.longest_streak = streak_duration
        self.current_streak_start = None

        logger.info(f"Phone pickup #{count}!")

        # Check if using Pure Reachy mode (no TTS, just emotions)
        if self.llm.personality == "pure_reachy" and self.emotions:
            # Randomly pick a shame emotion from the config list
            import random
            personality_data = PERSONALITIES["pure_reachy"]
            shame_emotions = personality_data.get("shame_emotions", ["reprimand1"])
            emotion_name = random.choice(shame_emotions)
            emotion = self.emotions.get(emotion_name)
            logger.info(f"Pure Reachy shame: {emotion_name}")

            # Play emotion (includes sound + animation automatically)
            reachy.play_move(emotion)
        else:
            # Normal mode: Get snarky response via TTS
            text = self.llm.get_response(count)
            logger.info(f"Response: {text}")

            # Generate and play audio
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                audio_path = loop.run_until_complete(self.tts.synthesize(text))
                loop.close()

                # Play audio
                reachy.media.play_sound(audio_path)

                # Animate based on offense count
                animation = get_animation_for_count(count)
                animation(reachy)

            except Exception as e:
                logger.error(f"Shame response error: {e}")
                # Fallback: just animate
                play_sound_safe(reachy, "confused1.wav")
                disappointed_shake(reachy)

    def _handle_phone_putdown(self, reachy: ReachyMini):
        """Handle phone put down event."""
        logger.info("Phone put down!")

        # Start new streak
        self.current_streak_start = time.time()

        # Check if using Pure Reachy mode (no TTS, just emotions)
        if self.llm.personality == "pure_reachy" and self.emotions:
            # Randomly pick a praise emotion from the config list
            import random
            personality_data = PERSONALITIES["pure_reachy"]
            praise_emotions = personality_data.get("praise_emotions", ["yes1"])
            emotion_name = random.choice(praise_emotions)
            emotion = self.emotions.get(emotion_name)
            logger.info(f"Pure Reachy praise: {emotion_name}")

            # Play emotion (includes sound + animation automatically)
            reachy.play_move(emotion)
        else:
            # Normal mode: Get praise via TTS
            text = self.llm.get_praise()
            logger.info(f"Praise: {text}")

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                audio_path = loop.run_until_complete(self.tts.synthesize(text))
                loop.close()

                reachy.media.play_sound(audio_path)

                approving_nod(reachy)

            except Exception as e:
                logger.debug(f"Praise error: {e}")
                approving_nod(reachy)

    def _run_ui(self, reachy_mini: ReachyMini, stop_event: threading.Event):
        """Setup FastAPI routes for the UI."""

        # API models
        class ToggleRequest(BaseModel):
            groq_key: str = ""
            eleven_key: str = ""
            eleven_voice: str = ""  # Custom ElevenLabs voice ID
            edge_voice: str = ""  # Custom Edge TTS voice
            cooldown: int = 30
            praise: bool = True
            reset: bool = False  # If True, reset all stats (Start Fresh)
            personality: str = DEFAULT_PERSONALITY  # Robot personality

        # API endpoint: Get loading status (like demo.js)
        @self.settings_app.get("/api/loading-status")
        def get_loading_status():
            camera_status = self.camera_loading_status
            camera_message = self.camera_loading_message
            if (
                camera_status == "ready"
                and self.latest_frame_at
                and time.monotonic() - self.latest_frame_at > 3.0
            ):
                camera_status = "connecting"
                camera_message = "Camera stream stalled; reconnecting..."
            return {
                "model_status": self.model_loading_status,
                "model_message": self.model_loading_message,
                "camera_status": camera_status,
                "camera_message": camera_message,
                "tts_status": self.tts_loading_status,
                "tts_message": self.tts_loading_message,
                "overall_ready": (
                    self.model_loading_status == "ready" and
                    camera_status == "ready"
                )
            }

        # API endpoint: Get video frame
        @self.settings_app.get("/api/video-frame")
        def get_video_frame():
            if self.latest_frame_jpeg:
                return {
                    "frame": base64.b64encode(self.latest_frame_jpeg).decode("ascii"),
                    "fps": self.camera_fps,
                }
            return {"frame": None, "fps": 0}

        @self.settings_app.get("/api/video-stream")
        def get_video_stream():
            """Stream only new frames; slow clients automatically skip old frames."""
            def frames():
                last_frame_at = 0.0
                while not stop_event.is_set():
                    with self._frame_condition:
                        self._frame_condition.wait_for(
                            lambda: self.latest_frame_at > last_frame_at
                            or stop_event.is_set(),
                            timeout=2.0,
                        )
                        if stop_event.is_set():
                            break
                        if self.latest_frame_at <= last_frame_at:
                            continue
                        jpeg = self.latest_frame_jpeg
                        last_frame_at = self.latest_frame_at
                    if jpeg:
                        yield (
                            b"--frame\r\nContent-Type: image/jpeg\r\n"
                            + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                            + jpeg
                            + b"\r\n"
                        )

            return StreamingResponse(
                frames(),
                media_type="multipart/x-mixed-replace; boundary=frame",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

        # API endpoint: Get status
        @self.settings_app.get("/api/status")
        def get_status():
            stats = self.detector.get_stats()

            # Calculate streak
            if self.is_monitoring:
                if self.current_streak_start:
                    current_streak = time.time() - self.current_streak_start
                else:
                    current_streak = 0
            else:
                current_streak = self.frozen_streak

            current_streak_display = self._format_duration(current_streak)
            longest_streak_display = self._format_duration(self.longest_streak)

            # Status text
            if not self.is_monitoring:
                status_text = "Not monitoring"
            elif stats["phone_visible"]:
                status_text = "📱 PHONE DETECTED!"
            else:
                status_text = "✅ Phone-free"

            mode_text = "YOLO phone + YOLO face → Kokoro-82M (local)"

            # Determine button text
            if self.is_monitoring:
                button_text = "🛑 Stop Monitoring"
            elif self.has_previous_session:
                button_text = "▶️ Continue Monitoring"
            else:
                button_text = "▶️ Start Monitoring"

            return {
                "status_text": status_text,
                "phone_count": stats['phone_count'],
                "total_shames": self.total_shames,
                "current_streak": current_streak_display,
                "longest_streak": longest_streak_display,
                "mode": mode_text,
                "is_monitoring": self.is_monitoring,
                "button_text": button_text,
                "has_previous_session": self.has_previous_session
            }

        # API endpoint: Toggle monitoring
        @self.settings_app.post("/api/toggle")
        def toggle_monitoring(req: ToggleRequest):
            if self.is_monitoring:
                # Stop monitoring - save current state
                if self.current_streak_start:
                    self.frozen_streak = time.time() - self.current_streak_start
                else:
                    self.frozen_streak = 0

                self.frozen_phone_count = self.detector.phone_count
                self.has_previous_session = True
                self.is_monitoring = False

                # Return appropriate button text based on whether there's data
                button_text = "▶️ Continue Monitoring" if self.has_previous_session else "▶️ Start Monitoring"
                return {"button_text": button_text}
            else:
                # Start or Continue monitoring
                # Always update LLM responder with personality (for prewritten lines even without API key)
                if req.groq_key:
                    logger.info(f"Initializing LLM with Groq API key: {req.groq_key[:10]}... personality: {req.personality}")
                    self.llm = LLMResponder(api_key=req.groq_key, personality=req.personality)
                else:
                    logger.info(f"No Groq API key provided, using pre-written lines with personality: {req.personality}")
                    self.llm = LLMResponder(api_key="", personality=req.personality)

                # Initialize TTS - pass custom voices only if explicitly set (empty string means use personality default)
                if req.eleven_key:
                    logger.info(f"Initializing TTS with ElevenLabs key: {req.eleven_key[:10]}...")
                    self.tts = TextToSpeech(
                        elevenlabs_key=req.eleven_key,
                        voice=req.edge_voice,  # Pass empty string if not set, let personality defaults handle it
                        eleven_voice_id=req.eleven_voice,
                        personality=req.personality
                    )
                else:
                    logger.info(f"No ElevenLabs key provided, using Edge TTS")
                    self.tts = TextToSpeech(
                        voice=req.edge_voice,  # Pass empty string if not set, let personality defaults handle it
                        personality=req.personality
                    )

                self.config.COOLDOWN_SECONDS = req.cooldown
                self.praise_enabled = req.praise

                self.is_monitoring = True
                self.session_start = time.time()

                if req.reset or not self.has_previous_session:
                    # Start Fresh - reset everything
                    self.detector.reset_count()
                    self.total_shames = 0
                    self.longest_streak = 0
                    self.current_streak_start = time.time()
                    self.frozen_streak = 0
                    self.frozen_phone_count = 0
                    self.has_previous_session = False
                else:
                    # Continue - restore previous state
                    self.detector.phone_count = self.frozen_phone_count
                    self.current_streak_start = time.time() - self.frozen_streak if self.frozen_streak > 0 else time.time()

                return {"button_text": "🛑 Stop Monitoring"}

        # API endpoint: Validate API keys
        @self.settings_app.post("/api/validate-keys")
        def validate_keys(req: ToggleRequest):
            """Test API keys and voice IDs without starting monitoring."""
            return {
                "groq_valid": False,
                "eleven_valid": False,
                "eleven_voice_valid": False,
                "edge_voice_valid": False,
                "mode": "YOLO phone + YOLO face → Kokoro-82M (local)",
                "errors": [],
            }

            result = {
                "groq_valid": False,
                "eleven_valid": False,
                "eleven_voice_valid": False,
                "edge_voice_valid": False,
                "mode": "Pre-written lines → Edge TTS",
                "errors": []
            }

            # Test Groq
            if req.groq_key:
                try:
                    from groq import Groq
                    test_client = Groq(api_key=req.groq_key)
                    # Quick test call
                    test_client.chat.completions.create(
                        model="llama-3.1-8b-instant",
                        max_tokens=5,
                        messages=[{"role": "user", "content": "test"}]
                    )
                    result["groq_valid"] = True
                    logger.info("Groq API key validated successfully")
                except Exception as e:
                    logger.warning(f"Groq API key validation failed: {e}")
                    result["errors"].append(f"Groq: {str(e)}")

            # Test ElevenLabs
            if req.eleven_key:
                try:
                    from elevenlabs import ElevenLabs
                    test_eleven = ElevenLabs(api_key=req.eleven_key)
                    result["eleven_valid"] = True
                    logger.info("ElevenLabs API key validated")

                    # Only validate voice ID if user entered a custom one
                    if req.eleven_voice:
                        try:
                            audio_gen = test_eleven.text_to_speech.convert(
                                text="test",
                                voice_id=req.eleven_voice,
                                model_id="eleven_multilingual_v2"
                            )
                            # Consume generator to trigger any errors
                            for _ in audio_gen:
                                break
                            result["eleven_voice_valid"] = True
                            logger.info(f"ElevenLabs voice validated: {req.eleven_voice}")
                        except Exception as voice_error:
                            result["eleven_voice_valid"] = False
                            logger.warning(f"ElevenLabs voice validation failed: {voice_error}")
                            result["errors"].append(f"ElevenLabs voice '{req.eleven_voice}': Invalid or no access")
                    else:
                        # No custom voice entered, will use config default
                        result["eleven_voice_valid"] = True
                        logger.info(f"No custom ElevenLabs voice, using default: {self.config.ELEVENLABS_VOICE_ID}")

                except Exception as e:
                    logger.warning(f"ElevenLabs API key validation failed: {e}")
                    result["errors"].append(f"ElevenLabs key: {str(e)}")

            # Test Edge TTS voice (only if user entered one)
            if req.edge_voice:
                try:
                    import edge_tts
                    # Validate by trying to create a Communicate object
                    async def validate_edge_voice():
                        try:
                            communicate = edge_tts.Communicate("test", req.edge_voice)
                            # If no error thrown, voice is valid
                            return True
                        except Exception:
                            return False

                    voice_valid = asyncio.run(validate_edge_voice())
                    if voice_valid:
                        result["edge_voice_valid"] = True
                        logger.info(f"Edge TTS voice validated: {req.edge_voice}")
                    else:
                        result["errors"].append(f"Edge TTS voice '{req.edge_voice}': Not found")
                        logger.warning(f"Edge TTS voice not found: {req.edge_voice}")
                except Exception as e:
                    logger.warning(f"Edge TTS validation error: {e}")
                    # Don't block on validation errors
                    result["edge_voice_valid"] = True
            else:
                # No custom voice entered, skip validation
                result["edge_voice_valid"] = True

            # Build mode string
            llm_text = "LLM + TTS" if result["groq_valid"] else "Pre-written lines"
            tts_text = "ElevenLabs" if result["eleven_valid"] else "Edge TTS"
            result["mode"] = f"YOLO26m | {llm_text} → {tts_text}"

            return result

        # API endpoint: Reset all stats
        @self.settings_app.post("/api/reset")
        def reset_stats():
            """Reset all statistics and start fresh."""
            self.detector.reset_count()
            self.total_shames = 0
            self.longest_streak = 0
            self.current_streak_start = None
            self.frozen_streak = 0
            self.frozen_phone_count = 0
            self.has_previous_session = False
            return {
                "success": True,
                "button_text": "▶️ Start Monitoring"
            }

        # API endpoint: Test shame
        @self.settings_app.post("/api/test")
        def test_shame(req: ToggleRequest):
            # Apply settings from UI before testing (but don't start monitoring)
            # Always update LLM responder with personality (for prewritten lines even without API key)
            if req.groq_key:
                self.llm = LLMResponder(api_key=req.groq_key, personality=req.personality)
            else:
                self.llm = LLMResponder(api_key="", personality=req.personality)

            # Pass voice overrides only if explicitly set (empty string means use personality default)
            if req.eleven_key:
                self.tts = TextToSpeech(
                    elevenlabs_key=req.eleven_key,
                    voice=req.edge_voice,
                    eleven_voice_id=req.eleven_voice,
                    personality=req.personality
                )
            else:
                self.tts = TextToSpeech(
                    voice=req.edge_voice,
                    personality=req.personality
                )

            # Run test without starting monitoring
            self.detector.phone_count += 1
            self.total_shames += 1

            # Check if using Pure Reachy mode (no TTS, just emotions)
            if req.personality == "pure_reachy" and self.emotions:
                # Randomly pick a shame emotion from the config list
                import random
                personality_data = PERSONALITIES["pure_reachy"]
                shame_emotions = personality_data.get("shame_emotions", ["curious1"])
                emotion_name = random.choice(shame_emotions)
                emotion = self.emotions.get(emotion_name)
                logger.info(f"Pure Reachy test: {emotion_name}")

                reachy_mini.play_move(emotion)
            else:
                # Normal mode: Get response via TTS
                text = self.llm.get_response(self.detector.phone_count)
                logger.info(f"Test response: {text}")

                # Play audio and animate
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    audio_path = loop.run_until_complete(self.tts.synthesize(text))
                    loop.close()

                    reachy_mini.media.play_sound(audio_path)
                    animation = get_animation_for_count(self.detector.phone_count)
                    animation(reachy_mini)
                except Exception as e:
                    logger.error(f"Test error: {e}")
                    play_sound_safe(reachy_mini, "confused1.wav")
                    disappointed_shake(reachy_mini)

            return {"success": True}

        # API endpoint: Update personality while monitoring
        @self.settings_app.post("/api/update-personality")
        def update_personality(req: ToggleRequest):
            """Update personality, voice, and API keys while monitoring is running."""
            # Update LLM with new personality
            if req.groq_key:
                self.llm = LLMResponder(api_key=req.groq_key, personality=req.personality)
                logger.info(f"Updated LLM: personality={req.personality}, groq_key={'SET' if req.groq_key else 'NONE'}")
            else:
                self.llm = LLMResponder(api_key="", personality=req.personality)
                logger.info(f"Updated LLM: personality={req.personality}, using prewritten lines")

            # Update TTS with new personality and voices
            if req.eleven_key:
                self.tts = TextToSpeech(
                    elevenlabs_key=req.eleven_key,
                    voice=req.edge_voice,
                    eleven_voice_id=req.eleven_voice,
                    personality=req.personality
                )
                logger.info(f"Updated TTS: personality={req.personality}, ElevenLabs enabled")
            else:
                self.tts = TextToSpeech(
                    voice=req.edge_voice,
                    personality=req.personality
                )
                logger.info(f"Updated TTS: personality={req.personality}, Edge TTS only")

            # Update other settings
            self.config.COOLDOWN_SECONDS = req.cooldown
            self.praise_enabled = req.praise

            return {"success": True, "message": f"Updated to {req.personality}"}

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            return f"{mins}m"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h{mins}m"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    app = JudgyReachyNoPhone()
    try:
        app.wrapped_run(host=ROBOT_HOST)
    except KeyboardInterrupt:
        app.stop()
