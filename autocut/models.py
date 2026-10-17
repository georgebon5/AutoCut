from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
class WordTimestamp:
    word: str
    start: float       # seconds from clip start
    end: float
    probability: float
    clip_id: str


@dataclass
class Transcript:
    clip_id: str
    language: str
    words: list[WordTimestamp]

    @property
    def text(self) -> str:
        return " ".join(w.word for w in self.words)

    def words_in_range(self, start: float, end: float) -> list[WordTimestamp]:
        """Words whose start falls within [start, end]."""
        return [w for w in self.words if start <= w.start <= end]

    def to_snap_format(self) -> list[dict]:
        """Convert to the list[dict] format expected by _snap_to_word_boundary."""
        return [{"word": w.word, "start": w.start, "end": w.end} for w in self.words]


@dataclass
class RenderChunk:
    """Maps one rendered segment back to its position in the output mp4.

    ``segment_index`` is the index into ``EDL.segments`` of the source
    segment, or ``-1`` for the opening hook (which lives on ``EDL.hook`` and
    isn't in ``segments``). The frontend uses these to drive instant preview
    skipping: toggle a segment to "cut" and the player can seek past the
    corresponding ``[output_start, output_end]`` without a re-render.
    """
    segment_index: int
    output_start: float    # seconds from the start of the rendered mp4
    output_end: float


@dataclass
class EDL:
    clips: list[ProxyInfo]
    segments: list[Segment]
    transcripts: list[Transcript] = field(default_factory=list)
    hook: Optional[Segment] = None
    render_chunks: list[RenderChunk] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "1.0"
