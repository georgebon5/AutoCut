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
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from autocut.captions import write_cut_captions
from autocut.config import AutoCutConfig
from autocut.edl import EDL, save_edl
from autocut.features import enrich_segments
from autocut.fillers import detect_fillers, punch_out_fillers
from autocut.hook import find_hook_segment
from autocut.ingest import IngestError, ingest_clips, normalize_voiceover
from autocut.keywords import enrich_segments_keywords
from autocut.models import ProxyInfo, RenderChunk, Segment, SpeechRegion, Transcript, WordTimestamp
from autocut.motion import enrich_segments_motion
from autocut.render import RenderError, render, render_voiceover_vlog
from autocut.scenes import detect_scene_boundaries, enrich_segments_scenes
from autocut.scoring import score_segments, score_segments_with_model
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
    # Optional trained LogisticModel to replace the heuristic weights.
    # When set, features must be enabled; validate_options enforces this.
    model_path: Path | None = None
    # Voiceover-vlog mode: a separately-recorded narration track replaces the
    # clip audios entirely. The clips become B-roll (visual only, muted) and
    # the voiceover drives analysis (VAD, Whisper, fillers, captions). The
    # output's audio IS the voiceover; the output's video is the concatenated
    # clips, trimmed to the voiceover's post-silence-removal duration.
    voiceover_audio_path: Path | None = None
    # Audio polish applied at render time: enhance = highpass + compressor +
    # loudness normalisation (sounds "produced"); denoise = ffmpeg's afftdn
    # spectral subtraction for ambient hiss / wind / crowd. Both are no-op
    # false by default so the raw audio stays untouched unless the user asks.
    audio_enhance: bool = False
    audio_denoise: bool = False
    # Output aspect: "source" keeps original clip aspect, "9:16" center-crops
    # to vertical TikTok, "16:9" to horizontal YouTube. Only takes effect at
    # render time; the EDL + review stay in source dimensions.
    output_aspect: str = "source"
    # Semantic matching (voiceover mode only): embed each clip visually via
    # CLIP, embed each voiceover sentence as text, pair them. The B-roll is
    # scheduled so the matching clip plays while its matching narration is
    # heard. Opt-in because it costs a few seconds for the CLIP forward pass
    # and only makes sense when there are enough clips to actually choose.
    semantic_match: bool = False
    # Optional background music track. When set, the music is mixed under the
    # voice audio at render time with sidechain ducking so it auto-lowers while
    # the speaker talks and returns to full volume in silent moments. Works in
    # both normal mode and voiceover-vlog mode.
    music_path: Path | None = None


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


