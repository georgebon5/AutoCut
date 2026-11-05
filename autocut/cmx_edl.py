"""CMX 3600 EDL export — industry-standard edit decision list.

The output is a plain-text ``.edl`` file readable by Premiere Pro, DaVinci
Resolve, Avid, and most other professional NLEs.  Each kept segment in render
order becomes one numbered event pointing at its source clip.

Timecodes:
  - Record (output) timeline: NDF at ``_RECORD_FPS`` (30 fps).
  - Source clips: the clip's native fps as stored in the EDL.

Reel names are derived from the clip filename stem, truncated to 8 uppercase
alphanumeric characters as required by the CMX 3600 spec.
"""

from __future__ import annotations

import re
from pathlib import Path

from autocut.models import EDL

_RECORD_FPS: int = 30


def _tc(seconds: float, fps: float) -> str:
    """Convert seconds to ``HH:MM:SS:FF`` (NDF) at the given frame rate."""
    fps_int = max(1, round(fps))
    total_frames = round(seconds * fps)
    ff = total_frames % fps_int
    total_secs = total_frames // fps_int
    hh = total_secs // 3600
    mm = (total_secs % 3600) // 60
    ss = total_secs % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _reel_name(clip_id: str) -> str:
    """Derive a CMX reel name: max 8 uppercase alphanumeric characters."""
    clean = re.sub(r"[^A-Za-z0-9]", "", clip_id).upper()
    return clean[:8] or "AX"


def edl_to_cmx(edl: EDL, title: str = "AutoCut Export") -> str:
    """Return a CMX 3600 EDL string for all kept segments in render order."""
    clip_by_id = {c.clip_id: c for c in edl.clips}
    seg_by_idx = {i: s for i, s in enumerate(edl.segments)}

    lines = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
    ]

    event = 1
    for chunk in edl.render_chunks:
        if chunk.segment_index == -1:
            seg = edl.hook
        else:
            seg = seg_by_idx.get(chunk.segment_index)
        if seg is None:
            continue

        clip = clip_by_id.get(seg.clip_id)
        src_fps: float = clip.fps if clip else float(_RECORD_FPS)
        reel = _reel_name(clip.clip_id if clip else seg.clip_id)

        src_in  = _tc(seg.start,          src_fps)
        src_out = _tc(seg.end,            src_fps)
        rec_in  = _tc(chunk.output_start, _RECORD_FPS)
        rec_out = _tc(chunk.output_end,   _RECORD_FPS)

        lines.append(
            f"{event:03d}  {reel:<8} {'V':<5} {'C':<5}    "
            f"{src_in} {src_out} {rec_in} {rec_out}"
        )
        if clip:
            lines.append(
                f"* FROM CLIP NAME:  {Path(clip.original_path).name}"
            )
        lines.append("")
        event += 1

    return "\n".join(lines)


def save_cmx(edl: EDL, path: Path, title: str = "AutoCut Export") -> None:
    """Write a CMX 3600 EDL file to ``path``."""
    path.write_text(edl_to_cmx(edl, title=title), encoding="utf-8")
