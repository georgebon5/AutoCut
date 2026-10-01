"""Render: FFmpeg complex-filter concat + audio crossfades → rough_cut.mp4."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from autocut.config import RenderConfig
from autocut.models import ProxyInfo, Segment

# (proxy_path, audio_wav_path, segment_start, segment_end)
# Proxies are video-only (created with -an by ingest); audio comes from the
# separate 16 kHz WAV.  audio_wav_path is None for B-roll clips with no audio.
_SegmentTuple = tuple[Path, Optional[Path], float, float]


class RenderError(Exception):
    pass


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(
            f"ffmpeg render failed:\n{result.stderr[-3000:]}"
        )


def _build_render_cmd(
    kept: list[_SegmentTuple],
    output_path: Path,
    cfg: RenderConfig,
) -> list[str]:
    """Build the complete FFmpeg command for rendering kept segments.

    Each segment contributes up to two inputs: a video-only proxy and an
    optional audio WAV.  They are separate files because the ingest step
    strips audio from proxies (-an) to keep proxies small.

    Filter graph:
    - trim/atrim per segment (frame-accurate)
    - concat for video
    - chained acrossfade for audio (avoids clicks at every cut)
    - B-roll (no audio): silent aevalsrc of matching duration
    """
    N = len(kept)
    cf_s = cfg.audio_crossfade_ms / 1000.0

    # Build the input list and record which ffmpeg index maps to video/audio
    # for each segment.
    cmd: list[str] = ["ffmpeg", "-y"]
    video_idx: list[int] = []   # ffmpeg input index for video of segment i
    audio_idx: list[Optional[int]] = []  # …for audio (None → use aevalsrc)

    fi = 0  # running ffmpeg input index
    for proxy_path, audio_path, _, _ in kept:
        cmd += ["-i", str(proxy_path)]
        video_idx.append(fi)
        fi += 1

        if audio_path is not None:
            cmd += ["-i", str(audio_path)]
            audio_idx.append(fi)
            fi += 1
        else:
            audio_idx.append(None)

    filters: list[str] = []

    for i, (_, _, start, end) in enumerate(kept):
        dur = end - start
        vi = video_idx[i]
        ai = audio_idx[i]

        filters.append(
            f"[{vi}:v]trim=start={start:.6f}:end={end:.6f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        if ai is not None:
            filters.append(
                f"[{ai}:a]atrim=start={start:.6f}:end={end:.6f},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )
        else:
            filters.append(
                f"aevalsrc=0:s=16000:c=mono,"
                f"atrim=duration={dur:.6f}[a{i}]"
            )

    if N == 1:
        video_out = "[v0]"
        audio_out = "[a0]"
    else:
        video_in = "".join(f"[v{i}]" for i in range(N))
        filters.append(f"{video_in}concat=n={N}:v=1:a=0[vout]")
        video_out = "[vout]"

        current = "[a0]"
        for i in range(1, N):
            label = f"[af{i}]" if i < N - 1 else "[aout]"
            filters.append(
                f"{current}[a{i}]acrossfade=d={cf_s:.4f}:c1=tri:c2=tri{label}"
            )
            current = label
        audio_out = "[aout]"

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", video_out,
        "-map", audio_out,
        "-c:v", "libx264", "-crf", str(cfg.crf), "-preset", cfg.preset,
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    return cmd


def render(
    proxies: list[ProxyInfo],
    segments: list[Segment],
    output_dir: Path,
    cfg: RenderConfig,
) -> Path:
    """Concatenate kept segments across all proxy clips into rough_cut.mp4.

    Args:
        proxies: ProxyInfo list sorted by creation_time (from ingest).
        segments: All Segment objects from apply_silence_removal.
        output_dir: Directory where rough_cut.mp4 will be written.
        cfg: Render settings.

    Returns:
        Path to the rendered rough_cut.mp4.

    Raises:
        RenderError: If no kept segments exist or ffmpeg fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "rough_cut.mp4"

    proxy_by_id = {p.clip_id: p for p in proxies}
    clip_order = {p.clip_id: i for i, p in enumerate(proxies)}

    kept = [s for s in segments if s.decision == "keep" and s.duration > 1e-3]
    kept.sort(key=lambda s: (clip_order.get(s.clip_id, 0), s.start))

    if not kept:
        raise RenderError("No kept segments to render.")

    cf_s = cfg.audio_crossfade_ms / 1000.0
    for s in kept:
        if s.duration < 2 * cf_s and len(kept) > 1:
            raise RenderError(
                f"Segment {s.clip_id}@{s.start:.3f}s is shorter than "
                f"2×crossfade ({2 * cf_s:.3f}s). Reduce audio_crossfade_ms or "
                f"increase silence.min_silence."
            )

    kept_tuples: list[_SegmentTuple] = [
        (
            proxy_by_id[s.clip_id].proxy_path,
            proxy_by_id[s.clip_id].audio_path,   # separate WAV, not proxy audio
            s.start,
            s.end,
        )
        for s in kept
    ]

    cmd = _build_render_cmd(kept_tuples, output_path, cfg)
    _run(cmd)
    return output_path
