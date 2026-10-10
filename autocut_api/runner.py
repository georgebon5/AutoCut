"""Background job runner — thin wrapper around ThreadPoolExecutor.

The runner lives on ``app.state.runner`` and is shut down by the FastAPI
lifespan context. We bound concurrency because the pipeline is CPU/GPU-heavy
(ffmpeg, whisper, opencv) — running too many in parallel hurts throughput.
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Callable

log = logging.getLogger("autocut_api.runner")


class JobRunner:
    def __init__(self, max_workers: int = 2) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="autocut-job",
        )

    def submit(self, fn: Callable[[], None]) -> None:
        future = self._executor.submit(fn)
        future.add_done_callback(self._log_exceptions)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    @staticmethod
    def _log_exceptions(future: concurrent.futures.Future) -> None:
        """Surface background exceptions in the server log — otherwise they
        would be swallowed by the executor and only observable via a failed
        job state."""
        try:
            future.result()
        except Exception:   # noqa: BLE001 — intentional broad catch, logged
            log.exception("job runner task raised")
