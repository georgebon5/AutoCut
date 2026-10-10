"""Output listing + download endpoints.

Only files produced by the pipeline are exposed; proxies, extracted WAVs and
the SQLite DB stay internal. The whitelist keeps that boundary explicit — any
new output artifact must opt in by matching a pattern below.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from autocut_api.dependencies import get_db
from autocut_api.models import Job
from autocut_api.schemas import OutputFile, OutputsListResponse

router = APIRouter(prefix="/jobs", tags=["outputs"])

# Patterns and the classification label returned to clients.
_OUTPUT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("rough_cut*.mp4", "video"),
    ("edl*.json", "edl"),
    ("*.srt", "captions_srt"),
    ("*.ass", "captions_ass"),
)

_MEDIA_TYPES = {
    "video": "video/mp4",
    "edl": "application/json",
    "captions_srt": "application/x-subrip",
    "captions_ass": "text/plain; charset=utf-8",
}


def _classify(name: str) -> str | None:
    """Return the output kind for a filename, or None if not whitelisted."""
    p = Path(name)
    for pattern, kind in _OUTPUT_PATTERNS:
        if p.match(pattern):
            return kind
    return None


def _get_done_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"job not finished (status={job.status})",
        )
    if not job.workspace:
        raise HTTPException(status_code=409, detail="job has no workspace")
    return job


@router.get("/{job_id}/outputs", response_model=OutputsListResponse)
def list_outputs(
    job_id: str,
    session: Session = Depends(get_db),
) -> OutputsListResponse:
    job = _get_done_job(session, job_id)
    workspace = Path(job.workspace)

    seen: dict[str, OutputFile] = {}
    for pattern, kind in _OUTPUT_PATTERNS:
        for path in workspace.glob(pattern):
            if not path.is_file() or path.name in seen:
                continue
            seen[path.name] = OutputFile(
                name=path.name,
                size=path.stat().st_size,
                kind=kind,
            )
    outputs = sorted(seen.values(), key=lambda o: (o.kind, o.name))
    return OutputsListResponse(outputs=outputs)


@router.get("/{job_id}/outputs/{filename}")
def download_output(
    job_id: str,
    filename: str,
    session: Session = Depends(get_db),
) -> FileResponse:
    # Reject path traversal: anything but a plain basename is rejected.
    if filename != Path(filename).name or filename.startswith("."):
        raise HTTPException(status_code=400, detail="invalid filename")
    kind = _classify(filename)
    if kind is None:
        raise HTTPException(status_code=400, detail="file not downloadable")

    job = _get_done_job(session, job_id)
    path = Path(job.workspace) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="output not found")

    return FileResponse(
        path=path,
        media_type=_MEDIA_TYPES[kind],
        filename=filename,
    )
