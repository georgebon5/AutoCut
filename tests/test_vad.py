from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wav_io
import torch

from autocut.config import VADConfig
from autocut.models import SpeechRegion
from autocut.vad import _frame_probs, _load_wav, _mean_prob, detect_speech


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_silence_wav(path: Path, duration_s: float = 2.0, sr: int = 16000) -> Path:
    samples = np.zeros(int(duration_s * sr), dtype=np.int16)
    wav_io.write(str(path), sr, samples)
    return path


def write_tone_wav(
    path: Path,
    freq_hz: float = 440.0,
    duration_s: float = 2.0,
    sr: int = 16000,
    amplitude: float = 0.3,
) -> Path:
    t = np.linspace(0, duration_s, int(duration_s * sr), endpoint=False)
    samples = (amplitude * np.sin(2 * np.pi * freq_hz * t) * 32767).astype(np.int16)
    wav_io.write(str(path), sr, samples)
    return path


class FakeVADModel:
    """Minimal Silero API surface for unit tests."""

    def __init__(self, fixed_prob: float = 0.9):
        self._prob = fixed_prob

    def reset_states(self) -> None:
        pass

    def __call__(self, chunk: torch.Tensor, sr: int) -> torch.Tensor:
        return torch.tensor(self._prob)


def fake_get_timestamps_factory(regions: list[dict]):
    """Return a get_timestamps function that always returns `regions`."""
    def _fn(wav, model, **kwargs):
        return regions
    return _fn


# ---------------------------------------------------------------------------
# Pure unit tests — no Silero model needed
# ---------------------------------------------------------------------------

def test_mean_prob_basic():
    probs = [0.1, 0.9, 0.9, 0.1]
    # 16000 Hz, chunk=512 → chunk 1 starts at 0.032s, chunk 2 at 0.064s
    val = _mean_prob(probs, start_s=0.032, end_s=0.064, sr=16000)
    assert 0.0 <= val <= 1.0


def test_mean_prob_empty_region():
    probs = [0.5, 0.5]
    # start > end of array → region list is empty
    val = _mean_prob(probs, start_s=999.0, end_s=1000.0, sr=16000)
    assert val == 0.0


def test_mean_prob_full_array():
    probs = [0.4, 0.6, 0.8]
    val = _mean_prob(probs, start_s=0.0, end_s=999.0, sr=16000)
    assert val == pytest.approx(sum(probs) / len(probs))


def test_frame_probs_length():
    model = FakeVADModel(fixed_prob=0.7)
    wav = torch.zeros(1600)  # 0.1 s at 16 kHz
    probs = _frame_probs(model, wav, sample_rate=16000)
    expected_chunks = -(-1600 // 512)  # ceil(1600/512) = 4
    assert len(probs) == expected_chunks


def test_frame_probs_values():
    model = FakeVADModel(fixed_prob=0.42)
    wav = torch.zeros(512)
    probs = _frame_probs(model, wav, sample_rate=16000)
    assert probs == [pytest.approx(0.42)]


def test_detect_speech_with_fake_model(tmp_path):
    wav_path = write_silence_wav(tmp_path / "clip.wav", duration_s=2.0)
    cfg = VADConfig()

    fake_model = FakeVADModel(fixed_prob=0.9)
    fake_ts = fake_get_timestamps_factory([
        {"start": 0.5, "end": 1.2},
        {"start": 1.5, "end": 1.9},
    ])

    regions = detect_speech(wav_path, "clip", cfg, model=fake_model, get_timestamps=fake_ts)

    assert len(regions) == 2
    assert regions[0].start == pytest.approx(0.5)
    assert regions[0].end == pytest.approx(1.2)
    assert regions[1].start == pytest.approx(1.5)
    assert 0.0 <= regions[0].speech_prob <= 1.0


def test_detect_speech_output_types(tmp_path):
    wav_path = write_silence_wav(tmp_path / "clip.wav")
    cfg = VADConfig()
    regions = detect_speech(
        wav_path, "myclip", cfg,
        model=FakeVADModel(),
        get_timestamps=fake_get_timestamps_factory([{"start": 0.1, "end": 0.8}]),
    )
    assert len(regions) == 1
    r = regions[0]
    assert isinstance(r, SpeechRegion)
    assert r.clip_id == "myclip"
    assert isinstance(r.start, float)
    assert isinstance(r.end, float)
    assert isinstance(r.speech_prob, float)


def test_detect_speech_empty_result(tmp_path):
    wav_path = write_silence_wav(tmp_path / "clip.wav")
    cfg = VADConfig()
    regions = detect_speech(
        wav_path, "clip", cfg,
        model=FakeVADModel(),
        get_timestamps=fake_get_timestamps_factory([]),
    )
    assert regions == []


def test_detect_speech_sorted(tmp_path):
    wav_path = write_silence_wav(tmp_path / "clip.wav", duration_s=5.0)
    cfg = VADConfig()
    # deliberately unsorted to verify sorting
    regions = detect_speech(
        wav_path, "clip", cfg,
        model=FakeVADModel(),
        get_timestamps=fake_get_timestamps_factory([
            {"start": 3.0, "end": 4.0},
            {"start": 0.5, "end": 1.0},
        ]),
    )
    assert regions[0].start < regions[1].start


def test_load_wav_returns_1d(tmp_path):
    wav_path = write_silence_wav(tmp_path / "clip.wav", duration_s=1.0, sr=16000)
    tensor = _load_wav(wav_path, target_sr=16000)
    assert tensor.dim() == 1
    assert tensor.shape[0] == 16000


def test_load_wav_resamples(tmp_path):
    # Write at 44100 Hz, load as 16000 Hz
    path = tmp_path / "clip.wav"
    samples = np.zeros(44100, dtype=np.int16)
    wav_io.write(str(path), 44100, samples)
    tensor = _load_wav(path, target_sr=16000)
    assert tensor.dim() == 1
    # Resampled length should be ~16000 (±1 sample tolerance)
    assert abs(tensor.shape[0] - 16000) <= 2


# ---------------------------------------------------------------------------
# Integration test — requires Silero model download (~20 MB, cached)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_detect_speech_on_real_silence(tmp_path):
    """Silero should find zero speech regions in a silent file."""
    wav_path = write_silence_wav(tmp_path / "silence.wav", duration_s=3.0)
    cfg = VADConfig()
    regions = detect_speech(wav_path, "silence", cfg)
    assert regions == [], f"Expected no speech, got {regions}"
