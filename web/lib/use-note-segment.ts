"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { patchSegment } from "./api";
import type { Edl } from "./api.types";

export function useNoteSegment(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({ index, notes }: { index: number; notes: string }) =>
      patchSegment(jobId, index, { notes }, preset),

    onMutate: async ({ index, notes }) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Edl>(key);
      if (prev) {
        qc.setQueryData<Edl>(key, {
          ...prev,
          segments: prev.segments.map((s, i) =>
            i === index ? { ...s, notes } : s,
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
