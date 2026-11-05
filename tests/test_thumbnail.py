"""Unit tests for autocut.thumbnail."""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autocut.models import EDL, ProxyInfo, Segment
from autocut.thumbnail import _best_output_time, extract_thumbnail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proxy(clip_id: str = "c") -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(f"{clip_id}.mp4"),
        proxy_path=Path(f"{clip_id}_proxy.mp4"),
        audio_path=Path(f"{clip_id}.wav"),
        duration=10.0, fps=30.0, width=1280, height=720,
    )


def _seg(start: float, end: float, decision: str = "keep", score: float = 0.5) -> Segment:
    s = Segment(clip_id="c", start=start, end=end, decision=decision)
    s.interest_score = score
    return s


def _chunk(segment_index: int, out_start: float, out_end: float):
    return types.SimpleNamespace(
        segment_index=segment_index,
        output_start=out_start,
        output_end=out_end,
    )


def _edl(segs, chunks) -> EDL:
    edl = EDL(clips=[_proxy()], segments=segs)
    edl.render_chunks = chunks
    return edl


# ---------------------------------------------------------------------------
# _best_output_time
# ---------------------------------------------------------------------------

def test_no_chunks_returns_none():
    edl = _edl([_seg(0, 1)], [])
    assert _best_output_time(edl) is None


def test_hook_chunk_skipped():
    segs = [_seg(0, 1, score=0.9)]
    chunks = [_chunk(-1, 0.0, 1.0)]  # hook segment_index=-1
    assert _best_output_time(_edl(segs, chunks)) is None


def test_cut_segment_skipped():
    segs = [_seg(0, 1, decision="cut", score=0.9)]
    chunks = [_chunk(0, 0.0, 1.0)]
    assert _best_output_time(_edl(segs, chunks)) is None


def test_picks_highest_interest_midpoint():
    segs = [
        _seg(0, 1, score=0.3),
        _seg(1, 2, score=0.9),
        _seg(2, 3, score=0.5),
    ]
    chunks = [
        _chunk(0, 0.0, 1.0),
        _chunk(1, 1.0, 2.0),
        _chunk(2, 2.0, 3.0),
    ]
    t = _best_output_time(_edl(segs, chunks))
    # midpoint of chunk 1 (output 1.0–2.0)
    assert t == pytest.approx(1.5)


def test_out_of_range_segment_index_skipped():
    segs = [_seg(0, 1, score=0.9)]
    chunks = [_chunk(99, 0.0, 1.0)]  # index 99 doesn't exist
    assert _best_output_time(_edl(segs, chunks)) is None


def test_missing_interest_score_treated_as_zero():
    s = Segment(clip_id="c", start=0, end=1, decision="keep")
    # no interest_score attribute set
    segs = [s]
    chunks = [_chunk(0, 0.0, 1.0)]
    t = _best_output_time(_edl(segs, chunks))
    assert t == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# extract_thumbnail
# ---------------------------------------------------------------------------

def test_extract_thumbnail_returns_true_on_success(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "thumb.jpg"

    segs = [_seg(0, 4, score=0.8), _seg(4, 8, score=0.3)]
    chunks = [_chunk(0, 0.0, 4.0), _chunk(1, 4.0, 8.0)]
    edl = _edl(segs, chunks)

    captured: list[list] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        out.write_bytes(b"jpeg-data")
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("autocut.thumbnail.subprocess.run", side_effect=_fake_run):
        ok = extract_thumbnail(video, edl, out)

    assert ok is True
    assert out.exists()
    # -ss value should be midpoint of best chunk (0+4)/2 = 2.0
    assert "-ss" in captured[0]
    ss_val = float(captured[0][captured[0].index("-ss") + 1])
    assert ss_val == pytest.approx(2.0)


def test_extract_thumbnail_fallback_time_when_no_chunks(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "thumb.jpg"
    edl = _edl([], [])

    captured: list[list] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        out.write_bytes(b"jpeg-data")
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("autocut.thumbnail.subprocess.run", side_effect=_fake_run):
        extract_thumbnail(video, edl, out)

    ss_val = float(captured[0][captured[0].index("-ss") + 1])
    assert ss_val == pytest.approx(0.5)


def test_extract_thumbnail_returns_false_on_ffmpeg_error(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "thumb.jpg"
    edl = _edl([_seg(0, 1)], [_chunk(0, 0.0, 1.0)])

    def _fail(*args, **kwargs):
        r = MagicMock()
        r.returncode = 1
        return r

    with patch("autocut.thumbnail.subprocess.run", side_effect=_fail):
        ok = extract_thumbnail(video, edl, out)

    assert ok is False


def test_extract_thumbnail_returns_false_on_os_error(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "thumb.jpg"
    edl = _edl([_seg(0, 1)], [_chunk(0, 0.0, 1.0)])

    with patch("autocut.thumbnail.subprocess.run", side_effect=OSError("not found")):
        ok = extract_thumbnail(video, edl, out)

    assert ok is False


def test_extract_thumbnail_uses_q2_quality(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    out = tmp_path / "thumb.jpg"
    edl = _edl([_seg(0, 1)], [_chunk(0, 0.0, 1.0)])

    captured: list[list] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        out.write_bytes(b"jpeg-data")
        r = MagicMock()
        r.returncode = 0
        return r

    with patch("autocut.thumbnail.subprocess.run", side_effect=_fake_run):
        extract_thumbnail(video, edl, out)

    cmd = captured[0]
    assert "-q:v" in cmd
    assert cmd[cmd.index("-q:v") + 1] == "2"
