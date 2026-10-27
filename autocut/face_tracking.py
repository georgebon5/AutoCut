"""Per-clip face position estimation using OpenCV's bundled Haar cascade.

The aspect-crop filter chain can center on the detected face instead of
the geometric centre — if your friend walks sideways in the frame, her
head stays in-shot after the 9:16 crop instead of being clipped off.

We sample a handful of evenly-spaced frames per proxy, take the median
of detected face centres (robust against false positives on textured
backgrounds), and return a (cx_ratio, cy_ratio) tuple in [0, 1]. When no
face is seen we fall back to (0.5, 0.5) — plain centre crop.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

_SAMPLE_FRAMES = 8   # evenly spaced; enough to be robust without full decode
_MIN_FACE_FRACTION = 0.05   # ignore faces < 5% of frame height (false positives)

# OpenCV ships this cascade; no model file to download.
_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_cascade_cache: cv2.CascadeClassifier | None = None


def _cascade() -> cv2.CascadeClassifier:
    global _cascade_cache
    if _cascade_cache is None:
        _cascade_cache = cv2.CascadeClassifier(_CASCADE_PATH)
    return _cascade_cache


def estimate_face_center(proxy_path: Path) -> tuple[float, float]:
    """Return (cx_ratio, cy_ratio) in [0, 1] — the normalised face centre.

    Falls back to (0.5, 0.5) if the file can't open, has no detectable
    faces, or every detection is implausibly small.
    """
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        return (0.5, 0.5)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return (0.5, 0.5)
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if frame_h <= 0:
            return (0.5, 0.5)

        detector = _cascade()
        step = max(1, total // _SAMPLE_FRAMES)
        centers_x: list[float] = []
        centers_y: list[float] = []
        for fi in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ret, frame = cap.read()
            if not ret:
                continue
            gray = np.array(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            faces = detector.detectMultiScale(
                gray, scaleFactor=1.2, minNeighbors=4,
                minSize=(int(frame_h * _MIN_FACE_FRACTION),) * 2,
            )
            if len(faces) == 0:
                continue
            # Take the largest face (closest subject = main character).
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            fh, fw = gray.shape[:2]
            centers_x.append((x + w / 2) / fw)
            centers_y.append((y + h / 2) / fh)
    finally:
        cap.release()

    if not centers_x:
        return (0.5, 0.5)
    return (float(np.median(centers_x)), float(np.median(centers_y)))
