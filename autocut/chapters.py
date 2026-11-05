"""Auto-chapter generation from EDL render_chunks.

Chapters are detected by finding large output-timeline gaps between kept
segments.  Each chapter is titled using the most frequent content words from
its transcript — giving YouTube-ready markers like "Part 1: Camera Setup".

Two output formats:
  chapters.txt  — YouTube copy-paste format  (0:00 Title\\n1:23 Next…)
  chapters.json — machine-readable [{start, end, title}]

Generation is intentionally conservative:
  • Only called when there are ≥ 2 chapters (single-chapter video → no file).
  • Chapters shorter than ``min_duration_s`` are merged into the previous one.
  • Missing transcript → numbered titles ("Part 1", "Part 2", …).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from autocut.models import EDL

# Small hardcoded stopword set covering the most common Greek + English words.
_STOPWORDS = frozenset({
    # English
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "that", "this", "it", "he", "she", "we",
    "they", "you", "i", "me", "my", "our", "your", "his", "her", "their",
    "so", "if", "not", "no", "yes", "just", "like", "get", "got", "go",
    "going", "also", "can", "now", "here", "there", "then", "than",
    "about", "what", "when", "where", "which", "who", "how", "all", "some",
    "im", "its", "its", "dont", "cant", "wont", "thats", "its", "were",
    # Greek
    "και", "το", "τη", "τον", "την", "τα", "της", "του", "των", "τους",
    "που", "να", "δεν", "θα", "με", "για", "σε", "από", "στο", "στη",
    "στον", "ότι", "αλλά", "ή", "αν", "ως", "κι", "πιο", "πολύ", "έχει",
    "έχω", "είναι", "ήταν", "μια", "ένα", "ένας", "έχουμε", "αυτό",
    "αυτά", "αυτή", "αυτός", "αυτοί", "μου", "σου", "πώς", "τι", "πότε",
    "μας", "τώρα", "εδώ", "εκεί", "κάτι", "πάρα", "μόνο", "κάθε",
})


@dataclass
class Chapter:
    output_start: float
    output_end: float
    title: str
    segment_indices: list[int] = field(default_factory=list)


def _words_for_segments(edl: EDL, seg_indices: list[int]) -> list[str]:
    """Return lowercased, stripped words from the transcripts of these segments."""
    transcript_map: dict[str, list] = {t.clip_id: t.words for t in edl.transcripts}
    words: list[str] = []
    for idx in seg_indices:
        if idx < 0 or idx >= len(edl.segments):
            continue
        seg = edl.segments[idx]
        for w in transcript_map.get(seg.clip_id, []):
            if w.start >= seg.start - 0.05 and w.end <= seg.end + 0.1:
                cleaned = re.sub(r"[^\w]", "", w.word.lower())
                if cleaned:
                    words.append(cleaned)
    return words


def _title_from_words(words: list[str], index: int) -> str:
    base = f"Part {index + 1}"
    content = [
        w for w in words
        if w and w not in _STOPWORDS and len(w) > 2 and re.match(r"^\w+$", w)
    ]
    if not content:
        return base
    top = [w for w, _ in Counter(content).most_common(3)]
    subtitle = " ".join(w.capitalize() for w in top)
    return f"{base}: {subtitle}"


def generate_chapters(
    edl: EDL,
    min_gap_s: float = 2.0,
    min_duration_s: float = 5.0,
) -> list[Chapter]:
    """Return chapters split at output-timeline gaps ≥ ``min_gap_s``.

    Chapters shorter than ``min_duration_s`` are merged into the preceding
    chapter so we don't produce micro-chapters for quick B-roll cuts.
    Returns an empty list when only one chapter would result — callers should
    suppress output in that case.
    """
    kept = sorted(
        [
            c for c in edl.render_chunks
            if c.segment_index >= 0
            and c.segment_index < len(edl.segments)
            and edl.segments[c.segment_index].decision == "keep"
        ],
        key=lambda c: c.output_start,
    )
    if not kept:
        return []

    # Group consecutive chunks into raw chapter buckets.
    raw: list[list] = [[kept[0]]]
    for prev, cur in zip(kept, kept[1:]):
        if cur.output_start - prev.output_end >= min_gap_s:
            raw.append([])
        raw[-1].append(cur)

    # Build Chapter objects with keyword titles.
    chapters: list[Chapter] = []
    for i, group in enumerate(raw):
        seg_indices = [c.segment_index for c in group]
        title = _title_from_words(_words_for_segments(edl, seg_indices), i)
        chapters.append(Chapter(
            output_start=group[0].output_start,
            output_end=group[-1].output_end,
            title=title,
            segment_indices=seg_indices,
        ))

    # Merge chapters that are too short into the preceding one.
    merged: list[Chapter] = []
    for ch in chapters:
        if merged and (ch.output_end - ch.output_start) < min_duration_s:
            merged[-1].output_end = ch.output_end
            merged[-1].segment_indices.extend(ch.segment_indices)
            words = _words_for_segments(edl, merged[-1].segment_indices)
            merged[-1].title = _title_from_words(words, len(merged) - 1)
        else:
            merged.append(ch)

    return merged


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def write_chapters_txt(chapters: list[Chapter], path: Path) -> None:
    """Write YouTube-compatible chapter markers (0:00 Title per line)."""
    lines = [f"{_fmt_time(ch.output_start)} {ch.title}" for ch in chapters]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_chapters_json(chapters: list[Chapter], path: Path) -> None:
    data = [
        {"start": ch.output_start, "end": ch.output_end, "title": ch.title}
        for ch in chapters
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
