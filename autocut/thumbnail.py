"""Thumbnail extraction: pick the best frame from a rendered cut.

We extract a single JPEG from the rendered mp4 (not from a proxy) so the
thumbnail already has the correct output aspect ratio (9:16, 16:9, or source)
and any zoom/crop effects are baked in.

The best frame is the midpoint of the kept segment with the highest
``interest_score`` as recorded in the EDL's render_chunks.  Highest-interest
means it's likely to have an expressive face, good lighting, or action — all
desirable for a thumbnail.

The extraction uses a single ``ffmpeg`` call with ``-ss`` before ``-i`` (fast
seek) so it completes in milliseconds even on a 4K file.  Quality is set to
``-q:v 2`` (scale 1–31 where 2 = near-lossless JPEG), which keeps the file
under ~500 kB for typical phone footage.

Failure is intentionally silent — a missing thumbnail should never abort the
pipeline or the review workflow.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from autocut.models import EDL


def _best_output_time(edl: EDL) -> float | None:
    """Return the output-timeline midpoint of the highest-interest segment.

    render_chunks map segment indices to output-timeline positions, so this
    gives us the exact time to seek to in the rendered mp4.  Hook segments
    (segment_index == -1) are skipped because they're often context-free
    action shots that don't represent the video's content.
    """
    best_t: float | None = None
    best_score = -1.0
    for chunk in edl.render_chunks:
        if chunk.segment_index < 0:
            continue
        if chunk.segment_index >= len(edl.segments):
            continue
        seg = edl.segments[chunk.segment_index]
        if seg.decision != "keep":
            continue
        score = getattr(seg, "interest_score", 0.0)
        if score > best_score:
            best_score = score
            best_t = (chunk.output_start + chunk.output_end) / 2.0
    return best_t


def extract_thumbnail(
    video_path: Path,
    edl: EDL,
    output_path: Path,
) -> bool:
    """Write a JPEG thumbnail to ``output_path``.

    Returns True on success, False on any failure (missing file, ffmpeg
    not found, no kept segments with render_chunks, etc.).
    """
    t = _best_output_time(edl)
    if t is None:
        # Fallback: grab the first few seconds of the video.
        t = 0.5

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{t:.3f}",
        "-i", str(video_path.resolve()),
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path.resolve()),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0 and output_path.exists()
    except (OSError, subprocess.SubprocessError):
        return False
