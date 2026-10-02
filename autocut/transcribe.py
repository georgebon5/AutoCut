"""Transcription: faster-whisper with word-level timestamps, VAD-gated."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import librosa
import numpy as np

from autocut.config import TranscribeConfig
from autocut.models import SpeechRegion, Transcript, WordTimestamp

# Whisper can hallucinate on very short chunks; skip anything under 0.1 s.
_MIN_CHUNK_SAMPLES = 1600   # 0.1 s × 16 000 Hz


def load_whisper_model(cfg: TranscribeConfig) -> Any:
    """Load a faster-whisper model (auto GPU → CPU fallback)."""
    from faster_whisper import WhisperModel
    return WhisperModel(
        cfg.model_size,
        device="auto",
        compute_type=cfg.compute_type,
    )


def _load_audio(audio_path: Path, sr: int = 16000) -> np.ndarray:
    """Load audio as a 1-D float32 numpy array at `sr` Hz."""
    wav, _ = librosa.load(str(audio_path), sr=sr, mono=True)
    return np.asarray(wav, dtype=np.float32)


def _collect_words(
    segments_iter: Any,
    offset: float,
    clip_id: str,
) -> list[WordTimestamp]:
    """Exhaust a faster-whisper segments iterator and return WordTimestamps.

    `offset` is added to every word's start/end so timestamps are
    clip-absolute (not chunk-relative).
    """
    words: list[WordTimestamp] = []
    for seg in segments_iter:
        if not seg.words:
            continue
        for w in seg.words:
            words.append(WordTimestamp(
                word=w.word,
                start=w.start + offset,
                end=w.end + offset,
                probability=w.probability,
                clip_id=clip_id,
            ))
    return words


def transcribe_clip(
    audio_path: Path,
    speech_regions: list[SpeechRegion],
    clip_id: str,
    cfg: TranscribeConfig,
    model: Any = None,
) -> Transcript:
    """Transcribe a clip using only its VAD-detected speech regions.

    Passing each speech region as a separate numpy chunk avoids running
    Whisper over silence, which causes hallucinations.  Word timestamps
    are offset back to clip-absolute coordinates before returning.

    Args:
        audio_path: 16 kHz mono WAV produced by ingest.
        speech_regions: VAD output for this clip (from detect_speech).
        clip_id: Propagated into every WordTimestamp.
        cfg: Model size, language, compute type, beam size.
        model: Pre-loaded faster-whisper model (auto-loaded if None).

    Returns:
        Transcript with word-level timestamps in clip-absolute seconds.
    """
    if model is None:
        model = load_whisper_model(cfg)

    if not speech_regions:
        return Transcript(clip_id=clip_id, language=cfg.language, words=[])

    audio = _load_audio(audio_path)
    sr = 16000
    all_words: list[WordTimestamp] = []
    detected_language = cfg.language

    for region in sorted(speech_regions, key=lambda r: r.start):
        start_i = int(region.start * sr)
        end_i = int(region.end * sr)
        chunk = audio[start_i:end_i]

        if len(chunk) < _MIN_CHUNK_SAMPLES:
            continue

        # vad_filter=False: we already applied Silero; double-VAD causes
        # timestamp drift in some faster-whisper versions.
        segments_iter, info = model.transcribe(
            chunk,
            language=cfg.language,
            word_timestamps=True,
            beam_size=cfg.beam_size,
            vad_filter=False,
        )
        detected_language = info.language
        all_words.extend(_collect_words(segments_iter, region.start, clip_id))

    return Transcript(
        clip_id=clip_id,
        language=detected_language,
        words=sorted(all_words, key=lambda w: w.start),
    )