def validate_options(options: PipelineOptions) -> None:
    """Raise PipelineError if any option combination is invalid.

    Callers that want to fail fast at request time (e.g. the HTTP API)
    should invoke this directly before enqueueing a job; ``run_pipeline``
    also calls it so a bare in-process invocation stays safe.
    """
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
    if options.model_path is not None and not options.features:
        raise PipelineError("model_path requires features=True")


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

    validate_options(options)
    if options.pacing:
        cfg.pacing.enabled = True
    if options.zoom:
        cfg.zoom.enabled = True

    do_transcribe = options.transcribe
    do_fillers = do_transcribe and options.fillers
    do_takes = do_transcribe and options.takes
    do_captions = do_transcribe and options.captions
    do_features = options.features

    # Voiceover-vlog mode: branch out early. Analysis targets the narration
    # file instead of the clip audios, and the render path concatenates
    # muted clips as B-roll under the voiceover audio. The regular code
    # path below stays unchanged for face-to-camera vlogs.
    if options.voiceover_audio_path is not None:
        return _run_voiceover_pipeline(
            clips, output_dir, cfg, options, rep, t0,
            do_transcribe=do_transcribe,
            do_fillers=do_fillers,
            do_takes=do_takes,
            do_captions=do_captions,
        )

    # ── 1. Ingest ────────────────────────────────────────────────────────────
    rep.stage("ingesting", _STAGE_PROGRESS["ingesting"])
    try:
        proxies = ingest_clips(clips, output_dir, cfg.ingest)
    except IngestError as e:
        raise PipelineError(f"ingest failed: {e}") from e
    for p in proxies:
        rep.info(f"{p.clip_id}  {p.duration:.1f}s  {p.width}×{p.height}  {p.fps:.2f}fps")

    # ── 2. Parallel analysis: VAD + Whisper + Scenes ─────────────────────────
    # These three stages all read the same proxy but are otherwise
    # independent — VAD + Whisper hit the audio WAV read-only, scene detect
    # shells out to ffmpeg on the video. Running them concurrently in a
    # thread pool cuts the wall-time of the dominant analysis step from
    # (vad + whisper + scenes) to max(vad, whisper, scenes).
    rep.stage("vad", _STAGE_PROGRESS["vad"])
    vad_model, get_ts = load_vad_model()
    whisper = load_whisper_model(cfg.transcribe) if do_transcribe else None

    all_regions: dict[str, list[SpeechRegion]] = {}
    all_transcripts: dict[str, Transcript] = {}
    scene_boundaries_by_clip: dict[str, list[float]] = {}

    def _run_vad(p: ProxyInfo) -> tuple[str, list[SpeechRegion]]:
        if p.audio_path is None:
            return p.clip_id, []
        regions = detect_speech(
            p.audio_path, p.clip_id, cfg.vad,
            model=vad_model, get_timestamps=get_ts,
        )
        return p.clip_id, regions

    def _run_whisper(p: ProxyInfo, regions: list[SpeechRegion]) -> tuple[str, Transcript]:
        if p.audio_path is None:
            return p.clip_id, Transcript(
                clip_id=p.clip_id, language=cfg.transcribe.language, words=[],
            )
        t = transcribe_clip(
            p.audio_path, regions, p.clip_id, cfg.transcribe, model=whisper,
        )
        return p.clip_id, t

    def _run_scenes(p: ProxyInfo) -> tuple[str, list[float]]:
        return p.clip_id, detect_scene_boundaries(p.proxy_path, cfg.scenes)

    # Scenes have no dependencies — fire them all upfront. VAD also starts
    # immediately. Whisper has to wait on its clip's VAD regions (used to
    # gate the Whisper chunks) so we chain it via a per-clip callback.
    with ThreadPoolExecutor(max_workers=min(8, max(2, len(proxies) * 3))) as pool:
        scene_futs = [pool.submit(_run_scenes, p) for p in proxies]
        vad_futs = [pool.submit(_run_vad, p) for p in proxies]
        whisper_futs = []
        if do_transcribe:
            for p, vfut in zip(proxies, vad_futs):
                # Submit whisper as soon as its matching VAD finishes.
                def _after_vad(p: ProxyInfo = p, vfut: Future = vfut) -> tuple[str, Transcript]:
                    _, regions = vfut.result()
                    return _run_whisper(p, regions)
                whisper_futs.append(pool.submit(_after_vad))

        proxies_by_id = {p.clip_id: p for p in proxies}
        for fut in vad_futs:
            cid, regions = fut.result()
            all_regions[cid] = regions
            if proxies_by_id[cid].audio_path is None:
                rep.info(f"{cid}: no audio — treated as B-roll")
                continue
            speech_s = sum(r.end - r.start for r in regions)
            rep.info(f"{cid}: {len(regions)} speech region(s)  ({speech_s:.1f}s speech)")

        if do_transcribe:
            rep.stage("transcribing", _STAGE_PROGRESS["transcribing"])
            for fut in whisper_futs:
                cid, t = fut.result()
                all_transcripts[cid] = t
                rep.info(f"{cid}: {len(t.words)} words [{t.language}]")

        for fut in scene_futs:
            cid, bounds = fut.result()
            scene_boundaries_by_clip[cid] = bounds
            if bounds:
                rep.info(f"{cid}: {len(bounds)} scene boundary/-ies")

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

        # Load the trained model once if provided — reused across clips.
        # If the file exists but is corrupt / unloadable (truncated JSON,
        # wrong schema, etc.) fall back to the heuristic rather than crashing
        # the whole job with a cryptic error. The user sees an info line and
        # the pipeline still produces a usable cut.
        model = None
        if options.model_path is not None:
            from autocut.trainer import LogisticModel
            try:
                model = LogisticModel.load(options.model_path)
                rep.info(
                    f"scoring with model: {options.model_path.name} "
                    f"({len(model.feature_names)} features)"
                )
            except (OSError, ValueError, KeyError, TypeError) as e:
                rep.info(
                    f"model {options.model_path.name} unreadable "
                    f"({type(e).__name__}: {e}); using heuristic weights"
                )
                model = None

        for proxy in proxies:
            segs = [s for s in all_segments if s.clip_id == proxy.clip_id]
            transcript = all_transcripts.get(proxy.clip_id)
            enrich_segments(segs, proxy.audio_path, transcript)
            enrich_segments_motion(segs, proxy.proxy_path)
            enrich_segments_keywords(segs, transcript, cfg.keywords)
            # Scene boundaries were pre-computed in parallel with VAD/Whisper
            # back in step 2 — reuse them so we don't re-scan the video here.
            boundaries = scene_boundaries_by_clip.get(proxy.clip_id, [])
            enrich_segments_scenes(segs, {proxy.clip_id: boundaries}, cfg.scenes)
            if model is not None:
                score_segments_with_model(segs, model)
            else:
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

    # ── 9. Render + EDL (+ cut-aligned captions) per preset ──────────────────
    rep.stage("rendering", _STAGE_PROGRESS["rendering"])
    outputs: list[Path] = []
    edls: list[Path] = []
    caption_paths: list[Path] = []
    if options.preset == "all":
        presets_to_render = list(cfg.presets.targets.keys())
    elif options.preset == "none":
        presets_to_render = []
    else:
        presets_to_render = [options.preset]

    clip_order = {p.clip_id: i for i, p in enumerate(proxies)}

    def _compute_render_chunks(
        segs: list[Segment],
        hook: Segment | None,
        clip_order: dict[str, int],
    ) -> list[RenderChunk]:
        """Replay render's segment ordering to produce the output-time map.

        Mirrors ``render()`` exactly: hook first (if any), then kept segments
        sorted by (clip_order, start). Each contributes its own duration to a
        running output offset. Hook uses segment_index=-1 since it isn't in
        ``edl.segments``.
        """
        chunks: list[RenderChunk] = []
        cursor = 0.0
        if hook is not None:
            d = hook.end - hook.start
            chunks.append(RenderChunk(segment_index=-1, output_start=0.0, output_end=d))
            cursor = d
        kept = [(i, s) for i, s in enumerate(segs) if s.decision == "keep" and s.duration > 1e-3]
        kept.sort(key=lambda t: (clip_order.get(t[1].clip_id, 0), t[1].start))
        for idx, s in kept:
            d = s.duration
            chunks.append(RenderChunk(
                segment_index=idx, output_start=cursor, output_end=cursor + d,
            ))
            cursor += d
        return chunks

    def _render_pass(segs: list[Segment], stem: str) -> None:
        video_name = f"{stem}.mp4"
        edl_name = f"edl{stem.removeprefix('rough_cut')}.json"
        try:
            out_mp4 = render(
                proxies, segs, output_dir, cfg.render,
                output_name=video_name, hook=hook_seg, zoom=cfg.zoom,
                audio_enhance=options.audio_enhance,
                audio_denoise=options.audio_denoise,
                output_aspect=options.output_aspect,
                music_path=options.music_path,
                music_cfg=cfg.music,
            )
        except RenderError as e:
            raise PipelineError(f"render failed: {e}") from e
        outputs.append(out_mp4)
        rep.info(f"wrote {out_mp4.name}")

        chunks = _compute_render_chunks(segs, hook_seg, clip_order)
        edl = EDL(
            clips=proxies, segments=segs,
            transcripts=list(all_transcripts.values()), hook=hook_seg,
            render_chunks=chunks,
        )
        edl_path = output_dir / edl_name
        save_edl(edl, edl_path)
        edls.append(edl_path)
        rep.info(f"wrote {edl_path.name}")

        # Cut-aligned captions: timestamps match the mp4 we just produced,
        # so the user can burn them in or import into an NLE without drift.
        if do_captions and any(t.words for t in all_transcripts.values()):
            written = write_cut_captions(
                edl, cfg.captions, output_dir, stem=stem, clip_order=clip_order,
            )
            for fmt, path in written.items():
                caption_paths.append(path)
                rep.info(f"wrote {path.name}")

    if not presets_to_render:
        _render_pass(all_segments, "rough_cut")
    else:
        for p in presets_to_render:
            segs = select_segments(all_segments, p, cfg.presets, pacing=cfg.pacing)
            kept = sum(s.duration for s in segs if s.decision == "keep")
            target = cfg.presets.targets[p]
            rep.info(f"preset={p}: target {target:.0f}s, selected {kept:.0f}s")
            _render_pass(segs, f"rough_cut_{p}")

    if do_captions:
        rep.stage("captions", _STAGE_PROGRESS["captions"])

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


