from __future__ import annotations

from pathlib import Path

import click

from autocut.config import load_config


@click.group()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to YAML config file (default: config/default.yaml).",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@cli.command()
@click.argument("clips", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=Path("output"))
@click.pass_context
def ingest(ctx: click.Context, clips: tuple[Path, ...], output_dir: Path) -> None:
    """Normalize clips to CFR proxies and produce a rough cut."""
    cfg = ctx.obj["config"]
    click.echo(f"AutoCut ingest — {len(clips)} clip(s) → {output_dir}")
    # Stages wired in Task 2–5
    raise NotImplementedError("Pipeline stages not yet implemented.")
