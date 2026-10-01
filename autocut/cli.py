from __future__ import annotations

import time
from pathlib import Path

import click

from autocut.config import load_config
from autocut.edl import EDL, save_edl
from autocut.ingest import IngestError, ingest_clips
from autocut.models import SpeechRegion, Segment
from autocut.render import RenderError, render
from autocut.silence import apply_silence_removal
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
@click.pass_context
def ingest(ctx: click.Context, clips: tuple[Path, ...], output_dir: Path) -> None:
    """Normalize clips, detect speech, remove silences, and render a rough cut.

    CLIPS: one or more video files (VFR, HEVC, HDR all accepted).
    """
    cfg = ctx.obj["config"]
    t0 = time.perf_counter()
    click.echo(f"AutoCut — {len(clips)} clip(s) → {output_dir}\n")

    # ── 1. Ingest ────────────────────────────────────────────────────────────
    click.echo("[1/5] Ingesting clips (normalising to CFR proxy + 16 kHz audio)...")
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
    click.echo("\n[2/5] VAD: detecting speech regions...")
    model, get_ts = load_vad_model()

    all_regions: dict[str, list[SpeechRegion]] = {}
    for proxy in proxies:
        if proxy.audio_path is None:
            all_regions[proxy.clip_id] = []
            click.echo(f"    {proxy.clip_id}: no audio — treated as B-roll")
            continue
        regions = detect_speech(
            proxy.audio_path, proxy.clip_id, cfg.vad,
            model=model, get_timestamps=get_ts,
        )
        all_regions[proxy.clip_id] = regions
        speech_s = sum(r.end - r.start for r in regions)
        silence_s = proxy.duration - speech_s
        click.echo(
            f"    {proxy.clip_id}: {len(regions)} regions  "
            f"({_fmt(speech_s)} speech / {_fmt(silence_s)} silence)"
        )

    # ── 3. Silence removal ───────────────────────────────────────────────────
    click.echo("\n[3/5] Applying silence removal...")
    all_segments: list[Segment] = []
    for proxy in proxies:
        segs = apply_silence_removal(
            all_regions[proxy.clip_id],
            proxy.clip_id,
            proxy.duration,
            cfg.silence,
        )
        all_segments.extend(segs)
        n_cut = sum(1 for s in segs if s.decision == "cut")
        removed = sum(s.duration for s in segs if s.decision == "cut")
        click.echo(
            f"    {proxy.clip_id}: {n_cut} cut(s), "
            f"{_fmt(removed)} removed"
        )

    dur_kept = sum(s.duration for s in all_segments if s.decision == "keep")
    dur_total = sum(p.duration for p in proxies)
    pct = 100 * dur_kept / dur_total if dur_total else 0
    click.echo(f"    → keeping {_fmt(dur_kept)} of {_fmt(dur_total)} ({pct:.0f}%)")

    # ── 4. Render ─────────────────────────────────────────────────────────────
    click.echo("\n[4/5] Rendering rough_cut.mp4...")
    try:
        output_mp4 = render(proxies, all_segments, output_dir, cfg.render)
    except RenderError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"    → {output_mp4}")

    # ── 5. EDL ───────────────────────────────────────────────────────────────
    click.echo("\n[5/5] Saving EDL (decision list)...")
    edl = EDL(clips=proxies, segments=all_segments)
    edl_path = output_dir / "edl.json"
    save_edl(edl, edl_path)
    click.echo(f"    → {edl_path}")

    elapsed = time.perf_counter() - t0
    click.echo(f"\nDone in {elapsed:.1f}s")