_VOICEOVER_CLIP_ID = "voiceover"


def _sentences_for_clip_matching(
    kept_segs: list[Segment],
    transcript: Transcript,
    vo_wav: Path,
    tr_cfg: Any,
    rep: PipelineReporter,
) -> list[str]:
    """Produce per-segment text in **English** for CLIP matching.

    CLIP's text encoder is trained predominantly on English — Greek text
    embeddings land far from the matching visual concepts. We lean on
    Whisper's built-in ``task="translate"`` (any-language-to-English) to
    get aligned English segments at the SAME timestamps, then group words
    the same way we group the Greek transcript by kept segment.

    If translation fails or the voiceover is already English, we fall back
    to the Greek word join — CLIP will still match proper nouns and the
    user gets some pairing instead of nothing.
    """
    import numpy as np
    try:
        from faster_whisper import WhisperModel
        import librosa
    except ImportError:
        return _group_words_by_segment(kept_segs, transcript.words)

    try:
        model = load_whisper_model(tr_cfg)
        audio = np.asarray(librosa.load(str(vo_wav), sr=16000, mono=True)[0], dtype=np.float32)
        segs_iter, _info = model.transcribe(
            audio, task="translate", word_timestamps=True,
            beam_size=tr_cfg.beam_size, vad_filter=True,
        )
        en_words: list[tuple[float, str]] = []
        for seg in segs_iter:
            if not seg.words:
                continue
            for w in seg.words:
                en_words.append((w.start, w.word))
        if not en_words:
            raise RuntimeError("empty translation")
    except Exception as e:   # noqa: BLE001 — fall back to greek words
        rep.info(f"english translation failed ({type(e).__name__}): using greek text")
        return _group_words_by_segment(kept_segs, transcript.words)

    # Group English words by kept segment boundaries (same timeline, so
    # start-time comparisons match the Greek words we already segmented).
    out: list[str] = []
    for s in kept_segs:
        words = [w for t, w in en_words if s.start <= t < s.end]
        out.append(" ".join(words).strip() or "(scene)")
    rep.info(f"english translation: {sum(len(x.split()) for x in out)} words for CLIP")
    return out


