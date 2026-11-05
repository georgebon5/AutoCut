"""Outbound webhook notifications fired when a job reaches a terminal state.

Webhooks let external systems (Zapier, Make, Slack, custom scripts) react to
job completion without polling.  The call is non-blocking: a daemon thread
fires the POST so the pipeline thread is never held up by a slow endpoint.

Retry policy: one immediate attempt + one retry after 2 s.  Client errors
(4xx) are not retried — they indicate a misconfigured URL.  After two failed
attempts the error is logged and silently dropped; the job itself is unaffected.

Payload (JSON):
  {
    "job_id":   "...",
    "status":   "done" | "failed",
    "error":    null | "error message",
    "timestamp": "2024-01-01T12:00:00Z"   # UTC ISO-8601
  }
"""

from __future__ import annotations

import datetime
import logging
import threading
import time

import httpx

log = logging.getLogger("autocut_api.webhook")

_TIMEOUT_S = 10
_RETRY_DELAY_S = 2


def _post(url: str, payload: dict) -> None:
    for attempt in range(2):
        try:
            resp = httpx.post(url, json=payload, timeout=_TIMEOUT_S)
            if resp.is_success:
                log.info("webhook %s → %s", url, resp.status_code)
                return
            if 400 <= resp.status_code < 500:
                log.warning("webhook %s rejected with %s — not retrying", url, resp.status_code)
                return
            log.warning("webhook %s got %s on attempt %d", url, resp.status_code, attempt + 1)
        except httpx.RequestError as exc:
            log.warning("webhook %s network error on attempt %d: %s", url, attempt + 1, exc)
        if attempt == 0:
            time.sleep(_RETRY_DELAY_S)
    log.error("webhook %s failed after %d attempts", url, 2)


def fire(url: str, job_id: str, status: str, error: str | None = None) -> None:
    """POST a completion notification to ``url`` in a background daemon thread.

    Returns immediately — the HTTP call happens asynchronously so callers are
    never blocked.  Safe to call with ``url=""`` or ``url=None``; both are no-ops.
    """
    if not url:
        return
    payload = {
        "job_id": job_id,
        "status": status,
        "error": error,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    threading.Thread(target=_post, args=(url, payload), daemon=True, name=f"webhook-{job_id}").start()
