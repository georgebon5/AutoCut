"""Preset-driven segment selection: pick top-score keep segments to a target duration."""

from __future__ import annotations

import copy

from autocut.config import PacingConfig, PresetConfig
from autocut.models import Segment


def _in_opening(seg: Segment, pacing: PacingConfig | None) -> bool:
    """True iff ``seg`` starts within the pacing opening window of its clip."""
    return (
        pacing is not None
        and pacing.enabled
        and seg.start < pacing.opening_window_s
    )


def _effective_score(seg: Segment, pacing: PacingConfig | None) -> float:
    """Score used for ranking — boosted/penalised by pacing when applicable.

    Opening segments shorter than ``opening_ideal_duration_s`` get a bonus;
    longer ones get a penalty. ``opening_bias`` controls the strength.
    """
    base = seg.interest_score
    if not _in_opening(seg, pacing):
        return base
    assert pacing is not None   # narrowed by _in_opening
    duration = max(seg.duration, 1e-3)
    factor = (pacing.opening_ideal_duration_s / duration) ** pacing.opening_bias
    return base * factor


def select_segments(
    segments: list[Segment],
    preset_name: str,
    cfg: PresetConfig,
    pacing: PacingConfig | None = None,
) -> list[Segment]:
    """Return a copy of `segments` with decisions updated for the given preset.

    Only segments already marked ``keep`` are considered — upstream cuts
    (silence, fillers, repeated takes) always stay cut. Candidates are ranked
    by ``interest_score`` descending (ties broken by shorter duration, so we
    pack more variety at the same score). We take them greedily until the
    cumulative kept duration reaches the preset's target, respecting a
    ``cfg.tolerance`` overshoot allowance.

    Chronological order is preserved implicitly: we only flip decisions on a
    copy of the original segment list — the order of the list itself is not
    changed, so the renderer still concatenates in time order.

    Input segments are not mutated.
    """
    if preset_name not in cfg.targets:
        raise ValueError(
            f"Unknown preset '{preset_name}'. Available: {sorted(cfg.targets)}"
        )
    target = cfg.targets[preset_name]
    out = copy.deepcopy(segments)
    candidates = [s for s in out if s.decision == "keep"]
    total_keep = sum(s.duration for s in candidates)

    # Target already covers everything available → keep all candidates.
    if total_keep <= target:
        for s in candidates:
            tag = ", pacing" if _in_opening(s, pacing) else ""
            s.reasons.append(
                f"selected (score={s.interest_score:.2f}, preset={preset_name}{tag})"
            )
        return out

    ranked = sorted(
        candidates,
        key=lambda s: (-_effective_score(s, pacing), s.duration),
    )
    upper = target * (1.0 + cfg.tolerance)

    cumulative = 0.0
    keep_ids: set[int] = set()
    for s in ranked:
        if cumulative >= target:
            break
        projected = cumulative + s.duration
        # Skip if adding this segment would overshoot the tolerance window —
        # unless we have nothing yet (edge case: a single very long segment).
        if projected > upper and cumulative > 0.0:
            continue
        keep_ids.add(id(s))
        cumulative = projected

    for s in candidates:
        tag = ", pacing" if _in_opening(s, pacing) else ""
        if id(s) in keep_ids:
            s.reasons.append(
                f"selected (score={s.interest_score:.2f}, preset={preset_name}{tag})"
            )
        else:
            s.decision = "cut"
            s.reasons.append(
                f"dropped (score={s.interest_score:.2f}, preset={preset_name}{tag})"
            )

    return out
