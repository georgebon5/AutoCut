from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autocut.config import RenderConfig, ZoomConfig
from autocut.models import ProxyInfo, Segment
from autocut.render import RenderError, _RenderSegment, _build_render_cmd, render
from tests.conftest import make_clip, skip_no_ffmpeg


def rs(
    video: Path,
    audio: Path | None,
    start: float,
    end: float,
    zoom_end: float = 1.0,
    start_zoom: float = 1.0,
) -> _RenderSegment:
    return _RenderSegment(
        video_path=video, audio_path=audio,
        start=start, end=end,
        zoom_end=zoom_end, start_zoom=start_zoom,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_proxy(clip_id: str, video: Path, audio: Path | None = None) -> ProxyInfo:
    """Build a ProxyInfo where video and audio may be separate files."""
    return ProxyInfo(
        clip_id=clip_id,
        original_path=video,
        proxy_path=video,
        audio_path=audio,
        duration=10.0,
        fps=30.0,
        width=1280,
        height=720,
    )


def make_keep(clip_id: str, start: float, end: float) -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision="keep")


def make_cut(clip_id: str, start: float, end: float) -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision="cut")


def default_cfg(**kw) -> RenderConfig:
    return RenderConfig(audio_crossfade_ms=10, crf=28, preset="ultrafast", **kw)


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", str(path),
    ])
    return float(json.loads(out)["format"]["duration"])


# ---------------------------------------------------------------------------
# _build_render_cmd — pure unit tests (no ffmpeg)
# ---------------------------------------------------------------------------

def test_cmd_starts_with_ffmpeg():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd


def test_cmd_single_segment_no_concat_filter():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 1.0, 3.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat" not in fc
    assert "acrossfade" not in fc
    assert "trim=start=1" in fc
    assert "atrim=start=1" in fc


def test_cmd_single_segment_maps_v0_a0():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    maps = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "[v0]" in maps
    assert "[a0]" in maps


def test_cmd_multi_segment_has_concat_filter():
    kept = [rs(Path(f"{i}.mp4"), Path(f"{i}.wav"), float(i), float(i) + 1.0) for i in range(3)]
    cmd = _build_render_cmd(kept, Path("out.mp4"), default_cfg())
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=3" in fc


def test_cmd_multi_segment_has_acrossfade():
    kept = [rs(Path(f"{i}.mp4"), Path(f"{i}.wav"), float(i), float(i) + 1.0) for i in range(3)]
    cmd = _build_render_cmd(kept, Path("out.mp4"), default_cfg())
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc.count("acrossfade") == 2


def test_cmd_crossfade_uses_config_duration():
    kept = [rs(Path(f"{i}.mp4"), Path(f"{i}.wav"), 0.0, 2.0) for i in range(2)]
    cmd = _build_render_cmd(kept, Path("out.mp4"), RenderConfig(audio_crossfade_ms=20))
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "d=0.0200" in fc


