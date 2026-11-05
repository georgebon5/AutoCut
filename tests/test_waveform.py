"""Tests for autocut.waveform — audio peak extraction."""

from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest

from autocut.waveform import extract_peaks, save_waveform_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav(path: Path, samples: list[float], sample_rate: int = 16000) -> None:
    """Write a mono 16-bit PCM WAV file."""
    int_samples = [max(-32768, min(32767, int(s * 32767))) for s in samples]
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(int_samples)}h", *int_samples))


def _sine_samples(freq: float = 440.0, duration: float = 1.0, sr: int = 16000) -> list[float]:
    import math
    n = int(duration * sr)
    return [math.sin(2 * math.pi * freq * i / sr) for i in range(n)]


# ---------------------------------------------------------------------------
# extract_peaks
# ---------------------------------------------------------------------------

def test_extract_peaks_returns_correct_length(tmp_path):
    p = tmp_path / "audio.wav"
    _write_wav(p, _sine_samples())
    peaks = extract_peaks(p, n_peaks=100)
    assert len(peaks) == 100


def test_extract_peaks_normalized_max_is_one(tmp_path):
    p = tmp_path / "audio.wav"
    _write_wav(p, _sine_samples())
    peaks = extract_peaks(p, n_peaks=50)
    assert max(peaks) == pytest.approx(1.0, abs=0.05)


def test_extract_peaks_all_in_range(tmp_path):
    p = tmp_path / "audio.wav"
    _write_wav(p, _sine_samples())
    peaks = extract_peaks(p, n_peaks=50)
    assert all(0.0 <= v <= 1.0 for v in peaks)


def test_extract_peaks_silence_gives_zeros(tmp_path):
    p = tmp_path / "silence.wav"
    _write_wav(p, [0.0] * 16000)
    peaks = extract_peaks(p, n_peaks=50)
    assert all(v == 0.0 for v in peaks)


def test_extract_peaks_different_n_peaks(tmp_path):
    p = tmp_path / "audio.wav"
    _write_wav(p, _sine_samples(duration=2.0))
    for n in (50, 100, 200):
        peaks = extract_peaks(p, n_peaks=n)
        assert len(peaks) == n


def test_extract_peaks_louder_section_has_higher_peak(tmp_path):
    """Second half is 3× louder — its peaks should be higher on average."""
    import math
    sr = 16000
    quiet = [0.1 * math.sin(2 * math.pi * 440 * i / sr) for i in range(sr)]
    loud  = [0.9 * math.sin(2 * math.pi * 440 * i / sr) for i in range(sr)]
    p = tmp_path / "audio.wav"
    _write_wav(p, quiet + loud)
    peaks = extract_peaks(p, n_peaks=100)
    first_half  = peaks[:50]
    second_half = peaks[50:]
    assert sum(second_half) > sum(first_half)


# ---------------------------------------------------------------------------
# save_waveform_json
# ---------------------------------------------------------------------------

def test_save_waveform_json_creates_file(tmp_path):
    audio = tmp_path / "audio.wav"
    out   = tmp_path / "waveform.json"
    _write_wav(audio, _sine_samples())
    save_waveform_json(audio, out)
    assert out.exists()


def test_save_waveform_json_valid_json(tmp_path):
    audio = tmp_path / "audio.wav"
    out   = tmp_path / "waveform.json"
    _write_wav(audio, _sine_samples())
    save_waveform_json(audio, out)
    data = json.loads(out.read_text())
    assert "peaks" in data
    assert "n_peaks" in data


def test_save_waveform_json_peak_count_matches(tmp_path):
    audio = tmp_path / "audio.wav"
    out   = tmp_path / "waveform.json"
    _write_wav(audio, _sine_samples())
    save_waveform_json(audio, out, n_peaks=150)
    data = json.loads(out.read_text())
    assert data["n_peaks"] == 150
    assert len(data["peaks"]) == 150
