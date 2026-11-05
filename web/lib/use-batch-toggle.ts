"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { batchPatchSegments } from "./api";
import type { Decision, Edl } from "./api.types";

/**
 * Applies multiple keep/cut overrides in a single PATCH call.
 * Used for bulk operations: Keep All, Cut All, Invert.
 * Optimistic update mirrors the approach in use-segment-toggle.ts.
 */
export function useBatchToggle(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: (updates: Array<{ index: number; decision: Decision }>) =>
      batchPatchSegments(jobId, updates, preset),

    onMutate: async (updates) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Edl>(key);
      if (prev) {
        const updateMap = new Map(updates.map((u) => [u.index, u.decision]));
        qc.setQueryData<Edl>(key, {
          ...prev,
          segments: prev.segments.map((s, i) => {
            const d = updateMap.get(i);
            return d !== undefined ? { ...s, decision: d, decision_source: "user" } : s;
          }),
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
