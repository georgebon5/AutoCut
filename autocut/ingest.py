"""Ingest: normalize clips to CFR H.264 proxies + 16 kHz mono WAV audio."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from autocut.config import IngestConfig
from autocut.models import ProxyInfo


class IngestError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise IngestError(
            f"Command failed ({cmd[0]} {cmd[1]}):\n{result.stderr[-2000:]}"
        )
    return result


def _probe(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(path),
    ])
    return json.loads(result.stdout)


def _parse_fps(s: str) -> float:
    """Parse a 'num/den' fps string; returns 0.0 on bad input."""
    try:
        num, den = s.split("/")
        d = float(den)
        return float(num) / d if d else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _parse_probe(probe: dict) -> tuple[float, float, int, int, Optional[datetime], bool]:
    """Extract (duration, fps, width, height, creation_time, has_audio) from ffprobe output."""
    streams = probe.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise IngestError("No video stream found.")

    fps_str = video.get("avg_frame_rate") or video.get("r_frame_rate", "30/1")
    fps = _parse_fps(fps_str)
    if fps <= 0:
        fps = 30.0

    width = int(video["width"])
    height = int(video["height"])
    duration = float(probe.get("format", {}).get("duration", 0))
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    ct_str = (
        probe.get("format", {}).get("tags", {}).get("creation_time")
        or video.get("tags", {}).get("creation_time")
    )
    creation_time: Optional[datetime] = None
    if ct_str:
        try:
            creation_time = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    return duration, fps, width, height, creation_time, has_audio


def _build_proxy(src: Path, dst: Path, target_fps: int) -> None:
    """Transcode to CFR H.264 proxy (no audio).

    `format=yuv420p` in the filtergraph handles 10-bit / HDR input correctly
    (straight bit-depth reduction — fine for analysis proxies).
    VFR is resolved by the fps filter which drops/dupes frames as needed.
    """
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"fps={target_fps},format=yuv420p",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-an",
        str(dst),
    ])


def _build_audio(src: Path, dst: Path, sample_rate: int) -> None:
    """Extract first audio stream as 16 kHz mono PCM WAV."""
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-vn",
        "-ar", str(sample_rate), "-ac", "1",
        "-c:a", "pcm_s16le",
        str(dst),
    ])


def _make_unique_ids(clips: list[Path]) -> list[str]:
    """Generate collision-free clip IDs from stems."""
    stems = [p.stem for p in clips]
    counts: Counter = Counter(stems)
    seen: Counter = Counter()
    ids: list[str] = []
    for stem in stems:
        if counts[stem] > 1:
            ids.append(f"{stem}_{seen[stem]}")
            seen[stem] += 1
        else:
            ids.append(stem)
    return ids


def ingest_clips(
    clips: list[Path],
    output_dir: Path,
    cfg: IngestConfig,
) -> list[ProxyInfo]:
    """Normalize clips to CFR proxies + 16 kHz WAV, sorted by creation_time then filename.

    Args:
        clips: Input clip paths (any order; VFR, HEVC, HDR all accepted).
        output_dir: Root output directory; proxy/ and audio/ subdirs are created.
        cfg: Ingest configuration (target_fps, audio_sample_rate).

    Returns:
        ProxyInfo list sorted by file creation_time (fallback: filename).
    """
    proxy_dir = output_dir / "proxies"
    audio_dir = output_dir / "audio"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    clip_ids = _make_unique_ids(clips)
    infos: list[ProxyInfo] = []

    for clip, clip_id in zip(clips, clip_ids):
        probe = _probe(clip)
        duration, fps, width, height, creation_time, has_audio = _parse_probe(probe)

        proxy_path = proxy_dir / f"{clip_id}_proxy.mp4"
        audio_path: Optional[Path] = (
            audio_dir / f"{clip_id}_audio.wav" if has_audio else None
        )

        _build_proxy(clip, proxy_path, cfg.target_fps)
        if has_audio and audio_path is not None:
            _build_audio(clip, audio_path, cfg.audio_sample_rate)

        infos.append(ProxyInfo(
            clip_id=clip_id,
            original_path=clip,
            proxy_path=proxy_path,
            audio_path=audio_path,
            duration=duration,
            fps=fps,
            width=width,
            height=height,
            creation_time=creation_time,
        ))

    infos.sort(key=lambda p: (
        p.creation_time.timestamp() if p.creation_time else 0.0,
        p.original_path.name,
    ))
    return infos
