"""Tests for autocut.captions — SRT/ASS generation from word transcripts."""

from __future__ import annotations

import re

import pytest

from autocut.captions import (
    _ass_time,
    _build_ass,
    _build_srt,
    _group_words,
    _karaoke_text,
    _srt_time,
    write_captions,
)
from autocut.config import CaptionConfig
from autocut.models import Transcript, WordTimestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_word(word: str, start: float, end: float, clip_id: str = "c") -> WordTimestamp:
    return WordTimestamp(word=word, start=start, end=end, probability=0.9, clip_id=clip_id)


def make_transcript(*words: WordTimestamp, clip_id: str = "c") -> Transcript:
    return Transcript(clip_id=clip_id, language="el", words=list(words))


def default_cfg(**overrides) -> CaptionConfig:
    defaults = dict(
        formats=["srt", "ass"],
        max_words=5,
        max_duration_s=3.0,
        min_gap_s=0.4,
        style="tiktok",
    )
    defaults.update(overrides)
    return CaptionConfig(**defaults)


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def test_srt_time_zero():
    assert _srt_time(0.0) == "00:00:00,000"


def test_srt_time_one_second():
    assert _srt_time(1.0) == "00:00:01,000"


def test_srt_time_one_hour():
    assert _srt_time(3600.0) == "01:00:00,000"


def test_srt_time_milliseconds():
    assert _srt_time(1.5) == "00:00:01,500"


def test_srt_time_fractional():
    assert _srt_time(61.123) == "00:01:01,123"


def test_ass_time_zero():
    assert _ass_time(0.0) == "0:00:00.00"


def test_ass_time_one_second():
    assert _ass_time(1.0) == "0:00:01.00"


def test_ass_time_centiseconds():
    assert _ass_time(1.05) == "0:00:01.05"


def test_ass_time_one_minute():
    assert _ass_time(60.0) == "0:01:00.00"


def test_ass_time_one_hour():
    assert _ass_time(3600.0) == "1:00:00.00"


# ---------------------------------------------------------------------------
# _group_words
# ---------------------------------------------------------------------------

def test_group_empty():
    assert _group_words([], 5, 3.0, 0.4) == []


def test_group_single_word():
    words = [make_word("α", 0.0, 0.3)]
    groups = _group_words(words, max_words=5, max_duration_s=3.0, min_gap_s=0.4)
    assert len(groups) == 1
    assert len(groups[0]) == 1


def test_group_splits_at_max_words():
    words = [make_word(str(i), i * 0.1, i * 0.1 + 0.09) for i in range(10)]
    groups = _group_words(words, max_words=3, max_duration_s=10.0, min_gap_s=10.0)
    assert all(len(g) <= 3 for g in groups)
    assert sum(len(g) for g in groups) == 10


def test_group_splits_at_max_duration():
    # 6 close words each lasting 0.6s → each 2-word group = 1.2s, 3-word = 1.8s, 4-word = 2.4s
    words = [make_word("w", i * 0.6, i * 0.6 + 0.5) for i in range(6)]
    groups = _group_words(words, max_words=10, max_duration_s=2.0, min_gap_s=10.0)
    for g in groups:
        dur = g[-1].end - g[0].start
        assert dur <= 2.0 + 0.6  # allow one word overage from projection logic


def test_group_splits_at_gap():
    words = [
        make_word("α", 0.0, 0.3),
        make_word("β", 0.4, 0.7),   # gap = 0.1s < 0.4s → same group
        make_word("γ", 2.0, 2.3),   # gap = 1.3s >= 0.4s → new group
    ]
    groups = _group_words(words, max_words=5, max_duration_s=3.0, min_gap_s=0.4)
    assert len(groups) == 2
    assert groups[0][-1].word == "β"
    assert groups[1][0].word == "γ"


def test_group_all_words_covered():
    words = [make_word(str(i), i * 0.5, i * 0.5 + 0.4) for i in range(8)]
    groups = _group_words(words, max_words=3, max_duration_s=3.0, min_gap_s=0.4)
    all_words = [w for g in groups for w in g]
    assert len(all_words) == 8


# ---------------------------------------------------------------------------
# _build_srt
# ---------------------------------------------------------------------------

def test_srt_empty_groups():
    assert _build_srt([]) == ""


def test_srt_single_group():
    group = [make_word("γεια", 0.1, 0.4), make_word("σου", 0.5, 0.8)]
    srt = _build_srt([group])
    assert "1\n" in srt
    assert "00:00:00,100 --> 00:00:00,800" in srt
    assert "γεια σου" in srt


def test_srt_numbering():
    groups = [
        [make_word("α", 0.0, 0.3)],
        [make_word("β", 1.0, 1.3)],
        [make_word("γ", 2.0, 2.3)],
    ]
    srt = _build_srt(groups)
    assert "1\n" in srt
    assert "2\n" in srt
    assert "3\n" in srt


def test_srt_uses_group_start_and_end():
    group = [make_word("α", 1.5, 1.8), make_word("β", 1.9, 2.2)]
    srt = _build_srt([group])
    assert "00:00:01,500 --> 00:00:02,200" in srt


def test_srt_strips_word_whitespace():
    group = [make_word(" γεια ", 0.0, 0.3)]
    srt = _build_srt([group])
    assert "γεια\n" in srt


# ---------------------------------------------------------------------------
# _karaoke_text / _build_ass
# ---------------------------------------------------------------------------

def test_karaoke_text_single_word():
    group = [make_word("γεια", 1.0, 1.3)]
    text = _karaoke_text(group, line_start=1.0)
    assert r"{\k" in text or "{\\k" in text
    assert "γεια" in text


