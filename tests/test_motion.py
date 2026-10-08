"""Tests for autocut.motion — motion feature extraction via frame differencing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autocut.motion import _zero_motion, enrich_segments_motion, extract_motion_features
from autocut.models import Segment
from tests.conftest import skip_no_ffmpeg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_segment(start: float, end: float, decision: str = "keep") -> Segment:
    return Segment(clip_id="c", start=start, end=end, decision=decision)


def make_static_video(path: Path, duration_s: float = 3.0, fps: float = 10.0) -> Path:
    """All-black video: consecutive frames are identical → motion ≈ 0."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=black:size=32x32:rate={fps}",
        "-t", str(duration_s),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(path),
    ], capture_output=True, check=True)
    return path


def make_motion_video(path: Path, duration_s: float = 3.0, fps: float = 10.0) -> Path:
    """Animated test-pattern video: frames change every cycle → motion > 0."""
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=32x32:rate={fps}",
        "-t", str(duration_s),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(path),
    ], capture_output=True, check=True)
    return path


# ---------------------------------------------------------------------------
# _zero_motion
# ---------------------------------------------------------------------------

def test_zero_motion_has_correct_keys():
    assert set(_zero_motion().keys()) == {"motion_mean", "motion_std", "motion_max"}


def test_zero_motion_all_zero():
    assert all(v == 0.0 for v in _zero_motion().values())


# ---------------------------------------------------------------------------
# extract_motion_features — edge cases
# ---------------------------------------------------------------------------

def test_extract_nonexistent_file_returns_zeros(tmp_path):
    seg = make_segment(0.0, 1.0)
    feats = extract_motion_features(tmp_path / "nonexistent.mp4", seg)
    assert feats == _zero_motion()


@skip_no_ffmpeg
def test_extract_very_short_segment_returns_zeros(tmp_path):
    vid = make_static_video(tmp_path / "v.mp4")
    seg = make_segment(0.0, 0.05)   # 50ms < _MIN_DURATION_S
    feats = extract_motion_features(vid, seg)
    assert feats == _zero_motion()


# ---------------------------------------------------------------------------
# extract_motion_features — static video
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_static_video_motion_mean_near_zero(tmp_path):
    vid = make_static_video(tmp_path / "static.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    assert feats["motion_mean"] < 0.01


@skip_no_ffmpeg
def test_static_video_motion_max_near_zero(tmp_path):
    vid = make_static_video(tmp_path / "static.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    assert feats["motion_max"] < 0.01


@skip_no_ffmpeg
def test_static_video_returns_all_keys(tmp_path):
    vid = make_static_video(tmp_path / "static.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    assert set(feats.keys()) == {"motion_mean", "motion_std", "motion_max"}


# ---------------------------------------------------------------------------
# extract_motion_features — motion video
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_motion_video_mean_greater_than_zero(tmp_path):
    vid = make_motion_video(tmp_path / "motion.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    assert feats["motion_mean"] > 0.0


@skip_no_ffmpeg
def test_motion_video_max_ge_mean(tmp_path):
    vid = make_motion_video(tmp_path / "motion.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    assert feats["motion_max"] >= feats["motion_mean"]


@skip_no_ffmpeg
def test_motion_greater_than_static(tmp_path):
    static = make_static_video(tmp_path / "static.mp4")
    motion = make_motion_video(tmp_path / "motion.mp4")
    seg = make_segment(0.0, 2.0)
    static_feats = extract_motion_features(static, seg)
    motion_feats = extract_motion_features(motion, seg)
    assert motion_feats["motion_mean"] > static_feats["motion_mean"]


@skip_no_ffmpeg
def test_values_are_floats(tmp_path):
    vid = make_motion_video(tmp_path / "v.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    assert all(isinstance(v, float) for v in feats.values())


@skip_no_ffmpeg
def test_values_in_range_zero_to_one(tmp_path):
    """Pixel diffs are normalised to [0, 1]."""
    vid = make_motion_video(tmp_path / "v.mp4")
    feats = extract_motion_features(vid, make_segment(0.0, 2.0))
    for key, val in feats.items():
        assert 0.0 <= val <= 1.0, f"{key}={val} out of [0,1]"


# ---------------------------------------------------------------------------
# extract_motion_features — segment offset
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_segment_offset_static_part_returns_near_zero(tmp_path):
    """Extract from the static half of a two-part video → near-zero motion."""
    # First 1.5s: animated; last 1.5s: black static
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=32x32:rate=10",
        "-f", "lavfi", "-i", "color=black:size=32x32:rate=10",
        "-filter_complex", "[0:v]trim=0:1.5[a];[1:v]trim=0:1.5[b];[a][b]concat=n=2:v=1[out]",
        "-map", "[out]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(tmp_path / "mixed.mp4"),
    ], capture_output=True, check=True)

    vid = tmp_path / "mixed.mp4"
    motion_seg = extract_motion_features(vid, make_segment(0.0, 1.2))
    static_seg = extract_motion_features(vid, make_segment(1.5, 2.8))
    assert motion_seg["motion_mean"] > static_seg["motion_mean"]


# ---------------------------------------------------------------------------
# enrich_segments_motion
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_enrich_fills_keep_segments(tmp_path):
    vid = make_static_video(tmp_path / "v.mp4", duration_s=3.0)
    segs = [make_segment(0.0, 1.0), make_segment(1.5, 2.5)]
    enrich_segments_motion(segs, vid)
    for s in segs:
        assert "motion_mean" in s.features


@skip_no_ffmpeg
def test_enrich_skips_cut_segments(tmp_path):
    vid = make_static_video(tmp_path / "v.mp4", duration_s=2.0)
    seg = make_segment(0.0, 1.0, decision="cut")
    enrich_segments_motion([seg], vid)
    assert seg.features == {}


@skip_no_ffmpeg
def test_enrich_modifies_in_place(tmp_path):
    vid = make_motion_video(tmp_path / "v.mp4", duration_s=3.0)
    segs = [make_segment(0.0, 2.0)]
    enrich_segments_motion(segs, vid)
    assert segs[0].features.get("motion_mean", -1) >= 0.0


@skip_no_ffmpeg
def test_enrich_does_not_overwrite_existing_features(tmp_path):
    """Motion enrichment should add to, not replace, existing features."""
    vid = make_static_video(tmp_path / "v.mp4", duration_s=2.0)
    seg = make_segment(0.0, 1.0)
    seg.features["rms_energy"] = 0.05
    enrich_segments_motion([seg], vid)
    assert "rms_energy" in seg.features      # audio feature preserved
    assert "motion_mean" in seg.features     # motion feature added
