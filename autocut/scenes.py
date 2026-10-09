"""Scene-boundary detection via OpenCV HSV frame differencing.

The spec calls for PySceneDetect, but PySceneDetect's internal decode thread
is broken on OpenCV 5 (cv2.Mat vs np.ndarray mismatch, same issue motion.py
works around). The algorithm here is the same core idea as PySceneDetect's
ContentDetector — weighted HSV frame difference with a threshold and a
minimum scene length — just implemented in-process so we control the
np.ndarray handoff.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from autocut.config import SceneConfig
from autocut.models import Segment

# Channel weights used by PySceneDetect's ContentDetector default; emphasises
# hue/saturation changes (true shot cuts) over value flicker.
_HSV_WEIGHTS = np.array([1.0, 1.0, 1.0], dtype=np.float32)


def _frame_difference_hsv(prev_hsv: np.ndarray, curr_hsv: np.ndarray) -> float:
    """Weighted mean |ΔHSV| across pixels, matched to ContentDetector units."""
    diff = np.abs(curr_hsv - prev_hsv).mean(axis=(0, 1))
    return float((diff * _HSV_WEIGHTS).sum() / _HSV_WEIGHTS.sum())


def detect_scene_boundaries(proxy_path: Path, cfg: SceneConfig) -> list[float]:
    """Return clip-relative shot-change timestamps (seconds).

    Returns an empty list if the video can't be opened or no cuts are detected.
    The first frame is never reported as a boundary.
    """
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        cap.release()
        return []

    min_frames = max(1, int(cfg.min_scene_len_s * fps))

    boundaries: list[float] = []
    last_cut_fi = -min_frames   # allow a boundary at any time initially
    prev_hsv: np.ndarray | None = None
    fi = 0
    while True:
        ret, raw = cap.read()
        if not ret:
            break
        # Wrap cvtColor's output in np.array — same workaround motion.py uses
        # to survive the OpenCV 5 Mat/ndarray mismatch.
        hsv = np.array(
            cv2.cvtColor(raw, cv2.COLOR_BGR2HSV), dtype=np.float32
        )
        if prev_hsv is not None:
            diff = _frame_difference_hsv(prev_hsv, hsv)
            if diff > cfg.threshold and (fi - last_cut_fi) >= min_frames:
                boundaries.append(fi / fps)
                last_cut_fi = fi
        prev_hsv = hsv
        fi += 1
    cap.release()
    return boundaries


def scene_boundary_near_feature(
    seg: Segment,
    boundaries: list[float],
    window_s: float,
) -> float:
    """1.0 if any boundary falls within ``window_s`` of either segment edge, else 0.0.

    The feature targets cut-point quality: a segment whose start or end lands
    close to a shot change produces a visually clean cut.
    """
    for b in boundaries:
        if abs(b - seg.start) <= window_s or abs(b - seg.end) <= window_s:
            return 1.0
    return 0.0


def enrich_segments_scenes(
    segments: list[Segment],
    boundaries_by_clip: dict[str, list[float]],
    cfg: SceneConfig,
) -> None:
    """Set ``features['scene_boundary_near']`` on keep segments in-place.

    Clips absent from ``boundaries_by_clip`` are treated as having no
    boundaries → feature is 0.0 for their segments.
    """
    for seg in segments:
        if seg.decision != "keep":
            continue
        bounds = boundaries_by_clip.get(seg.clip_id, [])
        seg.features["scene_boundary_near"] = scene_boundary_near_feature(
            seg, bounds, cfg.boundary_window_s
        )
