"use client";

import { useEffect } from "react";

import { jobEventsUrl } from "./api";

/**
 * Subscribe to SSE for a single job. Calls `onUpdate` on every state change
 * and `onDone` when the job reaches a terminal state.
 *
 * The EventSource is opened only when `active` is true (job is pending or
 * running) and automatically closed on cleanup or when `active` becomes false.
 */
export function useJobEvents(
  jobId: string,
  active: boolean,
  onUpdate: () => void,
  onDone: () => void,
): void {
  useEffect(() => {
    if (!active) return;

    const es = new EventSource(jobEventsUrl(jobId));

    es.onmessage = () => onUpdate();

    es.addEventListener("done", () => {
      onDone();
      es.close();
    });

    es.onerror = () => {
      // On network error or server restart, close cleanly. The calling
      // component falls back to its own polling if any.
      es.close();
    };

    return () => es.close();
  // Re-subscribe if the job ID or active flag changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, active]);
}
