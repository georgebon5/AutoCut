"""Tests for the chunked / resumable upload endpoints."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocut_api.config import ApiConfig
from autocut_api.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SMALL_CHUNK = 1024   # 1 KB — keeps tests fast and easy to reason about


def _cfg(tmp_path: Path, chunk_size: int = SMALL_CHUNK) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_chunk_size=chunk_size,
    )


@pytest.fixture
def client_cfg(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    return TestClient(app), cfg


def _create_upload(client: TestClient, filename: str, total_size: int) -> dict:
    r = client.post("/uploads", json={"filename": filename, "total_size": total_size})
    assert r.status_code == 201, r.text
    return r.json()


def _put_chunk(client: TestClient, upload_id: str, index: int, data: bytes) -> dict:
    r = client.put(f"/uploads/{upload_id}/chunks/{index}", content=data)
    assert r.status_code == 200, r.text
    return r.json()


def _split(data: bytes, chunk_size: int) -> list[bytes]:
    return [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]


# ---------------------------------------------------------------------------
# Create session
# ---------------------------------------------------------------------------

def test_create_returns_upload_id_and_chunk_count(client_cfg):
    client, _ = client_cfg
    data = _create_upload(client, "clip.mp4", total_size=SMALL_CHUNK * 3)
    assert data["id"]
    assert data["filename"] == "clip.mp4"
    assert data["chunk_size"] == SMALL_CHUNK
    assert data["total_chunks"] == 3
    assert data["status"] == "pending"
    assert data["received"] == []


def test_create_rounds_up_total_chunks_for_partial_last(client_cfg):
    client, _ = client_cfg
    # 2.5 chunks → should round up to 3 chunks.
    data = _create_upload(client, "x.mp4", total_size=SMALL_CHUNK * 2 + 100)
    assert data["total_chunks"] == 3


def test_create_rejects_zero_size(client_cfg):
    client, _ = client_cfg
    r = client.post("/uploads", json={"filename": "a.mp4", "total_size": 0})
    assert r.status_code == 422


def test_create_strips_path_traversal_from_filename(client_cfg):
    client, _ = client_cfg
    data = _create_upload(client, "../../etc/passwd", total_size=SMALL_CHUNK)
    assert data["filename"] == "passwd"


# ---------------------------------------------------------------------------
# Single-chunk round-trip
# ---------------------------------------------------------------------------

def test_single_chunk_upload_then_complete(client_cfg):
    client, cfg = client_cfg
    payload = os.urandom(SMALL_CHUNK // 2)      # smaller than chunk_size → one partial chunk
    created = _create_upload(client, "a.bin", total_size=len(payload))
    assert created["total_chunks"] == 1

    ack = _put_chunk(client, created["id"], 0, payload)
    assert ack["received"] == [0]
    assert ack["status"] == "uploading"

    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete"
    final = Path(body["final_path"])
    assert final.read_bytes() == payload


# ---------------------------------------------------------------------------
# Multi-chunk assembly
# ---------------------------------------------------------------------------

def test_multi_chunk_upload_assembles_byte_identical(client_cfg):
    client, cfg = client_cfg
    payload = os.urandom(SMALL_CHUNK * 3 + 123)   # 4 chunks, last partial
    created = _create_upload(client, "clip.mp4", total_size=len(payload))
    chunks = _split(payload, SMALL_CHUNK)
    assert len(chunks) == created["total_chunks"]

    for i, c in enumerate(chunks):
        _put_chunk(client, created["id"], i, c)

    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 200
    final = Path(r.json()["final_path"])
    assert final.read_bytes() == payload


def test_chunks_dir_removed_after_complete(client_cfg):
    client, cfg = client_cfg
    payload = os.urandom(SMALL_CHUNK + 10)
    created = _create_upload(client, "a.bin", total_size=len(payload))
    for i, c in enumerate(_split(payload, SMALL_CHUNK)):
        _put_chunk(client, created["id"], i, c)
    client.post(f"/uploads/{created['id']}/complete")
    assert not (cfg.uploads_dir / created["id"] / "chunks").exists()


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def test_get_status_reflects_received_chunks(client_cfg):
    client, _ = client_cfg
    payload = os.urandom(SMALL_CHUNK * 4)
    created = _create_upload(client, "r.bin", total_size=len(payload))
    chunks = _split(payload, SMALL_CHUNK)
    _put_chunk(client, created["id"], 0, chunks[0])
    _put_chunk(client, created["id"], 2, chunks[2])

    r = client.get(f"/uploads/{created['id']}")
    assert r.status_code == 200
    status = r.json()
    assert status["received"] == [0, 2]
    assert status["status"] == "uploading"


def test_resume_uploads_remaining_chunks(client_cfg):
    client, cfg = client_cfg
    payload = os.urandom(SMALL_CHUNK * 4)
    created = _create_upload(client, "r.bin", total_size=len(payload))
    chunks = _split(payload, SMALL_CHUNK)

    # First pass: upload chunks 0, 2.
    _put_chunk(client, created["id"], 0, chunks[0])
    _put_chunk(client, created["id"], 2, chunks[2])

    # Complete should fail — missing 1 and 3.
    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 400
    assert "missing" in r.json()["detail"].lower()

    # Resume: upload the missing indexes.
    _put_chunk(client, created["id"], 1, chunks[1])
    _put_chunk(client, created["id"], 3, chunks[3])
    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 200
    assert Path(r.json()["final_path"]).read_bytes() == payload


def test_idempotent_rechunk_overwrites(client_cfg):
    """Re-sending a chunk should overwrite (client retries after network blip)."""
    client, _ = client_cfg
    payload = os.urandom(SMALL_CHUNK * 2)
    created = _create_upload(client, "r.bin", total_size=len(payload))
    chunks = _split(payload, SMALL_CHUNK)
    _put_chunk(client, created["id"], 0, chunks[0])
    _put_chunk(client, created["id"], 0, chunks[0])    # re-send — must not break
    _put_chunk(client, created["id"], 1, chunks[1])
    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_get_unknown_upload_returns_404(client_cfg):
    client, _ = client_cfg
    r = client.get("/uploads/does-not-exist")
    assert r.status_code == 404


def test_chunk_index_out_of_range_returns_400(client_cfg):
    client, _ = client_cfg
    created = _create_upload(client, "a.bin", total_size=SMALL_CHUNK)   # 1 chunk
    r = client.put(f"/uploads/{created['id']}/chunks/5", content=b"x")
    assert r.status_code == 400
    assert "out of range" in r.json()["detail"]


def test_negative_chunk_index_returns_400(client_cfg):
    client, _ = client_cfg
    created = _create_upload(client, "a.bin", total_size=SMALL_CHUNK)
    r = client.put(f"/uploads/{created['id']}/chunks/-1", content=b"x")
    # FastAPI parses -1 as a path int; it reaches our handler and we 400 it.
    assert r.status_code == 400


def test_non_last_chunk_wrong_size_returns_400(client_cfg):
    client, _ = client_cfg
    created = _create_upload(client, "a.bin", total_size=SMALL_CHUNK * 2)
    r = client.put(f"/uploads/{created['id']}/chunks/0", content=b"too-short")
    assert r.status_code == 400
    assert "match" in r.json()["detail"]


def test_last_chunk_can_be_smaller_than_chunk_size(client_cfg):
    client, _ = client_cfg
    payload = os.urandom(SMALL_CHUNK + 500)
    created = _create_upload(client, "a.bin", total_size=len(payload))
    _put_chunk(client, created["id"], 0, payload[:SMALL_CHUNK])
    _put_chunk(client, created["id"], 1, payload[SMALL_CHUNK:])   # 500 bytes
    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 200


def test_empty_chunk_body_returns_400(client_cfg):
    client, _ = client_cfg
    created = _create_upload(client, "a.bin", total_size=SMALL_CHUNK)
    r = client.put(f"/uploads/{created['id']}/chunks/0", content=b"")
    assert r.status_code == 400


def test_complete_without_all_chunks_returns_400(client_cfg):
    client, _ = client_cfg
    created = _create_upload(client, "a.bin", total_size=SMALL_CHUNK * 2)
    _put_chunk(client, created["id"], 0, os.urandom(SMALL_CHUNK))
    r = client.post(f"/uploads/{created['id']}/complete")
    assert r.status_code == 400


def test_chunk_upload_rejected_after_complete(client_cfg):
    client, _ = client_cfg
    payload = os.urandom(SMALL_CHUNK)
    created = _create_upload(client, "a.bin", total_size=len(payload))
    _put_chunk(client, created["id"], 0, payload)
    client.post(f"/uploads/{created['id']}/complete")
    r = client.put(f"/uploads/{created['id']}/chunks/0", content=payload)
    assert r.status_code == 409


def test_complete_is_idempotent(client_cfg):
    client, _ = client_cfg
    payload = os.urandom(SMALL_CHUNK)
    created = _create_upload(client, "a.bin", total_size=len(payload))
    _put_chunk(client, created["id"], 0, payload)
    r1 = client.post(f"/uploads/{created['id']}/complete")
    r2 = client.post(f"/uploads/{created['id']}/complete")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["final_path"] == r2.json()["final_path"]


# ---------------------------------------------------------------------------
# OpenAPI exposes the routes
# ---------------------------------------------------------------------------

def test_openapi_includes_upload_routes(client_cfg):
    client, _ = client_cfg
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/uploads" in paths
    # path parameter format uses literal braces in OpenAPI
    assert "/uploads/{upload_id}" in paths
    assert "/uploads/{upload_id}/chunks/{index}" in paths
    assert "/uploads/{upload_id}/complete" in paths
