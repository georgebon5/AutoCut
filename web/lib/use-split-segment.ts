"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { splitSegment } from "./api";

/**
 * Split a segment into two halves at a given source timestamp.
 * Splits shift all subsequent indices, so we invalidate the full EDL
 * rather than doing an optimistic update.
 */
export function useSplitSegment(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({ index, at }: { index: number; at: number }) =>
      splitSegment(jobId, index, at, preset),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
}