def test_karaoke_text_contains_k_tags():
    group = [make_word("γεια", 0.0, 0.3), make_word("σου", 0.5, 0.8)]
    text = _karaoke_text(group, line_start=0.0)
    assert text.count(r"\k") == 2


def test_karaoke_first_word_duration():
    """First \\k duration = (word.end - line_start) * 100."""
    group = [make_word("γεια", 1.0, 1.3)]
    text = _karaoke_text(group, line_start=1.0)
    # duration should be (1.3 - 1.0) * 100 = 30cs
    assert r"\k30" in text


def test_karaoke_second_word_duration():
    """Second word's \\k duration = (word.end - prev_word.end) * 100."""
    group = [make_word("γεια", 0.0, 0.3), make_word("σου", 0.5, 0.8)]
    text = _karaoke_text(group, line_start=0.0)
    # "γεια": (0.3 - 0.0)*100 = 30cs
    # "σου": (0.8 - 0.3)*100 = 50cs
    assert r"\k30" in text
    assert r"\k50" in text


def test_karaoke_minimum_duration_is_one():
    """Duration must be at least 1cs even for zero-length words."""
    group = [make_word("α", 0.0, 0.0)]
    text = _karaoke_text(group, line_start=0.0)
    assert r"\k1" in text


def test_build_ass_contains_script_info():
    ass = _build_ass([], style="tiktok")
    assert "[Script Info]" in ass
    assert "ScriptType: v4.00+" in ass


def test_build_ass_contains_styles_section():
    ass = _build_ass([], style="tiktok")
    assert "[V4+ Styles]" in ass
    assert "TikTok" in ass


def test_build_ass_contains_events_section():
    ass = _build_ass([], style="tiktok")
    assert "[Events]" in ass


def test_build_ass_dialogue_lines():
    groups = [
        [make_word("γεια", 0.0, 0.3), make_word("σου", 0.4, 0.7)],
        [make_word("πώς", 1.5, 1.8)],
    ]
    ass = _build_ass(groups, style="tiktok")
    dialogue_lines = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogue_lines) == 2


def test_build_ass_tiktok_play_res():
    ass = _build_ass([], style="tiktok")
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass


def test_build_ass_default_play_res():
    ass = _build_ass([], style="default")
    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass


def test_build_ass_unknown_style_falls_back_to_default():
    ass = _build_ass([], style="unknown_preset")
    assert "[V4+ Styles]" in ass  # should not crash


def test_build_ass_dialogue_timing():
    group = [make_word("α", 1.5, 2.0)]
    ass = _build_ass([group], style="default")
    assert "0:00:01.50" in ass
    assert "0:00:02.00" in ass


def test_build_ass_empty_transcript_no_dialogue():
    ass = _build_ass([], style="tiktok")
    assert "Dialogue:" not in ass


# ---------------------------------------------------------------------------
# write_captions
# ---------------------------------------------------------------------------

def test_write_creates_srt_file(tmp_path):
    t = make_transcript(
        make_word("γεια", 0.1, 0.4),
        make_word("σου", 0.5, 0.8),
    )
    cfg = default_cfg(formats=["srt"])
    written = write_captions(t, cfg, tmp_path, "clip1")
    assert "srt" in written
    assert written["srt"].exists()
    assert written["srt"].suffix == ".srt"


def test_write_creates_ass_file(tmp_path):
    t = make_transcript(make_word("γεια", 0.1, 0.4))
    cfg = default_cfg(formats=["ass"])
    written = write_captions(t, cfg, tmp_path, "clip1")
    assert "ass" in written
    assert written["ass"].exists()
    assert written["ass"].suffix == ".ass"


def test_write_both_formats(tmp_path):
    t = make_transcript(make_word("γεια", 0.1, 0.4))
    cfg = default_cfg(formats=["srt", "ass"])
    written = write_captions(t, cfg, tmp_path, "myclip")
    assert len(written) == 2
    assert (tmp_path / "myclip.srt").exists()
    assert (tmp_path / "myclip.ass").exists()


def test_write_uses_stem_in_filename(tmp_path):
    t = make_transcript(make_word("γεια", 0.1, 0.4))
    cfg = default_cfg(formats=["srt"])
    written = write_captions(t, cfg, tmp_path, "myvideo")
    assert written["srt"].name == "myvideo.srt"


def test_write_empty_transcript_produces_empty_srt(tmp_path):
    t = Transcript(clip_id="c", language="el", words=[])
    cfg = default_cfg(formats=["srt"])
    written = write_captions(t, cfg, tmp_path, "empty")
    assert written["srt"].read_text(encoding="utf-8") == ""


def test_write_srt_is_utf8(tmp_path):
    t = make_transcript(make_word("γεια", 0.1, 0.4))
    cfg = default_cfg(formats=["srt"])
    written = write_captions(t, cfg, tmp_path, "c")
    content = written["srt"].read_bytes()
    assert "γεια".encode("utf-8") in content


def test_write_creates_output_dir(tmp_path):
    t = make_transcript(make_word("γεια", 0.1, 0.4))
    cfg = default_cfg(formats=["srt"])
    nested = tmp_path / "sub" / "dir"
    write_captions(t, cfg, nested, "c")
    assert nested.exists()


def test_write_only_srt_no_ass_key(tmp_path):
    t = make_transcript(make_word("γεια", 0.1, 0.4))
    cfg = default_cfg(formats=["srt"])
    written = write_captions(t, cfg, tmp_path, "c")
    assert "ass" not in written
