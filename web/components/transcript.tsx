"use client";

import { useMemo } from "react";

import { ApiError } from "@/lib/api";
import { useSegmentToggle } from "@/lib/use-segment-toggle";
import type { Decision, Edl, SegmentDict, Transcript as TranscriptT, WordTimestamp } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  edl: Edl;
  /** Index into edl.segments of the currently-selected segment (optional). */
  selectedIndex?: number | null;
  onSelect?: (index: number) => void;
}

/** O(words + segments·log) assignment of each word to the segment that covers it. */
function buildWordToSegment(
  edl: Edl,
): Array<{ segIndex: number; word: WordTimestamp; clipId: string }> {
  // Per-clip, sort segment indexes once; binary-search per word.
  const byClip = new Map<string, { index: number; seg: SegmentDict }[]>();
  edl.segments.forEach((seg, index) => {
    const b = byClip.get(seg.clip_id) ?? [];
    b.push({ index, seg });
    byClip.set(seg.clip_id, b);
  });
  for (const list of byClip.values()) {
    list.sort((a, b) => a.seg.start - b.seg.start);
  }

  const out: Array<{ segIndex: number; word: WordTimestamp; clipId: string }> = [];
  for (const t of edl.transcripts) {
    const segs = byClip.get(t.clip_id) ?? [];
    for (const w of t.words) {
      const segIndex = findContainingSegment(segs, w.start);
      if (segIndex < 0) continue;    // transcript word outside any segment: skip
      out.push({ segIndex, word: w, clipId: t.clip_id });
    }
  }
  return out;
}

function findContainingSegment(
  segs: { index: number; seg: SegmentDict }[],
  t: number,
): number {
  // Linear scan is fine for a few hundred segments. Switch to binary search
  // if transcripts grow much larger.
  for (const { index, seg } of segs) {
    if (t >= seg.start && t <= seg.end) return index;
  }
  return -1;
}


export default function Transcript({ jobId, preset, edl, selectedIndex, onSelect }: Props) {
  const toggleMut = useSegmentToggle(jobId, preset);

  // Group assignments by clip for display, preserving transcript word order.
  const groupedByClip = useMemo(() => {
    const assignments = buildWordToSegment(edl);
    const map = new Map<string, typeof assignments>();
    for (const a of assignments) {
      const b = map.get(a.clipId) ?? [];
      b.push(a);
      map.set(a.clipId, b);
    }
    return map;
  }, [edl]);

  const hasAnyWords = edl.transcripts.some((t) => t.words.length > 0);
  if (!hasAnyWords) {
    return (
      <p className="text-xs text-gray-500">
        No transcript available for this cut.
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-medium uppercase tracking-wide text-gray-400">
        Transcript · tap any word to toggle its segment
      </h2>

      <div className="flex max-h-80 flex-col gap-4 overflow-y-auto rounded-lg border border-gray-800 bg-gray-950 p-4">
        {edl.transcripts.map((t) => (
          <ClipTranscript
            key={t.clip_id}
            transcript={t}
            segments={edl.segments}
            assignments={groupedByClip.get(t.clip_id) ?? []}
            selectedIndex={selectedIndex ?? null}
            onSelect={(i) => {
              onSelect?.(i);
              const seg = edl.segments[i];
              if (seg) {
                toggleMut.mutate({
                  index: i,
                  decision: flip(seg.decision),
                });
              }
            }}
          />
        ))}
      </div>

      {toggleMut.isError ? (
        <p className="text-sm text-red-400">
          {(toggleMut.error as ApiError | Error).message}
        </p>
      ) : null}
    </section>
  );
}


function ClipTranscript({
  transcript,
  segments,
  assignments,
  selectedIndex,
  onSelect,
}: {
  transcript: TranscriptT;
  segments: SegmentDict[];
  assignments: Array<{ segIndex: number; word: WordTimestamp; clipId: string }>;
  selectedIndex: number | null;
  onSelect: (segIndex: number) => void;
}) {
  if (assignments.length === 0) return null;

  return (
    <div>
      <div className="mb-1 text-xs text-gray-500">
        {transcript.clip_id} · {transcript.language}
      </div>
      <p className="text-sm leading-relaxed">
        {assignments.map((a, i) => {
          const seg = segments[a.segIndex];
          if (!seg) return null;
          const keep = seg.decision === "keep";
          const active = selectedIndex === a.segIndex;
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(a.segIndex)}
              className={[
                "inline rounded px-0.5 transition",
                keep
                  ? "text-gray-100 hover:bg-emerald-500/20"
                  : "text-gray-500 line-through hover:bg-gray-700/60",
                active ? "ring-1 ring-sky-400" : "",
              ].join(" ")}
            >
              {a.word.word.trim()}
            </button>
          );
        }).reduce<React.ReactNode[]>((acc, el, i) => {
          if (i > 0) acc.push(" ");
          acc.push(el);
          return acc;
        }, [])}
      </p>
    </div>
  );
}


function flip(d: Decision): Decision {
  return d === "keep" ? "cut" : "keep";
}
