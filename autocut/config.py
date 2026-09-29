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


class AutoCutConfig(BaseModel):
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    silence: SilenceConfig = Field(default_factory=SilenceConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)


_DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "default.yaml"


def load_config(path: Path | None = None) -> AutoCutConfig:
    cfg_path = path or _DEFAULT_CONFIG
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text())
        return AutoCutConfig.model_validate(raw or {})
    return AutoCutConfig()
