"""Local MediaPipe face detection and gentle Reachy head following."""

import logging
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
    """Track the closest face with MediaPipe; all inference is local."""

    def __init__(self, enabled: bool = True, confidence: float = 0.6):
        self.enabled = enabled
        self.target: FaceTarget | None = None
        self._detector = None
        self._last_detected_at = 0.0
        self._last_command_at = 0.0

        if not enabled:
            return
        try:
            import mediapipe as mp
            self._detector = mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=confidence
            )
        except Exception as exc:
            self.enabled = False
            logger.warning("Local MediaPipe face tracking unavailable: %s", exc)

    def process(self, frame):
        """Detect and annotate the largest face in a BGR camera frame."""
        if not self.enabled or self._detector is None:
            return None
        try:
            result = self._detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if not result.detections:
                return self.target
            best = max(
                result.detections,
                key=lambda d: d.location_data.relative_bounding_box.width
                * d.location_data.relative_bounding_box.height,
            )
            box = best.location_data.relative_bounding_box
            self.target = FaceTarget(
                x=max(0.0, min(1.0, box.xmin + box.width / 2)),
                y=max(0.0, min(1.0, box.ymin + box.height / 2)),
                width=box.width,
                height=box.height,
                seen_at=time.monotonic(),
            )
            self._last_detected_at = self.target.seen_at
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

    def follow(self, reachy) -> None:
        """Move at most 5 times/second; ignore stale faces and tiny jitter."""
        target = self.target
        now = time.monotonic()
        if (
            not self.enabled or not target or now - target.seen_at > 0.8
            or now - self._last_command_at < 0.2
        ):
            return

        horizontal = target.x - 0.5
        vertical = target.y - 0.5
        if abs(horizontal) < 0.06 and abs(vertical) < 0.06:
            return
        try:
            from reachy_mini.utils import create_head_pose
            # Clamp motion to a comfortable range; invert vertical because image Y grows down.
            yaw = max(-20.0, min(20.0, -horizontal * 40.0))
            pitch = max(-12.0, min(12.0, vertical * 24.0))
            reachy.goto_target(
                head=create_head_pose(pitch=pitch, yaw=yaw, degrees=True),
                duration=0.25,
                method="minjerk",
            )
            self._last_command_at = now
        except Exception as exc:
            logger.debug("Face-follow movement failed: %s", exc)
