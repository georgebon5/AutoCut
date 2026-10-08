"""Motion feature extraction: frame-differencing via OpenCV per segment."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from autocut.models import Segment

_MIN_FRAMES = 2       # need at least 2 frames to compute a difference
_MIN_DURATION_S = 0.1


def _zero_motion() -> dict[str, float]:
    return {"motion_mean": 0.0, "motion_std": 0.0, "motion_max": 0.0}


def extract_motion_features(
    proxy_path: Path,
    segment: Segment,
    sample_fps: float = 5.0,
) -> dict[str, float]:
    """Return motion statistics for one video segment.

    Samples frames at `sample_fps` from [segment.start, segment.end],
    computes per-pair mean absolute pixel difference (normalised to [0,1]),
    and returns mean, std, and max across all pairs.
    """
    duration = segment.end - segment.start
    if duration < _MIN_DURATION_S:
        return _zero_motion()

    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        return _zero_motion()

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    if video_fps <= 0:
        cap.release()
        return _zero_motion()

    start_frame = int(segment.start * video_fps)
    end_frame = int(segment.end * video_fps)
    step = max(1, round(video_fps / sample_fps))

    gray_frames: list[np.ndarray] = []
    fi = start_frame
    while fi < end_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ret, raw = cap.read()
        if not ret:
            break
        # np.array() forces a plain ndarray — required for OpenCV 5 compatibility
        # (cv2.Mat's dtype property is broken with older numpy versions).
        gray = np.array(cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY), dtype=np.float32) / 255.0
        gray_frames.append(gray)
        fi += step

    cap.release()

    if len(gray_frames) < _MIN_FRAMES:
        return _zero_motion()

    diffs = np.array([
        float(np.mean(np.abs(gray_frames[i + 1] - gray_frames[i])))
        for i in range(len(gray_frames) - 1)
    ])

    return {
        "motion_mean": float(diffs.mean()),
        "motion_std": float(diffs.std()),
        "motion_max": float(diffs.max()),
    }


def enrich_segments_motion(
    segments: list[Segment],
    proxy_path: Path,
    sample_fps: float = 5.0,
) -> None:
    """Add motion features to every keep segment in-place."""
    for seg in segments:
        if seg.decision != "keep":
            continue
        seg.features.update(
            extract_motion_features(proxy_path, seg, sample_fps=sample_fps)
        )
