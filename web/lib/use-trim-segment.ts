"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { patchSegment } from "./api";
import type { Edl } from "./api.types";

/**
 * Persist a boundary trim (start and/or end) for one segment.
 * Performs an optimistic update on the EDL cache so the timeline redraws
 * instantly; rolls back on error.
 */
export function useTrimSegment(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({
      index,
      start,
      end,
    }: {
      index: number;
      start?: number;
      end?: number;
    }) => patchSegment(jobId, index, { start, end }, preset),

    onMutate: async ({ index, start, end }) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Edl>(key);
      if (prev) {
        qc.setQueryData<Edl>(key, {
          ...prev,
          segments: prev.segments.map((s, i) => {
            if (i !== index) return s;
            const ns = start ?? s.start;
            const ne = end ?? s.end;
            return { ...s, start: ns, end: ne };
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
