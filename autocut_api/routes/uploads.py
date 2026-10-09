"""Chunked / resumable upload endpoints.

Protocol:
    POST   /uploads                       → create session, get upload_id + chunk_size
    PUT    /uploads/{id}/chunks/{index}   → store one chunk (raw body)
    GET    /uploads/{id}                  → status + indexes already received
    POST   /uploads/{id}/complete         → assemble chunks into final file

Clients detect gaps via GET and re-PUT only the missing indexes. Chunks are
written to ``{data_dir}/uploads/{id}/chunks/{index:04d}.bin``; the final file
is assembled at ``{data_dir}/uploads/{id}/{filename}``.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from autocut_api.config import ApiConfig
from autocut_api.dependencies import get_config, get_db
from autocut_api.models import Upload
from autocut_api.schemas import (
    ChunkAck,
    CompleteResponse,
    CreateUploadRequest,
    UploadResponse,
)

router = APIRouter(prefix="/uploads", tags=["uploads"])

_CHUNK_FILENAME = "{:04d}.bin"


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _upload_dir(cfg: ApiConfig, upload_id: str) -> Path:
    return cfg.uploads_dir / upload_id


def _chunks_dir(cfg: ApiConfig, upload_id: str) -> Path:
    return _upload_dir(cfg, upload_id) / "chunks"


def _received_indexes(cfg: ApiConfig, upload_id: str) -> list[int]:
    d = _chunks_dir(cfg, upload_id)
    if not d.exists():
        return []
    out: list[int] = []
    for p in d.iterdir():
        if not p.is_file() or not p.name.endswith(".bin"):
            continue
        try:
            out.append(int(p.stem))
        except ValueError:
            continue
    out.sort()
    return out


def _response(upload: Upload, received: list[int]) -> UploadResponse:
    return UploadResponse(
        id=upload.id,
        filename=upload.filename,
        total_size=upload.total_size,
        chunk_size=upload.chunk_size,
        total_chunks=upload.total_chunks,
        status=upload.status,
        received=received,
        final_path=upload.final_path,
    )


def _get_upload_or_404(session: Session, upload_id: str) -> Upload:
    upload = session.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return upload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def create_upload(
    body: CreateUploadRequest,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> UploadResponse:
    if body.total_size <= 0:
        raise HTTPException(status_code=422, detail="total_size must be positive")
    if not body.filename.strip():
        raise HTTPException(status_code=422, detail="filename required")
    # Reject path traversal: strip to basename to be safe.
    clean_name = Path(body.filename).name
    if not clean_name:
        raise HTTPException(status_code=422, detail="invalid filename")

    chunk_size = cfg.upload_chunk_size
    total_chunks = math.ceil(body.total_size / chunk_size)

    upload = Upload(
        filename=clean_name,
        total_size=body.total_size,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
        status="pending",
    )
    session.add(upload)
    session.commit()
    session.refresh(upload)

    _chunks_dir(cfg, upload.id).mkdir(parents=True, exist_ok=True)
    return _response(upload, received=[])


@router.get("/{upload_id}", response_model=UploadResponse)
def get_upload(
    upload_id: str,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> UploadResponse:
    upload = _get_upload_or_404(session, upload_id)
    return _response(upload, _received_indexes(cfg, upload_id))


@router.put("/{upload_id}/chunks/{index}", response_model=ChunkAck)
async def put_chunk(
    upload_id: str,
    index: int,
    request: Request,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> ChunkAck:
    upload = _get_upload_or_404(session, upload_id)

    if upload.status == "complete":
        raise HTTPException(status_code=409, detail="upload already completed")
    if index < 0 or index >= upload.total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"chunk index {index} out of range [0, {upload.total_chunks})",
        )

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty chunk body")

    is_last = index == upload.total_chunks - 1
    if is_last:
        # Last chunk fills whatever bytes remain; must not exceed chunk_size.
        if len(body) > upload.chunk_size:
            raise HTTPException(
                status_code=400,
                detail=f"last chunk size {len(body)} exceeds chunk_size {upload.chunk_size}",
            )
    else:
        if len(body) != upload.chunk_size:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"chunk {index} size {len(body)} does not match "
                    f"chunk_size {upload.chunk_size}"
                ),
            )

    chunk_path = _chunks_dir(cfg, upload_id) / _CHUNK_FILENAME.format(index)
    # Overwriting an already-received chunk is intentional — supports client
    # retry without special handling.
    chunk_path.write_bytes(body)

    if upload.status == "pending":
        upload.status = "uploading"
        session.commit()

    received = _received_indexes(cfg, upload_id)
    return ChunkAck(
        id=upload_id,
        chunk_index=index,
        received=received,
        total_received=len(received),
        status=upload.status,
    )


@router.post("/{upload_id}/complete", response_model=CompleteResponse)
def complete_upload(
    upload_id: str,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> CompleteResponse:
    upload = _get_upload_or_404(session, upload_id)
    if upload.status == "complete" and upload.final_path:
        return CompleteResponse(
            id=upload.id, status=upload.status,
            final_path=upload.final_path, total_size=upload.total_size,
        )

    received = _received_indexes(cfg, upload_id)
    missing = sorted(set(range(upload.total_chunks)) - set(received))
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing chunks: {missing[:10]}{'...' if len(missing) > 10 else ''}",
        )

    final_path = _upload_dir(cfg, upload_id) / upload.filename
    chunks_dir = _chunks_dir(cfg, upload_id)
    with final_path.open("wb") as out:
        for i in range(upload.total_chunks):
            chunk = chunks_dir / _CHUNK_FILENAME.format(i)
            with chunk.open("rb") as src:
                shutil.copyfileobj(src, out)

    actual = final_path.stat().st_size
    if actual != upload.total_size:
        upload.status = "failed"
        session.commit()
        raise HTTPException(
            status_code=400,
            detail=(
                f"assembled size {actual} does not match declared total_size "
                f"{upload.total_size}"
            ),
        )

    upload.status = "complete"
    upload.final_path = str(final_path)
    session.commit()

    # Chunk files are now redundant; free the space.
    shutil.rmtree(chunks_dir, ignore_errors=True)

    return CompleteResponse(
        id=upload.id, status=upload.status,
        final_path=upload.final_path, total_size=upload.total_size,
    )
