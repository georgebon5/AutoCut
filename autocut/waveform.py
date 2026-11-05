"""Audio waveform peak extraction for timeline visualization.

Computes a fixed-length array of RMS amplitude values from an audio file.
The result is a normalized 0..1 list saved as JSON alongside the other
pipeline outputs so the review UI can display a waveform background in the
timeline without an extra API call per segment.
"""

from __future__ import annotations

import json
from pathlib import Path


def extract_peaks(audio_path: Path, n_peaks: int = 200) -> list[float]:
    """Return ``n_peaks`` normalized RMS values spanning the audio file.

    Uses librosa (which is already a pipeline dependency). Falls back to an
    all-zero list when librosa is not installed so callers never raise.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return [0.0] * n_peaks

    y, _sr = librosa.load(str(audio_path), sr=None, mono=True)
    if len(y) == 0:
        return [0.0] * n_peaks

    chunk_size = max(1, len(y) // n_peaks)
    peaks: list[float] = []
    for i in range(n_peaks):
        start = i * chunk_size
        end = min(start + chunk_size, len(y))
        chunk = y[start:end]
        rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) > 0 else 0.0
        peaks.append(rms)

    # Normalize so the loudest bar reaches 1.0
    max_val = max(peaks) if peaks else 0.0
    if max_val > 0.0:
        peaks = [p / max_val for p in peaks]

    return peaks


def save_waveform_json(audio_path: Path, out_path: Path, n_peaks: int = 200) -> None:
    """Write peak data as JSON to ``out_path``."""
    peaks = extract_peaks(audio_path, n_peaks)
    out_path.write_text(
        json.dumps({"peaks": peaks, "n_peaks": n_peaks}),
        encoding="utf-8",
    )
