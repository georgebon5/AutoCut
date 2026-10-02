"""CLI tests including the Phase 1 end-to-end pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
from click.testing import CliRunner

from autocut.cli import cli
from autocut.models import SpeechRegion, Transcript
from tests.conftest import make_clip, skip_no_ffmpeg


# ---------------------------------------------------------------------------
# Minimal fakes for deterministic E2E tests (no model downloads)
# ---------------------------------------------------------------------------

class _FakeModel:
    def reset_states(self) -> None: ...
    def __call__(self, chunk: torch.Tensor, sr: int) -> torch.Tensor:
        return torch.tensor(0.9)


def _fake_get_ts(wav: torch.Tensor, model, sampling_rate: int = 16000, **kw):
    """Return two speech regions with a 1-second gap in the middle."""
    dur = len(wav) / sampling_rate
    mid = dur / 2.0
    return [
        {"start": 0.1, "end": mid - 0.5},
        {"start": mid + 0.5, "end": dur - 0.1},
    ]


def _fake_load_vad():
    return _FakeModel(), _fake_get_ts


class _FakeWhisperModel:
    pass


def _fake_load_whisper(cfg):
    return _FakeWhisperModel()


def _fake_transcribe(audio_path, speech_regions, clip_id, cfg, model=None):
    return Transcript(clip_id=clip_id, language="el", words=[])


def _patch_pipeline(monkeypatch):
    monkeypatch.setattr("autocut.cli.load_vad_model", _fake_load_vad)
    monkeypatch.setattr("autocut.cli.load_whisper_model", _fake_load_whisper)
    monkeypatch.setattr("autocut.cli.transcribe_clip", _fake_transcribe)


# ---------------------------------------------------------------------------
# Unit-level CLI tests (monkeypatched VAD + Whisper, no model downloads)
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_ingest_command_exits_zero(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output


@skip_no_ffmpeg
def test_ingest_produces_rough_cut_mp4(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])
    assert (tmp_path / "out" / "rough_cut.mp4").exists()


@skip_no_ffmpeg
def test_ingest_produces_edl_json(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])
    edl_path = tmp_path / "out" / "edl.json"
    assert edl_path.exists()
    data = json.loads(edl_path.read_text())
    assert "clips" in data and "segments" in data


@skip_no_ffmpeg
def test_ingest_edl_has_cut_segments(tmp_path, monkeypatch):
    """The 1-second silence gap from _fake_get_ts must appear as a cut segment."""
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])
    data = json.loads((tmp_path / "out" / "edl.json").read_text())
    cuts = [s for s in data["segments"] if s["decision"] == "cut"]
    assert len(cuts) >= 1


@skip_no_ffmpeg
def test_ingest_no_cut_overlaps_speech_region(tmp_path, monkeypatch):
    """No cut segment should overlap a detected speech region."""
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])
    data = json.loads((tmp_path / "out" / "edl.json").read_text())

    # Reconstruct expected speech regions from _fake_get_ts with 6s clip
    mid = 3.0
    speech_intervals = [(0.1, mid - 0.5), (mid + 0.5, 5.9)]

    for seg in data["segments"]:
        if seg["decision"] != "cut":
            continue
        cs, ce = seg["start"], seg["end"]
        for ss, se in speech_intervals:
            overlap = min(ce, se) - max(cs, ss)
            assert overlap <= 0.0, (
                f"Cut [{cs:.3f},{ce:.3f}] overlaps speech [{ss:.3f},{se:.3f}]"
            )


@skip_no_ffmpeg
def test_ingest_output_reports_runtime(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])
    assert "Done in" in result.output


@skip_no_ffmpeg
def test_ingest_multi_clip(tmp_path, monkeypatch):
    _patch_pipeline(monkeypatch)
    clip_a = make_clip(tmp_path / "a.mp4", duration=4.0)
    clip_b = make_clip(tmp_path / "b.mp4", duration=4.0)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ingest", str(clip_a), str(clip_b), "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "rough_cut.mp4").exists()


@skip_no_ffmpeg
def test_ingest_silent_clip_treated_as_broll(tmp_path, monkeypatch):
    """A clip with no audio should succeed (B-roll path)."""
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "broll.mp4", duration=4.0, with_audio=False)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ingest", str(clip), "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code == 0, result.output
    assert "B-roll" in result.output


@skip_no_ffmpeg
def test_ingest_no_av_drift(tmp_path, monkeypatch):
    """Audio and video stream durations in the output must be within 200 ms."""
    import subprocess
    _patch_pipeline(monkeypatch)
    clip = make_clip(tmp_path / "clip.mp4", duration=8.0)
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(clip), "--output-dir", str(tmp_path / "out")])

    out = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", str(tmp_path / "out" / "rough_cut.mp4"),
    ])
    streams = json.loads(out)["streams"]
    video_dur = next(
        float(s["duration"]) for s in streams if s["codec_type"] == "video"
    )
    audio_dur = next(
        float(s["duration"]) for s in streams if s["codec_type"] == "audio"
    )
    assert abs(video_dur - audio_dur) < 0.2, (
        f"A/V drift: video={video_dur:.3f}s, audio={audio_dur:.3f}s"
    )


@skip_no_ffmpeg
def test_ingest_no_transcribe_flag(tmp_path, monkeypatch):
    """--no-transcribe must skip Whisper and still produce output."""
    monkeypatch.setattr("autocut.cli.load_vad_model", _fake_load_vad)
    clip = make_clip(tmp_path / "clip.mp4", duration=6.0)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ingest", str(clip), "--no-transcribe", "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "rough_cut.mp4").exists()
    assert "Transcrib" not in result.output


# ---------------------------------------------------------------------------
# Slow / real-Silero E2E (optional; run with pytest -m slow)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@skip_no_ffmpeg
def test_e2e_with_real_silero(tmp_path):
    """Full pipeline with real Silero VAD + Whisper (requires model downloads)."""
    clip = make_clip(tmp_path / "clip.mp4", duration=10.0)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ingest", str(clip), "--output-dir", str(tmp_path / "out")],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "rough_cut.mp4").exists()
    assert "Done in" in result.output
