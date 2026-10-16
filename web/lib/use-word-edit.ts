"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { patchWord } from "./api";
import type { Edl } from "./api.types";

/**
 * Edit a transcript word's text. Optimistically patches the cached EDL so
 * the transcript view reflects the correction immediately; captions on next
 * re-render pick up the new text.
 */
export function useWordEdit(jobId: string, preset: string | undefined) {
  const qc = useQueryClient();
  const key = ["edl", jobId, preset ?? null] as const;

  return useMutation({
    mutationFn: ({ clipId, wordIndex, word }: {
      clipId: string; wordIndex: number; word: string;
    }) => patchWord(jobId, clipId, wordIndex, word, preset),

    onMutate: async ({ clipId, wordIndex, word }) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<Edl>(key);
      if (prev) {
        qc.setQueryData<Edl>(key, {
          ...prev,
          transcripts: prev.transcripts.map((t) =>
            t.clip_id === clipId
              ? {
                  ...t,
                  words: t.words.map((w, i) =>
                    i === wordIndex ? { ...w, word } : w,
                  ),
                }
              : t,
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
