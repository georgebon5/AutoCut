"""Compare an EDL's keep/cut decisions against which raw segments survived
into the editor's final published cut.

Approach: normalised cross-correlation between the raw segment's audio and
the published cut's audio. If the correlation peak crosses ``match_threshold``,
the segment is counted as kept by the editor. Precision / recall are then
computed per-segment against the EDL's auto decisions.

Audio matching (rather than visual) was chosen because:
  - A segment edited in a NLE keeps its original audio sample-accurate (any
    re-encoding is near-lossless), so cross-correlation on PCM is cheap and
    discriminative.
  - Visual re-encodes change pixels enough to make frame hashing brittle.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from autocut.models import EDL, Segment


def _fft_correlate_valid(long: np.ndarray, short: np.ndarray) -> np.ndarray:
    """FFT-based cross-correlation of ``long`` vs ``short`` ('valid' mode).

    Avoids scipy.signal to keep the dependency surface slim and sidestep
    a numpy/scipy ABI mismatch seen on some installs.
    """
    L, S = len(long), len(short)
    n = L + S - 1
    nfft = int(1 << (n - 1).bit_length())      # next power of two
    # correlate(a, b) ≡ convolve(a, b[::-1])
    A = np.fft.rfft(long, nfft)
    B = np.fft.rfft(short[::-1], nfft)
    full = np.fft.irfft(A * B, nfft)[:n]
    start = S - 1
    return full[start:start + (L - S + 1)]


@dataclass
class EvalConfig:
    sr: int = 16000
    # Peak of normalised cross-correlation ∈ [-1, 1] — identical audio reaches
    # ~1.0; 0.5 keeps false positives low on 16 kHz speech.
    match_threshold: float = 0.5
    # Segments shorter than this are skipped (either direction).
    min_segment_s: float = 0.3


@dataclass
class SegmentEval:
    index: int
    clip_id: str
    start: float
    end: float
    duration: float
    auto_decision: str              # "keep" | "cut"
    editor_kept: bool                # ground truth
    match_peak: float                # correlation strength [-1, 1]
    match_position_s: float          # where in published audio the match lands
    outcome: str                     # TP / FP / TN / FN / SKIP


@dataclass
class EvalResult:
    segments: list[SegmentEval]
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    skipped: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    kept_by_pipeline_s: float = 0.0
    kept_by_editor_s: float = 0.0
    total_segment_s: float = 0.0


class EvalError(Exception):
    pass


# ---------------------------------------------------------------------------
# Audio extraction + loading
# ---------------------------------------------------------------------------

def extract_audio(video: Path, out: Path, sr: int = 16000) -> Path:
    """Extract a mono WAV from a video via ffmpeg. Raises EvalError on failure."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "wav", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise EvalError(f"ffmpeg failed:\n{result.stderr[-2000:]}")
    return out


def _load_audio(path: Path, sr: int) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    return np.asarray(y, dtype=np.float32)


def _normalise(x: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-variance (safe on silence)."""
    m = float(np.mean(x))
    s = float(np.std(x))
    if s < 1e-9:
        return x - m
    return (x - m) / s


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_segment(
    seg_audio: np.ndarray,
    pub_audio: np.ndarray,
    sr: int,
) -> tuple[float, float]:
    """Return (peak correlation ∈ [-1, 1], position in seconds).

    Zero-mean / unit-variance both signals, then run FFT-based cross-
    correlation. The peak divided by segment length gives a bounded score
    where 1.0 means identical, 0.0 means uncorrelated.
    """
    if len(seg_audio) == 0 or len(pub_audio) < len(seg_audio):
        return 0.0, 0.0
    sn = _normalise(seg_audio.astype(np.float64))
    pn = _normalise(pub_audio.astype(np.float64))
    corr = _fft_correlate_valid(pn, sn) / len(sn)
    pos = int(np.argmax(corr))
    peak = float(corr[pos])
    return peak, pos / sr


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def evaluate_cut(
    edl: EDL,
    published_audio_path: Path,
    cfg: Optional[EvalConfig] = None,
) -> EvalResult:
    """Run the audio-matching evaluation and return precision/recall + per-segment outcomes."""
    cfg = cfg or EvalConfig()
    pub_audio = _load_audio(published_audio_path, cfg.sr)

    # Cache per-clip raw audio to avoid re-loading when many segments share a clip.
    clip_audio: dict[str, np.ndarray] = {}
    for proxy in edl.clips:
        if proxy.audio_path is None:
            continue
        try:
            clip_audio[proxy.clip_id] = _load_audio(proxy.audio_path, cfg.sr)
        except Exception as e:   # noqa: BLE001 — missing audio = can't score
            raise EvalError(f"failed to load {proxy.audio_path}: {e}") from e

    result = EvalResult(segments=[])
    for index, seg in enumerate(edl.segments):
        duration = seg.end - seg.start
        if duration < cfg.min_segment_s:
            result.segments.append(_skipped(index, seg, duration))
            result.skipped += 1
            continue

        raw = clip_audio.get(seg.clip_id)
        if raw is None:
            result.segments.append(_skipped(index, seg, duration))
            result.skipped += 1
            continue

        start_i = int(seg.start * cfg.sr)
        end_i = int(seg.end * cfg.sr)
        seg_audio = raw[start_i:end_i]
        peak, pos_s = match_segment(seg_audio, pub_audio, cfg.sr)
        editor_kept = peak >= cfg.match_threshold

        outcome = _classify(seg.decision, editor_kept)
        _bump(result, outcome, duration)
        if seg.decision == "keep":
            result.kept_by_pipeline_s += duration
        if editor_kept:
            result.kept_by_editor_s += duration
        result.total_segment_s += duration

        result.segments.append(SegmentEval(
            index=index, clip_id=seg.clip_id, start=seg.start, end=seg.end,
            duration=duration, auto_decision=seg.decision, editor_kept=editor_kept,
            match_peak=peak, match_position_s=pos_s, outcome=outcome,
        ))

    _finalise_metrics(result)
    return result


def _skipped(index: int, seg: Segment, duration: float) -> SegmentEval:
    return SegmentEval(
        index=index, clip_id=seg.clip_id, start=seg.start, end=seg.end,
        duration=duration, auto_decision=seg.decision, editor_kept=False,
        match_peak=0.0, match_position_s=0.0, outcome="SKIP",
    )


def _classify(auto: str, editor_kept: bool) -> str:
    if auto == "keep" and editor_kept:      return "TP"
    if auto == "keep" and not editor_kept:  return "FP"
    if auto == "cut"  and not editor_kept:  return "TN"
    return "FN"   # auto cut, editor kept → we missed


def _bump(r: EvalResult, outcome: str, duration: float) -> None:
    if outcome == "TP": r.tp += 1
    elif outcome == "FP": r.fp += 1
    elif outcome == "TN": r.tn += 1
    elif outcome == "FN": r.fn += 1


def _finalise_metrics(r: EvalResult) -> None:
    tp, fp, fn = r.tp, r.fp, r.fn
    r.precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r.recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if r.precision + r.recall > 0:
        r.f1 = 2 * r.precision * r.recall / (r.precision + r.recall)
