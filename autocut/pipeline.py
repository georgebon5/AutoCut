"""End-to-end pipeline: ingest → VAD → transcribe → score → render.

This is the shared implementation used by both the CLI and the HTTP API.
It takes a progress reporter so each caller can plug its own output sink
(stdout for CLI, Job updates for API, nothing for tests).

The functions imported at module level (``load_vad_model``, ``transcribe_clip``,
``enrich_segments``, ``enrich_segments_motion``) are monkey-patched in tests
to avoid downloading models — do not rebind them inside ``run_pipeline`` or
the fakes will be bypassed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from autocut.captions import write_captions
from autocut.config import AutoCutConfig
from autocut.edl import EDL, save_edl
from autocut.features import enrich_segments
from autocut.fillers import detect_fillers, punch_out_fillers
from autocut.hook import find_hook_segment
from autocut.ingest import IngestError, ingest_clips
from autocut.keywords import enrich_segments_keywords
from autocut.models import ProxyInfo, Segment, SpeechRegion, Transcript
from autocut.motion import enrich_segments_motion
from autocut.render import RenderError, render
from autocut.scenes import detect_scene_boundaries, enrich_segments_scenes
from autocut.scoring import score_segments
from autocut.select import select_segments
from autocut.silence import apply_silence_removal
from autocut.takes import detect_repeated_takes
from autocut.transcribe import load_whisper_model, transcribe_clip
from autocut.vad import detect_speech, load_vad_model


class PipelineError(Exception):
    pass


@dataclass
class PipelineOptions:
    transcribe: bool = True
    fillers: bool = True
    takes: bool = True
    captions: bool = True
    features: bool = True
    hook: bool = False
    pacing: bool = False
    zoom: bool = False
    # "none" → full silence-removed cut only; "all" → tight+medium+loose.
    preset: str = "none"


@dataclass
class PipelineResult:
    proxies: list[ProxyInfo]
    all_segments: list[Segment]
    transcripts: list[Transcript]
    hook_segment: Segment | None
    outputs: list[Path] = field(default_factory=list)      # rendered mp4 paths
    edls: list[Path] = field(default_factory=list)
    captions: list[Path] = field(default_factory=list)
    elapsed_s: float = 0.0


class PipelineReporter(Protocol):
    """Dual-channel progress feed used by run_pipeline()."""
    def stage(self, name: str, progress: float) -> None: ...
    def info(self, message: str) -> None: ...


class NullReporter:
    """Default reporter used when the caller does not supply one."""
    def stage(self, name: str, progress: float) -> None:
        pass
    def info(self, message: str) -> None:
        pass


# Progress milestones per stage name. Not every run hits every stage; the
# reporter simply sees the ones that fire. Whisper dominates runtime so it
# gets the biggest jump.
_STAGE_PROGRESS = {
    "ingesting": 0.05,
    "vad": 0.15,
    "transcribing": 0.50,
    "fillers": 0.55,
    "takes": 0.58,
    "silence_removal": 0.62,
    "features": 0.80,
    "hook": 0.82,
    "rendering": 0.95,
    "captions": 1.0,
}


def _validate(options: PipelineOptions) -> None:
    if options.preset not in ("none", "tight", "medium", "loose", "all"):
        raise PipelineError(f"unknown preset '{options.preset}'")
    if options.preset != "none" and not options.features:
        raise PipelineError("preset selection requires features=True")
    if options.hook and not options.features:
        raise PipelineError("hook requires features=True")
    if options.pacing and options.preset == "none":
        raise PipelineError("pacing only applies with a preset (not 'none')")
    if options.zoom and not options.features:
        raise PipelineError("zoom requires features=True")


def run_pipeline(
    clips: list[Path],
    output_dir: Path,
    cfg: AutoCutConfig,
    options: PipelineOptions,
    reporter: PipelineReporter | None = None,
) -> PipelineResult:
    """Execute the full pipeline against ``clips`` and write to ``output_dir``.

    Raises PipelineError (wrapping IngestError / RenderError) on failure.
    """
    rep = reporter or NullReporter()
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    _validate(options)
    if options.pacing:
        cfg.pacing.enabled = True
    if options.zoom:
        cfg.zoom.enabled = True

    do_transcribe = options.transcribe
    do_fillers = do_transcribe and options.fillers
    do_takes = do_transcribe and options.takes
    do_captions = do_transcribe and options.captions
    do_features = options.features

    # ── 1. Ingest ────────────────────────────────────────────────────────────
    rep.stage("ingesting", _STAGE_PROGRESS["ingesting"])
    try:
        proxies = ingest_clips(clips, output_dir, cfg.ingest)
    except IngestError as e:
        raise PipelineError(f"ingest failed: {e}") from e
    for p in proxies:
        rep.info(f"{p.clip_id}  {p.duration:.1f}s  {p.width}×{p.height}  {p.fps:.2f}fps")

    # ── 2. VAD ───────────────────────────────────────────────────────────────
    rep.stage("vad", _STAGE_PROGRESS["vad"])
    vad_model, get_ts = load_vad_model()
    all_regions: dict[str, list[SpeechRegion]] = {}
    for proxy in proxies:
        if proxy.audio_path is None:
            all_regions[proxy.clip_id] = []
            rep.info(f"{proxy.clip_id}: no audio — treated as B-roll")
            continue
        regions = detect_speech(
            proxy.audio_path, proxy.clip_id, cfg.vad,
            model=vad_model, get_timestamps=get_ts,
        )
        all_regions[proxy.clip_id] = regions
        speech_s = sum(r.end - r.start for r in regions)
        rep.info(f"{proxy.clip_id}: {len(regions)} speech region(s)  ({speech_s:.1f}s speech)")

    # ── 3. Transcribe (optional) ─────────────────────────────────────────────
    all_transcripts: dict[str, Transcript] = {}
    if do_transcribe:
        rep.stage("transcribing", _STAGE_PROGRESS["transcribing"])
        whisper = load_whisper_model(cfg.transcribe)
        for proxy in proxies:
            if proxy.audio_path is None:
                all_transcripts[proxy.clip_id] = Transcript(
                    clip_id=proxy.clip_id, language=cfg.transcribe.language, words=[],
                )
                continue
            t = transcribe_clip(
                proxy.audio_path, all_regions[proxy.clip_id], proxy.clip_id,
                cfg.transcribe, model=whisper,
            )
            all_transcripts[proxy.clip_id] = t
            rep.info(f"{proxy.clip_id}: {len(t.words)} words [{t.language}]")

    # ── 4. Fillers (optional) ────────────────────────────────────────────────
    all_filler_cuts: dict[str, list[Segment]] = {p.clip_id: [] for p in proxies}
    if do_fillers:
        rep.stage("fillers", _STAGE_PROGRESS["fillers"])
        for proxy in proxies:
            transcript = all_transcripts.get(proxy.clip_id)
            if transcript is None:
                continue
            cuts = detect_fillers(transcript, cfg.fillers)
            all_filler_cuts[proxy.clip_id] = cuts
            rep.info(f"{proxy.clip_id}: {len(cuts)} filler(s)")

    # ── 5. Repeated takes (optional) ─────────────────────────────────────────
    all_take_cuts: dict[str, list[Segment]] = {p.clip_id: [] for p in proxies}
    if do_takes:
        rep.stage("takes", _STAGE_PROGRESS["takes"])
        for proxy in proxies:
            transcript = all_transcripts.get(proxy.clip_id)
            if transcript is None:
                continue
            cuts = detect_repeated_takes(transcript, cfg.takes)
            all_take_cuts[proxy.clip_id] = cuts
            rep.info(f"{proxy.clip_id}: {len(cuts)} repeated take(s)")

    # ── 6. Silence removal ───────────────────────────────────────────────────
    rep.stage("silence_removal", _STAGE_PROGRESS["silence_removal"])
    all_segments: list[Segment] = []
    for proxy in proxies:
        word_ts = (
            all_transcripts[proxy.clip_id].to_snap_format()
            if proxy.clip_id in all_transcripts else []
        )
        segs = apply_silence_removal(
            all_regions[proxy.clip_id], proxy.clip_id, proxy.duration,
            cfg.silence, word_timestamps=word_ts or None,
        )
        extra_cuts = all_filler_cuts[proxy.clip_id] + all_take_cuts[proxy.clip_id]
        segs = punch_out_fillers(segs, extra_cuts)
        all_segments.extend(segs)
        n_cut = sum(1 for s in segs if s.decision == "cut")
        removed = sum(s.duration for s in segs if s.decision == "cut")
        rep.info(f"{proxy.clip_id}: {n_cut} cut(s), {removed:.1f}s removed")

    # ── 7. Features + scoring (optional) ─────────────────────────────────────
    if do_features:
        rep.stage("features", _STAGE_PROGRESS["features"])
        for proxy in proxies:
            segs = [s for s in all_segments if s.clip_id == proxy.clip_id]
            transcript = all_transcripts.get(proxy.clip_id)
            enrich_segments(segs, proxy.audio_path, transcript)
            enrich_segments_motion(segs, proxy.proxy_path)
            enrich_segments_keywords(segs, transcript, cfg.keywords)
            boundaries = detect_scene_boundaries(proxy.proxy_path, cfg.scenes)
            enrich_segments_scenes(segs, {proxy.clip_id: boundaries}, cfg.scenes)
            score_segments(segs, cfg.scoring)
            kept = [s for s in segs if s.decision == "keep"]
            avg = (sum(s.interest_score for s in kept) / len(kept)) if kept else 0.0
            rep.info(f"{proxy.clip_id}: {len(kept)} keep segment(s)  avg score {avg:.2f}")

    # ── 8. Hook (optional) ───────────────────────────────────────────────────
    hook_seg: Segment | None = None
    if options.hook:
        rep.stage("hook", _STAGE_PROGRESS["hook"])
        hook_seg = find_hook_segment(all_segments, cfg.hook)
        if hook_seg is None:
            rep.info("no eligible hook segment")
        else:
            rep.info(
                f"hook: {hook_seg.clip_id} @ "
                f"{hook_seg.start:.2f}-{hook_seg.end:.2f}s "
                f"(score {hook_seg.interest_score:.2f})"
            )

    # ── 9. Render + EDL (per preset) ─────────────────────────────────────────
    rep.stage("rendering", _STAGE_PROGRESS["rendering"])
    outputs: list[Path] = []
    edls: list[Path] = []
    if options.preset == "all":
        presets_to_render = list(cfg.presets.targets.keys())
    elif options.preset == "none":
        presets_to_render = []
    else:
        presets_to_render = [options.preset]

    def _render_pass(segs: list[Segment], video_name: str, edl_name: str) -> None:
        try:
            out_mp4 = render(
                proxies, segs, output_dir, cfg.render,
                output_name=video_name, hook=hook_seg, zoom=cfg.zoom,
            )
        except RenderError as e:
            raise PipelineError(f"render failed: {e}") from e
        outputs.append(out_mp4)
        rep.info(f"wrote {out_mp4.name}")

        edl = EDL(
            clips=proxies, segments=segs,
            transcripts=list(all_transcripts.values()), hook=hook_seg,
        )
        edl_path = output_dir / edl_name
        save_edl(edl, edl_path)
        edls.append(edl_path)
        rep.info(f"wrote {edl_path.name}")

    if not presets_to_render:
        _render_pass(all_segments, "rough_cut.mp4", "edl.json")
    else:
        for p in presets_to_render:
            segs = select_segments(all_segments, p, cfg.presets, pacing=cfg.pacing)
            kept = sum(s.duration for s in segs if s.decision == "keep")
            target = cfg.presets.targets[p]
            rep.info(f"preset={p}: target {target:.0f}s, selected {kept:.0f}s")
            _render_pass(segs, f"rough_cut_{p}.mp4", f"edl_{p}.json")

    # ── 10. Captions (optional) ──────────────────────────────────────────────
    caption_paths: list[Path] = []
    if do_captions:
        rep.stage("captions", _STAGE_PROGRESS["captions"])
        for proxy in proxies:
            transcript = all_transcripts.get(proxy.clip_id)
            if transcript is None or not transcript.words:
                continue
            written = write_captions(transcript, cfg.captions, output_dir, proxy.clip_id)
            for fmt, path in written.items():
                caption_paths.append(path)
                rep.info(f"{proxy.clip_id} [{fmt}] → {path.name}")

    return PipelineResult(
        proxies=proxies,
        all_segments=all_segments,
        transcripts=list(all_transcripts.values()),
        hook_segment=hook_seg,
        outputs=outputs,
        edls=edls,
        captions=caption_paths,
        elapsed_s=time.perf_counter() - t0,
    )
