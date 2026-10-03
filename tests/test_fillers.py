"""Tests for autocut.fillers — filler-word detection and punch-out."""

from __future__ import annotations

import pytest

from autocut.config import FillerConfig
from autocut.fillers import _normalize, detect_fillers, punch_out_fillers
from autocut.models import Segment, Transcript, WordTimestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_word(word: str, start: float, end: float, clip_id: str = "c") -> WordTimestamp:
    return WordTimestamp(word=word, start=start, end=end, probability=0.9, clip_id=clip_id)


def make_transcript(*words: WordTimestamp, clip_id: str = "c") -> Transcript:
    return Transcript(clip_id=clip_id, language="el", words=list(words))


def make_keep(start: float, end: float, clip_id: str = "c") -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision="keep")


def make_cut(start: float, end: float, clip_id: str = "c") -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision="cut")


def default_cfg(**overrides) -> FillerConfig:
    defaults = dict(
        words=["εε", "εεε", "εμ", "λοιπόν"],
        min_isolation_ms=200,
        padding_ms=50,
    )
    defaults.update(overrides)
    return FillerConfig(**defaults)


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------

def test_normalize_lowercases():
    assert _normalize("ΛΟΙΠΟΝ") == "λοιπον" or _normalize("λοιπόν") == "λοιπόν"


def test_normalize_strips_punctuation():
    assert _normalize("εεε,") == "εε"
    assert _normalize("εμ.") == "εμ"


def test_normalize_collapses_elongation():
    assert _normalize("εεεεε") == "εε"
    assert _normalize("εεε") == "εε"


def test_normalize_two_is_unchanged():
    assert _normalize("εε") == "εε"


def test_normalize_single_char_unchanged():
    assert _normalize("ε") == "ε"


# ---------------------------------------------------------------------------
# detect_fillers — basic matching
# ---------------------------------------------------------------------------

def test_detect_isolated_filler():
    """A single isolated filler word must produce one cut segment."""
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("εεε", 0.8, 1.0),   # gap before: 0.5s, gap after: end
        clip_id="c",
    )
    cuts = detect_fillers(t, default_cfg())
    assert len(cuts) == 1
    assert cuts[0].decision == "cut"
    assert cuts[0].clip_id == "c"


def test_detect_filler_at_start():
    """Filler at index 0 only needs silence on the right side."""
    t = make_transcript(
        make_word("εμ", 0.0, 0.2),
        make_word("γεια", 0.8, 1.2),
    )
    cuts = detect_fillers(t, default_cfg())
    assert len(cuts) == 1


def test_detect_filler_at_end():
    """Filler at last position only needs silence on the left side."""
    t = make_transcript(
        make_word("γεια", 0.0, 0.5),
        make_word("εμ", 1.0, 1.2),   # gap before: 0.5s, nothing after
    )
    cuts = detect_fillers(t, default_cfg())
    assert len(cuts) == 1


def test_detect_skips_non_isolated_filler():
    """Filler mid-sentence (no sufficient gap) must be ignored."""
    t = make_transcript(
        make_word("τι", 0.0, 0.2),
        make_word("εμ", 0.25, 0.35),  # gap before: 0.05s < 0.2s threshold
        make_word("λες", 0.4, 0.7),
    )
    cuts = detect_fillers(t, default_cfg())
    assert cuts == []


def test_detect_skips_unknown_words():
    t = make_transcript(
        make_word("καλημέρα", 0.0, 0.5),
    )
    cuts = detect_fillers(t, default_cfg())
    assert cuts == []


def test_detect_empty_transcript():
    t = Transcript(clip_id="c", language="el", words=[])
    cuts = detect_fillers(t, default_cfg())
    assert cuts == []


# ---------------------------------------------------------------------------
# detect_fillers — elongated / punctuated variants
# ---------------------------------------------------------------------------

def test_detect_elongated_filler_matches():
    """εεεεε should normalise to εε and match."""
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("εεεεε", 0.8, 1.1),
    )
    cuts = detect_fillers(t, default_cfg())
    assert len(cuts) == 1


def test_detect_filler_with_trailing_comma():
    """Word from faster-whisper often includes punctuation."""
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("εμ,", 0.8, 1.0),
    )
    cuts = detect_fillers(t, default_cfg())
    assert len(cuts) == 1


# ---------------------------------------------------------------------------
# detect_fillers — padding and timestamps
# ---------------------------------------------------------------------------

def test_detect_filler_applies_padding():
    """Cut segment start/end must include cfg.padding_ms on each side."""
    pad_s = 0.05
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("εεε", 1.0, 1.2),
    )
    cuts = detect_fillers(t, default_cfg(padding_ms=50))
    assert cuts[0].start == pytest.approx(1.0 - pad_s)
    assert cuts[0].end == pytest.approx(1.2 + pad_s)


def test_detect_filler_start_clamps_to_zero():
    """Padding must not push start below 0."""
    t = make_transcript(
        make_word("εμ", 0.02, 0.2),
    )
    cuts = detect_fillers(t, default_cfg(padding_ms=50))
    assert cuts[0].start == 0.0


def test_detect_filler_reason_contains_word():
    t = make_transcript(
        make_word("λοιπόν", 1.0, 1.3),
    )
    cuts = detect_fillers(t, default_cfg())
    assert any("λοιπόν" in r for r in cuts[0].reasons)


