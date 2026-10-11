"""CLI thin wrapper over autocut.pipeline — handles argparse + stdout output."""

from __future__ import annotations

import tempfile
from pathlib import Path

import click

from autocut.config import load_config
from autocut.edl import load_edl
from autocut.eval import EvalConfig, EvalError, evaluate_cut, extract_audio
from autocut.pipeline import PipelineError, PipelineOptions, run_pipeline
from autocut.trainer import collect_from_eval, train_and_evaluate


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
@click.option("--model", "model_path",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None,
              help="Use a trained LogisticModel (JSON) for scoring instead of "
                   "the hand-tuned heuristic weights.")
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
    model_path: Path | None,
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
        model_path=model_path,
    )

    click.echo(f"AutoCut — {len(clips)} clip(s) → {output_dir}")

    try:
        result = run_pipeline(
            list(clips), output_dir, cfg, options, reporter=_ClickReporter(),
        )
    except PipelineError as e:
        raise click.ClickException(str(e)) from e

    click.echo(f"\nDone in {result.elapsed_s:.1f}s")


@cli.command()
@click.argument("edl_path", type=click.Path(exists=True, path_type=Path))
@click.argument("published", type=click.Path(exists=True, path_type=Path))
@click.option("--threshold", type=float, default=0.5, show_default=True,
              help="Correlation peak needed to count a segment as kept.")
@click.option("--min-seg", type=float, default=0.3, show_default=True,
              help="Segments shorter than this are skipped.")
@click.option("--verbose", "-v", is_flag=True,
              help="Print the per-segment outcome table.")
def eval(
    edl_path: Path,
    published: Path,
    threshold: float,
    min_seg: float,
    verbose: bool,
) -> None:
    """Score an EDL's auto decisions against a published cut.

    EDL_PATH: edl.json produced by `autocut ingest`.
    PUBLISHED: final edit (mp4/mov) the editor exported.
    """
    edl = load_edl(edl_path)
    cfg = EvalConfig(match_threshold=threshold, min_segment_s=min_seg)
    with tempfile.TemporaryDirectory() as td:
        pub_wav = Path(td) / "published.wav"
        try:
            extract_audio(published, pub_wav, sr=cfg.sr)
        except EvalError as e:
            raise click.ClickException(str(e)) from e
        result = evaluate_cut(edl, pub_wav, cfg)

    click.echo(
        f"TP={result.tp}  FP={result.fp}  TN={result.tn}  FN={result.fn}  "
        f"(skipped {result.skipped})"
    )
    click.echo(
        f"precision={result.precision:.3f}  "
        f"recall={result.recall:.3f}  "
        f"F1={result.f1:.3f}"
    )
    click.echo(
        f"kept by pipeline: {result.kept_by_pipeline_s:.1f}s  ·  "
        f"kept by editor: {result.kept_by_editor_s:.1f}s  ·  "
        f"total: {result.total_segment_s:.1f}s"
    )

    if verbose:
        click.echo("\nidx  clip        start    end     peak   outcome")
        for s in result.segments:
            click.echo(
                f"{s.index:>3}  {s.clip_id:<10}  {s.start:6.2f}  {s.end:6.2f}  "
                f"{s.match_peak:5.2f}  {s.outcome}"
            )


@cli.command()
@click.argument("pairs", nargs=-1, required=True)
@click.option("--out", "out_path", type=click.Path(path_type=Path),
              default=Path("model.json"), show_default=True,
              help="Where to save the trained LogisticModel.")
@click.option("--test-fraction", type=float, default=0.25, show_default=True)
@click.option("--seed", type=int, default=42, show_default=True)
@click.option("--threshold", type=float, default=0.5, show_default=True,
              help="Correlation peak needed to count a segment as kept.")
def train(
    pairs: tuple[str, ...],
    out_path: Path,
    test_fraction: float,
    seed: int,
    threshold: float,
) -> None:
    """Train a classifier from one or more (edl, published) pairs.

    PAIRS: alternating edl.json published.mp4 edl.json published.mp4 …
    Example:
        autocut train run1/edl.json run1/final.mp4 run2/edl.json run2/final.mp4
    """
    import tempfile

    if len(pairs) % 2 != 0:
        raise click.ClickException(
            "pass pairs of (edl.json, published.mp4) — got odd number of args"
        )

    examples = []
    cfg = EvalConfig(match_threshold=threshold)
    with tempfile.TemporaryDirectory() as td:
        for i in range(0, len(pairs), 2):
            edl_path = Path(pairs[i])
            pub_path = Path(pairs[i + 1])
            if not edl_path.exists() or not pub_path.exists():
                raise click.ClickException(f"missing input: {edl_path} / {pub_path}")
            click.echo(f"evaluating {edl_path.name} vs {pub_path.name}…")
            edl = load_edl(edl_path)
            wav = Path(td) / f"pub_{i}.wav"
            try:
                extract_audio(pub_path, wav, sr=cfg.sr)
            except EvalError as e:
                raise click.ClickException(str(e)) from e
            result = evaluate_cut(edl, wav, cfg)
            examples.extend(collect_from_eval(edl, result))

    click.echo(f"\ncollected {len(examples)} labelled segments")
    try:
        tr = train_and_evaluate(
            examples, test_fraction=test_fraction, seed=seed,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    click.echo(
        f"\nheld-out ({tr.n_test} segs):  "
        f"heuristic F1 = {tr.heuristic_test_f1:.3f}  "
        f"model F1 = {tr.model_test_f1:.3f}  "
        f"{'✓ beats baseline' if tr.beats_baseline else '✗ worse than baseline'}"
    )
    click.echo(f"train F1 ({tr.n_train} segs): {tr.model_train_f1:.3f}")

    tr.model.save(out_path)
    click.echo(f"\nmodel saved to {out_path}")
