"""CLI thin wrapper over autocut.pipeline — handles argparse + stdout output."""

from __future__ import annotations

from pathlib import Path

import click

from autocut.config import load_config
from autocut.pipeline import PipelineError, PipelineOptions, run_pipeline


class _ClickReporter:
    """Pipeline reporter that mirrors the pre-refactor CLI output format."""
    def stage(self, name: str, progress: float) -> None:
        click.echo(f"\n[{int(progress * 100):3d}%] {name}...")

    def info(self, message: str) -> None:
        click.echo(f"    {message}")


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
@click.option("--preset",
              type=click.Choice(["none", "tight", "medium", "loose", "all"]),
              default="none", show_default=True,
              help="Target-duration selection preset. 'all' renders tight+medium+loose.")
@click.option("--hook", "hook_opt", is_flag=True,
              help="Prepend the top-scoring 1-3s moment as an opening teaser.")
@click.option("--pacing", "pacing_opt", is_flag=True,
              help="More aggressive cutting in each clip's opening window.")
@click.option("--zoom", "zoom_opt", is_flag=True,
              help="Subtle punch-in zoom on high-score emphasis moments.")
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
    preset: str,
    hook_opt: bool,
    pacing_opt: bool,
    zoom_opt: bool,
) -> None:
    """Normalize clips, detect speech, transcribe, remove silences, render.

    CLIPS: one or more video files (VFR, HEVC, HDR all accepted).
    """
    cfg = ctx.obj["config"]
    options = PipelineOptions(
        transcribe=not no_transcribe,
        fillers=not no_fillers,
        takes=not no_takes,
        captions=not no_captions,
        features=not no_features,
        hook=hook_opt,
        pacing=pacing_opt,
        zoom=zoom_opt,
        preset=preset,
    )

    click.echo(f"AutoCut — {len(clips)} clip(s) → {output_dir}")

    try:
        result = run_pipeline(
            list(clips), output_dir, cfg, options, reporter=_ClickReporter(),
        )
    except PipelineError as e:
        raise click.ClickException(str(e)) from e

    click.echo(f"\nDone in {result.elapsed_s:.1f}s")
