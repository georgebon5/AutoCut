"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { patchSegment } from "./api";
import type { Decision, Edl } from "./api.types";

/**
 * Shared optimistic toggle used by both the Timeline and Transcript views.
 * They operate on the same EDL cache entry, so swapping between tap targets
 * stays in sync without extra wiring.
 */
export function useSegmentToggle(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({ index, decision }: { index: number; decision: Decision }) =>
      patchSegment(jobId, index, { decision }, preset),

    onMutate: async ({ index, decision }) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Edl>(key);
      if (prev) {
        qc.setQueryData<Edl>(key, {
          ...prev,
          segments: prev.segments.map((s, i) =>
            i === index ? { ...s, decision, decision_source: "user" } : s,
          ),
        });
      }
      return { prev };
    },
    onError: (_e, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
}