def _group_words_by_segment(
    kept_segs: list[Segment], words: list[WordTimestamp],
) -> list[str]:
    out: list[str] = []
    for s in kept_segs:
        joined = " ".join(w.word for w in words if s.start <= w.start < s.end).strip()
        out.append(joined or "(scene)")
    return out


def _run_voiceover_pipeline(
    clips: list[Path],
    output_dir: Path,
    cfg: AutoCutConfig,
    options: PipelineOptions,
    rep: PipelineReporter,
    t0: float,
    *,
    do_transcribe: bool,
    do_fillers: bool,
    do_takes: bool,
    do_captions: bool,
) -> PipelineResult:
    """Voiceover-vlog mode: analyse the narration file, render B-roll under it.

    Unlike the regular pipeline, segments live on the voiceover's timeline
    (not any clip's), motion/scenes/zoom aren't computed (video has no
    bearing on the cut), and the final mp4 is clip-concat (muted) + the
    voiceover audio restricted to kept segments.
    """
    assert options.voiceover_audio_path is not None

    # ── 1. Ingest clips (needed for the muted B-roll track) + voiceover ──
    rep.stage("ingesting", _STAGE_PROGRESS["ingesting"])
    try:
        proxies = ingest_clips(clips, output_dir, cfg.ingest)
    except IngestError as e:
        raise PipelineError(f"ingest failed: {e}") from e
    for p in proxies:
        rep.info(f"{p.clip_id}  {p.duration:.1f}s  {p.width}×{p.height}")

    try:
        vo_wav, vo_duration = normalize_voiceover(
            options.voiceover_audio_path, output_dir, cfg.ingest.audio_sample_rate,
        )
    except Exception as e:   # noqa: BLE001 — ingest sub-step
        raise PipelineError(f"voiceover ingest failed: {e}") from e
    rep.info(f"voiceover: {vo_duration:.1f}s")

    # ── 2. VAD + Whisper on voiceover ───────────────────────────────────
    rep.stage("vad", _STAGE_PROGRESS["vad"])
    vad_model, get_ts = load_vad_model()
    regions = detect_speech(
        vo_wav, _VOICEOVER_CLIP_ID, cfg.vad,
        model=vad_model, get_timestamps=get_ts,
    )
    rep.info(f"voiceover: {len(regions)} speech region(s)")

    transcript = Transcript(
        clip_id=_VOICEOVER_CLIP_ID, language=cfg.transcribe.language, words=[],
    )
    if do_transcribe:
        rep.stage("transcribing", _STAGE_PROGRESS["transcribing"])
        whisper = load_whisper_model(cfg.transcribe)
        transcript = transcribe_clip(
            vo_wav, regions, _VOICEOVER_CLIP_ID, cfg.transcribe, model=whisper,
        )
        rep.info(f"voiceover: {len(transcript.words)} words")

    # ── 3. Fillers + takes on voiceover ──────────────────────────────────
    filler_cuts: list[Segment] = []
    if do_fillers and transcript.words:
        rep.stage("fillers", _STAGE_PROGRESS["fillers"])
        filler_cuts = detect_fillers(transcript, cfg.fillers)
        rep.info(f"voiceover: {len(filler_cuts)} filler(s)")

    take_cuts: list[Segment] = []
    if do_takes and transcript.words:
        rep.stage("takes", _STAGE_PROGRESS["takes"])
        take_cuts = detect_repeated_takes(transcript, cfg.takes)
        rep.info(f"voiceover: {len(take_cuts)} repeated take(s)")

    # ── 4. Silence removal on voiceover ──────────────────────────────────
    rep.stage("silence_removal", _STAGE_PROGRESS["silence_removal"])
    word_ts = transcript.to_snap_format()
    segments = apply_silence_removal(
        regions, _VOICEOVER_CLIP_ID, vo_duration, cfg.silence,
        word_timestamps=word_ts or None,
    )
    segments = punch_out_fillers(segments, filler_cuts + take_cuts)
    kept_dur = sum(s.duration for s in segments if s.decision == "keep")
    rep.info(f"kept {kept_dur:.1f}s of voiceover across {len(segments)} segment(s)")

    # ── 4b. Semantic match: pair voiceover segments with best-fit clips ──
    clip_schedule: list[str] | None = None
    if options.semantic_match and len(proxies) > 1:
        rep.stage("semantic", _STAGE_PROGRESS.get("semantic", 0.72))
        try:
            from autocut.semantic import (
                match_sentences_to_clips, visual_embeddings,
            )
            kept_segs = [s for s in segments if s.decision == "keep"]
            sentences = _sentences_for_clip_matching(
                kept_segs, transcript, vo_wav, cfg.transcribe, rep,
            )
            vis_embeds = visual_embeddings(
                {p.clip_id: p.proxy_path for p in proxies}
            )
            if vis_embeds:
                clip_durs = {p.clip_id: p.duration for p in proxies}
                seg_durs = [s.duration for s in kept_segs]
                clip_schedule = match_sentences_to_clips(
                    sentences, vis_embeds,
                    clip_durations=clip_durs,
                    segment_durations=seg_durs,
                )
                rep.info(
                    f"semantic schedule: "
                    f"{len(set(clip_schedule))} unique clip(s) across "
                    f"{len(sentences)} sentence(s)"
                )
        except Exception as e:   # noqa: BLE001 — fail soft, keep render running
            rep.info(f"semantic matching skipped ({type(e).__name__}: {e})")
            clip_schedule = None

    # ── 5. Render muted B-roll + voiceover audio ─────────────────────────
    rep.stage("rendering", _STAGE_PROGRESS["rendering"])
    stem = "rough_cut"
    output_name = f"{stem}.mp4"

    # Captions first (same cut-aligned logic as the regular path) so the
    # single render pass can burn them in if asked.
    captions_written: dict[str, Path] = {}
    if do_captions and transcript.words:
        clip_order = {_VOICEOVER_CLIP_ID: 0}
        # Build a one-clip EDL so write_cut_captions can walk it.
        vo_edl = EDL(
            clips=[],
            segments=segments,
            transcripts=[transcript],
            hook=None,
        )
        captions_written = write_cut_captions(
            vo_edl, cfg.captions, output_dir,
            stem=stem, clip_order=clip_order,
        )
        for name, path in captions_written.items():
            rep.info(f"wrote {path.name}")

    try:
        out_mp4 = render_voiceover_vlog(
            proxies, segments, vo_wav, output_dir, cfg.render,
            output_name=output_name,
            subtitles_path=None,   # first render stays clean; re-render can burn.
            fast_preview=False,
            audio_enhance=options.audio_enhance,
            audio_denoise=options.audio_denoise,
            output_aspect=options.output_aspect,
            clip_schedule=clip_schedule,
            music_path=options.music_path,
            music_cfg=cfg.music,
        )
    except RenderError as e:
        raise PipelineError(f"render failed: {e}") from e
    rep.info(f"wrote {out_mp4.name}")

    # EDL for the review page: segments reference the pseudo "voiceover"
    # clip. render_chunks mirror the audio's post-silence-removal layout
    # so the frontend's instant-skip still works for voiceover edits.
    chunks: list[RenderChunk] = []
    cursor = 0.0
    for idx, s in enumerate(segments):
        if s.decision != "keep" or s.duration <= 1e-3:
            continue
        chunks.append(RenderChunk(
            segment_index=idx, output_start=cursor, output_end=cursor + s.duration,
        ))
        cursor += s.duration

    edl = EDL(
        clips=proxies,
        segments=segments,
        transcripts=[transcript],
        hook=None,
        render_chunks=chunks,
    )
    edl_path = output_dir / "edl.json"
    save_edl(edl, edl_path)
    rep.info(f"wrote {edl_path.name}")

    return PipelineResult(
        proxies=proxies,
        all_segments=segments,
        transcripts=[transcript],
        hook_segment=None,
        outputs=[out_mp4],
        edls=[edl_path],
        captions=list(captions_written.values()),
        elapsed_s=time.perf_counter() - t0,
    )
