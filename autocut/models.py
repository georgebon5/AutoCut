from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional


@dataclass
class ProxyInfo:
    clip_id: str
    original_path: Path
    proxy_path: Path            # CFR H.264 proxy
    audio_path: Optional[Path]  # 16 kHz mono WAV; None if clip has no audio stream
    duration: float         # seconds
    fps: float
    width: int
    height: int
    creation_time: Optional[datetime] = None


@dataclass
class SpeechRegion:
    clip_id: str
    start: float            # seconds
    end: float
    speech_prob: float


@dataclass
class Segment:
    clip_id: str
    start: float            # seconds, relative to proxy clip
    end: float
    features: dict[str, Any] = field(default_factory=dict)
    interest_score: float = 0.0
    decision: Literal["keep", "cut"] = "keep"
    decision_source: Literal["auto", "user"] = "auto"
    reasons: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class EDL:
    clips: list[ProxyInfo]
    segments: list[Segment]
    created_at: datetime = field(default_factory=datetime.utcnow)
    version: str = "1.0"
