from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autocut.edl import edl_from_dict, edl_to_dict, load_edl, save_edl
from autocut.models import EDL, ProxyInfo, Segment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_proxy(clip_id: str = "clip", has_audio: bool = True) -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(f"/raw/{clip_id}.mp4"),
        proxy_path=Path(f"/proxy/{clip_id}_proxy.mp4"),
        audio_path=Path(f"/audio/{clip_id}.wav") if has_audio else None,
        duration=60.0,
        fps=30.0,
        width=1080,
        height=1920,
        creation_time=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def make_segment(clip_id: str = "clip", decision: str = "keep") -> Segment:
    return Segment(
        clip_id=clip_id,
        start=0.5,
        end=2.5,
        features={"speech_prob": 0.9},
        interest_score=0.75,
        decision=decision,
        decision_source="auto",
        reasons=["silence 1.20s"] if decision == "cut" else [],
    )


def make_edl() -> EDL:
    return EDL(
        clips=[make_proxy("a"), make_proxy("b", has_audio=False)],
        segments=[
            make_segment("a", "keep"),
            make_segment("a", "cut"),
            make_segment("b", "keep"),
        ],
        created_at=datetime(2024, 6, 1, 10, 0, 0),
    )


# ---------------------------------------------------------------------------
# edl_to_dict
# ---------------------------------------------------------------------------

def test_to_dict_top_level_keys():
    d = edl_to_dict(make_edl())
    assert set(d.keys()) >= {"clips", "segments", "created_at", "version"}


def test_to_dict_paths_are_strings():
    d = edl_to_dict(make_edl())
    for clip in d["clips"]:
        assert isinstance(clip["proxy_path"], str)
        assert isinstance(clip["original_path"], str)


def test_to_dict_optional_path_none_is_null():
    d = edl_to_dict(make_edl())
    no_audio = next(c for c in d["clips"] if c["clip_id"] == "b")
    assert no_audio["audio_path"] is None


def test_to_dict_datetime_is_isoformat():
    d = edl_to_dict(make_edl())
    assert isinstance(d["created_at"], str)
    # Round-trip check
    datetime.fromisoformat(d["created_at"])


def test_to_dict_segment_fields():
    d = edl_to_dict(make_edl())
    seg = d["segments"][0]
    assert seg["decision"] in ("keep", "cut")
    assert isinstance(seg["features"], dict)
    assert isinstance(seg["reasons"], list)


# ---------------------------------------------------------------------------
# round-trip: edl_to_dict → edl_from_dict
# ---------------------------------------------------------------------------

def test_roundtrip_clip_count():
    original = make_edl()
    restored = edl_from_dict(edl_to_dict(original))
    assert len(restored.clips) == len(original.clips)


def test_roundtrip_segment_decisions():
    original = make_edl()
    restored = edl_from_dict(edl_to_dict(original))
    orig_decs = [s.decision for s in original.segments]
    rest_decs = [s.decision for s in restored.segments]
    assert orig_decs == rest_decs


def test_roundtrip_proxy_path():
    original = make_edl()
    restored = edl_from_dict(edl_to_dict(original))
    assert restored.clips[0].proxy_path == original.clips[0].proxy_path


def test_roundtrip_optional_audio_path_none():
    original = make_edl()
    restored = edl_from_dict(edl_to_dict(original))
    no_audio = next(c for c in restored.clips if c.clip_id == "b")
    assert no_audio.audio_path is None


def test_roundtrip_creation_time():
    original = make_edl()
    restored = edl_from_dict(edl_to_dict(original))
    assert restored.clips[0].creation_time == original.clips[0].creation_time


def test_roundtrip_features():
    original = make_edl()
    restored = edl_from_dict(edl_to_dict(original))
    assert restored.segments[0].features == original.segments[0].features


# ---------------------------------------------------------------------------
# save_edl / load_edl (file I/O round-trip)
# ---------------------------------------------------------------------------

def test_save_produces_valid_json(tmp_path):
    path = tmp_path / "edl.json"
    save_edl(make_edl(), path)
    data = json.loads(path.read_text())
    assert "clips" in data
    assert "segments" in data


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "edl.json"
    original = make_edl()
    save_edl(original, path)
    restored = load_edl(path)
    assert len(restored.clips) == len(original.clips)
    assert len(restored.segments) == len(original.segments)
    assert restored.version == original.version


def test_save_uses_utf8(tmp_path):
    """Greek text in reasons must survive the round-trip."""
    edl = make_edl()
    edl.segments[0].reasons = ["filler: εεε"]
    path = tmp_path / "edl.json"
    save_edl(edl, path)
    restored = load_edl(path)
    assert restored.segments[0].reasons == ["filler: εεε"]
