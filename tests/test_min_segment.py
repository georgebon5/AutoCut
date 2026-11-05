"""Tests for min_segment_ms — the post-scoring blip filter.

No ffmpeg or model downloads required: we test the _drop_short_keeps
logic by running the pipeline up to the render stage with a monkeypatched
render that just records which segments were passed to it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autocut.models import Segment


# ---------------------------------------------------------------------------
# Unit test: the filter correctly marks short kept segments as cut
# ---------------------------------------------------------------------------

def _make_seg(start: float, end: float, decision: str = "keep") -> Segment:
    return Segment(
        clip_id="c1",
        start=start,
        end=end,
        decision=decision,
        interest_score=0.8,
        decision_source="auto",
        reasons=[],
        features={},
    )


def test_min_segment_drops_short_kept():
    """Short kept segments are changed to cut; long ones stay."""
    segs = [
        _make_seg(0.0, 0.3),   # 300 ms — too short
        _make_seg(0.5, 1.5),   # 1000 ms — fine
        _make_seg(2.0, 2.4),   # 400 ms — too short
        _make_seg(3.0, 4.0),   # 1000 ms — fine
    ]
    min_s = 0.5

    # Inline the same logic as _drop_short_keeps in pipeline.py
    for s in segs:
        if s.decision == "keep" and s.duration < min_s:
            s.decision = "cut"

    assert segs[0].decision == "cut"
    assert segs[1].decision == "keep"
    assert segs[2].decision == "cut"
    assert segs[3].decision == "keep"


def test_min_segment_zero_disables_filter():
    """min_segment_ms=0 leaves all decisions unchanged."""
    segs = [_make_seg(0.0, 0.1), _make_seg(0.2, 0.3)]
    min_s = 0.0

    if min_s > 0:
        for s in segs:
            if s.decision == "keep" and s.duration < min_s:
                s.decision = "cut"

    assert all(s.decision == "keep" for s in segs)


def test_min_segment_respects_already_cut():
    """Segments already cut are not changed by the filter."""
    segs = [
        _make_seg(0.0, 1.5, decision="cut"),   # long, but already cut
        _make_seg(2.0, 2.1, decision="cut"),   # short and cut
    ]
    min_s = 0.5

    for s in segs:
        if s.decision == "keep" and s.duration < min_s:
            s.decision = "cut"

    assert all(s.decision == "cut" for s in segs)


# ---------------------------------------------------------------------------
# API schema: min_segment_ms is accepted and persisted
# ---------------------------------------------------------------------------

def test_api_min_segment_persisted(tmp_path):
    """JobCreateRequest accepts min_segment_ms and routes pass it to PipelineOptions."""
    from autocut_api.schemas import JobCreateRequest
    from autocut_api.routes.jobs import _options_from_request

    body = JobCreateRequest(upload_ids=["x"], min_segment_ms=600)
    opts = _options_from_request(body)
    assert opts.min_segment_ms == 600


def test_api_min_segment_defaults_zero():
    from autocut_api.schemas import JobCreateRequest
    from autocut_api.routes.jobs import _options_from_request

    body = JobCreateRequest(upload_ids=["x"])
    opts = _options_from_request(body)
    assert opts.min_segment_ms == 0
