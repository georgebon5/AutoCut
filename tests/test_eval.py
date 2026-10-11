"""Tests for autocut.eval — audio-matching precision/recall of EDL decisions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from autocut.eval import (
    EvalConfig,
    EvalError,
    EvalResult,
    evaluate_cut,
    extract_audio,
    match_segment,
)
from autocut.models import EDL, ProxyInfo, Segment
from tests.conftest import skip_no_ffmpeg

SR = 16000


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _tone(freq: float, duration: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _noise(duration: float, sr: int = SR, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (0.5 * rng.standard_normal(int(sr * duration))).astype(np.float32)


def _write_wav(path: Path, audio: np.ndarray, sr: int = SR) -> Path:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    wavfile.write(str(path), sr, pcm)
    return path


# ---------------------------------------------------------------------------
# match_segment
# ---------------------------------------------------------------------------

def test_identical_audio_peak_near_one():
    seg = _tone(440, 1.0)
    peak, _ = match_segment(seg, seg, SR)
    assert peak > 0.98


def test_segment_embedded_in_longer_published_matches():
    seg = _tone(440, 1.0)
    silence = np.zeros(int(SR * 0.5), dtype=np.float32)
    pub = np.concatenate([silence, seg, silence])
    peak, pos_s = match_segment(seg, pub, SR)
    assert peak > 0.9
    # Match should land close to where we inserted it (0.5 s).
    assert abs(pos_s - 0.5) < 0.05


def test_disjoint_audio_has_low_peak():
    seg = _tone(440, 1.0)
    pub = _noise(2.0)
    peak, _ = match_segment(seg, pub, SR)
    assert peak < 0.5


def test_published_shorter_than_segment_returns_zero_peak():
    seg = _tone(440, 2.0)
    pub = _tone(440, 0.5)
    peak, _ = match_segment(seg, pub, SR)
    assert peak == 0.0


def test_empty_segment_returns_zero():
    pub = _tone(440, 1.0)
    peak, _ = match_segment(np.zeros(0, dtype=np.float32), pub, SR)
    assert peak == 0.0


# ---------------------------------------------------------------------------
# evaluate_cut — synthetic end-to-end
# ---------------------------------------------------------------------------

def _build_raw_edl(
    tmp_path: Path, audio: np.ndarray, segments: list[tuple[float, float, str]],
) -> EDL:
    """Write a single raw clip's audio to disk and wrap it in an EDL."""
    audio_path = _write_wav(tmp_path / "c.wav", audio)
    proxy = ProxyInfo(
        clip_id="c",
        original_path=tmp_path / "c.mp4",
        proxy_path=tmp_path / "c_proxy.mp4",
        audio_path=audio_path,
        duration=len(audio) / SR,
        fps=30.0, width=1920, height=1080,
    )
    segs = [
        Segment(clip_id="c", start=s, end=e, decision=d)
        for s, e, d in segments
    ]
    return EDL(clips=[proxy], segments=segs)


def test_evaluate_perfect_pipeline(tmp_path: Path):
    """Pipeline says keep for regions in the published cut, cut for the rest."""
    # Raw audio: 3 distinct 2-second regions back to back.
    r1 = _tone(220, 2.0)
    r2 = _tone(500, 2.0)
    r3 = _tone(880, 2.0)
    raw = np.concatenate([r1, r2, r3])
    edl = _build_raw_edl(tmp_path, raw, segments=[
        (0.0, 2.0, "keep"),   # matches r1 → TP
        (2.0, 4.0, "cut"),    # editor dropped r2 → TN
        (4.0, 6.0, "keep"),   # matches r3 → TP
    ])
    published = _write_wav(tmp_path / "pub.wav", np.concatenate([r1, r3]))

    result = evaluate_cut(edl, published, EvalConfig())
    assert result.tp == 2
    assert result.fp == 0
    assert result.tn == 1
    assert result.fn == 0
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0


