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


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
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
    fast_preview: bool = False,
    subtitles_filename: str | None = None,
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

    # Build the pre-subtitles video label first; append the subtitles filter
    # as an additional link if requested. Doing concat + subs in one filter
    # graph saves a whole decode/encode pass vs. burning subs as a separate
    # ffmpeg call afterwards.
    if N == 1:
        video_pre_subs = "[v0]"
        audio_out = "[a0]"
    else:
        video_in = "".join(f"[v{i}]" for i in range(N))
        filters.append(f"{video_in}concat=n={N}:v=1:a=0[vconcat]")
        video_pre_subs = "[vconcat]"

        current = "[a0]"
        for i in range(1, N):
            label = f"[af{i}]" if i < N - 1 else "[aout]"
            filters.append(
                f"{current}[a{i}]acrossfade=d={cf_s:.4f}:c1=tri:c2=tri{label}"
            )
            current = label
        audio_out = "[aout]"

    if subtitles_filename:
        filters.append(f"{video_pre_subs}subtitles={subtitles_filename}[vout]")
        video_out = "[vout]"
    else:
        video_out = video_pre_subs

    # Pick the video codec. In fast_preview mode (used for re-renders during
    # review) we lean on Apple's hardware H.264 encoder — 3-5x faster than
    # libx264 at CRF 18 and plenty of quality for a scrubbing preview. The
    # final export is driven by FCPXML into the NLE anyway, so the preview
    # mp4 doesn't need archival quality.
    if fast_preview and _supports_videotoolbox():
        video_codec_args = [
            "-c:v", "h264_videotoolbox",
            "-b:v", "6M",
            "-realtime", "1",
        ]
    else:
        video_codec_args = [
            "-c:v", "libx264",
            "-crf", str(cfg.crf),
            "-preset", cfg.preset,
        ]

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", video_out,
        "-map", audio_out,
        *video_codec_args,
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    return cmd


# Cache the videotoolbox probe — ffmpeg startup to list encoders is ~100ms;
# re-renders fire often during review so this adds up.
_videotoolbox_cache: bool | None = None


def _supports_videotoolbox() -> bool:
    global _videotoolbox_cache
    if _videotoolbox_cache is not None:
        return _videotoolbox_cache
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        _videotoolbox_cache = False
        return False
    _videotoolbox_cache = "h264_videotoolbox" in result.stdout
    return _videotoolbox_cache


_subtitles_filter_cache: bool | None = None


def _has_subtitles_filter() -> bool:
    """True iff the installed ffmpeg was built with libass (filter available).

    Some Homebrew builds (9.x HEAD etc.) ship without libass and omit the
    ``subtitles`` filter entirely. Detecting that up front lets us raise an
    actionable error instead of a 3-KB ffmpeg traceback. Cached — probing
    ffmpeg's filter list costs ~100ms and we hit this on every re-render.
    """
    global _subtitles_filter_cache
    if _subtitles_filter_cache is not None:
        return _subtitles_filter_cache
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        _subtitles_filter_cache = False
        return False
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "subtitles":
            _subtitles_filter_cache = True
            return True
    _subtitles_filter_cache = False
    return False


def burn_subtitles(input_mp4: Path, subtitles_path: Path, output_mp4: Path) -> None:
    """Overlay an ASS/SRT subtitle file onto ``input_mp4`` → ``output_mp4``.

    Done as a second pass so the base render stays clean (no re-encode of the
    audio) and so burning is opt-in without complicating the main filter graph.
    Audio is stream-copied; only the video gets re-encoded by libass.
    """
    if not _has_subtitles_filter():
        raise RenderError(
            "Your ffmpeg was built without libass, so burn-in subtitles aren't "
            "available. On macOS Homebrew the regular `ffmpeg` formula ships "
            "without it — install the full build instead: "
            "`brew install ffmpeg-full && brew link --overwrite --force ffmpeg-full`. "
            "The SRT/ASS files in the Downloads panel still work for NLE import."
        )

    # ffmpeg's `subtitles` filter parses its arg as filter-graph syntax: ':'
    # separates options and '\' is an escape, so an absolute path on darwin
    # ("/Users/...") or a Windows drive letter trips the parser. Running ffmpeg
    # from the subtitles file's directory and passing just the filename
    # sidesteps all of that — the filename contains no filter metachars.
    workdir = subtitles_path.parent.resolve()
    # Match the fast_preview codec choice from _build_render_cmd: HW encoder
    # on Mac, libx264 fallback elsewhere. This pass is already a review-time
    # convenience; running it 5x slower on libx264 defeats the purpose.
    if _supports_videotoolbox():
        codec_args = ["-c:v", "h264_videotoolbox", "-b:v", "6M", "-realtime", "1"]
    else:
        codec_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_mp4.resolve()),
        "-vf", f"subtitles={subtitles_path.name}",
        *codec_args,
        "-c:a", "copy",
        str(output_mp4.resolve()),
    ]
    result = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(
            f"ffmpeg burn-subtitles failed:\n{result.stderr[-3000:]}"
        )


def render(
    proxies: list[ProxyInfo],
    segments: list[Segment],
    output_dir: Path,
    cfg: RenderConfig,
    output_name: str = "rough_cut.mp4",
    hook: Segment | None = None,
    zoom: ZoomConfig | None = None,
    fast_preview: bool = False,
    subtitles_path: Path | None = None,
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

    # When burning subs we pass just the filename and set ffmpeg's cwd to the
    # output dir — avoids filter-graph escaping headaches with ':' / '\' in
    # absolute paths. Proxies stay as absolute paths so cwd doesn't affect
    # them.
    subs_name: str | None = None
    cwd: Path | None = None
    if subtitles_path is not None:
        subs_name = subtitles_path.name
        cwd = output_dir
    cmd = _build_render_cmd(
        kept_render, output_path, cfg,
        fast_preview=fast_preview,
        subtitles_filename=subs_name,
    )
    _run(cmd, cwd=cwd)
    return output_path
