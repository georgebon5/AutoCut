from __future__ import annotations

import pytest
from pathlib import Path

from autocut.config import IngestConfig
from autocut.ingest import (
    IngestError,
    _make_unique_ids,
    _parse_fps,
    _parse_probe,
    _probe,
    ingest_clips,
)
from tests.conftest import make_clip, skip_no_ffmpeg


# --- Pure unit tests (no ffmpeg) ---

def test_parse_fps_integer():
    assert _parse_fps("30/1") == 30.0


def test_parse_fps_ntsc():
    assert _parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)


def test_parse_fps_zero_denominator():
    assert _parse_fps("0/0") == 0.0


def test_parse_fps_bad_string():
    assert _parse_fps("bad") == 0.0


def test_unique_ids_no_collision():
    paths = [Path("a/clip.mp4"), Path("b/clip.mp4"), Path("c/other.mp4")]
    ids = _make_unique_ids(paths)
    assert len(set(ids)) == 3
    assert "other" in ids


def test_unique_ids_no_collision_order():
    paths = [Path("a/clip.mp4"), Path("b/clip.mp4")]
    ids = _make_unique_ids(paths)
    assert ids[0] == "clip_0"
    assert ids[1] == "clip_1"


def test_unique_ids_no_collision_when_all_unique():
    paths = [Path("a.mp4"), Path("b.mp4"), Path("c.mp4")]
    ids = _make_unique_ids(paths)
    assert ids == ["a", "b", "c"]


# --- Integration tests (require ffmpeg) ---

@skip_no_ffmpeg
def test_probe_returns_expected_keys(tmp_path):
    clip = make_clip(tmp_path / "test.mp4")
    probe = _probe(clip)
    assert "streams" in probe
    assert "format" in probe
    assert any(s["codec_type"] == "video" for s in probe["streams"])


@skip_no_ffmpeg
def test_probe_raises_on_bad_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(IngestError):
        _probe(bad)


@skip_no_ffmpeg
def test_parse_probe_dimensions(tmp_path):
    clip = make_clip(tmp_path / "test.mp4", duration=2.0, width=1280, height=720, fps=30)
    probe = _probe(clip)
    duration, fps, width, height, creation_time, has_audio = _parse_probe(probe)
    assert width == 1280
    assert height == 720
    assert fps == pytest.approx(30.0, abs=0.1)
    assert duration == pytest.approx(2.0, abs=0.1)
    assert has_audio is True


@skip_no_ffmpeg
def test_parse_probe_no_audio(tmp_path):
    clip = make_clip(tmp_path / "silent.mp4", with_audio=False)
    _, _, _, _, _, has_audio = _parse_probe(_probe(clip))
    assert has_audio is False


@skip_no_ffmpeg
def test_ingest_creates_proxy_and_audio(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4", duration=2.0)
    infos = ingest_clips([clip], tmp_path / "out", IngestConfig())

    assert len(infos) == 1
    info = infos[0]
    assert info.proxy_path.exists(), "proxy file not created"
    assert info.audio_path is not None and info.audio_path.exists(), "audio file not created"
    assert info.clip_id == "clip"


@skip_no_ffmpeg
def test_proxy_is_h264_cfr(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4", duration=2.0, fps=30)
    cfg = IngestConfig(target_fps=24)
    infos = ingest_clips([clip], tmp_path / "out", cfg)

    probe = _probe(infos[0].proxy_path)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")

    assert video["codec_name"] == "h264"
    avg = _parse_fps(video["avg_frame_rate"])
    r = _parse_fps(video["r_frame_rate"])
    assert avg == pytest.approx(r, abs=0.5), "proxy is not CFR"
    assert avg == pytest.approx(24.0, abs=0.5)


@skip_no_ffmpeg
def test_audio_is_16khz_mono(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4", duration=2.0)
    infos = ingest_clips([clip], tmp_path / "out", IngestConfig(audio_sample_rate=16000))

    audio = infos[0].audio_path
    assert audio is not None

    probe = _probe(audio)
    astream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert int(astream["sample_rate"]) == 16000
    assert int(astream["channels"]) == 1


@skip_no_ffmpeg
def test_silent_clip_has_no_audio_path(tmp_path):
    clip = make_clip(tmp_path / "broll.mp4", duration=2.0, with_audio=False)
    infos = ingest_clips([clip], tmp_path / "out", IngestConfig())
    assert infos[0].audio_path is None


@skip_no_ffmpeg
def test_sort_by_filename_when_no_creation_time(tmp_path):
    b_clip = make_clip(tmp_path / "b_clip.mp4", duration=1.0)
    a_clip = make_clip(tmp_path / "a_clip.mp4", duration=1.0)

    infos = ingest_clips([b_clip, a_clip], tmp_path / "out", IngestConfig())
    assert infos[0].clip_id == "a_clip"
    assert infos[1].clip_id == "b_clip"


@skip_no_ffmpeg
def test_duplicate_stems_get_unique_ids(tmp_path):
    dir_a = tmp_path / "a"; dir_a.mkdir()
    dir_b = tmp_path / "b"; dir_b.mkdir()
    clip_a = make_clip(dir_a / "clip.mp4", duration=1.0)
    clip_b = make_clip(dir_b / "clip.mp4", duration=1.0)

    infos = ingest_clips([clip_a, clip_b], tmp_path / "out", IngestConfig())
    ids = [i.clip_id for i in infos]
    assert len(set(ids)) == 2, f"ID collision: {ids}"
