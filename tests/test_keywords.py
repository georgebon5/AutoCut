"""Tests for autocut.keywords — exclamations, prices, proper-name heuristic."""

from __future__ import annotations

import pytest

from autocut.config import KeywordsConfig
from autocut.keywords import (
    _find_exclamations,
    _find_prices,
    _find_proper_names,
    enrich_segments_keywords,
    extract_keyword_hits,
)
from autocut.models import Segment, Transcript, WordTimestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def W(word: str, start: float, end: float | None = None, clip_id: str = "c") -> WordTimestamp:
    return WordTimestamp(
        word=word, start=start, end=end if end is not None else start + 0.3,
        probability=0.9, clip_id=clip_id,
    )


def transcript(words: list[WordTimestamp], clip_id: str = "c") -> Transcript:
    return Transcript(clip_id=clip_id, language="el", words=words)


def seg(start: float, end: float, clip_id: str = "c", decision: str = "keep") -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision=decision)


# ---------------------------------------------------------------------------
# _find_exclamations
# ---------------------------------------------------------------------------

def test_exclamation_single_hit():
    assert _find_exclamations("ωραία μέρα", ["ωραία"]) == ["ωραία"]


def test_exclamation_multi_word():
    assert _find_exclamations("θεέ μου τι όμορφο", ["θεέ μου"]) == ["θεέ μου"]


def test_exclamation_no_hit():
    assert _find_exclamations("καλημέρα", ["ωραία"]) == []


def test_exclamation_boundary_not_inside_word():
    # "ωραία" must not match inside a longer glued compound.
    assert _find_exclamations("ωραίασκεφτηκα", ["ωραία"]) == []


def test_exclamation_multiple_occurrences_counted():
    hits = _find_exclamations("ωραία και ωραία πάλι", ["ωραία"])
    assert len(hits) == 2


def test_exclamation_case_insensitive():
    assert _find_exclamations("wow τι χαρα", ["wow"]) == ["wow"]


def test_exclamation_at_text_start():
    assert _find_exclamations("ωραία αρχή", ["ωραία"]) == ["ωραία"]


def test_exclamation_at_text_end_with_punct():
    assert _find_exclamations("ήταν ωραία.", ["ωραία"]) == ["ωραία"]


def test_exclamation_empty_phrase_skipped():
    assert _find_exclamations("ωραία", ["", "ωραία"]) == ["ωραία"]


# ---------------------------------------------------------------------------
# _find_prices
# ---------------------------------------------------------------------------

PRICE_RE = r"\d+(?:[.,]\d+)?\s*(?:€|ευρώ|ευρω|euro)"


def test_price_euro_symbol():
    assert _find_prices("κόστισε 15€", PRICE_RE) == ["15€"]


def test_price_word_ευρώ():
    hits = _find_prices("ήταν 25 ευρώ", PRICE_RE)
    assert hits == ["25 ευρώ"]


def test_price_decimal_with_dot():
    assert _find_prices("4.50€", PRICE_RE) == ["4.50€"]


def test_price_decimal_with_comma():
    assert _find_prices("4,99 ευρώ", PRICE_RE) == ["4,99 ευρώ"]


def test_price_without_currency_not_matched():
    assert _find_prices("ήταν 15 μαζί", PRICE_RE) == []


def test_price_multiple():
    hits = _find_prices("20€ και 35 ευρώ", PRICE_RE)
    assert len(hits) == 2


def test_price_case_insensitive_euro():
    assert _find_prices("10 EURO", PRICE_RE) == ["10 EURO"]


# ---------------------------------------------------------------------------
# _find_proper_names
# ---------------------------------------------------------------------------

def test_proper_name_mid_sentence_counted():
    # "σήμερα πήγα Αθήνα" → "Αθήνα" is capitalised mid-sentence.
    words = [W("σήμερα", 0), W("πήγα", 0.3), W("Αθήνα", 0.6)]
    assert _find_proper_names(words) == ["Αθήνα"]


def test_proper_name_first_word_skipped():
    # First word of a segment is treated as sentence start, not a proper name.
    words = [W("Σήμερα", 0), W("ήταν", 0.3), W("ωραία", 0.6)]
    assert _find_proper_names(words) == []


