"""EDL: serialize/deserialize the decision list as JSON."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from autocut.models import EDL, ProxyInfo, Segment, Transcript, WordTimestamp


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _jsonify(val: Any) -> Any:
    """Recursively convert Path/datetime so json.dumps accepts the tree."""
    if isinstance(val, Path):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, list):
        return [_jsonify(v) for v in val]
    if isinstance(val, dict):
        return {k: _jsonify(v) for k, v in val.items()}
    return val


def edl_to_dict(edl: EDL) -> dict:
    return _jsonify(dataclasses.asdict(edl))


def save_edl(edl: EDL, path: Path) -> None:
    path.write_text(
        json.dumps(edl_to_dict(edl), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------

def _proxy_from_dict(d: dict) -> ProxyInfo:
    return ProxyInfo(
        clip_id=d["clip_id"],
        original_path=Path(d["original_path"]),
        proxy_path=Path(d["proxy_path"]),
        audio_path=Path(d["audio_path"]) if d.get("audio_path") else None,
        duration=d["duration"],
        fps=d["fps"],
        width=d["width"],
        height=d["height"],
        creation_time=(
            datetime.fromisoformat(d["creation_time"])
            if d.get("creation_time") else None
        ),
    )


def _segment_from_dict(d: dict) -> Segment:
    return Segment(
        clip_id=d["clip_id"],
        start=d["start"],
        end=d["end"],
        features=d.get("features", {}),
        interest_score=d.get("interest_score", 0.0),
        decision=d["decision"],
        decision_source=d.get("decision_source", "auto"),
        reasons=d.get("reasons", []),
    )


def _word_from_dict(d: dict) -> WordTimestamp:
    return WordTimestamp(
        word=d["word"],
        start=d["start"],
        end=d["end"],
        probability=d["probability"],
        clip_id=d["clip_id"],
    )


def _transcript_from_dict(d: dict) -> Transcript:
    return Transcript(
        clip_id=d["clip_id"],
        language=d["language"],
        words=[_word_from_dict(w) for w in d.get("words", [])],
    )


def edl_from_dict(data: dict) -> EDL:
    return EDL(
        clips=[_proxy_from_dict(c) for c in data["clips"]],
        segments=[_segment_from_dict(s) for s in data["segments"]],
        transcripts=[_transcript_from_dict(t) for t in data.get("transcripts", [])],
        created_at=datetime.fromisoformat(data["created_at"]),
        version=data.get("version", "1.0"),
    )


def load_edl(path: Path) -> EDL:
    return edl_from_dict(json.loads(path.read_text(encoding="utf-8")))
