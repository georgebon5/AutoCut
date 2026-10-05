"""Audio feature extraction: RMS energy, pitch statistics, speaking rate per segment."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

from autocut.models import Segment, Transcript

# Minimum segment duration for feature extraction (shorter → zeros returned).
_MIN_DURATION_S = 0.05


def _zero_features() -> dict[str, float]:
    return {
        "rms_energy": 0.0,
        "pitch_mean": 0.0,
        "pitch_std": 0.0,
        "voiced_fraction": 0.0,
        "speaking_rate": 0.0,
    }


def _pitch_features(y: np.ndarray, sr: int) -> dict[str, float]:
    """Extract F0 statistics with parselmouth/Praat."""
    try:
        import parselmouth
        snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=float(sr))
        pitch = snd.to_pitch()
        freqs = pitch.selected_array["frequency"]
        if len(freqs) == 0:
            return {"pitch_mean": 0.0, "pitch_std": 0.0, "voiced_fraction": 0.0}
        voiced = freqs[freqs > 0]
        voiced_fraction = len(voiced) / len(freqs)
        pitch_mean = float(voiced.mean()) if len(voiced) > 0 else 0.0
        pitch_std = float(voiced.std()) if len(voiced) > 1 else 0.0
    except Exception:
        return {"pitch_mean": 0.0, "pitch_std": 0.0, "voiced_fraction": 0.0}
    return {
        "pitch_mean": pitch_mean,
        "pitch_std": pitch_std,
        "voiced_fraction": voiced_fraction,
    }


def extract_audio_features(
    audio_path: Path,
    segment: Segment,
    transcript: Transcript | None = None,
    sr: int = 16000,
) -> dict[str, float]:
    """Return a feature dict for one segment of audio.

    Keys: rms_energy, pitch_mean, pitch_std, voiced_fraction, speaking_rate.
    """
    duration = segment.end - segment.start
    if duration < _MIN_DURATION_S:
        return _zero_features()

    y, _ = librosa.load(
        str(audio_path), sr=sr,
        offset=segment.start, duration=duration, mono=True,
    )
    if len(y) == 0:
        return _zero_features()

    rms_energy = float(librosa.feature.rms(y=y)[0].mean())

    speaking_rate = 0.0
    if transcript is not None and duration > 0:
        n_words = sum(
            1 for w in transcript.words
            if segment.start <= w.start <= segment.end
        )
        speaking_rate = n_words / duration

    return {
        "rms_energy": rms_energy,
        "speaking_rate": speaking_rate,
        **_pitch_features(y, sr),
    }


def enrich_segments(
    segments: list[Segment],
    audio_path: Path | None,
    transcript: Transcript | None = None,
    sr: int = 16000,
) -> None:
    """Add audio features to every keep segment in-place.

    Skips cut segments and clips with no audio (B-roll).
    """
    if audio_path is None:
        return
    for seg in segments:
        if seg.decision != "keep":
            continue
        seg.features.update(
            extract_audio_features(audio_path, seg, transcript=transcript, sr=sr)
        )
