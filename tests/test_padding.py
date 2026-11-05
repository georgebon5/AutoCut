"""Tests for autocut.padding — silence-padding segment expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocut.models import ProxyInfo, Segment
from autocut.padding import apply_padding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seg(clip_id: str, start: float, end: float, decision: str = "keep") -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision=decision)


def _proxy(clip_id: str, duration: float) -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(f"{clip_id}.mp4"),
        proxy_path=Path(f"{clip_id}_proxy.mp4"),
        audio_path=None,
        duration=duration,
        fps=30.0,
        width=1920,
        height=1080,
    )


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

def test_zero_padding_returns_identical_copy():
    segs = [_seg("c", 1.0, 2.0)]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.0)
    assert result[0].start == 1.0
    assert result[0].end == 2.0
    assert result is not segs


def test_negative_padding_returns_copy_unchanged():
    segs = [_seg("c", 1.0, 2.0)]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, -0.1)
    assert result[0].start == 1.0
    assert result[0].end == 2.0


def test_padding_expands_start_and_end():
    segs = [_seg("c", 1.0, 2.0)]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.15)
    assert result[0].start == pytest.approx(0.85)
    assert result[0].end == pytest.approx(2.15)


def test_padding_does_not_mutate_input():
    segs = [_seg("c", 1.0, 2.0)]
    proxies = [_proxy("c", 10.0)]
    apply_padding(segs, proxies, 0.2)
    assert segs[0].start == 1.0
    assert segs[0].end == 2.0


# ---------------------------------------------------------------------------
# Clip-boundary clamping
# ---------------------------------------------------------------------------

def test_padding_clamps_start_to_zero():
    segs = [_seg("c", 0.1, 1.0)]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.3)
    assert result[0].start == 0.0


def test_padding_clamps_end_to_clip_duration():
    segs = [_seg("c", 8.0, 9.9)]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.3)
    assert result[0].end == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Cut segments stay unchanged
# ---------------------------------------------------------------------------

def test_cut_segment_not_padded():
    segs = [_seg("c", 1.0, 2.0, decision="cut")]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.2)
    assert result[0].start == 1.0
    assert result[0].end == 2.0


def test_mixed_keep_and_cut():
    segs = [
        _seg("c", 0.0, 1.0, decision="cut"),
        _seg("c", 2.0, 3.0, decision="keep"),
        _seg("c", 4.0, 5.0, decision="cut"),
    ]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.1)
    assert result[0].start == 0.0   # cut: unchanged
    assert result[1].start == pytest.approx(1.9)
    assert result[1].end == pytest.approx(3.1)
    assert result[2].end == 5.0    # cut: unchanged


# ---------------------------------------------------------------------------
# Adjacent kept segments: overlap resolution
# ---------------------------------------------------------------------------

def test_adjacent_segments_do_not_overlap():
    # Gap between segs is 0.4 s; padding is 0.3 s each side → would collide
    segs = [
        _seg("c", 1.0, 2.0),
        _seg("c", 2.4, 3.4),
    ]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.3)
    assert result[0].end <= result[1].start


def test_adjacent_segments_midpoint_at_gap_centre():
    # Original ends: 2.0, starts: 2.4 → gap midpoint = 2.2
    segs = [
        _seg("c", 1.0, 2.0),
        _seg("c", 2.4, 3.4),
    ]
    proxies = [_proxy("c", 10.0)]
    result = apply_padding(segs, proxies, 0.3)
    assert result[0].end == pytest.approx(2.2)
    assert result[1].start == pytest.approx(2.2)


def test_multiple_clips_independent():
    segs = [
        _seg("a", 1.0, 2.0),
        _seg("b", 1.0, 2.0),
    ]
    proxies = [_proxy("a", 10.0), _proxy("b", 10.0)]
    result = apply_padding(segs, proxies, 0.2)
    assert result[0].start == pytest.approx(0.8)
    assert result[1].start == pytest.approx(0.8)


def test_unknown_clip_does_not_crash():
    """A segment whose clip_id is not in proxies: no clip-duration cap applied."""
    segs = [_seg("mystery", 1.0, 2.0)]
    proxies = []
    result = apply_padding(segs, proxies, 0.1)
    assert result[0].start == pytest.approx(0.9)
    assert result[0].end == pytest.approx(2.1)
