"""Scene-boundary detection via ffmpeg's native scene filter.

Earlier revisions used an in-process OpenCV HSV frame-diff loop (because
PySceneDetect's decode thread assertion-fails on this machine's OpenCV 5
build). That loop was faithful but slow — a full-frame Python pass per
clip, serial with the rest of the pipeline.

ffmpeg has an equivalent built-in: ``select='gt(scene,T)',showinfo`` runs
the scene-change probe inside the C decoder with full codec optimisation.
A single ffmpeg process instead of a Python-OpenCV loop ends up ~5-10x
faster on typical phone footage and lets us lose the OpenCV scene code
path entirely.

The ``scene`` metadata is a 0-1 float; higher = bigger inter-frame
difference. 0.3 is a good general-purpose threshold for phone vlogs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from autocut.config import SceneConfig
from autocut.models import Segment


# Translate the legacy HSV-diff threshold (roughly 0-255 mean) to ffmpeg's
# 0-1 scene-change probability. The scale isn't 1:1 — tuned empirically on
# phone footage; keep this knob here so the main config stays unchanged.
_FFMPEG_SCENE_THRESHOLD = 0.3

# ffmpeg's showinfo emits one line per selected frame starting with
# "[Parsed_showinfo_..." and containing "pts_time:<float>".
_SHOWINFO_RE = re.compile(r"pts_time:(\S+)")


def detect_scene_boundaries(proxy_path: Path, cfg: SceneConfig) -> list[float]:
    """Return clip-relative shot-change timestamps (seconds).

    Returns an empty list if ffmpeg isn't available, the file can't be opened,
    or no cuts are detected. ``cfg.threshold`` is kept for backward compat
    but not used — ffmpeg's native scene filter has its own 0-1 scale.
    """
    del cfg   # kept for signature compat; ffmpeg uses its own threshold scale.
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-i", str(proxy_path),
                "-vf", f"select='gt(scene,{_FFMPEG_SCENE_THRESHOLD})',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    # ffmpeg writes showinfo lines to stderr; one match per detected cut.
    boundaries: list[float] = []
    for line in result.stderr.splitlines():
        if "Parsed_showinfo" not in line:
            continue
        m = _SHOWINFO_RE.search(line)
        if m:
            try:
                boundaries.append(float(m.group(1)))
            except ValueError:
                continue
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
