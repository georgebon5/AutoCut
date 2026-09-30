"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


skip_no_ffmpeg = pytest.mark.skipif(
    not has_ffmpeg(), reason="ffmpeg/ffprobe not installed"
)


def make_clip(
    path: Path,
    duration: float = 3.0,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    with_audio: bool = True,
    creation_time: str | None = None,
) -> Path:
    """Generate a synthetic MP4 using ffmpeg lavfi sources (fast; no real footage needed)."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if with_audio:
        cmd += [
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={duration}",
        ]

    cmd += [
        "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
    ]

    if with_audio:
        cmd += ["-c:a", "pcm_s16le", "-ar", "44100", "-ac", "1"]
    else:
        cmd += ["-an"]

    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]

    cmd.append(str(path))
    result = subprocess.run(cmd, capture_output=True, check=True)
    return path
