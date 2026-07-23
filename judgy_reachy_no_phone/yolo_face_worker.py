"""YOLO face detector subprocess."""

import os
from multiprocessing.connection import Connection


def run_face_detector(connection: Connection, confidence: float) -> None:
    import numpy as np
    from huggingface_hub import hf_hub_download
    from ultralytics import YOLO

    repo = os.getenv("FACE_TRACKING_MODEL_REPO", "AdamCodd/YOLOv11n-face-detection")
    filename = os.getenv("FACE_TRACKING_MODEL_FILE", "model.pt")
    device = os.getenv("FACE_TRACKING_DEVICE", "cpu")
    model = YOLO(hf_hub_download(repo_id=repo, filename=filename)).to(device)

    while True:
        frame = connection.recv()
        if frame is None:
            return

        result = model(frame, conf=confidence, verbose=False)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            connection.send(None)
            continue

        xyxy = boxes.xyxy.detach().cpu().numpy()
        scores = boxes.conf.detach().cpu().numpy()
        areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
        combined = scores * 0.7 + areas / max(float(areas.max()), 1.0) * 0.3
        box = xyxy[int(np.argmax(combined))]
        height, width = frame.shape[:2]
        connection.send(
            (
                float((box[0] + box[2]) / (2 * width)),
                float((box[1] + box[3]) / (2 * height)),
                float((box[2] - box[0]) / width),
                float((box[3] - box[1]) / height),
            )
        )
