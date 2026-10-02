"""Tests for autocut.transcribe — word-timestamp transcription pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wav_io

from autocut.config import TranscribeConfig
from autocut.models import SpeechRegion, Transcript, WordTimestamp
from autocut.transcribe import (
    _MIN_CHUNK_SAMPLES,
    _collect_words,
    _load_audio,
    transcribe_clip,
)


# ---------------------------------------------------------------------------
# Fake faster-whisper objects (no download required)
# ---------------------------------------------------------------------------

class _FakeWord:
    def __init__(self, word: str, start: float, end: float, probability: float = 0.9):
        self.word = word
        self.start = start
        self.end = end
        self.probability = probability


class _FakeSegment:
    def __init__(self, words: list[_FakeWord]):
        self.words = words


class _FakeInfo:
    language = "el"
    language_probability = 0.99


class FakeWhisperModel:
    """Returns 2 words at fixed offsets within each chunk."""
    def transcribe(self, audio: np.ndarray, language=None, word_timestamps=True, **kw):
        dur = len(audio) / 16000
        if dur < 0.1:
            return iter([]), _FakeInfo()
        words = [
            _FakeWord("γεια", 0.05, 0.30),
            _FakeWord("σου",  0.40, 0.65),
        ]
        return iter([_FakeSegment(words)]), _FakeInfo()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_silence_wav(path: Path, duration_s: float = 2.0, sr: int = 16000) -> Path:
    samples = np.zeros(int(duration_s * sr), dtype=np.int16)
    wav_io.write(str(path), sr, samples)
    return path


def make_region(start: float, end: float, clip_id: str = "c") -> SpeechRegion:
    return SpeechRegion(clip_id=clip_id, start=start, end=end, speech_prob=0.9)


def default_cfg() -> TranscribeConfig:
    return TranscribeConfig(model_size="medium", language="el")


# ---------------------------------------------------------------------------
# _load_audio
# ---------------------------------------------------------------------------

def test_load_audio_returns_float32(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=1.0)
    audio = _load_audio(wav_path)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert len(audio) == pytest.approx(16000, abs=32)


# ---------------------------------------------------------------------------
# _collect_words
# ---------------------------------------------------------------------------

def test_collect_words_applies_offset():
    words = [_FakeWord("γεια", 0.1, 0.4), _FakeWord("σου", 0.5, 0.8)]
    segs = iter([_FakeSegment(words)])
    result = _collect_words(segs, offset=3.0, clip_id="c")
    assert result[0].start == pytest.approx(3.1)
    assert result[0].end == pytest.approx(3.4)
    assert result[1].start == pytest.approx(3.5)


def test_collect_words_propagates_clip_id():
    words = [_FakeWord("test", 0.0, 0.3)]
    segs = iter([_FakeSegment(words)])
    result = _collect_words(segs, offset=0.0, clip_id="myclip")
    assert result[0].clip_id == "myclip"


def test_collect_words_empty_segment():
    class _EmptySeg:
        words = None
    result = _collect_words(iter([_EmptySeg()]), offset=0.0, clip_id="c")
    assert result == []


def test_collect_words_empty_iterator():
    result = _collect_words(iter([]), offset=0.0, clip_id="c")
    assert result == []


# ---------------------------------------------------------------------------
# transcribe_clip
# ---------------------------------------------------------------------------

def test_transcribe_no_regions_returns_empty(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav")
    t = transcribe_clip(wav_path, [], "c", default_cfg(), model=FakeWhisperModel())
    assert isinstance(t, Transcript)
    assert t.words == []
    assert t.clip_id == "c"


def test_transcribe_returns_transcript_with_correct_types(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=3.0)
    regions = [make_region(0.5, 2.0)]
    t = transcribe_clip(wav_path, regions, "c", default_cfg(), model=FakeWhisperModel())
    assert isinstance(t, Transcript)
    for w in t.words:
        assert isinstance(w, WordTimestamp)
        assert isinstance(w.start, float)
        assert isinstance(w.probability, float)


def test_transcribe_offsets_timestamps_by_region_start(tmp_path):
    """Words returned at 0.05s / 0.40s in chunk must be offset by region.start."""
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=5.0)
    region_start = 2.0
    regions = [make_region(region_start, 3.5)]
    t = transcribe_clip(wav_path, regions, "c", default_cfg(), model=FakeWhisperModel())
    assert len(t.words) == 2
    assert t.words[0].start == pytest.approx(region_start + 0.05, abs=1e-6)
    assert t.words[1].start == pytest.approx(region_start + 0.40, abs=1e-6)


def test_transcribe_skips_chunk_shorter_than_minimum(tmp_path):
    """A 0.05s region is below _MIN_CHUNK_SAMPLES and should produce 0 words."""
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=1.0)
    short_region = make_region(0.0, 0.05)   # 800 samples < 1600 minimum
    t = transcribe_clip(wav_path, [short_region], "c", default_cfg(), model=FakeWhisperModel())
    assert t.words == []


def test_transcribe_multiple_regions_all_offset(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=8.0)
    regions = [make_region(1.0, 2.5), make_region(4.0, 5.5)]
    t = transcribe_clip(wav_path, regions, "c", default_cfg(), model=FakeWhisperModel())
    # 2 words per region → 4 total
    assert len(t.words) == 4
    # All words must be in clip-absolute coordinates (>= region starts)
    assert all(w.start >= 1.0 for w in t.words)


def test_transcribe_words_sorted_by_start(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=8.0)
    # Two regions; words from region 2 must not precede words from region 1
    regions = [make_region(0.5, 2.0), make_region(3.0, 4.5)]
    t = transcribe_clip(wav_path, regions, "c", default_cfg(), model=FakeWhisperModel())
    starts = [w.start for w in t.words]
    assert starts == sorted(starts)


def test_transcribe_clip_id_propagated(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=3.0)
    t = transcribe_clip(
        wav_path, [make_region(0.5, 2.0)], "myclip",
        default_cfg(), model=FakeWhisperModel()
    )
    assert all(w.clip_id == "myclip" for w in t.words)


def test_transcribe_language_from_model(tmp_path):
    wav_path = write_silence_wav(tmp_path / "a.wav", duration_s=3.0)
    t = transcribe_clip(
        wav_path, [make_region(0.5, 2.0)], "c",
        default_cfg(), model=FakeWhisperModel()
    )
    assert t.language == "el"


# ---------------------------------------------------------------------------
# Transcript model methods
# ---------------------------------------------------------------------------

def test_transcript_text_property():
    words = [
        WordTimestamp("γεια", 0.1, 0.4, 0.9, "c"),
        WordTimestamp("σου", 0.5, 0.8, 0.9, "c"),
    ]
    t = Transcript(clip_id="c", language="el", words=words)
    assert t.text == "γεια σου"


def test_transcript_text_empty():
    t = Transcript(clip_id="c", language="el", words=[])
    assert t.text == ""


def test_transcript_words_in_range():
    words = [
        WordTimestamp("α", 0.5, 0.8, 0.9, "c"),
        WordTimestamp("β", 1.5, 1.8, 0.9, "c"),
        WordTimestamp("γ", 2.5, 2.8, 0.9, "c"),
    ]
    t = Transcript(clip_id="c", language="el", words=words)
    result = t.words_in_range(1.0, 2.0)
    assert len(result) == 1
    assert result[0].word == "β"


def test_transcript_to_snap_format():
    words = [WordTimestamp("test", 0.1, 0.4, 0.9, "c")]
    t = Transcript(clip_id="c", language="el", words=words)
    fmt = t.to_snap_format()
    assert fmt == [{"word": "test", "start": 0.1, "end": 0.4}]
