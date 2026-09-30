"""VAD: Silero-based speech/silence region detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import librosa
import numpy as np
import torch
import torch.nn.functional as F

from autocut.config import VADConfig
from autocut.models import SpeechRegion

_SILERO_REPO = "snakers4/silero-vad"
_CHUNK_SAMPLES = 512  # Silero's processing window at 16 kHz


def load_vad_model() -> tuple[Any, Any]:
    """Load Silero VAD model + utils from torch.hub (cached after first download).

    Returns:
        (model, get_speech_timestamps_fn)
    """
    model, utils = torch.hub.load(
        _SILERO_REPO,
        "silero_vad",
        force_reload=False,
        trust_repo=True,
        verbose=False,
    )
    return model, utils[0]  # utils[0] is get_speech_timestamps


def _load_wav(path: Path, target_sr: int = 16000) -> torch.Tensor:
    """Load a WAV file as a 1D float32 tensor, resampling to target_sr if needed."""
    wav_np: np.ndarray = librosa.load(str(path), sr=target_sr, mono=True)[0]
    return torch.tensor(wav_np, dtype=torch.float32)


def _frame_probs(model: Any, wav: torch.Tensor, sample_rate: int) -> list[float]:
    """Run the Silero model on _CHUNK_SAMPLES windows and return per-chunk probs.

    Chunk boundaries match the ones used internally by get_speech_timestamps,
    so prob indices map directly to the timestamp samples.
    """
    model.reset_states()
    probs: list[float] = []
    with torch.no_grad():
        for i in range(0, len(wav), _CHUNK_SAMPLES):
            chunk = wav[i : i + _CHUNK_SAMPLES]
            if len(chunk) < _CHUNK_SAMPLES:
                chunk = F.pad(chunk, (0, _CHUNK_SAMPLES - len(chunk)))
            probs.append(model(chunk.unsqueeze(0), sample_rate).item())
    return probs


def _mean_prob(probs: list[float], start_s: float, end_s: float, sr: int) -> float:
    """Average per-chunk probability over the [start_s, end_s] interval."""
    start_i = int(start_s * sr / _CHUNK_SAMPLES)
    end_i = int(end_s * sr / _CHUNK_SAMPLES) + 1
    region = probs[max(0, start_i) : end_i]
    return float(sum(region) / len(region)) if region else 0.0


def detect_speech(
    audio_path: Path,
    clip_id: str,
    cfg: VADConfig,
    model: Optional[Any] = None,
    get_timestamps: Optional[Callable] = None,
) -> list[SpeechRegion]:
    """Run Silero VAD on a 16 kHz mono WAV and return speech regions.

    Args:
        audio_path: Path to 16 kHz mono WAV produced by ingest.
        clip_id: Propagated into every SpeechRegion.
        cfg: VAD thresholds and timing parameters.
        model: Pre-loaded Silero model (auto-loads if None).
        get_timestamps: Injectable for testing; loaded from torch.hub if None.

    Returns:
        SpeechRegion list sorted by start time.
    """
    if model is None or get_timestamps is None:
        _model, _get_ts = load_vad_model()
        model = model or _model
        get_timestamps = get_timestamps or _get_ts

    wav = _load_wav(audio_path, target_sr=16000)

    # Silero's own speech_pad_ms would double-up with our silence.padding in Task 4.
    timestamps = get_timestamps(
        wav,
        model,
        sampling_rate=16000,
        threshold=cfg.threshold,
        min_speech_duration_ms=cfg.min_speech_duration_ms,
        min_silence_duration_ms=cfg.min_silence_duration_ms,
        speech_pad_ms=0,        # padding is handled by autocut.silence
        return_seconds=True,
    )

    if not timestamps:
        return []

    probs = _frame_probs(model, wav, sample_rate=16000)

    regions = [
        SpeechRegion(
            clip_id=clip_id,
            start=ts["start"],
            end=ts["end"],
            speech_prob=_mean_prob(probs, ts["start"], ts["end"], sr=16000),
        )
        for ts in timestamps
    ]
    return sorted(regions, key=lambda r: r.start)
