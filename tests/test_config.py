from pathlib import Path
import tempfile

import pytest
import yaml

from autocut.config import AutoCutConfig, load_config


def test_load_defaults():
    cfg = load_config()
    assert cfg.ingest.target_fps == 30
    assert cfg.ingest.audio_sample_rate == 16000
    assert cfg.vad.threshold == 0.5
    assert cfg.silence.min_silence == 0.5
    assert cfg.silence.padding == 0.1
    assert cfg.render.audio_crossfade_ms == 10


def test_load_from_file():
    data = {
        "ingest": {"target_fps": 60},
        "silence": {"min_silence": 1.0, "padding": 0.2},
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        path = Path(f.name)

    cfg = load_config(path)
    assert cfg.ingest.target_fps == 60
    assert cfg.silence.min_silence == 1.0
    assert cfg.silence.padding == 0.2
    # unset keys fall back to defaults
    assert cfg.vad.threshold == 0.5


def test_missing_config_file_returns_defaults():
    cfg = load_config(Path("/nonexistent/path.yaml"))
    assert isinstance(cfg, AutoCutConfig)
    assert cfg.render.crf == 18
