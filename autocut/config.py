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


class SceneConfig(BaseModel):
    """Shot-change detection via PySceneDetect ContentDetector."""
    # Higher threshold → fewer detected cuts. 27.0 is the PySceneDetect default
    # and works well for typical phone footage; drop to ~20 for subtle changes.
    threshold: float = 27.0
    # Scenes shorter than this get merged into the previous scene.
    min_scene_len_s: float = 0.4
    # A segment edge within this distance (seconds) of a scene boundary is
    # considered "near a shot change" (feature value 1.0).
    boundary_window_s: float = 0.5


class KeywordsConfig(BaseModel):
    """Transcript keyword boosts for interest scoring."""
    # Greek emotional/reaction phrases typical in "day with me" vlogs.
    exclamations: list[str] = Field(default_factory=lambda: [
        "θεέ μου", "θεε μου",
        "δεν το πιστεύω", "δεν το πιστευω",
        "ωραία", "ωραια",
        "τέλεια", "τελεια",
        "απίστευτο", "απιστευτο",
        "ωπα", "ώπα",
        "ουάου", "ουαου", "wow",
        "αμάν", "αμαν",
        "χαχα", "χαχαχα",
    ])
    # Prices: digits optionally followed by €/ευρώ (and variants). Case-insensitive.
    price_pattern: str = r"\d+(?:[.,]\d+)?\s*(?:€|ευρώ|ευρω|euro)"
    enable_proper_names: bool = True
    # Weights for combining category counts into the keyword_hits feature.
    exclamation_weight: float = 1.0
    price_weight: float = 1.0
    proper_name_weight: float = 0.5


class ScoringConfig(BaseModel):
    # Per-feature contribution weights (need not sum to 1 — normalised internally).
    weights: dict[str, float] = Field(default_factory=lambda: {
        "rms_energy": 0.20,
        "pitch_std": 0.16,
        "voiced_fraction": 0.12,
        "speaking_rate": 0.15,
        "motion_mean": 0.13,
        "keyword_hits": 0.14,
        "scene_boundary_near": 0.10,
    })
    # Values at or above feature_max map to 1.0 after normalisation.
    feature_max: dict[str, float] = Field(default_factory=lambda: {
        "rms_energy": 0.15,
        "pitch_std": 50.0,
        "voiced_fraction": 1.0,
        "speaking_rate": 6.0,
        "motion_mean": 0.20,
        "keyword_hits": 3.0,
        "scene_boundary_near": 1.0,
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
    scenes: SceneConfig = Field(default_factory=SceneConfig)
    keywords: KeywordsConfig = Field(default_factory=KeywordsConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    presets: PresetConfig = Field(default_factory=PresetConfig)


_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "default.yaml"


def load_config(path: Path | None = None) -> AutoCutConfig:
    cfg_path = path or _DEFAULT_CONFIG
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text())
        return AutoCutConfig.model_validate(raw or {})
    return AutoCutConfig()
