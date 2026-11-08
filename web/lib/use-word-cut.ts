"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { cutWord } from "./api";

export function useWordCut(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({ clipId, start, end }: {
      clipId: string; start: number; end: number;
    }) => cutWord(jobId, clipId, start, end, preset),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });
}
