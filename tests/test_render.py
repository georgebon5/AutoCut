from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autocut.config import RenderConfig
from autocut.models import ProxyInfo, Segment
from autocut.render import RenderError, _build_render_cmd, render
from tests.conftest import make_clip, skip_no_ffmpeg


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
# _SegmentTuple = (proxy_path, audio_wav_path, start, end)
# ---------------------------------------------------------------------------

def test_cmd_starts_with_ffmpeg():
    cmd = _build_render_cmd(
        [(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd


def test_cmd_single_segment_no_concat_filter():
    cmd = _build_render_cmd(
        [(Path("a.mp4"), Path("a.wav"), 1.0, 3.0)],
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
        [(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    maps = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "[v0]" in maps
    assert "[a0]" in maps


def test_cmd_multi_segment_has_concat_filter():
    kept = [(Path(f"{i}.mp4"), Path(f"{i}.wav"), float(i), float(i) + 1.0) for i in range(3)]
    cmd = _build_render_cmd(kept, Path("out.mp4"), default_cfg())
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=3" in fc


def test_cmd_multi_segment_has_acrossfade():
    kept = [(Path(f"{i}.mp4"), Path(f"{i}.wav"), float(i), float(i) + 1.0) for i in range(3)]
    cmd = _build_render_cmd(kept, Path("out.mp4"), default_cfg())
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert fc.count("acrossfade") == 2


def test_cmd_crossfade_uses_config_duration():
    kept = [(Path(f"{i}.mp4"), Path(f"{i}.wav"), 0.0, 2.0) for i in range(2)]
    cmd = _build_render_cmd(kept, Path("out.mp4"), RenderConfig(audio_crossfade_ms=20))
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "d=0.0200" in fc


def test_cmd_no_audio_path_uses_aevalsrc():
    cmd = _build_render_cmd(
        [(Path("broll.mp4"), None, 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "aevalsrc" in fc


def test_cmd_output_path_is_last():
    cmd = _build_render_cmd(
        [(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("my_output.mp4"),
        default_cfg(),
    )
    assert cmd[-1] == "my_output.mp4"


def test_cmd_uses_libx264_and_aac():
    cmd = _build_render_cmd(
        [(Path("a.mp4"), Path("a.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    assert "libx264" in cmd
    assert "aac" in cmd


def test_cmd_video_and_audio_are_separate_inputs():
    """Proxy (video) and WAV (audio) must be listed as separate -i inputs."""
    cmd = _build_render_cmd(
        [(Path("proxy.mp4"), Path("audio.wav"), 0.0, 2.0)],
        Path("out.mp4"),
        default_cfg(),
    )
    inputs = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-i"]
    assert "proxy.mp4" in inputs
    assert "audio.wav" in inputs


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