def test_proper_name_after_sentence_end_skipped():
    # Word after "." isn't a proper name.
    words = [W("ωραία.", 0), W("Σήμερα", 0.3), W("είδα", 0.6), W("Μαρία", 0.9)]
    # "Σήμερα" after "." → not counted; "Μαρία" is mid-sentence → counted.
    assert _find_proper_names(words) == ["Μαρία"]


def test_proper_name_strips_trailing_punct():
    words = [W("πήγα", 0), W("στη", 0.3), W("Μύκονο.", 0.6)]
    assert _find_proper_names(words) == ["Μύκονο"]


def test_proper_name_lowercase_ignored():
    words = [W("ωραία", 0), W("μέρα", 0.3)]
    assert _find_proper_names(words) == []


def test_proper_name_handles_leading_quote():
    words = [W("είπε", 0), W('"Μαρία"', 0.3)]
    assert _find_proper_names(words) == ["Μαρία"]


# ---------------------------------------------------------------------------
# extract_keyword_hits — integration
# ---------------------------------------------------------------------------

def test_extract_no_transcript_returns_zero():
    hits, reasons = extract_keyword_hits(None, seg(0, 10), KeywordsConfig())
    assert hits == 0.0 and reasons == []


def test_extract_no_words_in_range_returns_zero():
    t = transcript([W("ωραία", 100.0)])
    hits, reasons = extract_keyword_hits(t, seg(0, 10), KeywordsConfig())
    assert hits == 0.0 and reasons == []


def test_extract_counts_mixed_categories():
    t = transcript([
        W("Σήμερα", 0.0),      # sentence start → not proper name
        W("πήγα", 0.5),
        W("Αθήνα", 1.0),       # proper name (+0.5)
        W("ωραία", 1.5),       # exclamation (+1.0)
        W("15€", 2.0),         # price (+1.0)
    ])
    cfg = KeywordsConfig()
    hits, reasons = extract_keyword_hits(t, seg(0, 10), cfg)
    assert hits == pytest.approx(1.0 + 1.0 + 0.5)   # excl + price + name
    assert any('"ωραία"' in r for r in reasons)
    assert any("price (15€)" in r for r in reasons)
    assert any("name (Αθήνα)" in r for r in reasons)


def test_extract_weights_applied():
    t = transcript([W("είδα", 0.0), W("τη", 0.3), W("Μαρία", 0.6)])
    cfg = KeywordsConfig(proper_name_weight=2.0)
    hits, _ = extract_keyword_hits(t, seg(0, 10), cfg)
    assert hits == pytest.approx(2.0)


def test_extract_proper_names_disabled():
    t = transcript([W("πήγα", 0.0), W("Αθήνα", 0.5)])
    cfg = KeywordsConfig(enable_proper_names=False)
    hits, _ = extract_keyword_hits(t, seg(0, 10), cfg)
    assert hits == 0.0


# ---------------------------------------------------------------------------
# enrich_segments_keywords — in-place
# ---------------------------------------------------------------------------

def test_enrich_sets_keyword_hits_feature():
    s = seg(0, 10)
    t = transcript([W("είναι", 0.0), W("ωραία", 0.5)])
    enrich_segments_keywords([s], t, KeywordsConfig())
    assert s.features["keyword_hits"] == pytest.approx(1.0)


def test_enrich_skips_cut_segments():
    s = seg(0, 10, decision="cut")
    t = transcript([W("ωραία", 0.5)])
    enrich_segments_keywords([s], t, KeywordsConfig())
    assert "keyword_hits" not in s.features


def test_enrich_appends_reasons():
    s = seg(0, 10)
    t = transcript([W("είναι", 0.0), W("ωραία", 0.5)])
    enrich_segments_keywords([s], t, KeywordsConfig())
    assert any("ωραία" in r for r in s.reasons)


def test_enrich_handles_no_transcript():
    s = seg(0, 10)
    enrich_segments_keywords([s], None, KeywordsConfig())
    assert "keyword_hits" not in s.features
    assert s.reasons == []


def test_enrich_multiple_segments_isolated():
    s1 = seg(0, 1)
    s2 = seg(1, 2)
    t = transcript([W("ωραία", 0.5), W("Αθήνα", 1.5)])
    # s2: "Αθήνα" is only word in segment → treated as sentence start → NOT counted.
    enrich_segments_keywords([s1, s2], t, KeywordsConfig())
    assert s1.features["keyword_hits"] == pytest.approx(1.0)
    assert s2.features["keyword_hits"] == pytest.approx(0.0)
