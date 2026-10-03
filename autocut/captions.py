"""Caption generation: SRT and ASS (karaoke) from word-timestamp transcripts."""

from __future__ import annotations

from pathlib import Path

from autocut.config import CaptionConfig
from autocut.models import Transcript, WordTimestamp


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def _srt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm (SRT)."""
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(seconds: float) -> str:
    """Format seconds as H:MM:SS.cc (ASS centiseconds)."""
    cs = round(seconds * 100)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# Word grouping
# ---------------------------------------------------------------------------

def _group_words(
    words: list[WordTimestamp],
    max_words: int,
    max_duration_s: float,
    min_gap_s: float,
) -> list[list[WordTimestamp]]:
    """Partition words into caption groups.

    A new group starts when any of these conditions are met:
    - The current group already has max_words words.
    - Adding the next word would exceed max_duration_s.
    - There is a silence gap >= min_gap_s before the next word.
    """
    if not words:
        return []
    groups: list[list[WordTimestamp]] = [[words[0]]]
    for prev, curr in zip(words, words[1:]):
        g = groups[-1]
        gap = curr.start - prev.end
        projected_dur = curr.end - g[0].start
        if len(g) >= max_words or projected_dur > max_duration_s or gap >= min_gap_s:
            groups.append([curr])
        else:
            g.append(curr)
    return groups


# ---------------------------------------------------------------------------
# SRT builder
# ---------------------------------------------------------------------------

def _build_srt(groups: list[list[WordTimestamp]]) -> str:
    """Return a complete SRT file as a string."""
    lines: list[str] = []
    for i, group in enumerate(groups, 1):
        start = _srt_time(group[0].start)
        end = _srt_time(group[-1].end)
        text = " ".join(w.word.strip() for w in group)
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ASS builder
# ---------------------------------------------------------------------------

# Colors in ASS ABGR hex format (&HAABBGGRR where AA=00 is opaque):
#   White   &H00FFFFFF   — RGB(255, 255, 255)
#   Yellow  &H0000FFFF   — RGB(255, 255,   0)
#   Black   &H00000000

_ASS_STYLE_DEFS: dict[str, str] = {
    # Portrait TikTok: large Impact, white text, yellow karaoke, heavy outline.
    # Alignment=2 (bottom centre), PlayRes 1080×1920.
    "tiktok": (
        "Style: TikTok,Impact,80,"
        "&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,4,0,2,20,20,80,1"
    ),
    # Standard landscape default.
    "default": (
        "Style: Default,Arial,60,"
        "&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,40,1"
    ),
}

_ASS_PLAY_RES: dict[str, tuple[int, int]] = {
    "tiktok": (1080, 1920),
    "default": (1920, 1080),
}

_ASS_HEADER_TMPL = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_def}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _karaoke_text(group: list[WordTimestamp], line_start: float) -> str:
    """Build ASS karaoke text with \\k tags (duration in cs from previous tag end)."""
    parts: list[str] = []
    prev_end = line_start
    for w in group:
        cs = max(1, round((w.end - prev_end) * 100))
        parts.append(f"{{\\k{cs}}}{w.word.strip()}")
        prev_end = w.end
    return " ".join(parts)


def _build_ass(groups: list[list[WordTimestamp]], style: str = "tiktok") -> str:
    """Return a complete ASS file as a string."""
    style_def = _ASS_STYLE_DEFS.get(style, _ASS_STYLE_DEFS["default"])
    style_name = style_def.split(",", 1)[0].removeprefix("Style: ")
    w, h = _ASS_PLAY_RES.get(style, _ASS_PLAY_RES["default"])

    header = _ASS_HEADER_TMPL.format(w=w, h=h, style_def=style_def)
    events: list[str] = []
    for group in groups:
        start = _ass_time(group[0].start)
        end = _ass_time(group[-1].end)
        text = _karaoke_text(group, group[0].start)
        events.append(
            f"Dialogue: 0,{start},{end},{style_name},,0,0,0,,{text}"
        )
    return header + "\n".join(events) + ("\n" if events else "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_captions(
    transcript: Transcript,
    cfg: CaptionConfig,
    output_dir: Path,
    stem: str,
) -> dict[str, Path]:
    """Write caption files for *transcript* to *output_dir*.

    Args:
        transcript: Word-level transcript for one clip.
        cfg: Caption config (formats, grouping, style).
        output_dir: Directory to write files into.
        stem: Filename stem (clip_id or custom name).

    Returns:
        Mapping of format name → written Path for each enabled format.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    groups = _group_words(
        transcript.words,
        max_words=cfg.max_words,
        max_duration_s=cfg.max_duration_s,
        min_gap_s=cfg.min_gap_s,
    )
    written: dict[str, Path] = {}

    if "srt" in cfg.formats:
        srt_path = output_dir / f"{stem}.srt"
        srt_path.write_text(_build_srt(groups), encoding="utf-8")
        written["srt"] = srt_path

    if "ass" in cfg.formats:
        ass_path = output_dir / f"{stem}.ass"
        ass_path.write_text(_build_ass(groups, style=cfg.style), encoding="utf-8")
        written["ass"] = ass_path

    return written
