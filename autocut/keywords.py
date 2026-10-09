"""Transcript keyword boosts: exclamations, prices, proper names → interest signal."""

from __future__ import annotations

import re

from autocut.config import KeywordsConfig
from autocut.models import Segment, Transcript, WordTimestamp

# Greek + Latin sentence terminators; `·` is the Greek ano teleia.
_SENTENCE_END_CHARS = (".", "!", "?", ";", "·")

# Characters commonly glued to a word (quotes, brackets) that we strip before
# looking at the first letter when deciding "is this capitalised".
_LEADING_STRIP = "\"'«“‘([{"
_TRAILING_STRIP = "\"'»”’)]}" + "".join(_SENTENCE_END_CHARS) + ",:"


def _words_in_segment(
    words: list[WordTimestamp], seg: Segment
) -> list[WordTimestamp]:
    return [w for w in words if seg.start <= w.start <= seg.end]


def _find_exclamations(text_norm: str, phrases: list[str]) -> list[str]:
    """Non-overlapping substring matches, honouring word boundaries."""
    hits: list[str] = []
    for phrase in phrases:
        p = phrase.lower().strip()
        if not p:
            continue
        i = 0
        while True:
            idx = text_norm.find(p, i)
            if idx < 0:
                break
            before = text_norm[idx - 1] if idx > 0 else " "
            after_i = idx + len(p)
            after = text_norm[after_i] if after_i < len(text_norm) else " "
            # Match only when the phrase is bounded by non-letters on both sides,
            # so "ωραία" doesn't fire inside a longer compound word.
            if not before.isalpha() and not after.isalpha():
                hits.append(p)
            i = idx + len(p)
    return hits


def _find_prices(text: str, pattern: str) -> list[str]:
    return re.findall(pattern, text, flags=re.IGNORECASE)


def _find_proper_names(words: list[WordTimestamp]) -> list[str]:
    """Capitalised words that aren't at a sentence boundary.

    Heuristic (no NER): Greek Whisper output capitalises proper nouns, so a
    mid-sentence capital is a reasonable positive signal. Skipping sentence
    starts avoids counting every opening word.
    """
    out: list[str] = []
    prev_ended_sentence = True   # treat segment start as sentence start
    for w in words:
        raw = w.word.strip()
        if not raw:
            continue
        head = raw.lstrip(_LEADING_STRIP)
        first = head[:1]
        if first.isupper() and not prev_ended_sentence:
            out.append(head.rstrip(_TRAILING_STRIP))
        prev_ended_sentence = raw.endswith(_SENTENCE_END_CHARS)
    return out


def extract_keyword_hits(
    transcript: Transcript | None,
    seg: Segment,
    cfg: KeywordsConfig,
) -> tuple[float, list[str]]:
    """Return (weighted hit count, human-readable reasons) for one segment."""
    if transcript is None or not transcript.words:
        return 0.0, []
    words = _words_in_segment(transcript.words, seg)
    if not words:
        return 0.0, []

    text = " ".join(w.word.strip() for w in words)
    text_norm = text.lower()

    exclamations = _find_exclamations(text_norm, cfg.exclamations)
    prices = _find_prices(text, cfg.price_pattern)
    names = _find_proper_names(words) if cfg.enable_proper_names else []

    score = (
        len(exclamations) * cfg.exclamation_weight
        + len(prices) * cfg.price_weight
        + len(names) * cfg.proper_name_weight
    )

    reasons: list[str] = []
    reasons += [f'keyword: "{e}"' for e in exclamations]
    reasons += [f"keyword: price ({p.strip()})" for p in prices]
    reasons += [f"keyword: name ({n})" for n in names]
    return score, reasons


def enrich_segments_keywords(
    segments: list[Segment],
    transcript: Transcript | None,
    cfg: KeywordsConfig,
) -> None:
    """Set `features['keyword_hits']` on keep segments and append reasons in-place.

    Does nothing for cut segments or when there is no transcript.
    """
    if transcript is None:
        return
    for seg in segments:
        if seg.decision != "keep":
            continue
        hits, reasons = extract_keyword_hits(transcript, seg, cfg)
        seg.features["keyword_hits"] = hits
        seg.reasons.extend(reasons)
