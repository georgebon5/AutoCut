"""Tests for autocut.takes — repeated take detection."""

from __future__ import annotations

import pytest

from autocut.config import TakesConfig
from autocut.models import Transcript, WordTimestamp
from autocut.takes import _split_into_takes, _take_text, detect_repeated_takes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_word(word: str, start: float, end: float, clip_id: str = "c") -> WordTimestamp:
    return WordTimestamp(word=word, start=start, end=end, probability=0.9, clip_id=clip_id)


def make_transcript(*words: WordTimestamp, clip_id: str = "c") -> Transcript:
    return Transcript(clip_id=clip_id, language="el", words=list(words))


def default_cfg(**overrides) -> TakesConfig:
    defaults = dict(similarity_threshold=0.85, min_gap_s=0.5, min_words=2)
    defaults.update(overrides)
    return TakesConfig(**defaults)


# ---------------------------------------------------------------------------
# _take_text
# ---------------------------------------------------------------------------

def test_take_text_joins_words():
    words = [make_word("γεια", 0.0, 0.3), make_word("σου", 0.4, 0.6)]
    assert _take_text(words) == "γεια σου"


def test_take_text_strips_punctuation():
    words = [make_word("γεια,", 0.0, 0.3), make_word("σου!", 0.4, 0.6)]
    assert _take_text(words) == "γεια σου"


def test_take_text_lowercases():
    words = [make_word("ΓΕΙΑ", 0.0, 0.3)]
    assert _take_text(words) == "γεια"


def test_take_text_skips_blank_words():
    words = [make_word("γεια", 0.0, 0.3), make_word(",", 0.35, 0.4), make_word("σου", 0.4, 0.6)]
    assert _take_text(words) == "γεια σου"


# ---------------------------------------------------------------------------
# _split_into_takes
# ---------------------------------------------------------------------------

def test_split_empty_words():
    assert _split_into_takes([], min_gap_s=0.5, min_words=2) == []


def test_split_single_take_no_gap():
    words = [make_word("α", 0.0, 0.2), make_word("β", 0.3, 0.5)]
    takes = _split_into_takes(words, min_gap_s=0.5, min_words=1)
    assert len(takes) == 1
    assert len(takes[0]) == 2


def test_split_two_takes_by_gap():
    words = [
        make_word("α", 0.0, 0.2),
        make_word("β", 1.0, 1.3),  # gap = 0.8s >= 0.5s
    ]
    takes = _split_into_takes(words, min_gap_s=0.5, min_words=1)
    assert len(takes) == 2


def test_split_gap_exactly_at_threshold_splits():
    # Use clean values to avoid IEEE-754 edge cases: gap = 0.5 - 0.0 = 0.5 exactly
    words = [make_word("α", 0.0, 0.0), make_word("β", 0.5, 0.8)]
    takes = _split_into_takes(words, min_gap_s=0.5, min_words=1)
    assert len(takes) == 2


def test_split_gap_below_threshold_no_split():
    words = [make_word("α", 0.0, 0.2), make_word("β", 0.69, 0.9)]
    # gap = 0.49s < 0.5s — should NOT split
    takes = _split_into_takes(words, min_gap_s=0.5, min_words=1)
    assert len(takes) == 1


def test_split_filters_short_takes():
    words = [
        make_word("α", 0.0, 0.2),               # take 1: 1 word (filtered out)
        make_word("β", 1.0, 1.2),               # take 2: 1 word (filtered out)
        make_word("γ", 2.0, 2.2),               # take 3 begins
        make_word("δ", 2.3, 2.5),
    ]
    takes = _split_into_takes(words, min_gap_s=0.5, min_words=2)
    assert len(takes) == 1
    assert takes[0][0].word == "γ"


def test_split_three_takes():
    words = [
        make_word("α", 0.0, 0.2),
        make_word("β", 0.3, 0.5),
        make_word("γ", 1.5, 1.7),   # gap 1.0s
        make_word("δ", 1.8, 2.0),
        make_word("ε", 3.5, 3.7),   # gap 1.5s
        make_word("ζ", 3.8, 4.0),
    ]
    takes = _split_into_takes(words, min_gap_s=0.5, min_words=2)
    assert len(takes) == 3


# ---------------------------------------------------------------------------
# detect_repeated_takes — no repeat cases
# ---------------------------------------------------------------------------

def test_detect_empty_transcript():
    t = Transcript(clip_id="c", language="el", words=[])
    assert detect_repeated_takes(t, default_cfg()) == []


def test_detect_single_take_no_repeat():
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("σου", 0.4, 0.6),
    )
    assert detect_repeated_takes(t, default_cfg()) == []


def test_detect_two_different_takes_no_cut():
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("σου", 0.4, 0.6),
        make_word("καλη", 2.0, 2.3),
        make_word("μερα", 2.4, 2.7),
    )
    assert detect_repeated_takes(t, default_cfg()) == []


def test_detect_below_min_words_ignored():
    """Takes shorter than min_words must be excluded from repeat detection."""
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),   # 1-word takes are filtered at min_words=2
        make_word("γεια", 2.0, 2.3),
    )
    # min_words=2 → both are single-word takes, filtered out → no repeat groups
    cfg = default_cfg(min_words=2)
    assert detect_repeated_takes(t, cfg) == []


