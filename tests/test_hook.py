"""Tests for autocut.hook — opening-hook segment selection + EDL round-trip."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autocut.config import HookConfig, RenderConfig
from autocut.edl import edl_from_dict, edl_to_dict, load_edl, save_edl
from autocut.hook import find_hook_segment
from autocut.models import EDL, ProxyInfo, Segment
from autocut.render import render
from tests.conftest import make_clip, skip_no_ffmpeg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_seg(
    start: float,
    end: float,
    score: float = 0.0,
    decision: str = "keep",
    clip_id: str = "c",
) -> Segment:
    s = Segment(clip_id=clip_id, start=start, end=end, decision=decision)
    s.interest_score = score
    return s


# ---------------------------------------------------------------------------
# find_hook_segment
# ---------------------------------------------------------------------------

def test_no_segments_returns_none():
    assert find_hook_segment([], HookConfig()) is None


def test_no_keep_segments_returns_none():
    segs = [make_seg(0, 5, score=1.0, decision="cut")]
    assert find_hook_segment(segs, HookConfig()) is None


def test_short_only_segments_returns_none():
    # All candidates are shorter than min_duration_s.
    segs = [make_seg(0, 0.5, score=1.0), make_seg(1, 1.3, score=0.9)]
    assert find_hook_segment(segs, HookConfig(min_duration_s=1.0)) is None


def test_picks_highest_score_among_eligible():
    segs = [
        make_seg(0, 2, score=0.3),
        make_seg(2, 4, score=0.9),  # highest, eligible
        make_seg(4, 6, score=0.5),
    ]
    hook = find_hook_segment(segs, HookConfig(min_duration_s=1.0, max_duration_s=3.0))
    assert hook is not None
    assert hook.interest_score == pytest.approx(0.9)
    assert (hook.start, hook.end) == (2.0, 4.0)


def test_short_high_score_skipped_in_favour_of_long_lower_score():
    """A 0.5s segment with high score can't be used; a 2s with lower score wins."""
    segs = [
        make_seg(0, 0.5, score=1.0),   # too short
        make_seg(1, 3, score=0.6),     # eligible, picked
    ]
    hook = find_hook_segment(segs, HookConfig(min_duration_s=1.0))
    assert hook is not None
    assert hook.interest_score == pytest.approx(0.6)


def test_long_segment_centre_sliced():
    seg = make_seg(10.0, 20.0, score=0.9)   # 10s long, max is 3s
    hook = find_hook_segment([seg], HookConfig(min_duration_s=1.0, max_duration_s=3.0))
    assert hook is not None
    assert hook.start == pytest.approx(13.5)
    assert hook.end == pytest.approx(16.5)
    assert hook.duration == pytest.approx(3.0)


def test_segment_within_window_kept_whole():
    seg = make_seg(5.0, 7.5, score=0.8)   # 2.5s — fits in [1, 3]
    hook = find_hook_segment([seg], HookConfig(min_duration_s=1.0, max_duration_s=3.0))
    assert (hook.start, hook.end) == (5.0, 7.5)


def test_hook_reason_set():
    segs = [make_seg(0, 2, score=0.8)]
    hook = find_hook_segment(segs, HookConfig())
    assert "hook (opening)" in hook.reasons


def test_hook_is_new_object_source_unchanged():
    src = make_seg(0, 2, score=0.8)
    hook = find_hook_segment([src], HookConfig())
    assert hook is not src
    assert src.reasons == []  # source untouched


def test_hook_copies_clip_id_and_features():
    src = make_seg(0, 2, score=0.8, clip_id="clip42")
    src.features = {"rms_energy": 0.1, "keyword_hits": 2.0}
    hook = find_hook_segment([src], HookConfig())
    assert hook.clip_id == "clip42"
    assert hook.features == {"rms_energy": 0.1, "keyword_hits": 2.0}
    assert hook.features is not src.features    # independent dict


# ---------------------------------------------------------------------------
# EDL round-trip with hook
# ---------------------------------------------------------------------------

def test_edl_roundtrip_without_hook(tmp_path: Path):
    edl = EDL(clips=[], segments=[])
    p = tmp_path / "edl.json"
    save_edl(edl, p)
    loaded = load_edl(p)
    assert loaded.hook is None


def test_edl_roundtrip_with_hook(tmp_path: Path):
    hook = make_seg(5.0, 7.0, score=0.9, clip_id="c")
    hook.reasons = ["hook (opening)"]
    edl = EDL(clips=[], segments=[], hook=hook)
    p = tmp_path / "edl.json"
    save_edl(edl, p)
    loaded = load_edl(p)
    assert loaded.hook is not None
    assert loaded.hook.start == pytest.approx(5.0)
    assert loaded.hook.end == pytest.approx(7.0)
    assert "hook (opening)" in loaded.hook.reasons


def test_edl_to_dict_contains_hook_key():
    hook = make_seg(0.0, 2.0, score=0.5, clip_id="c")
    edl = EDL(clips=[], segments=[], hook=hook)
    d = edl_to_dict(edl)
    assert d.get("hook") is not None


def test_edl_from_dict_handles_null_hook():
    """Legacy EDLs without a hook field must still load."""
    base = {
        "clips": [],
        "segments": [],
        "transcripts": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "version": "1.0",
    }
    assert edl_from_dict(base).hook is None


# ---------------------------------------------------------------------------
# render with hook — integration (needs ffmpeg)
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_render_with_hook_prepends_duration(tmp_path: Path):
    """Rendered output should include hook duration on top of the kept segments."""
    import json
    video = make_clip(tmp_path / "v.mp4", duration=5.0)
    audio = tmp_path / "v.wav"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video), "-vn",
        "-ar", "16000", "-ac", "1", str(audio),
    ], capture_output=True, check=True)

    proxy = ProxyInfo(
        clip_id="c",
        original_path=video,
        proxy_path=video,
        audio_path=audio,
        duration=5.0,
        fps=30.0,
        width=1280,
        height=720,
    )
    kept = [Segment(clip_id="c", start=0.5, end=2.5, decision="keep")]  # 2s
    hook = Segment(clip_id="c", start=3.0, end=4.5, decision="keep")    # 1.5s
    cfg = RenderConfig(audio_crossfade_ms=10, crf=28, preset="ultrafast")

    out = render([proxy], kept, tmp_path, cfg, hook=hook)
    assert out.exists()

    probe = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", str(out),
    ])
    dur = float(json.loads(probe)["format"]["duration"])
    # Expected total ≈ 2.0 + 1.5 = 3.5s (one acrossfade of 10ms barely changes it).
    assert dur == pytest.approx(3.5, abs=0.2)


def test_render_with_only_hook_no_kept(tmp_path: Path):
    """If there are no kept segments but there IS a hook, render should still work.

    Pure unit variant: we check `render` doesn't raise on `no kept segments`
    when a hook is supplied (full ffmpeg integration is covered by the test
    above).
    """
    # Minimal mock: patch _run so we don't actually invoke ffmpeg.
    import autocut.render as render_mod
    calls: list[list[str]] = []
    orig = render_mod._run
    render_mod._run = lambda cmd: calls.append(cmd)
    try:
        proxy = ProxyInfo(
            clip_id="c", original_path=Path("v.mp4"), proxy_path=Path("v.mp4"),
            audio_path=Path("v.wav"), duration=5.0, fps=30.0, width=1280, height=720,
        )
        hook = Segment(clip_id="c", start=0.0, end=2.0, decision="keep")
        render_mod.render([proxy], [], tmp_path, RenderConfig(), hook=hook)
    finally:
        render_mod._run = orig
    assert calls, "ffmpeg command was not built"
