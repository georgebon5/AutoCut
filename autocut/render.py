"""Render: FFmpeg complex-filter concat + audio crossfades → rough_cut.mp4."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from autocut.config import MusicConfig, RenderConfig, ZoomConfig
from autocut.models import ProxyInfo, Segment


@dataclass
class _RenderSegment:
    """One trimmed segment going into the ffmpeg concat.

    ``zoom_end`` of 1.0 means no zoom (plain trim). ``zoom_end`` > 1.0 adds a
    linear crop+scale push-in from ``start_zoom`` to ``zoom_end`` across the
    segment's duration.

    ``face_cx`` / ``face_cy`` are the normalised face centre used by the
    aspect-crop filter when the output aspect differs from the source.
    (0.5, 0.5) is the plain geometric centre — used when the clip has no
    face or the user requested ``output_aspect="source"``.
    """
    video_path: Path
    audio_path: Optional[Path]      # None for B-roll (no audio)
    start: float
    end: float
    zoom_end: float = 1.0
    start_zoom: float = 1.0
    face_cx: float = 0.5
    face_cy: float = 0.5

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


def _audio_polish_chain(enhance: bool, denoise: bool) -> str:
    """ffmpeg filter chain to clean up the final audio track.

    v2 chain (vs the previous highpass+comp+loudnorm):
    - afftdn with tuned params + anlmdn for residual broadband noise
    - highpass + lowpass bracketing the voice band (80 Hz – 12 kHz) kills
      sub-rumble + ultrasonic hiss outside speech
    - equalizer presence boost at ~3 kHz (voice intelligibility sweet spot)
    - deesser pulls down harsh S/SH sibilance
    - acompressor evens dynamics
    - loudnorm as the final gain-staging to streaming-standard -14 LUFS

    Order matters: denoise FIRST (so the compressor doesn't amplify noise
    during quiet frames) and loudnorm LAST (so it measures the final mix).
    """
    parts: list[str] = []
    if denoise:
        # Two-stage denoise:
        #   afftdn — fast FFT spectral subtraction, catches constant ambient
        #   anlmdn — Non-Local Means, catches broadband hiss/breath residue
        # Combined they produce noticeably cleaner speech than either alone,
        # especially on phone-recorded outdoor footage.
        parts += [
            "afftdn=nr=15:nf=-28:nt=w",
            "anlmdn=s=0.001:p=0.003:r=0.01",
        ]
    if enhance:
        parts += [
            # Voice band isolation — anything above/below is noise or hum.
            "highpass=f=80",
            "lowpass=f=12000",
            # Presence boost: +2 dB bell around 3 kHz where intelligibility
            # consonants live (K/T/S formants).
            "equalizer=f=3000:t=q:w=1.4:g=2",
            # De-essing: tame harsh S/SH that otherwise feel brittle on
            # earbuds after the loudnorm boost.
            "deesser=i=0.5:m=0.5:f=0.5",
            # Even dynamics: pulls shouts down, lifts whispers up.
            "acompressor=threshold=-20dB:ratio=3:attack=5:release=50",
            # Streaming-standard loudness. Runs last so it sees the whole
            # polished mix and picks one coherent gain for the full output.
            "loudnorm=I=-14:TP=-1.5:LRA=11",
        ]
    return ",".join(parts)


def _aspect_crop_chain(aspect: str, cx: float = 0.5, cy: float = 0.5) -> str:
    """Face-aware crop to the requested output aspect.

    ``cx`` and ``cy`` are the horizontal / vertical centre as a ratio of the
    source frame (0 = left/top, 1 = right/bottom). (0.5, 0.5) is plain
    geometric centre. The crop window is clamped so it never slides past
    the frame edge — a face at x=0 still produces a valid left-aligned
    window instead of a negative offset.

    "source" is a no-op.
    """
    if aspect == "9:16":
        w_expr = "min(iw,ih*9/16)"
    elif aspect == "16:9":
        w_expr = "min(iw,ih*16/9)"
    else:
        return ""
    h_expr = "min(ih,iw*16/9)" if aspect == "9:16" else "min(ih,iw*9/16)"
    # Offset expressions: ideal centre - half window, clamped to [0, frame-window].
    x_expr = f"max(0\\,min(iw-{w_expr}\\,iw*{cx:.4f}-{w_expr}/2))"
    y_expr = f"max(0\\,min(ih-{h_expr}\\,ih*{cy:.4f}-{h_expr}/2))"
    return f"crop=w='{w_expr}':h='{h_expr}':x='{x_expr}':y='{y_expr}'"


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


def _music_duck_filters(
    voice_label: str,
    music_fi: int,
    total_dur: float,
    music_cfg: MusicConfig,
    out_label: str = "[afinal]",
) -> tuple[list[str], str]:
    """Return filter-graph lines that duck music under voice.

    Takes the current voice audio label and the ffmpeg input index of the
    music file.  Returns (extra_filters, new_audio_label).

    Filter chain:
      1. asplit the voice so it feeds both the sidechain and the final mix.
      2. Loop the music (handles files shorter than the video).
      3. Fade the music out near the end of the video so it doesn't clip off.
      4. Set baseline volume.
      5. sidechaincompress: music is the signal, voice is the sidechain — the
         music ducks whenever the voice exceeds `threshold`.
      6. amix: blend voice + ducked music.  `normalize=0` preserves levels
         (avoids halving the voice when mixing); `duration=first` ends the
         audio when the voice ends.
    """
    fade_start = max(0.0, total_dur - music_cfg.fadeout_duration_s)
    fade_dur = min(music_cfg.fadeout_duration_s, total_dur)

    f: list[str] = [
        # Split voice: [_vspk] for the final mix, [_vsc] for the sidechain.
        f"{voice_label}asplit=2[_vspk][_vsc]",
        # Loop + fade + volume baseline for the music track.
        (
            f"[{music_fi}:a]"
            f"aloop=loop=-1:size=2000000000,"
            f"afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f},"
            f"volume={music_cfg.baseline_volume:.4f}"
            f"[_mloop]"
        ),
        # Sidechain compress: music ([_mloop]) ducked by voice ([_vsc]).
        (
            f"[_mloop][_vsc]"
            f"sidechaincompress="
            f"threshold={music_cfg.threshold:.4f}:"
            f"ratio={music_cfg.duck_ratio:.1f}:"
            f"attack={music_cfg.attack_ms:.0f}:"
            f"release={music_cfg.release_ms:.0f}:"
            f"level_sc=1"
            f"[_mducked]"
        ),
        # Final mix: voice + ducked music → out_label.
        f"[_vspk][_mducked]amix=inputs=2:duration=first:normalize=0:dropout_transition=3{out_label}",
    ]
    return f, out_label


def _build_render_cmd(
    kept: list[_RenderSegment],
    output_path: Path,
    cfg: RenderConfig,
    fast_preview: bool = False,
    subtitles_filename: str | None = None,
    audio_enhance: bool = False,
    audio_denoise: bool = False,
    output_aspect: str = "source",
    music_path: Path | None = None,
    music_cfg: MusicConfig | None = None,
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

    # Pre-input `-ss` + `-t` has the decoder seek to the nearest keyframe
    # and only read the segment's duration — orders of magnitude cheaper than
    # feeding the whole proxy through a `trim` filter. `-accurate_seek` keeps
    # it frame-accurate on seekable containers (mp4/wav here). `setpts` after
    # decoding still zeros each segment's PTS so concat behaves identically.
    fi = 0
    for rs in kept:
        cmd += [
            "-accurate_seek",
            "-ss", f"{rs.start:.6f}",
            "-t", f"{rs.duration:.6f}",
            "-i", str(rs.video_path.resolve()),
        ]
        video_idx.append(fi)
        fi += 1

        if rs.audio_path is not None:
            cmd += [
                "-accurate_seek",
                "-ss", f"{rs.start:.6f}",
                "-t", f"{rs.duration:.6f}",
                "-i", str(rs.audio_path.resolve()),
            ]
            audio_idx.append(fi)
            fi += 1
        else:
            audio_idx.append(None)

    # Music input: no time-seek here — ducking works on the full track length.
    music_fi: int | None = None
    if music_path is not None:
        cmd += ["-i", str(music_path.resolve())]
        music_fi = fi
        fi += 1

    filters: list[str] = []

    for i, rs in enumerate(kept):
        vi = video_idx[i]
        ai = audio_idx[i]

        video_chain = f"[{vi}:v]setpts=PTS-STARTPTS"
        if rs.has_zoom:
            video_chain += "," + _zoom_filter(rs)
        # Per-segment aspect crop with the proxy's face center. Each kept
        # segment inherits its proxy's face position (same clip = same crop
        # window); applying before concat means the output's crop follows
        # the subject when they move between clips.
        per_crop = _aspect_crop_chain(output_aspect, rs.face_cx, rs.face_cy)
        if per_crop:
            video_chain += "," + per_crop
        filters.append(f"{video_chain}[v{i}]")

        if ai is not None:
            filters.append(
                f"[{ai}:a]asetpts=PTS-STARTPTS[a{i}]"
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

        if fast_preview:
            # Hard-cut audio: concat all tracks with no crossfade. The
            # chained acrossfade adds non-trivial filter-graph setup for
            # every pair; for a scrubbing preview the click at each
            # boundary is unnoticeable.
            audio_in = "".join(f"[a{i}]" for i in range(N))
            filters.append(f"{audio_in}concat=n={N}:v=0:a=1[aout]")
        else:
            current = "[a0]"
            for i in range(1, N):
                label = f"[af{i}]" if i < N - 1 else "[aout]"
                filters.append(
                    f"{current}[a{i}]acrossfade=d={cf_s:.4f}:c1=tri:c2=tri{label}"
                )
                current = label
        audio_out = "[aout]"

    # Audio polish (denoise / enhance) runs on the concatenated audio so
    # loudness normalisation sees the whole output and sets one consistent
    # gain across all segments, instead of per-segment lurches.
    polish = _audio_polish_chain(audio_enhance, audio_denoise)
    if polish:
        filters.append(f"{audio_out}{polish}[apolished]")
        audio_out = "[apolished]"

    # Background music + sidechain ducking. Runs AFTER polish so the voice
    # audio the compressor sees is already cleaned up (accurate loudness →
    # more reliable ducking threshold).
    if music_fi is not None and music_cfg is not None:
        total_dur = sum(rs.duration for rs in kept)
        duck_filters, audio_out = _music_duck_filters(
            audio_out, music_fi, total_dur, music_cfg,
        )
        filters.extend(duck_filters)

    # Aspect-ratio crop is applied per-segment above (so each clip's face
    # position is respected). Nothing left to do here.

    # In fast_preview mode, cap the long edge at 720px before anything else
    # downstream. On 1080p/4K phone footage this ~4-9x fewer pixels for the
    # encoder to chew through, dominating the end-to-end render time. We
    # scale BEFORE subtitles so libass renders onto the final-size frame
    # (text stays sharp instead of being downscaled with the video).
    if fast_preview:
        filters.append(
            f"{video_pre_subs}scale='if(gt(iw,ih),min(iw,720),-2)':"
            f"'if(gt(iw,ih),-2,min(ih,720))'[vscaled]"
        )
        video_pre_subs = "[vscaled]"

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
        # Preview-grade bitrate: 2.5M at 1080p is enough to spot-check edits
        # without burning cycles on quality nobody will see — the final cut
        # lives in the NLE off the FCPXML + original footage anyway. Combined
        # with `-prio_speed 1` and no-SW fallback this is as fast as the Mac
        # HW encoder goes for a review scrub.
        video_codec_args = [
            "-c:v", "h264_videotoolbox",
            "-b:v", "2500k",
            "-prio_speed", "1",
            "-allow_sw", "0",
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
        str(output_path.resolve()),
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


def render_voiceover_vlog(
    proxies: list[ProxyInfo],
    voiceover_segments: list[Segment],
    voiceover_audio_path: Path,
    output_dir: Path,
    cfg: RenderConfig,
    output_name: str = "rough_cut.mp4",
    subtitles_path: Path | None = None,
    fast_preview: bool = False,
    audio_enhance: bool = False,
    audio_denoise: bool = False,
    output_aspect: str = "source",
    clip_schedule: list[str] | None = None,
    music_path: Path | None = None,
    music_cfg: MusicConfig | None = None,
) -> Path:
    """Render a voiceover-driven vlog.

    Audio = concatenation of KEPT voiceover segments (narration slices).
    Video = concatenation of visual proxy clips in supplied order, muted,
    trimmed to the kept-audio total duration (held last frame if the clips
    collectively run short). This is the "B-roll + narration" format where
    the clip sounds don't appear in the output — only the user's voice.

    ``voiceover_segments`` use the voiceover's absolute timeline (not any
    clip's). Each kept segment contributes ``[s.start, s.end]`` of the
    voiceover WAV; cut segments are skipped. Zoom and the hook aren't
    meaningful in this mode and are ignored.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name

    kept = [s for s in voiceover_segments
            if s.decision == "keep" and s.duration > 1e-3]
    kept.sort(key=lambda s: s.start)
    if not kept:
        raise RenderError("No kept voiceover segments to render.")
    audio_dur = sum(s.duration for s in kept)

    # Face centres per clip (once, reused regardless of schedule mode).
    face_centers: dict[str, tuple[float, float]] = {}
    if output_aspect != "source":
        from autocut.face_tracking import estimate_face_center
        for p in proxies:
            face_centers[p.clip_id] = estimate_face_center(p.proxy_path)

    proxy_by_id = {p.clip_id: p for p in proxies}
    use_schedule = (
        clip_schedule is not None
        and len(clip_schedule) == len(kept)
        and all(cid in proxy_by_id for cid in clip_schedule)
    )

    cmd: list[str] = ["ffmpeg", "-y"]
    filters: list[str] = []

    if use_schedule:
        assert clip_schedule is not None   # narrow for the type checker
        # Semantic-matched schedule: each kept voiceover segment plays a
        # specific clip. Add one -i per segment (same file can appear many
        # times — ffmpeg gives each its own decoder) with `-stream_loop -1`
        # so short clips loop to fit long segments. Per-segment trim +
        # aspect-crop then concat all per-segment video streams.
        for cid in clip_schedule:
            p = proxy_by_id[cid]
            cmd += ["-stream_loop", "-1", "-i", str(p.proxy_path.resolve())]
        vo_in = len(clip_schedule)
        cmd += ["-i", str(voiceover_audio_path.resolve())]
        music_fi_vo: int | None = None
        if music_path is not None:
            cmd += ["-i", str(music_path.resolve())]
            music_fi_vo = vo_in + 1

        for i, (seg, cid) in enumerate(zip(kept, clip_schedule)):
            cx, cy = face_centers.get(cid, (0.5, 0.5))
            crop = _aspect_crop_chain(output_aspect, cx, cy)
            chain = (
                f"[{i}:v]trim=duration={seg.duration:.6f},"
                f"setpts=PTS-STARTPTS"
            )
            if crop:
                chain += "," + crop
            filters.append(f"{chain}[v{i}]")
        video_in_lbl = "".join(f"[v{i}]" for i in range(len(kept)))
        if len(kept) == 1:
            video_pre = "[v0]"
        else:
            filters.append(f"{video_in_lbl}concat=n={len(kept)}:v=1:a=0[vcat]")
            video_pre = "[vcat]"
    else:
        # Default: upload-order concat, trimmed to audio duration (holds the
        # last frame if clips collectively run short).
        for p in proxies:
            cmd += ["-i", str(p.proxy_path.resolve())]
        vo_in = len(proxies)
        cmd += ["-i", str(voiceover_audio_path.resolve())]
        music_fi_vo: int | None = None
        if music_path is not None:
            cmd += ["-i", str(music_path.resolve())]
            music_fi_vo = vo_in + 1

        N = len(proxies)
        for i, p in enumerate(proxies):
            chain = f"[{i}:v]setpts=PTS-STARTPTS"
            cx, cy = face_centers.get(p.clip_id, (0.5, 0.5))
            crop = _aspect_crop_chain(output_aspect, cx, cy)
            if crop:
                chain += "," + crop
            filters.append(f"{chain}[v{i}]")
        if N == 1:
            video_pre = "[v0]"
        else:
            video_in_lbl = "".join(f"[v{i}]" for i in range(N))
            filters.append(f"{video_in_lbl}concat=n={N}:v=1:a=0[vcat]")
            video_pre = "[vcat]"
        filters.append(
            f"{video_pre}tpad=stop_mode=clone:stop_duration={audio_dur:.6f},"
            f"trim=duration={audio_dur:.6f},setpts=PTS-STARTPTS[vtimed]"
        )
        video_pre = "[vtimed]"

    # Audio chain: cut each kept segment out of the voiceover WAV in order,
    # concat them hard-cut (crossfades are overkill for narration flow).
    for i, s in enumerate(kept):
        filters.append(
            f"[{vo_in}:a]atrim=start={s.start:.6f}:end={s.end:.6f},"
            f"asetpts=PTS-STARTPTS[aseg{i}]"
        )
    audio_in_lbl = "".join(f"[aseg{i}]" for i in range(len(kept)))
    if len(kept) == 1:
        filters.append(f"{audio_in_lbl}anull[aout]")
    else:
        filters.append(f"{audio_in_lbl}concat=n={len(kept)}:v=0:a=1[aout]")
    audio_out_label = "[aout]"

    polish = _audio_polish_chain(audio_enhance, audio_denoise)
    if polish:
        filters.append(f"{audio_out_label}{polish}[apolished]")
        audio_out_label = "[apolished]"

    # Background music + sidechain ducking (same as the main render path).
    if music_fi_vo is not None and music_cfg is not None:
        duck_filters, audio_out_label = _music_duck_filters(
            audio_out_label, music_fi_vo, audio_dur, music_cfg,
        )
        filters.extend(duck_filters)

    # Aspect crop handled per-input above; nothing to do here.

    # Scale for preview, optional subs overlay (same policy as the main
    # render path keeps libass drawing onto final-size frames).
    if fast_preview:
        filters.append(
            f"{video_pre}scale='if(gt(iw,ih),min(iw,720),-2)':"
            f"'if(gt(iw,ih),-2,min(ih,720))'[vscaled]"
        )
        video_pre = "[vscaled]"
    if subtitles_path is not None:
        filters.append(
            f"{video_pre}subtitles={subtitles_path.name}[vout]"
        )
        video_out = "[vout]"
    else:
        video_out = video_pre

    # Codec picks mirror the main render path.
    if fast_preview and _supports_videotoolbox():
        video_codec_args = [
            "-c:v", "h264_videotoolbox",
            "-b:v", "2500k",
            "-prio_speed", "1",
            "-allow_sw", "0",
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
        "-map", audio_out_label,
        *video_codec_args,
        "-c:a", "aac", "-b:a", "128k",
        str(output_path.resolve()),
    ]
    cwd = (
        subtitles_path.parent.resolve()
        if subtitles_path is not None
        else None
    )
    _run(cmd, cwd=cwd)
    return output_path


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
    audio_enhance: bool = False,
    audio_denoise: bool = False,
    output_aspect: str = "source",
    music_path: Path | None = None,
    music_cfg: MusicConfig | None = None,
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

    # Compute face centre once per proxy — reused by every segment from that
    # clip. Skipped when the user kept the source aspect (no crop → nothing
    # to centre on).
    face_centers: dict[str, tuple[float, float]] = {}
    if output_aspect != "source":
        from autocut.face_tracking import estimate_face_center
        for p in proxies:
            face_centers[p.clip_id] = estimate_face_center(p.proxy_path)

    def _to_render_segment(s: Segment, allow_zoom: bool) -> _RenderSegment:
        zoom_end = _zoom_for(s) if allow_zoom else 1.0
        start_zoom = zoom.start_zoom if (zoom is not None and zoom_end > 1.0) else 1.0
        cx, cy = face_centers.get(s.clip_id, (0.5, 0.5))
        return _RenderSegment(
            video_path=proxy_by_id[s.clip_id].proxy_path,
            audio_path=proxy_by_id[s.clip_id].audio_path,
            start=s.start,
            end=s.end,
            zoom_end=zoom_end,
            start_zoom=start_zoom,
            face_cx=cx,
            face_cy=cy,
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
        audio_enhance=audio_enhance,
        audio_denoise=audio_denoise,
        output_aspect=output_aspect,
        music_path=music_path,
        music_cfg=music_cfg,
    )
    _run(cmd, cwd=cwd)
    return output_path
