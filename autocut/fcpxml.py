"""Export an EDL to FCPXML 1.11 so the editor can finish the cut in her NLE.

FCPXML is the native format for Final Cut Pro X and is imported by DaVinci
Resolve, Premiere Pro, iMovie and Motion — covering every editor the user
is likely to touch. Asset ``src`` attributes point at the ORIGINAL footage
paths (not the proxies ingest created) so the finishing session uses full-
quality media.

Timebase: 30000. All offsets/durations are emitted as ``N/30000s`` which
survives frame-accurate round-trips at 24/25/30/60 fps.

Frame duration for the <format> element is derived from the first real clip's
fps so the sequence rate matches the source footage.  Supported:
  29.97fps → 1001/30000s, 23.976fps → 1001/24000s, 59.94fps → 1001/60000s
  30fps → 1000/30000s, 25fps → 1200/30000s, 24fps → 1250/30000s, etc.

Voiceover-mode pseudo-clips (clip_id == "voiceover") are silently excluded;
FCP cannot reference audio-only source files as video-timeline assets.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from autocut.models import EDL, ProxyInfo, Segment

TIMEBASE = 30000
_SKIP_CLIP_IDS = frozenset({"voiceover"})


def _frame_duration_str(fps: float) -> str:
    """Return the CMTime fraction string for one frame at the given fps."""
    if abs(fps - 29.97) < 0.05:
        return "1001/30000s"
    if abs(fps - 23.976) < 0.05:
        return "1001/24000s"
    if abs(fps - 59.94) < 0.05:
        return "1001/60000s"
    # Clean integer rates: 30fps→1000/30000s, 25fps→1200/30000s, etc.
    return f"{round(TIMEBASE / fps)}/{TIMEBASE}s"


def _rational(seconds: float) -> str:
    return f"{round(seconds * TIMEBASE)}/{TIMEBASE}s"


def _file_uri(path: Path) -> str:
    try:
        return Path(path).resolve().as_uri()
    except (ValueError, OSError):
        return f"file://{path}"


def _timeline_order(edl: EDL) -> list[Segment]:
    """Keep segments ordered by (clip chronology, segment start).

    Matches the renderer's own ordering so the FCPXML and the mp4 line up
    frame-for-frame when the editor opens the project.
    """
    clip_order = {c.clip_id: i for i, c in enumerate(edl.clips)}
    kept = [
        s for s in edl.segments
        if s.decision == "keep" and s.end > s.start
        and s.clip_id not in _SKIP_CLIP_IDS
    ]
    kept.sort(key=lambda s: (clip_order.get(s.clip_id, 0), s.start))
    return kept


def edl_to_fcpxml(
    edl: EDL,
    sequence_name: str = "rough_cut",
    event_name: str = "AutoCut Export",
) -> str:
    """Return a complete FCPXML 1.11 document for the given EDL as a string."""
    fcpxml = ET.Element("fcpxml", {"version": "1.11"})
    resources = ET.SubElement(fcpxml, "resources")

    # Determine sequence fps from the first real (non-voiceover) clip.
    real_clips = [c for c in edl.clips if c.clip_id not in _SKIP_CLIP_IDS]
    fps = real_clips[0].fps if real_clips else 30.0
    frame_dur = _frame_duration_str(fps)

    ET.SubElement(resources, "format", {
        "id": "r0",
        "frameDuration": frame_dur,
    })

    # Deduplicate clips by original_path.
    asset_ids: dict[str, str] = {}
    for i, clip in enumerate(edl.clips):
        if clip.clip_id in _SKIP_CLIP_IDS:
            continue
        key = str(clip.original_path)
        if key in asset_ids:
            continue
        asset_id = f"r{i + 1}"
        asset_ids[key] = asset_id
        _append_asset(resources, asset_id, clip)

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": event_name})
    project = ET.SubElement(event, "project", {"name": sequence_name})

    kept = _timeline_order(edl)
    spine_clips: list[Segment] = []
    if edl.hook is not None and edl.hook.clip_id not in _SKIP_CLIP_IDS:
        spine_clips.append(edl.hook)
    spine_clips.extend(kept)

    total_dur = sum(s.end - s.start for s in spine_clips)
    sequence = ET.SubElement(project, "sequence", {
        "format": "r0",
        "duration": _rational(total_dur),
        "tcStart": "0s",
        "tcFormat": "NDF",
    })
    spine = ET.SubElement(sequence, "spine")

    timeline_offset = 0.0
    clip_by_id = {c.clip_id: c for c in edl.clips}
    for i, seg in enumerate(spine_clips):
        clip = clip_by_id.get(seg.clip_id)
        if clip is None:
            continue
        asset_id = asset_ids.get(str(clip.original_path))
        if asset_id is None:
            continue
        duration = seg.end - seg.start
        ET.SubElement(spine, "asset-clip", {
            "name": f"{clip.original_path.stem}_{i:03d}",
            "ref": asset_id,
            "offset": _rational(timeline_offset),
            "start": _rational(seg.start),
            "duration": _rational(duration),
        })
        timeline_offset += duration

    return ET.tostring(
        fcpxml, encoding="unicode", xml_declaration=True, short_empty_elements=True,
    )


def _append_asset(parent: ET.Element, asset_id: str, clip: ProxyInfo) -> None:
    src = _file_uri(clip.original_path)
    asset = ET.SubElement(parent, "asset", {
        "id": asset_id,
        "name": clip.original_path.stem,
        "start": "0s",
        "duration": _rational(clip.duration),
        "hasAudio": "1" if clip.audio_path is not None else "0",
        "hasVideo": "1",
        "format": "r0",
        "src": src,
    })
    ET.SubElement(asset, "media-rep", {
        "kind": "original-media",
        "src": src,
    })


def save_fcpxml(edl: EDL, path: Path, **kwargs) -> None:
    """Write FCPXML to ``path``."""
    Path(path).write_text(edl_to_fcpxml(edl, **kwargs), encoding="utf-8")
