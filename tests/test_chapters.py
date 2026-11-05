"""Unit tests for autocut.chapters."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from autocut.chapters import (
    Chapter,
    _fmt_time,
    _title_from_words,
    _words_for_segments,
    generate_chapters,
    write_chapters_json,
    write_chapters_txt,
)
from autocut.models import EDL, ProxyInfo, Segment, Transcript, WordTimestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proxy(clip_id: str = "c") -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(f"{clip_id}.mp4"),
        proxy_path=Path(f"{clip_id}_proxy.mp4"),
        audio_path=Path(f"{clip_id}.wav"),
        duration=60.0, fps=30.0, width=1280, height=720,
    )


def _seg(start: float, end: float, decision: str = "keep", clip_id: str = "c") -> Segment:
    s = Segment(clip_id=clip_id, start=start, end=end, decision=decision)
    s.interest_score = 0.5
    return s


def _chunk(segment_index: int, out_start: float, out_end: float):
    return types.SimpleNamespace(
        segment_index=segment_index,
        output_start=out_start,
        output_end=out_end,
    )


def _word(w: str, start: float, end: float, clip_id: str = "c") -> WordTimestamp:
    return WordTimestamp(word=w, start=start, end=end, probability=0.9, clip_id=clip_id)


def _transcript(words: list[WordTimestamp], clip_id: str = "c") -> Transcript:
    return Transcript(clip_id=clip_id, language="en", words=words)


def _edl(segs, chunks, transcripts=None) -> EDL:
    edl = EDL(
        clips=[_proxy()],
        segments=segs,
        transcripts=transcripts or [],
    )
    edl.render_chunks = chunks
    return edl


# ---------------------------------------------------------------------------
# _fmt_time
# ---------------------------------------------------------------------------

def test_fmt_time_under_one_hour():
    assert _fmt_time(0) == "0:00"
    assert _fmt_time(65) == "1:05"
    assert _fmt_time(3599) == "59:59"


def test_fmt_time_over_one_hour():
    assert _fmt_time(3600) == "1:00:00"
    assert _fmt_time(3661) == "1:01:01"


# ---------------------------------------------------------------------------
# _title_from_words
# ---------------------------------------------------------------------------

def test_title_no_words():
    assert _title_from_words([], 0) == "Part 1"


def test_title_all_stopwords():
    assert _title_from_words(["the", "and", "is", "it"], 2) == "Part 3"


def test_title_uses_top_keywords():
    words = ["camera", "camera", "camera", "setup", "lighting", "the", "a"]
    title = _title_from_words(words, 0)
    assert "Camera" in title
    assert title.startswith("Part 1:")


def test_title_max_three_keywords():
    words = ["alpha", "beta", "gamma", "delta", "epsilon"]
    title = _title_from_words(words, 0)
    # At most 3 content words after "Part 1:"
    after_colon = title.split(": ", 1)[1] if ": " in title else ""
    assert len(after_colon.split()) <= 3


# ---------------------------------------------------------------------------
# _words_for_segments
# ---------------------------------------------------------------------------

def test_words_for_segments_empty_transcript():
    segs = [_seg(0, 5)]
    edl = _edl(segs, [])
    assert _words_for_segments(edl, [0]) == []


def test_words_for_segments_filters_by_time():
    segs = [_seg(0.0, 3.0)]
    words = [
        _word("hello", 0.5, 1.0),
        _word("world", 1.5, 2.0),
        _word("outside", 5.0, 6.0),  # outside segment
    ]
    edl = _edl(segs, [], transcripts=[_transcript(words)])
    result = _words_for_segments(edl, [0])
    assert "hello" in result
    assert "world" in result
    assert "outside" not in result


# ---------------------------------------------------------------------------
# generate_chapters
# ---------------------------------------------------------------------------

def test_no_chunks_returns_empty():
    edl = _edl([_seg(0, 5)], [])
    assert generate_chapters(edl) == []


def test_single_contiguous_block_returns_one_chapter():
    segs = [_seg(0, 5), _seg(5, 10)]
    chunks = [_chunk(0, 0.0, 5.0), _chunk(1, 5.0, 10.0)]
    chapters = generate_chapters(_edl(segs, chunks), min_gap_s=2.0)
    assert len(chapters) == 1


def test_gap_splits_into_two_chapters():
    segs = [_seg(0, 5), _seg(10, 15)]
    chunks = [_chunk(0, 0.0, 5.0), _chunk(1, 8.0, 13.0)]  # 3s gap
    chapters = generate_chapters(_edl(segs, chunks), min_gap_s=2.0)
    assert len(chapters) == 2
    assert chapters[0].output_start == pytest.approx(0.0)
    assert chapters[0].output_end == pytest.approx(5.0)
    assert chapters[1].output_start == pytest.approx(8.0)


def test_cut_segments_excluded():
    segs = [_seg(0, 5), _seg(5, 10, decision="cut"), _seg(10, 15)]
    chunks = [
        _chunk(0, 0.0, 5.0),
        # chunk 1 would be cut — not in render_chunks
        _chunk(2, 7.0, 12.0),
    ]
    # Only segs 0 and 2 are "keep"; the gap is 2s → splits into 2 chapters
    chapters = generate_chapters(_edl(segs, chunks), min_gap_s=2.0)
    assert len(chapters) == 2


def test_short_chapter_merged_into_preceding():
    segs = [_seg(0, 20), _seg(25, 27), _seg(32, 50)]
    chunks = [
        _chunk(0, 0.0, 20.0),
        _chunk(1, 23.0, 25.0),   # only 2s long — will be merged
        _chunk(2, 29.0, 47.0),
    ]
    chapters = generate_chapters(_edl(segs, chunks), min_gap_s=2.0, min_duration_s=5.0)
    # chunk1 is 2s → merged into chunk0 → result: 2 chapters
    assert len(chapters) == 2


def test_hook_chunk_excluded():
    segs = [_seg(0, 10), _seg(15, 25)]
    chunks = [
        _chunk(-1, 0.0, 3.0),   # hook
        _chunk(0, 3.0, 13.0),
        _chunk(1, 16.0, 26.0),
    ]
    # Hook excluded → gap between chunk0 and chunk1 = 3s → 2 chapters
    chapters = generate_chapters(_edl(segs, chunks), min_gap_s=2.0)
    assert all(ch.segment_indices[0] >= 0 for ch in chapters)


def test_chapter_titles_numbered():
    segs = [_seg(0, 20), _seg(25, 45)]
    chunks = [_chunk(0, 0.0, 20.0), _chunk(1, 23.0, 43.0)]
    chapters = generate_chapters(_edl(segs, chunks), min_gap_s=2.0)
    assert chapters[0].title.startswith("Part 1")
    assert chapters[1].title.startswith("Part 2")


# ---------------------------------------------------------------------------
# write_chapters_txt / write_chapters_json
# ---------------------------------------------------------------------------

def test_write_chapters_txt(tmp_path: Path):
    chapters = [
        Chapter(output_start=0.0, output_end=60.0, title="Part 1: Setup"),
        Chapter(output_start=65.0, output_end=130.0, title="Part 2: Demo"),
    ]
    out = tmp_path / "chapters.txt"
    write_chapters_txt(chapters, out)
    text = out.read_text()
    assert "0:00 Part 1: Setup" in text
    assert "1:05 Part 2: Demo" in text


def test_write_chapters_json(tmp_path: Path):
    chapters = [
        Chapter(output_start=0.0, output_end=60.0, title="Part 1"),
        Chapter(output_start=65.0, output_end=130.0, title="Part 2"),
    ]
    out = tmp_path / "chapters.json"
    write_chapters_json(chapters, out)
    data = json.loads(out.read_text())
    assert len(data) == 2
    assert data[0]["start"] == pytest.approx(0.0)
    assert data[1]["title"] == "Part 2"
