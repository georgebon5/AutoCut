from __future__ import annotations

import time
from pathlib import Path

import click

from autocut.captions import write_captions
from autocut.config import load_config
from autocut.features import enrich_segments
from autocut.edl import EDL, save_edl
from autocut.fillers import detect_fillers, punch_out_fillers
from autocut.ingest import IngestError, ingest_clips
from autocut.models import Segment, SpeechRegion, Transcript
from autocut.render import RenderError, render
from autocut.silence import apply_silence_removal
from autocut.takes import detect_repeated_takes
from autocut.transcribe import load_whisper_model, transcribe_clip
from autocut.vad import detect_speech, load_vad_model


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


@click.group()
@click.option(
    "--config", "-c",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML config file (default: config/default.yaml).",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@cli.command()
@click.argument("clips", nargs=-1, required=True,
                type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o",
              type=click.Path(path_type=Path), default=Path("output"),
              show_default=True)
@click.option("--no-transcribe", is_flag=True,
              help="Skip Whisper transcription (cuts use VAD boundaries only).")
@click.option("--no-fillers", is_flag=True,
              help="Skip filler-word detection.")
@click.option("--no-takes", is_flag=True,
              help="Skip repeated take detection.")
@click.option("--no-captions", is_flag=True,
              help="Skip caption file generation.")
@click.option("--no-features", is_flag=True,
              help="Skip audio feature extraction.")
@click.pass_context
def ingest(
    ctx: click.Context,
    clips: tuple[Path, ...],
    output_dir: Path,
    no_transcribe: bool,
    no_fillers: bool,
    no_takes: bool,
    no_captions: bool,
    no_features: bool,
) -> None:
    """Normalize clips, detect speech, transcribe, remove silences, render.

    CLIPS: one or more video files (VFR, HEVC, HDR all accepted).
    """
    cfg = ctx.obj["config"]
    t0 = time.perf_counter()
    do_transcribe = not no_transcribe
    do_fillers = do_transcribe and not no_fillers
    do_takes = do_transcribe and not no_takes
    do_captions = do_transcribe and not no_captions
    do_features = not no_features
    n_steps = (5 + int(do_transcribe) + int(do_fillers)
               + int(do_takes) + int(do_captions) + int(do_features))
    click.echo(f"AutoCut — {len(clips)} clip(s) → {output_dir}\n")

    # ── 1. Ingest ────────────────────────────────────────────────────────────
    click.echo(f"[1/{n_steps}] Ingesting clips (normalising to CFR proxy + 16 kHz audio)...")
    try:
        proxies = ingest_clips(list(clips), output_dir, cfg.ingest)
    except IngestError as e:
        raise click.ClickException(str(e)) from e

    for p in proxies:
        click.echo(
            f"    {p.clip_id}  {_fmt(p.duration)}  "
            f"{p.width}×{p.height}  {p.fps:.2f}fps"
        )

    # ── 2. VAD ───────────────────────────────────────────────────────────────
    click.echo(f"\n[2/{n_steps}] VAD: detecting speech regions...")
    vad_model, get_ts = load_vad_model()

    all_regions: dict[str, list[SpeechRegion]] = {}
    for proxy in proxies:
        if proxy.audio_path is None:
            all_regions[proxy.clip_id] = []
            click.echo(f"    {proxy.clip_id}: no audio — treated as B-roll")
            continue
        regions = detect_speech(
            proxy.audio_path, proxy.clip_id, cfg.vad,
            model=vad_model, get_timestamps=get_ts,
        )
        all_regions[proxy.clip_id] = regions
        speech_s = sum(r.end - r.start for r in regions)
        silence_s = proxy.duration - speech_s
        click.echo(
            f"    {proxy.clip_id}: {len(regions)} regions  "
            f"({_fmt(speech_s)} speech / {_fmt(silence_s)} silence)"
        )

    _s = 2  # running step counter (ingest=1, VAD=2 already printed)

    # ── 3. Transcribe (optional) ─────────────────────────────────────────────
    all_transcripts: dict[str, Transcript] = {}
    if do_transcribe:
        _s += 1
        click.echo(f"\n[{_s}/{n_steps}] Transcribing speech (faster-whisper {cfg.transcribe.model_size})...")
        whisper = load_whisper_model(cfg.transcribe)
        for proxy in proxies:
            if proxy.audio_path is None:
                all_transcripts[proxy.clip_id] = Transcript(
                    clip_id=proxy.clip_id, language=cfg.transcribe.language, words=[]
                )
                continue
            t = transcribe_clip(
                proxy.audio_path,
                all_regions[proxy.clip_id],
                proxy.clip_id,
                cfg.transcribe,
                model=whisper,
            )
            all_transcripts[proxy.clip_id] = t
            click.echo(f"    {proxy.clip_id}: {len(t.words)} words  [{t.language}]")

    # ── 4. Filler detection (optional) ───────────────────────────────────────
    all_filler_cuts: dict[str, list[Segment]] = {p.clip_id: [] for p in proxies}
    if do_fillers:
        _s += 1
        click.echo(f"\n[{_s}/{n_steps}] Detecting filler words...")
        for proxy in proxies:
            transcript = all_transcripts.get(proxy.clip_id)
            if transcript is None:
                continue
            cuts = detect_fillers(transcript, cfg.fillers)
            all_filler_cuts[proxy.clip_id] = cuts
            click.echo(f"    {proxy.clip_id}: {len(cuts)} filler(s)")

    # ── 5. Repeated take detection (optional) ────────────────────────────────
    all_take_cuts: dict[str, list[Segment]] = {p.clip_id: [] for p in proxies}
    if do_takes:
        _s += 1
        click.echo(f"\n[{_s}/{n_steps}] Detecting repeated takes...")
        for proxy in proxies:
            transcript = all_transcripts.get(proxy.clip_id)
            if transcript is None:
                continue
            cuts = detect_repeated_takes(transcript, cfg.takes)
            all_take_cuts[proxy.clip_id] = cuts
            click.echo(f"    {proxy.clip_id}: {len(cuts)} repeated take(s)")

    # ── 6. Silence removal ───────────────────────────────────────────────────
    _s += 1
    click.echo(f"\n[{_s}/{n_steps}] Applying silence removal...")
    all_segments: list[Segment] = []
    for proxy in proxies:
        word_ts = (
            all_transcripts[proxy.clip_id].to_snap_format()
            if proxy.clip_id in all_transcripts else []
        )
        segs = apply_silence_removal(
            all_regions[proxy.clip_id],
            proxy.clip_id,
            proxy.duration,
            cfg.silence,
            word_timestamps=word_ts or None,
        )
        extra_cuts = all_filler_cuts[proxy.clip_id] + all_take_cuts[proxy.clip_id]
        segs = punch_out_fillers(segs, extra_cuts)
        all_segments.extend(segs)
        n_cut = sum(1 for s in segs if s.decision == "cut")
        removed = sum(s.duration for s in segs if s.decision == "cut")
        click.echo(
            f"    {proxy.clip_id}: {n_cut} cut(s), {_fmt(removed)} removed"
        )

    dur_kept = sum(s.duration for s in all_segments if s.decision == "keep")
    dur_total = sum(p.duration for p in proxies)
    pct = 100 * dur_kept / dur_total if dur_total else 0
    click.echo(f"    → keeping {_fmt(dur_kept)} of {_fmt(dur_total)} ({pct:.0f}%)")

    # ── Audio feature extraction (optional) ──────────────────────────────────
    if do_features:
        _s += 1
        click.echo(f"\n[{_s}/{n_steps}] Extracting audio features...")
        for proxy in proxies:
            segs = [s for s in all_segments if s.clip_id == proxy.clip_id]
            transcript = all_transcripts.get(proxy.clip_id)
            enrich_segments(segs, proxy.audio_path, transcript)
            n_enriched = sum(1 for s in segs if s.features)
            click.echo(f"    {proxy.clip_id}: {n_enriched} segment(s) enriched")

    # ── Render ────────────────────────────────────────────────────────────────
    _s += 1
    click.echo(f"\n[{_s}/{n_steps}] Rendering rough_cut.mp4...")
    try:
        output_mp4 = render(proxies, all_segments, output_dir, cfg.render)
    except RenderError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"    → {output_mp4}")

    # ── EDL ───────────────────────────────────────────────────────────────────
    _s += 1
    click.echo(f"\n[{_s}/{n_steps}] Saving EDL (decision list + transcript)...")
    edl = EDL(
        clips=proxies,
        segments=all_segments,
        transcripts=list(all_transcripts.values()),
    )
    edl_path = output_dir / "edl.json"
    save_edl(edl, edl_path)
    click.echo(f"    → {edl_path}")

    # ── Captions (optional) ───────────────────────────────────────────────────
    if do_captions:
        _s += 1
        fmts = ", ".join(cfg.captions.formats)
        click.echo(f"\n[{_s}/{n_steps}] Writing captions ({fmts})...")
        for proxy in proxies:
            transcript = all_transcripts.get(proxy.clip_id)
            if transcript is None or not transcript.words:
                continue
            written = write_captions(transcript, cfg.captions, output_dir, proxy.clip_id)
            for fmt, path in written.items():
                click.echo(f"    {proxy.clip_id} [{fmt}] → {path}")

    elapsed = time.perf_counter() - t0
    click.echo(f"\nDone in {elapsed:.1f}s")