def test_evaluate_false_positive_when_pipeline_kept_cut_region(tmp_path: Path):
    r1 = _tone(220, 2.0)
    r2 = _tone(500, 2.0)
    raw = np.concatenate([r1, r2])
    edl = _build_raw_edl(tmp_path, raw, segments=[
        (0.0, 2.0, "keep"),   # editor also kept → TP
        (2.0, 4.0, "keep"),   # editor dropped → FP
    ])
    published = _write_wav(tmp_path / "pub.wav", r1)

    result = evaluate_cut(edl, published, EvalConfig())
    assert result.tp == 1
    assert result.fp == 1
    assert result.recall == 1.0        # editor kept 1 region, we kept it
    assert result.precision == 0.5


def test_evaluate_false_negative_when_pipeline_cut_something_editor_kept(tmp_path: Path):
    r1 = _tone(220, 2.0)
    r2 = _tone(500, 2.0)
    raw = np.concatenate([r1, r2])
    edl = _build_raw_edl(tmp_path, raw, segments=[
        (0.0, 2.0, "cut"),    # editor kept → FN
        (2.0, 4.0, "keep"),   # editor also kept → TP
    ])
    published = _write_wav(tmp_path / "pub.wav", np.concatenate([r1, r2]))

    result = evaluate_cut(edl, published, EvalConfig())
    assert result.fn == 1
    assert result.tp == 1
    assert result.precision == 1.0
    assert result.recall == 0.5


def test_short_segments_skipped(tmp_path: Path):
    raw = _tone(440, 2.0)
    edl = _build_raw_edl(tmp_path, raw, segments=[
        (0.0, 0.1, "keep"),   # too short → SKIP
        (0.5, 1.5, "keep"),
    ])
    pub = _write_wav(tmp_path / "pub.wav", raw)
    result = evaluate_cut(edl, pub, EvalConfig(min_segment_s=0.3))
    assert result.skipped == 1
    assert result.tp + result.fp + result.tn + result.fn == 1


def test_segment_with_missing_audio_path_skipped(tmp_path: Path):
    """Clips with no audio_path (B-roll) → their segments are skipped."""
    audio = _tone(440, 1.0)
    pub = _write_wav(tmp_path / "pub.wav", audio)

    broll = ProxyInfo(
        clip_id="broll",
        original_path=tmp_path / "b.mp4",
        proxy_path=tmp_path / "b_proxy.mp4",
        audio_path=None,
        duration=1.0, fps=30.0, width=1920, height=1080,
    )
    edl = EDL(clips=[broll], segments=[
        Segment(clip_id="broll", start=0.0, end=1.0, decision="keep"),
    ])
    result = evaluate_cut(edl, pub, EvalConfig())
    assert result.skipped == 1
    assert result.tp == result.fp == result.tn == result.fn == 0


def test_threshold_affects_decision(tmp_path: Path):
    """Noisy published audio that only partially matches: threshold flips outcome."""
    seg = _tone(440, 1.0)
    noise = _noise(1.0, seed=42)
    raw = seg
    edl = _build_raw_edl(tmp_path, raw, segments=[(0.0, 1.0, "keep")])
    # Published is the segment mixed with heavy noise — correlation drops below 1.
    mix = (seg + noise * 2.0).astype(np.float32)
    pub = _write_wav(tmp_path / "pub.wav", mix)

    strict = evaluate_cut(edl, pub, EvalConfig(match_threshold=0.95))
    lenient = evaluate_cut(edl, pub, EvalConfig(match_threshold=0.1))
    assert strict.segments[0].editor_kept is False
    assert lenient.segments[0].editor_kept is True


# ---------------------------------------------------------------------------
# extract_audio integration (needs ffmpeg)
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_extract_audio_produces_wav(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "color=black:size=160x120:rate=30:duration=1",
        "-shortest", "-c:v", "libx264", "-c:a", "aac",
        str(video),
    ], capture_output=True, check=True)
    out = tmp_path / "out.wav"
    extract_audio(video, out, sr=16000)
    assert out.exists()
    sr, pcm = wavfile.read(str(out))
    assert sr == 16000
    assert len(pcm) > 0


def test_extract_audio_on_missing_file_raises(tmp_path: Path):
    with pytest.raises(EvalError, match="ffmpeg"):
        extract_audio(tmp_path / "nope.mp4", tmp_path / "out.wav")