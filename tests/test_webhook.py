"""Tests for autocut_api.webhook — outbound job-completion notifications."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from autocut_api.webhook import fire, _post


# ---------------------------------------------------------------------------
# _post unit tests (synchronous, no threads)
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300


def _mock_post(status_code: int):
    return MagicMock(return_value=FakeResponse(status_code))


def test_post_success_no_retry():
    with patch("autocut_api.webhook.httpx.post", _mock_post(200)) as p:
        _post("https://example.com/hook", {"job_id": "abc"})
    assert p.call_count == 1


def test_post_client_error_no_retry():
    """4xx responses are not retried."""
    with patch("autocut_api.webhook.httpx.post", _mock_post(404)) as p:
        _post("https://example.com/hook", {})
    assert p.call_count == 1


def test_post_server_error_retries_once():
    """5xx triggers one retry."""
    with patch("autocut_api.webhook.httpx.post", _mock_post(500)) as p, \
         patch("autocut_api.webhook.time.sleep"):
        _post("https://example.com/hook", {})
    assert p.call_count == 2


def test_post_server_error_then_success():
    """Second attempt succeeds after a 5xx first attempt."""
    responses = [FakeResponse(503), FakeResponse(200)]
    mock = MagicMock(side_effect=responses)
    with patch("autocut_api.webhook.httpx.post", mock), \
         patch("autocut_api.webhook.time.sleep"):
        _post("https://example.com/hook", {})
    assert mock.call_count == 2


def test_post_network_error_retries():
    import httpx
    mock = MagicMock(side_effect=httpx.RequestError("timeout"))
    with patch("autocut_api.webhook.httpx.post", mock), \
         patch("autocut_api.webhook.time.sleep"):
        _post("https://example.com/hook", {})
    assert mock.call_count == 2


def test_post_sends_json_payload():
    payload = {"job_id": "xyz", "status": "done"}
    with patch("autocut_api.webhook.httpx.post", _mock_post(200)) as p:
        _post("https://example.com/hook", payload)
    _, kwargs = p.call_args
    assert kwargs["json"] == payload


def test_post_timeout_param():
    with patch("autocut_api.webhook.httpx.post", _mock_post(200)) as p:
        _post("https://example.com/hook", {})
    _, kwargs = p.call_args
    assert kwargs["timeout"] == 10


# ---------------------------------------------------------------------------
# fire() — high-level async wrapper
# ---------------------------------------------------------------------------

def test_fire_empty_url_is_noop():
    with patch("autocut_api.webhook.httpx.post") as p:
        fire("", "job1", "done")
    p.assert_not_called()


def test_fire_none_url_is_noop():
    with patch("autocut_api.webhook.httpx.post") as p:
        fire(None, "job1", "done")  # type: ignore[arg-type]
    p.assert_not_called()


def test_fire_spawns_daemon_thread():
    barrier = threading.Barrier(2, timeout=3)

    def fake_post(url, **kwargs):
        barrier.wait()
        return FakeResponse(200)

    with patch("autocut_api.webhook.httpx.post", fake_post):
        fire("https://example.com/hook", "job1", "done")
        barrier.wait()   # unblocks once thread has entered _post


def test_fire_payload_contains_required_fields():
    captured: list[dict] = []

    def fake_post(url, json, **kwargs):  # noqa: A002
        captured.append(json)
        return FakeResponse(200)

    with patch("autocut_api.webhook.httpx.post", fake_post):
        fire("https://example.com/hook", "job42", "failed", "oops")

    # Wait for daemon thread
    time.sleep(0.1)
    assert len(captured) == 1
    p = captured[0]
    assert p["job_id"] == "job42"
    assert p["status"] == "failed"
    assert p["error"] == "oops"
    assert "timestamp" in p


def test_fire_done_has_null_error_by_default():
    captured: list[dict] = []

    def fake_post(url, json, **kwargs):  # noqa: A002
        captured.append(json)
        return FakeResponse(200)

    with patch("autocut_api.webhook.httpx.post", fake_post):
        fire("https://example.com/hook", "j1", "done")

    time.sleep(0.1)
    assert captured[0]["error"] is None


def test_fire_returns_immediately():
    """fire() must not block even if the HTTP call is slow."""
    slow_barrier = threading.Barrier(2, timeout=3)

    def slow_post(url, **kwargs):
        slow_barrier.wait()
        return FakeResponse(200)

    start = time.monotonic()
    with patch("autocut_api.webhook.httpx.post", slow_post):
        fire("https://example.com/hook", "j1", "done")
    elapsed = time.monotonic() - start
    # Should return before the barrier is hit (< 50 ms)
    assert elapsed < 0.05
    slow_barrier.wait()   # clean up thread
