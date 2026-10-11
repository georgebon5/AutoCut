"""Tests for autocut.fcpxml — EDL → FCPXML export for NLE handoff."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from autocut.fcpxml import edl_to_fcpxml, save_fcpxml
from autocut.models import EDL, ProxyInfo, Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proxy(clip_id: str, original: str, duration: float = 10.0) -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(original),
        proxy_path=Path(f"/tmp/{clip_id}_proxy.mp4"),
        audio_path=Path(f"/tmp/{clip_id}.wav"),
        duration=duration, fps=30.0, width=1920, height=1080,
    )


def _seg(
    clip_id: str, start: float, end: float, decision: str = "keep",
) -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision=decision)


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


# ---------------------------------------------------------------------------
# Document structure
# ---------------------------------------------------------------------------

def test_output_is_valid_xml_with_fcpxml_root():
    edl = EDL(clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)])
    root = _parse(edl_to_fcpxml(edl))
    assert root.tag == "fcpxml"
    assert root.attrib["version"].startswith("1.")


def test_contains_resources_library_event_project_sequence_spine():
    edl = EDL(clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)])
    root = _parse(edl_to_fcpxml(edl))
    assert root.find("resources") is not None
    assert root.find("library/event") is not None
    assert root.find("library/event/project") is not None
    assert root.find("library/event/project/sequence") is not None
    assert root.find("library/event/project/sequence/spine") is not None


def test_xml_declaration_present():
    edl = EDL(clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)])
    xml = edl_to_fcpxml(edl)
    assert xml.startswith("<?xml")


# ---------------------------------------------------------------------------
# Assets (deduplicated per original path)
# ---------------------------------------------------------------------------

def test_one_asset_per_unique_clip():
    edl = EDL(
        clips=[_proxy("a", "/footage/a.mp4"), _proxy("b", "/footage/b.mp4")],
        segments=[_seg("a", 0, 1), _seg("b", 2, 3)],
    )
    root = _parse(edl_to_fcpxml(edl))
    assets = root.findall("resources/asset")
    assert len(assets) == 2


def test_asset_src_points_at_original_not_proxy():
    """The whole point of the export is to let the editor finish on originals."""
    edl = EDL(
        clips=[_proxy("c", "/footage/original.mp4")],
        segments=[_seg("c", 0, 1)],
    )
    root = _parse(edl_to_fcpxml(edl))
    asset = root.find("resources/asset")
    assert "original.mp4" in asset.attrib["src"]
    assert "proxy" not in asset.attrib["src"]


def test_asset_uses_file_uri_scheme():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)],
    )
    root = _parse(edl_to_fcpxml(edl))
    assert root.find("resources/asset").attrib["src"].startswith("file://")


def test_media_rep_child_mirrors_src():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)],
    )
    root = _parse(edl_to_fcpxml(edl))
    rep = root.find("resources/asset/media-rep")
    assert rep.attrib["kind"] == "original-media"
    assert rep.attrib["src"].endswith("a.mp4")


# ---------------------------------------------------------------------------
# Timeline / spine
# ---------------------------------------------------------------------------

def test_only_keep_segments_land_in_spine():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")],
        segments=[
            _seg("c", 0, 1, "keep"),
            _seg("c", 1, 2, "cut"),
            _seg("c", 2, 3, "keep"),
        ],
    )
    root = _parse(edl_to_fcpxml(edl))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 2


def test_zero_duration_segments_skipped():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")],
        segments=[_seg("c", 0, 0, "keep"), _seg("c", 1, 2, "keep")],
    )
    root = _parse(edl_to_fcpxml(edl))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 1


def test_clips_sorted_chronologically_by_clip_order_then_start():
    edl = EDL(
        clips=[_proxy("a", "/footage/a.mp4"), _proxy("b", "/footage/b.mp4")],
        segments=[
            _seg("b", 1.0, 2.0, "keep"),
            _seg("a", 5.0, 6.0, "keep"),
            _seg("a", 1.0, 2.0, "keep"),
        ],
    )
    root = _parse(edl_to_fcpxml(edl))
    names = [c.attrib["name"] for c in root.findall(
        "library/event/project/sequence/spine/asset-clip"
    )]
    # Clip "a" is first in edl.clips → its segments come before "b".
    assert names[0].startswith("a_") and names[1].startswith("a_")
    assert names[2].startswith("b_")


def test_hook_prepended_before_keeps():
    hook = _seg("c", 20.0, 22.0, "keep")
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")],
        segments=[_seg("c", 0, 1, "keep")],
        hook=hook,
    )
    root = _parse(edl_to_fcpxml(edl))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 2
    # First spine clip is the hook (start=20s).
    assert clips[0].attrib["start"].startswith("600000/")   # 20s * 30000


# ---------------------------------------------------------------------------
# Timecode / rational time
# ---------------------------------------------------------------------------

def test_offsets_accumulate_from_zero():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4", duration=100.0)],
        segments=[
            _seg("c", 5.0, 7.0, "keep"),      # 2s → offsets 0 then 2
            _seg("c", 20.0, 23.0, "keep"),    # 3s → offset 2
        ],
    )
    root = _parse(edl_to_fcpxml(edl))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert clips[0].attrib["offset"] == "0/30000s"
    assert clips[1].attrib["offset"] == f"{round(2 * 30000)}/30000s"


def test_start_matches_segment_start_in_source():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")],
        segments=[_seg("c", 1.5, 2.5, "keep")],
    )
    root = _parse(edl_to_fcpxml(edl))
    clip = root.find("library/event/project/sequence/spine/asset-clip")
    assert clip.attrib["start"] == f"{round(1.5 * 30000)}/30000s"
    assert clip.attrib["duration"] == f"{round(1.0 * 30000)}/30000s"


def test_sequence_duration_sums_kept_segments():
    edl = EDL(
        clips=[_proxy("c", "/footage/a.mp4")],
        segments=[
            _seg("c", 0, 1.5, "keep"),
            _seg("c", 2, 2.5, "cut"),       # excluded
            _seg("c", 3, 5.0, "keep"),
        ],
    )
    root = _parse(edl_to_fcpxml(edl))
    seq = root.find("library/event/project/sequence")
    expected = round(3.5 * 30000)
    assert seq.attrib["duration"] == f"{expected}/30000s"


# ---------------------------------------------------------------------------
# Options + file write
# ---------------------------------------------------------------------------

def test_custom_sequence_and_event_names():
    edl = EDL(clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)])
    root = _parse(edl_to_fcpxml(
        edl, sequence_name="my_cut", event_name="My Project",
    ))
    assert root.find("library/event").attrib["name"] == "My Project"
    assert root.find("library/event/project").attrib["name"] == "my_cut"


def test_save_fcpxml_writes_file(tmp_path):
    edl = EDL(clips=[_proxy("c", "/footage/a.mp4")], segments=[_seg("c", 0, 1)])
    out = tmp_path / "cut.fcpxml"
    save_fcpxml(edl, out)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<?xml")
    assert "<fcpxml" in text


def test_empty_edl_produces_valid_document_with_empty_spine():
    """No kept segments → still a valid FCPXML file (editors tolerate empty spines)."""
    edl = EDL(clips=[], segments=[])
    root = _parse(edl_to_fcpxml(edl))
    spine = root.find("library/event/project/sequence/spine")
    assert spine is not None
    assert list(spine) == []
