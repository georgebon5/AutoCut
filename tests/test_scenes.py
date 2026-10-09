"""Tests for autocut.scenes — PySceneDetect boundaries + near-boundary feature."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autocut.config import SceneConfig
from autocut.models import Segment
from autocut.scenes import (
    detect_scene_boundaries,
    enrich_segments_scenes,
    scene_boundary_near_feature,
)
from tests.conftest import skip_no_ffmpeg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def seg(start: float, end: float, clip_id: str = "c", decision: str = "keep") -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision=decision)


def make_two_shot_clip(path: Path, fps: int = 30) -> Path:
    """Concatenate 2s of black + 2s of a moving test pattern.

    The abrupt change at t=2.0 is a reliable scene boundary.
    """
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=black:size=320x240:rate={fps}:duration=2",
        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate={fps}:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def make_static_clip(path: Path, fps: int = 30, duration: float = 3.0) -> Path:
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=black:size=320x240:rate={fps}:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


# ---------------------------------------------------------------------------
# scene_boundary_near_feature — pure function
# ---------------------------------------------------------------------------

def test_near_feature_no_boundaries_returns_zero():
    assert scene_boundary_near_feature(seg(0, 1), [], window_s=0.5) == 0.0


def test_near_feature_boundary_at_segment_start():
    # Boundary exactly at segment start.
    assert scene_boundary_near_feature(seg(5.0, 10.0), [5.0], 0.5) == 1.0


def test_near_feature_boundary_within_window_of_start():
    assert scene_boundary_near_feature(seg(5.0, 10.0), [5.3], 0.5) == 1.0


def test_near_feature_boundary_within_window_of_end():
    assert scene_boundary_near_feature(seg(5.0, 10.0), [10.4], 0.5) == 1.0


def test_near_feature_boundary_outside_window_but_inside_segment_returns_zero():
    # Boundary at 7.0 is inside [5, 10] but not within window of either edge.
    assert scene_boundary_near_feature(seg(5.0, 10.0), [7.0], 0.5) == 0.0


def test_near_feature_boundary_far_returns_zero():
    assert scene_boundary_near_feature(seg(5.0, 10.0), [20.0], 0.5) == 0.0


def test_near_feature_zero_window_requires_exact_match():
    assert scene_boundary_near_feature(seg(5.0, 10.0), [5.0], 0.0) == 1.0
    assert scene_boundary_near_feature(seg(5.0, 10.0), [5.001], 0.0) == 0.0


def test_near_feature_multiple_boundaries_any_hit_wins():
    # First far, second close to end → feature is 1.
    assert scene_boundary_near_feature(seg(5.0, 10.0), [100.0, 10.1], 0.5) == 1.0


# ---------------------------------------------------------------------------
# enrich_segments_scenes
# ---------------------------------------------------------------------------

def test_enrich_sets_feature_on_keep():
    s = seg(5.0, 10.0, clip_id="a")
    cfg = SceneConfig(boundary_window_s=0.5)
    enrich_segments_scenes([s], {"a": [5.2]}, cfg)
    assert s.features["scene_boundary_near"] == 1.0


def test_enrich_skips_cut_segments():
    s = seg(5.0, 10.0, clip_id="a", decision="cut")
    enrich_segments_scenes([s], {"a": [5.0]}, SceneConfig())
    assert "scene_boundary_near" not in s.features


def test_enrich_handles_missing_clip_id():
    s = seg(5.0, 10.0, clip_id="unknown")
    enrich_segments_scenes([s], {"a": [5.0]}, SceneConfig())
    assert s.features["scene_boundary_near"] == 0.0


def test_enrich_handles_empty_boundaries_dict():
    s = seg(5.0, 10.0, clip_id="a")
    enrich_segments_scenes([s], {}, SceneConfig())
    assert s.features["scene_boundary_near"] == 0.0


def test_enrich_isolates_per_clip():
    """Boundaries for one clip must not leak into another clip's segments."""
    s_a = seg(5.0, 10.0, clip_id="a")
    s_b = seg(5.0, 10.0, clip_id="b")
    cfg = SceneConfig(boundary_window_s=0.5)
    enrich_segments_scenes([s_a, s_b], {"a": [5.0], "b": [100.0]}, cfg)
    assert s_a.features["scene_boundary_near"] == 1.0
    assert s_b.features["scene_boundary_near"] == 0.0


# ---------------------------------------------------------------------------
# detect_scene_boundaries — integration with real video (needs ffmpeg)
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_detect_boundaries_on_two_shot_clip(tmp_path: Path):
    """Black→testsrc concat should produce a boundary near t=2.0s."""
    video = make_two_shot_clip(tmp_path / "two_shot.mp4")
    boundaries = detect_scene_boundaries(video, SceneConfig())
    assert len(boundaries) >= 1, "expected at least one scene cut"
    # Boundary should land in the vicinity of the join (allow ±0.5s tolerance).
    assert any(abs(b - 2.0) < 0.5 for b in boundaries)


@skip_no_ffmpeg
def test_detect_boundaries_on_static_clip_returns_empty(tmp_path: Path):
    video = make_static_clip(tmp_path / "static.mp4")
    assert detect_scene_boundaries(video, SceneConfig()) == []


def test_detect_boundaries_nonexistent_file_returns_empty(tmp_path: Path):
    assert detect_scene_boundaries(tmp_path / "nope.mp4", SceneConfig()) == []
