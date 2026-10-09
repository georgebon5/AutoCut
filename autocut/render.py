"""Render: FFmpeg complex-filter concat + audio crossfades → rough_cut.mp4."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from autocut.config import RenderConfig, ZoomConfig
from autocut.models import ProxyInfo, Segment


@dataclass
class _RenderSegment:
    """One trimmed segment going into the ffmpeg concat.

    ``zoom_end`` of 1.0 means no zoom (plain trim). ``zoom_end`` > 1.0 adds a
    linear crop+scale push-in from ``start_zoom`` to ``zoom_end`` across the
    segment's duration.
    """
    video_path: Path
    audio_path: Optional[Path]      # None for B-roll (no audio)
    start: float
    end: float
    zoom_end: float = 1.0
    start_zoom: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def has_zoom(self) -> bool:
        return self.zoom_end > self.start_zoom + 1e-6


class RenderError(Exception):
    pass


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(
            f"ffmpeg render failed:\n{result.stderr[-3000:]}"
        )


def _zoom_filter(rs: _RenderSegment) -> str:
    """Crop+scale filter chain producing a linear push-in zoom.

    Zoom is applied by cropping a progressively-smaller centred window and
    scaling it back to the original dimensions. Expressions use the segment-
    local time ``t`` (0 at segment start, ``duration`` at end).
    """
    k = rs.zoom_end - rs.start_zoom
    d = max(rs.duration, 1e-3)
    z = f"({rs.start_zoom}+{k:.4f}*t/{d:.4f})"
    return (
        f"crop=w='iw/{z}':h='ih/{z}':x='(iw-ow)/2':y='(ih-oh)/2',"
        f"scale=w='iw*{z}':h='ih*{z}':eval=frame"
    )


def _build_render_cmd(
    kept: list[_RenderSegment],
    output_path: Path,
    cfg: RenderConfig,
) -> list[str]:
    """Build the complete FFmpeg command for rendering kept segments.

    Each segment contributes up to two inputs: a video-only proxy and an
    optional audio WAV.  They are separate files because the ingest step
    strips audio from proxies (-an) to keep proxies small.

    Filter graph:
    - trim/atrim per segment (frame-accurate)
    - optional crop+scale per segment for punch-in zoom
    - concat for video
    - chained acrossfade for audio (avoids clicks at every cut)
    - B-roll (no audio): silent aevalsrc of matching duration
    """
    N = len(kept)
    cf_s = cfg.audio_crossfade_ms / 1000.0

    cmd: list[str] = ["ffmpeg", "-y"]
    video_idx: list[int] = []   # ffmpeg input index for video of segment i
    audio_idx: list[Optional[int]] = []  # …for audio (None → use aevalsrc)

    fi = 0  # running ffmpeg input index
    for rs in kept:
        cmd += ["-i", str(rs.video_path)]
        video_idx.append(fi)
        fi += 1

        if rs.audio_path is not None:
            cmd += ["-i", str(rs.audio_path)]
            audio_idx.append(fi)
            fi += 1
        else:
            audio_idx.append(None)

    filters: list[str] = []

    for i, rs in enumerate(kept):
        vi = video_idx[i]
        ai = audio_idx[i]

        video_chain = (
            f"[{vi}:v]trim=start={rs.start:.6f}:end={rs.end:.6f},"
            f"setpts=PTS-STARTPTS"
        )
        if rs.has_zoom:
            video_chain += "," + _zoom_filter(rs)
        filters.append(f"{video_chain}[v{i}]")

        if ai is not None:
            filters.append(
                f"[{ai}:a]atrim=start={rs.start:.6f}:end={rs.end:.6f},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )
        else:
            filters.append(
                f"aevalsrc=0:s=16000:c=mono,"
                f"atrim=duration={rs.duration:.6f}[a{i}]"
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
    output_name: str = "rough_cut.mp4",
    hook: Segment | None = None,
    zoom: ZoomConfig | None = None,
) -> Path:
    """Concatenate kept segments across all proxy clips into a single video.

    Args:
        proxies: ProxyInfo list sorted by creation_time (from ingest).
        segments: All Segment objects from apply_silence_removal.
        output_dir: Directory where the output will be written.
        cfg: Render settings.
        output_name: Filename for the rendered video (default rough_cut.mp4).
        hook: Optional opening-hook segment; rendered first, before the
            chronological sequence. The hook never receives punch-in zoom.
        zoom: Optional punch-in-zoom config. When enabled, keep segments with
            ``interest_score ≥ zoom.score_threshold`` get a linear push-in
            from ``zoom.start_zoom`` to ``zoom.end_zoom`` across the segment.

    Returns:
        Path to the rendered video.

    Raises:
        RenderError: If no kept segments exist or ffmpeg fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name

    proxy_by_id = {p.clip_id: p for p in proxies}
    clip_order = {p.clip_id: i for i, p in enumerate(proxies)}

    kept = [s for s in segments if s.decision == "keep" and s.duration > 1e-3]
    kept.sort(key=lambda s: (clip_order.get(s.clip_id, 0), s.start))

    if not kept and hook is None:
        raise RenderError("No kept segments to render.")

    cf_s = cfg.audio_crossfade_ms / 1000.0
    total_segments = len(kept) + (1 if hook is not None else 0)
    check_segments = list(kept)
    if hook is not None:
        check_segments.append(hook)
    for s in check_segments:
        if s.duration < 2 * cf_s and total_segments > 1:
            raise RenderError(
                f"Segment {s.clip_id}@{s.start:.3f}s is shorter than "
                f"2×crossfade ({2 * cf_s:.3f}s). Reduce audio_crossfade_ms or "
                f"increase silence.min_silence."
            )

    def _zoom_for(s: Segment) -> float:
        if zoom is None or not zoom.enabled:
            return 1.0
        if s.interest_score < zoom.score_threshold:
            return 1.0
        return zoom.end_zoom

    def _to_render_segment(s: Segment, allow_zoom: bool) -> _RenderSegment:
        zoom_end = _zoom_for(s) if allow_zoom else 1.0
        start_zoom = zoom.start_zoom if (zoom is not None and zoom_end > 1.0) else 1.0
        return _RenderSegment(
            video_path=proxy_by_id[s.clip_id].proxy_path,
            audio_path=proxy_by_id[s.clip_id].audio_path,
            start=s.start,
            end=s.end,
            zoom_end=zoom_end,
            start_zoom=start_zoom,
        )

    kept_render: list[_RenderSegment] = [
        _to_render_segment(s, allow_zoom=True) for s in kept
    ]
    if hook is not None:
        kept_render.insert(0, _to_render_segment(hook, allow_zoom=False))

    cmd = _build_render_cmd(kept_render, output_path, cfg)
    _run(cmd)
    return output_path
