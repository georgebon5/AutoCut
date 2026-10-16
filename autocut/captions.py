"""Caption generation: SRT and ASS (karaoke) from word-timestamp transcripts."""

from __future__ import annotations

from pathlib import Path

from autocut.config import CaptionConfig
from autocut.models import EDL, Segment, Transcript, WordTimestamp


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


# ASS alignment keypad codes (numpad layout): 1-3 bottom, 4-6 middle, 7-9 top;
# the middle column (2, 5, 8) is horizontally centred.
_ALIGNMENTS = {"bottom": 2, "middle": 5, "top": 8}


def _hex_to_ass_color(hex_rgb: str) -> str:
    """Convert '#RRGGBB' (HTML) to '&H00BBGGRR' (ASS ABGR, fully opaque)."""
    s = hex_rgb.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"expected #RRGGBB, got {hex_rgb!r}")
    r, g, b = s[0:2], s[2:4], s[4:6]
    return f"&H00{b}{g}{r}".upper()


def _render_style_def(
    *,
    name: str,
    font: str,
    size: int,
    primary_color: str,     # ASS format "&H00BBGGRR"
    outline_color: str,
    secondary_color: str = "&H0000FFFF",   # karaoke highlight (yellow default)
    bold: int = -1,         # -1 = true, 0 = false
    outline_width: int = 4,
    alignment: int = 2,     # ASS keypad alignment
    margin_v: int = 80,
) -> str:
    """Format one ASS style line from parts."""
    return (
        f"Style: {name},{font},{size},"
        f"{primary_color},{secondary_color},{outline_color},&H00000000,"
        f"{bold},0,0,0,100,100,0,0,1,{outline_width},0,{alignment},20,20,{margin_v},1"
    )

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


def _build_ass(
    groups: list[list[WordTimestamp]],
    style: str = "tiktok",
    overrides: dict | None = None,
) -> str:
    """Return a complete ASS file as a string.

    ``overrides`` lets the caller tweak font/size/colors/position without
    changing the preset — used by the review-time style picker in the UI.
    Accepted keys: font, size, text_color, outline_color, position
    ("top"|"middle"|"bottom"). All optional.
    """
    style_def = _ASS_STYLE_DEFS.get(style, _ASS_STYLE_DEFS["default"])
    style_name = style_def.split(",", 1)[0].removeprefix("Style: ")
    w, h = _ASS_PLAY_RES.get(style, _ASS_PLAY_RES["default"])

    if overrides:
        # Pull preset defaults so unset override fields stay consistent with
        # the preset's look (font weight, karaoke colour, outline width).
        base_parts = style_def.removeprefix("Style: ").split(",")
        base_font, base_size = base_parts[1], int(base_parts[2])
        base_primary, base_outline = base_parts[3], base_parts[5]
        style_def = _render_style_def(
            name=style_name,
            font=overrides.get("font") or base_font,
            size=int(overrides.get("size") or base_size),
            primary_color=(
                _hex_to_ass_color(overrides["text_color"])
                if overrides.get("text_color") else base_primary
            ),
            outline_color=(
                _hex_to_ass_color(overrides["outline_color"])
                if overrides.get("outline_color") else base_outline
            ),
            alignment=_ALIGNMENTS.get(overrides.get("position") or "", 2),
        )

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

def _remap_words_to_output(
    edl: EDL, clip_order: dict[str, int],
) -> list[WordTimestamp]:
    """Walk the final cut in render order and remap each word's timestamps to
    the output timeline.

    Mirrors render.py's ordering exactly: hook first (if present), then keep
    segments sorted by (clip_order, segment.start). Each word's new timestamps
    are measured from the start of the rendered mp4, so an SRT built from this
    aligns frame-for-frame with the video.
    """
    ordered: list[Segment] = []
    if edl.hook is not None:
        ordered.append(edl.hook)
    kept = [s for s in edl.segments if s.decision == "keep" and s.end > s.start]
    kept.sort(key=lambda s: (clip_order.get(s.clip_id, 0), s.start))
    ordered.extend(kept)

    transcripts_by_clip = {t.clip_id: t for t in edl.transcripts}

    out: list[WordTimestamp] = []
    cumulative = 0.0
    for seg in ordered:
        t = transcripts_by_clip.get(seg.clip_id)
        if t is None:
            cumulative += seg.end - seg.start
            continue
        for w in t.words:
            # Half-open: a word starting exactly at seg.end belongs to the
            # NEXT segment (if any). Keeps boundary words from being counted
            # twice or appearing in a cut-adjacent segment.
            if seg.start <= w.start < seg.end:
                # Clamp word.end to seg.end so a word straddling the cut
                # doesn't produce a caption that stretches past the output.
                w_end = min(w.end, seg.end)
                out.append(WordTimestamp(
                    word=w.word,
                    start=cumulative + (w.start - seg.start),
                    end=cumulative + (w_end - seg.start),
                    probability=w.probability,
                    clip_id=w.clip_id,
                ))
        cumulative += seg.end - seg.start
    return out


def write_cut_captions(
    edl: EDL,
    cfg: CaptionConfig,
    output_dir: Path,
    stem: str,
    clip_order: dict[str, int],
    style_overrides: dict | None = None,
) -> dict[str, Path]:
    """Write captions whose timestamps match the rendered rough cut.

    Unlike ``write_captions`` (which uses raw-clip timestamps), this walks the
    EDL in render order and emits a single SRT/ASS pair for the whole output,
    accumulating per-segment offsets. The result stays in sync when the user
    burns it onto the mp4 or imports both into an NLE.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    remapped = _remap_words_to_output(edl, clip_order)
    groups = _group_words(
        remapped,
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
        ass_path.write_text(
            _build_ass(groups, style=cfg.style, overrides=style_overrides),
            encoding="utf-8",
        )
        written["ass"] = ass_path
    return written


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