def test_cmd_no_audio_path_uses_aevalsrc():
    cmd = _build_render_cmd(
        [rs(Path("broll.mp4"), None, 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "aevalsrc" in fc


def test_cmd_output_path_is_last():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("my_output.mp4"),
        default_cfg(),
    )
    assert cmd[-1] == "my_output.mp4"


def test_cmd_uses_libx264_and_aac():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    assert "libx264" in cmd
    assert "aac" in cmd


def test_cmd_video_and_audio_are_separate_inputs():
    """Proxy (video) and WAV (audio) must be listed as separate -i inputs."""
    cmd = _build_render_cmd(
        [rs(Path("proxy.mp4"), Path("audio.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    inputs = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-i"]
    assert "proxy.mp4" in inputs
    assert "audio.wav" in inputs


# ---------------------------------------------------------------------------
# Zoom filter — pure unit tests
# ---------------------------------------------------------------------------

def test_cmd_no_zoom_does_not_add_crop_or_scale():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0, zoom_end=1.0)],
        Path("out.mp4"), default_cfg(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=" not in fc
    assert "scale=" not in fc


def test_cmd_zoom_adds_crop_scale_chain():
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0, zoom_end=1.08)],
        Path("out.mp4"), default_cfg(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "crop=w='iw/" in fc
    assert "scale=w='iw*" in fc
    assert "eval=frame" in fc


def test_cmd_zoom_uses_segment_duration_in_denominator():
    """The time-varying zoom expression should reference the segment duration."""
    cmd = _build_render_cmd(
        [rs(Path("a.mp4"), Path("a.wav"), 1.5, 4.5, zoom_end=1.1)],   # duration 3s
        Path("out.mp4"), default_cfg(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "t/3.0000" in fc


def test_cmd_only_zoomed_segments_get_zoom_filter():
    kept = [
        rs(Path("a.mp4"), Path("a.wav"), 0.0, 2.0, zoom_end=1.0),   # no zoom
        rs(Path("b.mp4"), Path("b.wav"), 0.0, 2.0, zoom_end=1.1),   # zoom
    ]
    cmd = _build_render_cmd(kept, Path("out.mp4"), default_cfg())
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc.count("crop=w='iw/") == 1


# ---------------------------------------------------------------------------
# render() zoom integration — threshold routing
# ---------------------------------------------------------------------------

def _patch_run(monkeypatch) -> list[list[str]]:
    captured: list[list[str]] = []
    import autocut.render as render_mod
    monkeypatch.setattr(render_mod, "_run", lambda cmd: captured.append(cmd))
    return captured


def test_render_zoom_skips_segments_below_threshold(tmp_path, monkeypatch):
    captured = _patch_run(monkeypatch)
    proxy = make_proxy("c", tmp_path / "c.mp4", tmp_path / "c.wav")
    s_low = make_keep("c", 0.0, 2.0); s_low.interest_score = 0.3
    s_hi = make_keep("c", 3.0, 5.0); s_hi.interest_score = 0.9
    zoom = ZoomConfig(enabled=True, score_threshold=0.7, start_zoom=1.0, end_zoom=1.1)
    render([proxy], [s_low, s_hi], tmp_path, default_cfg(), zoom=zoom)
    fc = captured[0][captured[0].index("-filter_complex") + 1]
    assert fc.count("crop=w='iw/") == 1   # only one segment zoomed


def test_render_zoom_disabled_adds_no_zoom(tmp_path, monkeypatch):
    captured = _patch_run(monkeypatch)
    proxy = make_proxy("c", tmp_path / "c.mp4", tmp_path / "c.wav")
    s = make_keep("c", 0.0, 2.0); s.interest_score = 0.95
    zoom = ZoomConfig(enabled=False, score_threshold=0.5, end_zoom=1.1)
    render([proxy], [s], tmp_path, default_cfg(), zoom=zoom)
    fc = captured[0][captured[0].index("-filter_complex") + 1]
    assert "crop=" not in fc


def test_render_hook_never_zoomed_even_at_high_score(tmp_path, monkeypatch):
    captured = _patch_run(monkeypatch)
    proxy = make_proxy("c", tmp_path / "c.mp4", tmp_path / "c.wav")
    body = make_keep("c", 5.0, 8.0); body.interest_score = 0.95
    hook = make_keep("c", 0.0, 2.0); hook.interest_score = 0.99
    zoom = ZoomConfig(enabled=True, score_threshold=0.5, end_zoom=1.1)
    render([proxy], [body], tmp_path, default_cfg(), hook=hook, zoom=zoom)
    fc = captured[0][captured[0].index("-filter_complex") + 1]
    # Two segments total; only body (not hook) should be zoomed.
    assert fc.count("crop=w='iw/") == 1


# ---------------------------------------------------------------------------
# render() — unit tests (no ffmpeg)
# ---------------------------------------------------------------------------

def test_render_raises_on_no_kept_segments(tmp_path):
    proxy = make_proxy("c", tmp_path / "c.mp4")
    segments = [make_cut("c", 0.0, 5.0)]
    with pytest.raises(RenderError, match="No kept segments"):
        render([proxy], segments, tmp_path / "out", default_cfg())


def test_render_raises_on_segment_shorter_than_double_crossfade(tmp_path):
    proxy = make_proxy("c", tmp_path / "c.mp4", tmp_path / "c.wav")
    segs = [make_keep("c", 0.0, 0.005), make_keep("c", 1.0, 1.005)]
    with pytest.raises(RenderError, match="shorter than 2×crossfade"):
        render([proxy], segs, tmp_path / "out", default_cfg())


# ---------------------------------------------------------------------------
# Integration tests — require ffmpeg
# Real ingest produces video-only proxies + separate 16 kHz WAV files.
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_render_produces_mp4(tmp_path):
    from autocut.config import IngestConfig
    from autocut.ingest import ingest_clips
    clips = [make_clip(tmp_path / "clip.mp4", duration=5.0)]
    proxies = ingest_clips(clips, tmp_path / "ws", IngestConfig())
    segs = [make_keep("clip", 0.5, 2.0), make_keep("clip", 3.0, 4.5)]
    out = render(proxies, segs, tmp_path / "out", default_cfg())
    assert out.exists()
    assert out.suffix == ".mp4"


@skip_no_ffmpeg
def test_render_single_segment(tmp_path):
    from autocut.config import IngestConfig
    from autocut.ingest import ingest_clips
    clips = [make_clip(tmp_path / "clip.mp4", duration=5.0)]
    proxies = ingest_clips(clips, tmp_path / "ws", IngestConfig())
    segs = [make_keep("clip", 1.0, 3.0)]
    out = render(proxies, segs, tmp_path / "out", default_cfg())
    assert out.exists()
    assert ffprobe_duration(out) == pytest.approx(2.0, abs=0.2)


@skip_no_ffmpeg
def test_render_duration_accounts_for_crossfades(tmp_path):
    from autocut.config import IngestConfig
    from autocut.ingest import ingest_clips
    clips = [make_clip(tmp_path / "clip.mp4", duration=10.0)]
    proxies = ingest_clips(clips, tmp_path / "ws", IngestConfig())
    segs = [make_keep("clip", 0.5, 2.5), make_keep("clip", 5.0, 7.0)]
    out = render(proxies, segs, tmp_path / "out", RenderConfig(audio_crossfade_ms=10))
    dur = ffprobe_duration(out)
    assert dur == pytest.approx(4.0 - 0.01, abs=0.3)


@skip_no_ffmpeg
def test_render_no_audio_clip(tmp_path):
    from autocut.config import IngestConfig
    from autocut.ingest import ingest_clips
    clips = [make_clip(tmp_path / "broll.mp4", duration=5.0, with_audio=False)]
    proxies = ingest_clips(clips, tmp_path / "ws", IngestConfig())
    segs = [make_keep("broll", 0.5, 2.5)]
    out = render(proxies, segs, tmp_path / "out", default_cfg())
    assert out.exists()


@skip_no_ffmpeg
def test_render_multi_clip_order(tmp_path):
    from autocut.config import IngestConfig
    from autocut.ingest import ingest_clips
    clips = [make_clip(tmp_path / "a.mp4", duration=5.0),
             make_clip(tmp_path / "b.mp4", duration=5.0)]
    proxies = ingest_clips(clips, tmp_path / "ws", IngestConfig())
    segs = [make_keep("a", 0.5, 2.0), make_keep("b", 1.0, 3.0)]
    out = render(proxies, segs, tmp_path / "out", default_cfg())
    assert out.exists()
    assert ffprobe_duration(out) == pytest.approx(3.49, abs=0.3)
