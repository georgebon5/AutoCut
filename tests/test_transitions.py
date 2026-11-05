"""Unit tests for video transitions (xfade filter generation)."""

from __future__ import annotations

import pytest

from autocut.config import TransitionConfig
from autocut.render import _VALID_XFADE, _video_xfade_chain


# ---------------------------------------------------------------------------
# _video_xfade_chain
# ---------------------------------------------------------------------------

def test_two_segments_single_xfade():
    filters, out = _video_xfade_chain(2, [5.0, 3.0], "fade", 0.3)
    assert len(filters) == 1
    assert "xfade=transition=fade" in filters[0]
    assert "duration=0.3000" in filters[0]
    # offset = 5.0 - 0.3 = 4.7
    assert "offset=4.7000" in filters[0]
    assert out == "[vxout]"


def test_three_segments_two_xfades():
    # d0=5, d1=4, d2=6; td=0.5
    # offset0 = 5.0 - 0.5 = 4.5
    # cumulative after first = 5 + 4 - 0.5 = 8.5
    # offset1 = 8.5 - 0.5 = 8.0
    filters, out = _video_xfade_chain(3, [5.0, 4.0, 6.0], "fadeblack", 0.5)
    assert len(filters) == 2
    assert "offset=4.5000" in filters[0]
    assert "offset=8.0000" in filters[1]
    assert "transition=fadeblack" in filters[1]
    # intermediate label is [vx1] (loop i=1), final output [vxout] via [vx1]
    assert "[vx1]" in filters[0]
    assert "[vx1]" in filters[1]
    assert out == "[vxout]"


def test_offset_clamped_to_zero_for_tiny_first_segment():
    # If td > d0, offset would be negative — clamp to 0.
    filters, _ = _video_xfade_chain(2, [0.2, 5.0], "fade", 0.5)
    assert "offset=0.0000" in filters[0]


def test_intermediate_labels_are_unique():
    filters, _ = _video_xfade_chain(4, [5.0, 4.0, 3.0, 6.0], "fade", 0.3)
    # For N=4: i=1 → [vx1], i=2 → [vx2], i=3 → [vxout]
    assert "[vx1]" in filters[0]
    assert "[vx2]" in filters[1]
    assert filters[2].endswith("[vxout]")


# ---------------------------------------------------------------------------
# TransitionConfig validation
# ---------------------------------------------------------------------------

def test_valid_xfade_set_contains_expected_types():
    assert "fade" in _VALID_XFADE
    assert "fadeblack" in _VALID_XFADE
    assert "fadewhite" in _VALID_XFADE
    assert "none" not in _VALID_XFADE


def test_transition_config_defaults():
    tc = TransitionConfig()
    assert tc.type == "none"
    assert tc.duration_ms == 300


# ---------------------------------------------------------------------------
# Integration: _build_render_cmd uses xfade when transition enabled
# ---------------------------------------------------------------------------

def _make_rs(duration: float = 5.0):
    """Minimal _RenderSegment-like object for _build_render_cmd."""
    from pathlib import Path
    from autocut.render import _RenderSegment
    return _RenderSegment(
        video_path=Path("proxy.mp4"),
        audio_path=Path("audio.wav"),
        start=0.0,
        end=duration,
        zoom_end=1.0,
        start_zoom=1.0,
        face_cx=0.5,
        face_cy=0.5,
    )


def test_build_render_cmd_uses_concat_by_default():
    from autocut.config import RenderConfig
    from autocut.render import _build_render_cmd
    from pathlib import Path

    kept = [_make_rs(5.0), _make_rs(4.0)]
    cmd = _build_render_cmd(
        kept, Path("/tmp/out.mp4"), RenderConfig(),
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat" in fc
    assert "xfade" not in fc


def test_build_render_cmd_uses_xfade_when_transition_set():
    from autocut.config import RenderConfig
    from autocut.render import _build_render_cmd
    from pathlib import Path

    kept = [_make_rs(5.0), _make_rs(4.0)]
    tc = TransitionConfig(type="fade", duration_ms=300)
    cmd = _build_render_cmd(
        kept, Path("/tmp/out.mp4"), RenderConfig(),
        transition_cfg=tc,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade" in fc
    assert "concat" not in fc


def test_build_render_cmd_single_segment_no_xfade():
    """Single segment → no concat or xfade regardless of transition config."""
    from autocut.config import RenderConfig
    from autocut.render import _build_render_cmd
    from pathlib import Path

    kept = [_make_rs(5.0)]
    tc = TransitionConfig(type="fade", duration_ms=300)
    cmd = _build_render_cmd(
        kept, Path("/tmp/out.mp4"), RenderConfig(),
        transition_cfg=tc,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade" not in fc
    assert "concat" not in fc


def test_xfade_duration_clamped_to_40pct_of_shortest_segment():
    """When transition_duration > 40% of shortest segment, it must be clamped."""
    from autocut.config import RenderConfig
    from autocut.render import _build_render_cmd
    from pathlib import Path

    kept = [_make_rs(1.0), _make_rs(5.0)]  # shortest = 1.0s
    tc = TransitionConfig(type="fade", duration_ms=800)  # 0.8s > 0.4s threshold
    cmd = _build_render_cmd(
        kept, Path("/tmp/out.mp4"), RenderConfig(),
        transition_cfg=tc,
    )
    fc = cmd[cmd.index("-filter_complex") + 1]
    # Actual xfade duration must be <= 0.4s (40% of 1.0s)
    import re
    m = re.search(r"duration=([\d.]+)", fc)
    assert m is not None
    actual_dur = float(m.group(1))
    assert actual_dur <= 0.4 + 1e-9
