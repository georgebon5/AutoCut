from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class IngestConfig(BaseModel):
    target_fps: int = 30
    audio_sample_rate: int = 16000


class VADConfig(BaseModel):
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100


class SilenceConfig(BaseModel):
    min_silence: float = 0.5
    padding: float = 0.1


class RenderConfig(BaseModel):
    audio_crossfade_ms: int = 10
    crf: int = 18
    preset: str = "fast"


class TranscribeConfig(BaseModel):
    model_size: str = "medium"
    language: str = "el"
    compute_type: str = "int8"
    beam_size: int = 5


class FillerConfig(BaseModel):
    words: list[str] = Field(default_factory=lambda: [
        "εε", "εεε", "εμ", "εμμ", "αα", "ααα", "μμ", "μμμ",
        "λοιπόν", "δηλαδή",
    ])
    min_isolation_ms: int = 200   # minimum silence gap on each side (ms)
    padding_ms: int = 50          # extra context cut on each side (ms)


class TakesConfig(BaseModel):
    similarity_threshold: float = 0.85  # rapidfuzz ratio [0, 1]
    min_gap_s: float = 0.5              # pause that separates two takes (seconds)
    min_words: int = 3                  # minimum words per take to qualify


class CaptionConfig(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["srt", "ass"])
    max_words: int = 5           # maximum words per caption group
    max_duration_s: float = 3.0  # maximum caption display duration (seconds)
    min_gap_s: float = 0.4       # silence gap that forces a new caption group
    style: str = "tiktok"        # ASS style preset ("tiktok" or "default")


class ScoringConfig(BaseModel):
    # Per-feature contribution weights (need not sum to 1 — normalised internally).
    weights: dict[str, float] = Field(default_factory=lambda: {
        "rms_energy": 0.25,
        "pitch_std": 0.20,
        "voiced_fraction": 0.15,
        "speaking_rate": 0.20,
        "motion_mean": 0.20,
    })
    # Values at or above feature_max map to 1.0 after normalisation.
    feature_max: dict[str, float] = Field(default_factory=lambda: {
        "rms_energy": 0.15,
        "pitch_std": 50.0,
        "voiced_fraction": 1.0,
        "speaking_rate": 6.0,
        "motion_mean": 0.20,
    })


class PresetConfig(BaseModel):
    """Target-duration presets for score-driven segment selection."""
    targets: dict[str, float] = Field(default_factory=lambda: {
        "tight": 45.0,
        "medium": 90.0,
        "loose": 180.0,
    })
    # Fraction above target allowed when the next-best segment would overshoot
    # (e.g. tolerance=0.10 → medium preset can go up to 99s instead of 90s
    # if dropping that segment would leave us well below target).
    tolerance: float = 0.10


class AutoCutConfig(BaseModel):
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    silence: SilenceConfig = Field(default_factory=SilenceConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    transcribe: TranscribeConfig = Field(default_factory=TranscribeConfig)
    fillers: FillerConfig = Field(default_factory=FillerConfig)
    takes: TakesConfig = Field(default_factory=TakesConfig)
    captions: CaptionConfig = Field(default_factory=CaptionConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    presets: PresetConfig = Field(default_factory=PresetConfig)


_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "default.yaml"


def load_config(path: Path | None = None) -> AutoCutConfig:
    cfg_path = path or _DEFAULT_CONFIG
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text())
        return AutoCutConfig.model_validate(raw or {})
    return AutoCutConfig()
