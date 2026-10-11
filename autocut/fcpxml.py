"""Export an EDL to FCPXML 1.11 so the editor can finish the cut in her NLE.

FCPXML is the native format for Final Cut Pro X and is imported by DaVinci
Resolve, Premiere Pro, iMovie and Motion — covering every editor the user
is likely to touch. Asset ``src`` attributes point at the ORIGINAL footage
paths (not the proxies ingest created) so the finishing session uses full-
quality media.

Timebase: 30000. All offsets/durations are emitted as ``N/30000s`` which
survives frame-accurate round-trips at 24/25/30/60 fps.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from autocut.models import EDL, ProxyInfo, Segment

TIMEBASE = 30000
_FRAME_DURATION = "1001/30000s"   # 29.97 fps NDF — matches typical phone footage


def _rational(seconds: float) -> str:
    return f"{round(seconds * TIMEBASE)}/{TIMEBASE}s"


def _file_uri(path: Path) -> str:
    try:
        return Path(path).resolve().as_uri()
    except (ValueError, OSError):
        # Non-absolute paths from relative EDLs — best-effort.
        return f"file://{path}"


def _timeline_order(edl: EDL) -> list[Segment]:
    """Keep segments ordered by (clip chronology, segment start).

    Matches the renderer's own ordering, so the FCPXML and the mp4 line up
    frame-for-frame when the editor opens the project.
    """
    clip_order = {c.clip_id: i for i, c in enumerate(edl.clips)}
    kept = [s for s in edl.segments if s.decision == "keep" and s.end > s.start]
    kept.sort(key=lambda s: (clip_order.get(s.clip_id, 0), s.start))
    return kept


def edl_to_fcpxml(
    edl: EDL,
    sequence_name: str = "rough_cut",
    event_name: str = "AutoCut Export",
) -> str:
    """Return a complete FCPXML document for the given EDL as a string."""
    fcpxml = ET.Element("fcpxml", {"version": "1.11"})
    resources = ET.SubElement(fcpxml, "resources")

    # One shared format for the sequence — editors conform clips at the project
    # frame rate on import, so the actual per-clip fps is cosmetic.
    ET.SubElement(resources, "format", {
        "id": "r0",
        "name": "FFVideoFormatRateUndefined",
        "frameDuration": _FRAME_DURATION,
    })

    # Deduplicate clips by original_path (two segments from the same clip must
    # share one <asset>, otherwise FCP ends up with duplicate media entries).
    asset_ids: dict[str, str] = {}
    for i, clip in enumerate(edl.clips):
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
    # If a hook is present, prepend it to the timeline — matches render() order.
    spine_clips: list[Segment] = []
    if edl.hook is not None:
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
            "name": f"{seg.clip_id}_{i:03d}",
            "ref": asset_id,
            "offset": _rational(timeline_offset),
            "start": _rational(seg.start),
            "duration": _rational(duration),
        })
        timeline_offset += duration

    # ElementTree writes the XML declaration when we ask for one; FCPXML files
    # customarily start with it.
    return ET.tostring(
        fcpxml, encoding="unicode", xml_declaration=True, short_empty_elements=True,
    )


def _append_asset(parent: ET.Element, asset_id: str, clip: ProxyInfo) -> None:
    src = _file_uri(clip.original_path)
    asset = ET.SubElement(parent, "asset", {
        "id": asset_id,
        "name": clip.clip_id,
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
    Path(path).write_text(edl_to_fcpxml(edl, **kwargs), encoding="utf-8")