# ---------------------------------------------------------------------------
# detect_repeated_takes — repeat detected
# ---------------------------------------------------------------------------

def test_detect_two_identical_takes_cuts_first():
    """Two identical takes → first must be cut, second kept."""
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("σου", 0.4, 0.6),
        make_word("γεια", 2.0, 2.3),
        make_word("σου", 2.4, 2.6),
    )
    cuts = detect_repeated_takes(t, default_cfg())
    assert len(cuts) == 1
    assert cuts[0].start == pytest.approx(0.0)
    assert cuts[0].end == pytest.approx(0.6)
    assert cuts[0].decision == "cut"


def test_detect_cut_spans_first_to_last_word_of_take():
    t = make_transcript(
        make_word("σήμερα", 1.0, 1.4),
        make_word("πήγα",   1.5, 1.8),
        make_word("σήμερα", 3.0, 3.4),
        make_word("πήγα",   3.5, 3.8),
    )
    cuts = detect_repeated_takes(t, default_cfg())
    assert cuts[0].start == pytest.approx(1.0)
    assert cuts[0].end == pytest.approx(1.8)


def test_detect_nearly_identical_takes_above_threshold():
    """Slight variation should still trigger if similarity >= threshold."""
    t = make_transcript(
        make_word("σήμερα", 0.0, 0.4),
        make_word("πήγα", 0.5, 0.8),
        make_word("στην", 0.9, 1.1),
        make_word("σήμερα", 3.0, 3.4),
        make_word("πήγα", 3.5, 3.8),
        make_word("στο", 3.9, 4.1),    # "στο" vs "στην" — mostly the same
    )
    cfg = default_cfg(similarity_threshold=0.70)
    cuts = detect_repeated_takes(t, cfg)
    assert len(cuts) == 1


def test_detect_below_threshold_no_cut():
    t = make_transcript(
        make_word("πήγα", 0.0, 0.4),
        make_word("σπίτι", 0.5, 0.8),
        make_word("αγόρασα", 2.0, 2.5),
        make_word("καφέ", 2.6, 2.9),
    )
    # "πήγα σπίτι" vs "αγόρασα καφέ" — very different
    cfg = default_cfg(similarity_threshold=0.85)
    assert detect_repeated_takes(t, cfg) == []


# ---------------------------------------------------------------------------
# detect_repeated_takes — chains and multiple groups
# ---------------------------------------------------------------------------

def test_detect_triple_take_cuts_first_two():
    """A, A, A → cut A, cut A, keep A (last)."""
    words_a = ["σήμερα", "πήγα", "σπίτι"]
    t = make_transcript(*[
        make_word(w, base + i * 0.1, base + i * 0.1 + 0.3)
        for base in [0.0, 2.0, 4.0]
        for i, w in enumerate(words_a)
    ])
    cfg = default_cfg(min_words=3)
    cuts = detect_repeated_takes(t, cfg)
    assert len(cuts) == 2
    cut_starts = sorted(c.start for c in cuts)
    assert cut_starts[0] == pytest.approx(0.0)
    assert cut_starts[1] == pytest.approx(2.0)


def test_detect_two_separate_repeat_groups():
    """A A B B → two groups, each cuts the first take."""
    def _take(words: list[str], base: float):
        return [make_word(w, base + i * 0.1, base + i * 0.1 + 0.08)
                for i, w in enumerate(words)]

    all_words = (
        _take(["α", "β", "γ"], 0.0)
        + _take(["α", "β", "γ"], 2.0)
        + _take(["δ", "ε", "ζ"], 4.0)
        + _take(["δ", "ε", "ζ"], 6.0)
    )
    t = Transcript(clip_id="c", language="el", words=all_words)
    cfg = default_cfg(min_words=3)
    cuts = detect_repeated_takes(t, cfg)
    assert len(cuts) == 2


def test_detect_repeat_reason_mentions_count():
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("σου", 0.4, 0.6),
        make_word("γεια", 2.0, 2.3),
        make_word("σου", 2.4, 2.6),
    )
    cuts = detect_repeated_takes(t, default_cfg())
    assert any("2" in r for r in cuts[0].reasons)


def test_detect_clip_id_propagated():
    t = make_transcript(
        make_word("γεια", 0.0, 0.3, clip_id="vid7"),
        make_word("σου", 0.4, 0.6, clip_id="vid7"),
        make_word("γεια", 2.0, 2.3, clip_id="vid7"),
        make_word("σου", 2.4, 2.6, clip_id="vid7"),
        clip_id="vid7",
    )
    cuts = detect_repeated_takes(t, default_cfg())
    assert cuts[0].clip_id == "vid7"


def test_detect_decision_source_is_auto():
    t = make_transcript(
        make_word("γεια", 0.0, 0.3),
        make_word("σου", 0.4, 0.6),
        make_word("γεια", 2.0, 2.3),
        make_word("σου", 2.4, 2.6),
    )
    cuts = detect_repeated_takes(t, default_cfg())
    assert cuts[0].decision_source == "auto"