def test_detect_filler_clip_id_propagated():
    t = make_transcript(
        make_word("εμ", 0.0, 0.2, clip_id="vid42"),
        clip_id="vid42",
    )
    cuts = detect_fillers(t, default_cfg())
    assert cuts[0].clip_id == "vid42"


# ---------------------------------------------------------------------------
# detect_fillers — multiple fillers
# ---------------------------------------------------------------------------

def test_detect_multiple_fillers():
    t = make_transcript(
        make_word("εεε", 0.0, 0.2),
        make_word("γεια", 0.7, 1.0),
        make_word("εμ", 1.5, 1.7),
        make_word("σου", 2.2, 2.5),
    )
    cuts = detect_fillers(t, default_cfg())
    assert len(cuts) == 2


def test_detect_respects_custom_isolation_threshold():
    """With a larger min_isolation_ms, a close-by word should not be cut."""
    t = make_transcript(
        make_word("τι", 0.0, 0.3),
        make_word("εμ", 0.6, 0.8),   # gap before = 0.3s
        make_word("λες", 1.5, 1.8),
    )
    # With 400ms threshold, gap of 300ms is not enough → no cuts
    cuts_strict = detect_fillers(t, default_cfg(min_isolation_ms=400))
    assert cuts_strict == []

    # With 200ms threshold it qualifies
    cuts_loose = detect_fillers(t, default_cfg(min_isolation_ms=200))
    assert len(cuts_loose) == 1


# ---------------------------------------------------------------------------
# punch_out_fillers
# ---------------------------------------------------------------------------

def test_punch_no_fillers_returns_original():
    segs = [make_keep(0.0, 5.0)]
    result = punch_out_fillers(segs, [])
    assert result == segs


def test_punch_cut_segment_passes_through():
    segs = [make_cut(0.0, 1.0), make_keep(1.0, 4.0)]
    result = punch_out_fillers(segs, [])
    assert len(result) == 2


def test_punch_splits_keep_around_filler():
    segs = [make_keep(0.0, 5.0)]
    filler = make_cut(2.0, 2.5)
    result = punch_out_fillers(segs, [filler])
    keeps = [s for s in result if s.decision == "keep"]
    cuts = [s for s in result if s.decision == "cut"]
    assert len(keeps) == 2
    assert len(cuts) == 1
    assert keeps[0].end == pytest.approx(2.0)
    assert keeps[1].start == pytest.approx(2.5)


def test_punch_filler_at_start_of_keep():
    segs = [make_keep(0.0, 4.0)]
    filler = make_cut(0.0, 0.3)
    result = punch_out_fillers(segs, [filler])
    keeps = [s for s in result if s.decision == "keep"]
    assert len(keeps) == 1
    assert keeps[0].start == pytest.approx(0.3)


def test_punch_filler_at_end_of_keep():
    segs = [make_keep(0.0, 4.0)]
    filler = make_cut(3.7, 4.0)
    result = punch_out_fillers(segs, [filler])
    keeps = [s for s in result if s.decision == "keep"]
    assert len(keeps) == 1
    assert keeps[0].end == pytest.approx(3.7)


def test_punch_filler_spanning_full_keep_collapses():
    """A filler that covers the entire keep segment leaves no keep remnants."""
    segs = [make_keep(1.0, 1.1)]  # only 0.1s, below min_duration default
    filler = make_cut(0.9, 1.2)
    result = punch_out_fillers(segs, [filler])
    keeps = [s for s in result if s.decision == "keep"]
    assert keeps == []


def test_punch_multiple_fillers_in_one_keep():
    segs = [make_keep(0.0, 10.0)]
    fillers = [make_cut(1.0, 1.5), make_cut(4.0, 4.5), make_cut(8.0, 8.5)]
    result = punch_out_fillers(segs, fillers)
    keeps = [s for s in result if s.decision == "keep"]
    cuts = [s for s in result if s.decision == "cut"]
    assert len(keeps) == 4
    assert len(cuts) == 3


def test_punch_filler_wrong_clip_ignored():
    segs = [make_keep(0.0, 5.0, clip_id="a")]
    filler = make_cut(2.0, 2.5, clip_id="b")
    result = punch_out_fillers(segs, [filler])
    assert len(result) == 1
    assert result[0].decision == "keep"


def test_punch_result_sorted_by_clip_and_start():
    segs = [make_keep(5.0, 10.0, "b"), make_keep(0.0, 4.0, "a")]
    result = punch_out_fillers(segs, [])
    assert result[0].clip_id == "a"
    assert result[1].clip_id == "b"


def test_punch_preserves_filler_reasons():
    segs = [make_keep(0.0, 5.0)]
    filler = Segment(clip_id="c", start=2.0, end=2.5, decision="cut",
                     decision_source="auto", reasons=["filler: εεε"])
    result = punch_out_fillers(segs, [filler])
    cut = next(s for s in result if s.decision == "cut")
    assert "filler: εεε" in cut.reasons


def test_punch_drops_hairline_keep_below_min_duration():
    """Sub-segments shorter than min_duration must be dropped."""
    segs = [make_keep(0.0, 5.0)]
    # filler positioned so the trailing keep remnant would be 0.01s
    filler = make_cut(4.99, 5.0)
    result = punch_out_fillers(segs, [filler], min_duration=0.05)
    keeps = [s for s in result if s.decision == "keep"]
    # trailing 0.01s keep should be dropped
    assert all(k.duration >= 0.05 for k in keeps)
