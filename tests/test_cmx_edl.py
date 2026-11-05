"""Tests for autocut.cmx_edl — CMX 3600 EDL generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocut.cmx_edl import _reel_name, _tc, edl_to_cmx, save_cmx
from autocut.models import EDL, ProxyInfo, RenderChunk, Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clip(clip_id: str, fps: float = 30.0, duration: float = 60.0) -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(f"/footage/{clip_id}.mp4"),
        proxy_path=Path(f"/tmp/{clip_id}.mp4"),
        audio_path=None,
        duration=duration,
        fps=fps,
        width=1920,
        height=1080,
        creation_time=None,
    )


def _seg(clip_id: str, start: float, end: float, decision: str = "keep") -> Segment:
    return Segment(
        clip_id=clip_id,
        start=start,
        end=end,
        decision=decision,
        interest_score=0.5,
        features={},
        reasons=[],
    )


def _edl(clips, segments, render_chunks, hook=None) -> EDL:
    return EDL(
        clips=clips,
        segments=segments,
        transcripts=[],
        hook=hook,
        render_chunks=render_chunks,
    )


# ---------------------------------------------------------------------------
# _tc
# ---------------------------------------------------------------------------

def test_tc_zero():
    assert _tc(0.0, 30.0) == "00:00:00:00"


def test_tc_one_second():
    assert _tc(1.0, 30.0) == "00:00:01:00"


def test_tc_one_minute():
    assert _tc(60.0, 30.0) == "00:01:00:00"


def test_tc_one_hour():
    assert _tc(3600.0, 30.0) == "01:00:00:00"


def test_tc_frames():
    # 1.5 s at 30 fps = 45 frames = 00:00:01:15
    assert _tc(1.5, 30.0) == "00:00:01:15"


def test_tc_24fps():
    # 1 s at 24 fps = frame 24 → 00:00:01:00; 1.5 s = 36 frames → 00:00:01:12
    assert _tc(1.5, 24.0) == "00:00:01:12"


def test_tc_non_integer_fps():
    # 29.97 rounded to 30 fps int
    tc = _tc(1.0, 29.97)
    assert tc.startswith("00:00:01:")


# ---------------------------------------------------------------------------
# _reel_name
# ---------------------------------------------------------------------------

def test_reel_name_simple():
    assert _reel_name("clip001") == "CLIP001"


def test_reel_name_truncated_to_8():
    assert len(_reel_name("averylongclipname")) == 8


def test_reel_name_strips_non_alphanumeric():
    assert _reel_name("clip-001_a") == "CLIP001A"


def test_reel_name_empty_fallback():
    assert _reel_name("---") == "AX"


def test_reel_name_uppercase():
    assert _reel_name("abcdef") == "ABCDEF"


# ---------------------------------------------------------------------------
# edl_to_cmx — structure
# ---------------------------------------------------------------------------

def test_cmx_starts_with_title():
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    result = edl_to_cmx(edl, title="My Edit")
    assert result.startswith("TITLE: My Edit")


def test_cmx_has_fcm_line():
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    result = edl_to_cmx(edl)
    assert "FCM: NON-DROP FRAME" in result


def test_cmx_event_number_format():
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    lines = edl_to_cmx(edl).splitlines()
    event_lines = [l for l in lines if l and l[0].isdigit()]
    assert event_lines[0].startswith("001")


def test_cmx_from_clip_name_comment():
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    result = edl_to_cmx(edl)
    assert "* FROM CLIP NAME:  c1.mp4" in result


def test_cmx_multiple_events():
    edl = _edl(
        clips=[_clip("c1"), _clip("c2")],
        segments=[_seg("c1", 0.0, 5.0), _seg("c2", 2.0, 7.0)],
        render_chunks=[
            RenderChunk(segment_index=0, output_start=0.0, output_end=5.0),
            RenderChunk(segment_index=1, output_start=5.0, output_end=10.0),
        ],
    )
    result = edl_to_cmx(edl)
    assert "001" in result
    assert "002" in result


def test_cmx_uses_v_track():
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    lines = edl_to_cmx(edl).splitlines()
    event_lines = [l for l in lines if l and l[0].isdigit()]
    assert " V " in event_lines[0] or "V     " in event_lines[0]


def test_cmx_uses_c_cut_type():
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    lines = edl_to_cmx(edl).splitlines()
    event_lines = [l for l in lines if l and l[0].isdigit()]
    assert " C " in event_lines[0] or "C     " in event_lines[0]


def test_cmx_hook_included():
    hook = _seg("c1", 1.0, 3.0)
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 5.0, 10.0)],
        render_chunks=[
            RenderChunk(segment_index=-1, output_start=0.0, output_end=2.0),
            RenderChunk(segment_index=0, output_start=2.0, output_end=7.0),
        ],
        hook=hook,
    )
    result = edl_to_cmx(edl)
    assert "001" in result
    assert "002" in result


def test_cmx_timecodes_in_event_line():
    edl = _edl(
        clips=[_clip("c1", fps=30.0)],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    lines = edl_to_cmx(edl).splitlines()
    event_lines = [l for l in lines if l and l[0].isdigit()]
    # Should contain 4 timecodes in HH:MM:SS:FF format
    import re
    tcs = re.findall(r"\d{2}:\d{2}:\d{2}:\d{2}", event_lines[0])
    assert len(tcs) == 4


# ---------------------------------------------------------------------------
# save_cmx
# ---------------------------------------------------------------------------

def test_save_cmx_writes_file(tmp_path):
    edl = _edl(
        clips=[_clip("c1")],
        segments=[_seg("c1", 0.0, 5.0)],
        render_chunks=[RenderChunk(segment_index=0, output_start=0.0, output_end=5.0)],
    )
    out = tmp_path / "test.edl"
    save_cmx(edl, out, title="Test")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "TITLE: Test" in content
