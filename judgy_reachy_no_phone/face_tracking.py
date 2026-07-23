"""YOLO face detection and calibrated Reachy head following."""

import logging
import multiprocessing
import time
from dataclasses import dataclass

import cv2

logger = logging.getLogger(__name__)


@dataclass
class FaceTarget:
    x: float
    y: float
    width: float
    height: float
    seen_at: float


class FaceTracker:
    """Track the most prominent face in a subprocess."""

    def __init__(self, enabled: bool = True, confidence: float = 0.6):
        self.enabled = enabled
        self.target: FaceTarget | None = None
        self._worker = None
        self._worker_connection = None
        self._worker_busy = False
        self._last_command_at = 0.0
        self._frame_size = (640, 480)

        if not enabled:
            return
        try:
            from .yolo_face_worker import run_face_detector

            context = multiprocessing.get_context("spawn")
            parent_connection, child_connection = context.Pipe()
            self._worker = context.Process(
                target=run_face_detector,
                args=(child_connection, confidence),
                daemon=True,
                name="yolo-face-detector",
            )
            self._worker.start()
            child_connection.close()
            self._worker_connection = parent_connection
            logger.info("Started isolated YOLO face detector")
        except Exception as exc:
            self.enabled = False
            logger.warning("Local YOLO face tracking unavailable: %s", exc)

    def process(self, frame):
        """Detect and annotate the largest face in a BGR camera frame."""
        if not self.enabled or self._worker_connection is None:
            return None
        try:
            if not self._worker.is_alive():
                self.enabled = False
                logger.warning("YOLO face worker exited; tracking disabled")
                return self.target
            if self._worker_busy and self._worker_connection.poll():
                detected = self._worker_connection.recv()
                self._worker_busy = False
                if detected is not None:
                    x, y, width, height = detected
                    self.target = FaceTarget(x, y, width, height, time.monotonic())
            if not self._worker_busy:
                height, width = frame.shape[:2]
                self._frame_size = (width, height)
                if width > 640:
                    frame = cv2.resize(frame, (640, round(height * 640 / width)))
                self._worker_connection.send(frame)
                self._worker_busy = True
            return self.target
        except Exception as exc:
            logger.debug("Face tracking error: %s", exc)
            return self.target

    def draw(self, frame):
        """Draw the most recently detected face when it is still fresh."""
        target = self.target
        if not target or time.monotonic() - target.seen_at > 0.8:
            return frame
        height, width = frame.shape[:2]
        x1 = int((target.x - target.width / 2) * width)
        y1 = int((target.y - target.height / 2) * height)
        x2 = int((target.x + target.width / 2) * width)
        y2 = int((target.y + target.height / 2) * height)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 190, 0), 2)
        cv2.putText(frame, "face", (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 190, 0), 2)
        return frame

    def close(self) -> None:
        """Stop the isolated detector without leaving a broken pipe behind."""
        if self._worker_connection is not None:
            try:
                if self._worker is not None and self._worker.is_alive():
                    self._worker_connection.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
            self._worker_connection.close()
            self._worker_connection = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            if self._worker.is_alive():
                self._worker.terminate()
                self._worker.join(timeout=1.0)
            self._worker = None

    def follow(self, reachy) -> None:
        """Use Reachy's camera calibration to follow the latest face."""
        target = self.target
        now = time.monotonic()
        if (
            not self.enabled or not target or now - target.seen_at > 0.8
            or now - self._last_command_at < 0.12
        ):
            return

        if abs(target.x - 0.5) < 0.035 and abs(target.y - 0.5) < 0.035:
            return
        try:
            import numpy as np
            from scipy.spatial.transform import Rotation

            width, height = self._frame_size
            pose = reachy.look_at_image(
                target.x * width,
                target.y * height,
                duration=0.0,
                perform_movement=False,
            )
            tracked_pose = np.eye(4)
            tracked_pose[:3, 3] = pose[:3, 3] * 0.6
            euler = Rotation.from_matrix(pose[:3, :3]).as_euler("xyz") * 0.6
            tracked_pose[:3, :3] = Rotation.from_euler("xyz", euler).as_matrix()
            reachy.goto_target(
                head=tracked_pose,
                duration=0.15,
                method="minjerk",
            )
            self._last_command_at = now
        except Exception as exc:
            logger.debug("Face-follow movement failed: %s", exc)
