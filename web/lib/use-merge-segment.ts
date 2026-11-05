"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { mergeSegments } from "./api";

/**
 * Merge segment `index` with segment `index+1` into one segment.
 * Merges shift all subsequent indices down by 1, so we invalidate the
 * full EDL cache rather than doing an optimistic update.
 */
export function useMergeSegment(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({ index }: { index: number }) =>
      mergeSegments(jobId, index, preset),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
}
