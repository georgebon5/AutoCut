"""Tests for autocut.pipeline — reporter protocol + end-to-end run.

The heavy models (Silero, Whisper, librosa) are stubbed so these run in
seconds without model downloads. The pipeline still exercises ingest,
silence removal, scoring, selection, and render against synthetic clips.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from autocut.config import load_config
from autocut.models import Transcript
from autocut.pipeline import (
    NullReporter,
    PipelineError,
    PipelineOptions,
    PipelineReporter,
    run_pipeline,
)
from tests.conftest import make_clip, skip_no_ffmpeg


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeVadModel:
    def reset_states(self) -> None: ...
    def __call__(self, chunk: torch.Tensor, sr: int) -> torch.Tensor:
        return torch.tensor(0.9)


def _fake_get_ts(wav, model, sampling_rate=16000, **_kw):
    dur = len(wav) / sampling_rate
    mid = dur / 2.0
    return [
        {"start": 0.1, "end": mid - 0.5},
        {"start": mid + 0.5, "end": dur - 0.1},
    ]


def _fake_load_vad():
    return _FakeVadModel(), _fake_get_ts


class _FakeWhisperModel:
    pass


def _fake_load_whisper(cfg):
    return _FakeWhisperModel()


def _fake_transcribe(audio_path, speech_regions, clip_id, cfg, model=None):
    return Transcript(clip_id=clip_id, language="el", words=[])


def _fake_enrich(segments, audio_path, transcript=None, sr=16000):
    pass


def _fake_enrich_motion(segments, proxy_path, sample_fps=5.0):
    pass


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("autocut.pipeline.load_vad_model", _fake_load_vad)
    monkeypatch.setattr("autocut.pipeline.load_whisper_model", _fake_load_whisper)
    monkeypatch.setattr("autocut.pipeline.transcribe_clip", _fake_transcribe)
    monkeypatch.setattr("autocut.pipeline.enrich_segments", _fake_enrich)
    monkeypatch.setattr("autocut.pipeline.enrich_segments_motion", _fake_enrich_motion)


# ---------------------------------------------------------------------------
# Reporter helpers
# ---------------------------------------------------------------------------

class _Capture:
    """Recording reporter — stores stage + info events for assertions."""
    def __init__(self) -> None:
        self.stages: list[tuple[str, float]] = []
        self.infos: list[str] = []

    def stage(self, name: str, progress: float) -> None:
        self.stages.append((name, progress))

    def info(self, message: str) -> None:
        self.infos.append(message)


# ---------------------------------------------------------------------------
# Option validation (no I/O)
# ---------------------------------------------------------------------------

def test_options_defaults():
    o = PipelineOptions()
    assert o.transcribe and o.features and o.captions
    assert o.preset == "none" and not o.hook


def test_null_reporter_is_noop():
    r = NullReporter()
    r.stage("x", 0.5)
    r.info("hi")   # no crash → good


def test_run_pipeline_rejects_unknown_preset(tmp_path):
    with pytest.raises(PipelineError, match="unknown preset"):
        run_pipeline([tmp_path / "x.mp4"], tmp_path / "out", load_config(),
                     PipelineOptions(preset="ultra"))


def test_run_pipeline_rejects_preset_without_features(tmp_path):
    with pytest.raises(PipelineError, match="features"):
        run_pipeline([tmp_path / "x.mp4"], tmp_path / "out", load_config(),
                     PipelineOptions(preset="tight", features=False))


def test_run_pipeline_rejects_hook_without_features(tmp_path):
    with pytest.raises(PipelineError, match="features"):
        run_pipeline([tmp_path / "x.mp4"], tmp_path / "out", load_config(),
                     PipelineOptions(hook=True, features=False))


def test_run_pipeline_rejects_pacing_without_preset(tmp_path):
    with pytest.raises(PipelineError, match="pacing"):
        run_pipeline([tmp_path / "x.mp4"], tmp_path / "out", load_config(),
                     PipelineOptions(pacing=True, preset="none"))


def test_run_pipeline_rejects_zoom_without_features(tmp_path):
    with pytest.raises(PipelineError, match="features"):
        run_pipeline([tmp_path / "x.mp4"], tmp_path / "out", load_config(),
                     PipelineOptions(zoom=True, features=False))


# ---------------------------------------------------------------------------
# End-to-end with fake models (needs ffmpeg)
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_run_pipeline_produces_outputs(tmp_path: Path, patched):
    clip = make_clip(tmp_path / "a.mp4", duration=6.0)
    result = run_pipeline(
        [clip], tmp_path / "out", load_config(),
        PipelineOptions(features=False, captions=False),  # keep it fast
    )
    assert result.outputs, "no rendered output"
    assert result.outputs[0].exists()
    assert result.outputs[0].name == "rough_cut.mp4"
    assert result.edls and result.edls[0].name == "edl.json"
    assert result.elapsed_s > 0


@skip_no_ffmpeg
def test_run_pipeline_reporter_sequence(tmp_path: Path, patched):
    clip = make_clip(tmp_path / "a.mp4", duration=6.0)
    cap = _Capture()
    run_pipeline(
        [clip], tmp_path / "out", load_config(),
        PipelineOptions(features=False, captions=False),
        reporter=cap,
    )
    stage_names = [s[0] for s in cap.stages]
    assert "ingesting" in stage_names
    assert "vad" in stage_names
    assert "rendering" in stage_names
    # Progress values must be monotonically non-decreasing.
    progresses = [s[1] for s in cap.stages]
    assert progresses == sorted(progresses)


@skip_no_ffmpeg
def test_run_pipeline_skips_transcribe_stage_when_disabled(tmp_path: Path, patched):
    clip = make_clip(tmp_path / "a.mp4", duration=6.0)
    cap = _Capture()
    run_pipeline(
        [clip], tmp_path / "out", load_config(),
        PipelineOptions(transcribe=False, features=False, captions=False),
        reporter=cap,
    )
    stage_names = [s[0] for s in cap.stages]
    assert "transcribing" not in stage_names


@skip_no_ffmpeg
def test_run_pipeline_broll_clip_info_message(tmp_path: Path, patched):
    clip = make_clip(tmp_path / "broll.mp4", duration=5.0, with_audio=False)
    cap = _Capture()
    run_pipeline(
        [clip], tmp_path / "out", load_config(),
        PipelineOptions(features=False, captions=False),
        reporter=cap,
    )
    assert any("B-roll" in msg for msg in cap.infos)
