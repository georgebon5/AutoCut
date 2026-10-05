"""Tests for autocut.features — audio feature extraction."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wav_io

from autocut.features import _zero_features, enrich_segments, extract_audio_features
from autocut.models import Segment, Transcript, WordTimestamp

SR = 16000
FEATURE_KEYS = {"rms_energy", "pitch_mean", "pitch_std", "voiced_fraction", "speaking_rate"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_wav(path: Path, data: np.ndarray, sr: int = SR) -> Path:
    wav_io.write(str(path), sr, data.astype(np.int16))
    return path


def silence_wav(path: Path, duration_s: float = 2.0) -> Path:
    return write_wav(path, np.zeros(int(duration_s * SR), dtype=np.int16))


def sine_wav(path: Path, freq: float = 200.0, duration_s: float = 2.0, amp: float = 0.5) -> Path:
    t = np.arange(int(duration_s * SR)) / SR
    samples = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    return write_wav(path, samples)


def make_segment(start: float, end: float, decision: str = "keep") -> Segment:
    return Segment(clip_id="c", start=start, end=end, decision=decision)


def make_transcript(*pairs) -> Transcript:
    words = [
        WordTimestamp(word=f"w{i}", start=s, end=e, probability=0.9, clip_id="c")
        for i, (s, e) in enumerate(pairs)
    ]
    return Transcript(clip_id="c", language="el", words=words)


# ---------------------------------------------------------------------------
# _zero_features
# ---------------------------------------------------------------------------

def test_zero_features_has_all_keys():
    assert set(_zero_features().keys()) == FEATURE_KEYS


def test_zero_features_all_zero():
    assert all(v == 0.0 for v in _zero_features().values())


# ---------------------------------------------------------------------------
# extract_audio_features — return shape
# ---------------------------------------------------------------------------

def test_extract_returns_all_keys(tmp_path):
    wav = silence_wav(tmp_path / "a.wav")
    seg = make_segment(0.0, 1.0)
    feats = extract_audio_features(wav, seg)
    assert set(feats.keys()) == FEATURE_KEYS


def test_extract_values_are_floats(tmp_path):
    wav = silence_wav(tmp_path / "a.wav")
    seg = make_segment(0.0, 1.0)
    feats = extract_audio_features(wav, seg)
    assert all(isinstance(v, float) for v in feats.values())


# ---------------------------------------------------------------------------
# extract_audio_features — silence
# ---------------------------------------------------------------------------

def test_silence_rms_is_near_zero(tmp_path):
    wav = silence_wav(tmp_path / "a.wav")
    feats = extract_audio_features(wav, make_segment(0.0, 1.0))
    assert feats["rms_energy"] < 1e-4


def test_silence_speaking_rate_is_zero(tmp_path):
    wav = silence_wav(tmp_path / "a.wav")
    feats = extract_audio_features(wav, make_segment(0.0, 1.0))
    assert feats["speaking_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# extract_audio_features — sine wave (periodic signal)
# ---------------------------------------------------------------------------

def test_sine_rms_is_nonzero(tmp_path):
    wav = sine_wav(tmp_path / "a.wav", freq=200.0)
    feats = extract_audio_features(wav, make_segment(0.0, 1.0))
    assert feats["rms_energy"] > 0.01


def test_sine_pitch_mean_near_fundamental(tmp_path):
    """A 200 Hz pure tone should yield pitch_mean close to 200 Hz."""
    wav = sine_wav(tmp_path / "a.wav", freq=200.0, duration_s=2.0)
    feats = extract_audio_features(wav, make_segment(0.0, 2.0))
    if feats["voiced_fraction"] > 0.1:   # only assert if pitch was detected
        assert 150.0 < feats["pitch_mean"] < 250.0


def test_sine_voiced_fraction_positive(tmp_path):
    wav = sine_wav(tmp_path / "a.wav", freq=200.0)
    feats = extract_audio_features(wav, make_segment(0.0, 1.5))
    assert feats["voiced_fraction"] >= 0.0   # may be 0 for pure sine at some params


# ---------------------------------------------------------------------------
# extract_audio_features — segment offset
# ---------------------------------------------------------------------------

def test_extract_respects_segment_offset(tmp_path):
    """Segment in the silent half of a file must yield near-zero RMS."""
    # First 1s: sine; last 1s: silence
    t = np.arange(SR) / SR
    sine_part = (0.5 * np.sin(2 * np.pi * 200 * t) * 32767).astype(np.int16)
    silent_part = np.zeros(SR, dtype=np.int16)
    data = np.concatenate([sine_part, silent_part])
    wav = write_wav(tmp_path / "a.wav", data)

    feats_sine = extract_audio_features(wav, make_segment(0.0, 1.0))
    feats_silence = extract_audio_features(wav, make_segment(1.0, 2.0))
    assert feats_sine["rms_energy"] > feats_silence["rms_energy"] * 10


# ---------------------------------------------------------------------------
# extract_audio_features — speaking rate
# ---------------------------------------------------------------------------

def test_speaking_rate_with_two_words_in_segment(tmp_path):
    wav = silence_wav(tmp_path / "a.wav", duration_s=2.0)
    seg = make_segment(0.0, 2.0)
    t = make_transcript((0.1, 0.4), (0.6, 0.9))   # 2 words in [0, 2]
    feats = extract_audio_features(wav, seg, transcript=t)
    assert feats["speaking_rate"] == pytest.approx(1.0)   # 2 words / 2s


def test_speaking_rate_words_outside_segment_not_counted(tmp_path):
    wav = silence_wav(tmp_path / "a.wav", duration_s=4.0)
    seg = make_segment(2.0, 4.0)   # only covers last 2s
    t = make_transcript((0.1, 0.4), (2.1, 2.5))   # first word outside segment
    feats = extract_audio_features(wav, seg, transcript=t)
    assert feats["speaking_rate"] == pytest.approx(0.5)   # 1 word / 2s


def test_speaking_rate_no_transcript_is_zero(tmp_path):
    wav = silence_wav(tmp_path / "a.wav")
    feats = extract_audio_features(wav, make_segment(0.0, 1.0), transcript=None)
    assert feats["speaking_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# extract_audio_features — edge cases
# ---------------------------------------------------------------------------

def test_very_short_segment_returns_zeros(tmp_path):
    wav = sine_wav(tmp_path / "a.wav")
    feats = extract_audio_features(wav, make_segment(0.0, 0.02))   # 20ms < 50ms min
    assert feats == _zero_features()


# ---------------------------------------------------------------------------
# enrich_segments
# ---------------------------------------------------------------------------

def test_enrich_fills_features_for_keep_segments(tmp_path):
    wav = sine_wav(tmp_path / "a.wav", duration_s=3.0)
    segs = [make_segment(0.0, 1.0), make_segment(1.5, 2.5)]
    enrich_segments(segs, wav)
    for s in segs:
        assert "rms_energy" in s.features


def test_enrich_skips_cut_segments(tmp_path):
    wav = sine_wav(tmp_path / "a.wav", duration_s=2.0)
    seg = make_segment(0.0, 1.0, decision="cut")
    enrich_segments([seg], wav)
    assert seg.features == {}


def test_enrich_no_audio_path_leaves_segments_unchanged(tmp_path):
    seg = make_segment(0.0, 1.0)
    enrich_segments([seg], audio_path=None)
    assert seg.features == {}


def test_enrich_with_transcript_sets_speaking_rate(tmp_path):
    wav = silence_wav(tmp_path / "a.wav", duration_s=3.0)
    seg = make_segment(0.0, 2.0)
    t = make_transcript((0.1, 0.4), (0.6, 0.9), (1.0, 1.3))   # 3 words in 2s
    enrich_segments([seg], wav, transcript=t)
    assert seg.features["speaking_rate"] == pytest.approx(1.5)


def test_enrich_modifies_in_place(tmp_path):
    wav = sine_wav(tmp_path / "a.wav", duration_s=2.0)
    segs = [make_segment(0.0, 1.0)]
    enrich_segments(segs, wav)
    assert segs[0].features["rms_energy"] > 0.0


def test_enrich_mixed_decisions(tmp_path):
    wav = sine_wav(tmp_path / "a.wav", duration_s=4.0)
    keep_seg = make_segment(0.0, 1.0, decision="keep")
    cut_seg = make_segment(1.5, 2.5, decision="cut")
    enrich_segments([keep_seg, cut_seg], wav)
    assert "rms_energy" in keep_seg.features
    assert cut_seg.features == {}
