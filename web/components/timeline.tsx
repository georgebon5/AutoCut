"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { ApiError, patchSegment } from "@/lib/api";
import type { Clip, Decision, Edl, SegmentDict } from "@/lib/api.types";

// 50 px / second → a 10 s clip is 500 px wide. Horizontal scroll handles long
// clips; proportionality lets the user eyeball cut lengths at a glance.
const PX_PER_SECOND = 50;
const MIN_BLOCK_PX = 6;    // keep tiny segments tappable
const ROW_HEIGHT_PX = 56;


interface Props {
  jobId: string;
  preset: string | undefined;
  edl: Edl;
}

export default function Timeline({ jobId, preset, edl }: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);

  // Group segments by clip for row layout; preserve original EDL index so we
  // can PATCH by it later.
  const bySegmentIndex = useMemo(() => {
    const map = new Map<string, { seg: SegmentDict; index: number }[]>();
    edl.segments.forEach((seg, index) => {
      const bucket = map.get(seg.clip_id) ?? [];
      bucket.push({ seg, index });
      map.set(seg.clip_id, bucket);
    });
    return map;
  }, [edl.segments]);

  const toggleMut = useMutation({
    mutationFn: ({ index, decision }: { index: number; decision: Decision }) =>
      patchSegment(jobId, index, { decision }, preset),

    // Optimistic: flip immediately, roll back on failure.
    onMutate: async ({ index, decision }) => {
      await qc.cancelQueries({ queryKey: ["edl", jobId, preset ?? null] });
      const prev = qc.getQueryData<Edl>(["edl", jobId, preset ?? null]);
      if (prev) {
        qc.setQueryData<Edl>(["edl", jobId, preset ?? null], {
          ...prev,
          segments: prev.segments.map((s, i) =>
            i === index ? { ...s, decision, decision_source: "user" } : s,
          ),
        });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) {
        qc.setQueryData(["edl", jobId, preset ?? null], ctx.prev);
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["edl", jobId, preset ?? null] }),
  });

  const stats = useMemo(() => summarise(edl.segments), [edl.segments]);
  const selectedSeg = selected !== null ? edl.segments[selected] ?? null : null;

  return (
    <section className="flex flex-col gap-4">
      <StatsBar stats={stats} />

      <div className="overflow-x-auto rounded-lg border border-gray-800 bg-gray-950">
        <div className="flex min-w-full flex-col divide-y divide-gray-800">
          {edl.clips.map((clip) => (
            <ClipRow
              key={clip.clip_id}
              clip={clip}
              items={bySegmentIndex.get(clip.clip_id) ?? []}
              selectedIndex={selected}
              onSelect={setSelected}
              onToggle={(index, decision) => toggleMut.mutate({ index, decision })}
            />
          ))}
        </div>
      </div>

      {toggleMut.isError ? (
        <p className="text-sm text-red-400">
          {(toggleMut.error as ApiError | Error).message}
        </p>
      ) : null}

      {selectedSeg ? (
        <SegmentDetail index={selected!} seg={selectedSeg} />
      ) : (
        <p className="text-xs text-gray-500">
          Tap a segment to toggle keep/cut and inspect its reasons.
        </p>
      )}
    </section>
  );
}


function ClipRow({
  clip,
  items,
  selectedIndex,
  onSelect,
  onToggle,
}: {
  clip: Clip;
  items: { seg: SegmentDict; index: number }[];
  selectedIndex: number | null;
  onSelect: (index: number) => void;
  onToggle: (index: number, next: Decision) => void;
}) {
  const widthPx = Math.max(120, Math.round(clip.duration * PX_PER_SECOND));

  return (
    <div className="flex gap-3 px-3 py-3">
      <div className="w-24 shrink-0 text-xs text-gray-400">
        <div className="truncate font-medium text-gray-200" title={clip.clip_id}>
          {clip.clip_id}
        </div>
        <div>{clip.duration.toFixed(1)}s</div>
      </div>
      <div
        className="relative"
        style={{ width: widthPx, height: ROW_HEIGHT_PX }}
      >
        {items.map(({ seg, index }) => {
          const left = Math.round(seg.start * PX_PER_SECOND);
          const w = Math.max(
            MIN_BLOCK_PX,
            Math.round((seg.end - seg.start) * PX_PER_SECOND),
          );
          const keep = seg.decision === "keep";
          const active = selectedIndex === index;
          const next: Decision = keep ? "cut" : "keep";
          return (
            <button
              key={index}
              type="button"
              onClick={() => {
                onSelect(index);
                onToggle(index, next);
              }}
              style={{ left, width: w, height: ROW_HEIGHT_PX }}
              title={`${seg.start.toFixed(2)}–${seg.end.toFixed(2)}s · ${seg.decision}`}
              className={[
                "absolute top-0 rounded border transition",
                keep
                  ? "bg-emerald-500/40 border-emerald-400 hover:bg-emerald-500/60"
                  : "border-dashed border-gray-600 bg-gray-800/40 hover:bg-gray-700/60",
                active ? "ring-2 ring-sky-400" : "",
              ].join(" ")}
            />
          );
        })}
      </div>
    </div>
  );
}


function SegmentDetail({ index, seg }: { index: number; seg: SegmentDict }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-gray-200">
          Segment #{index} · {seg.clip_id}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ${
            seg.decision === "keep"
              ? "bg-emerald-500/20 text-emerald-300"
              : "bg-gray-700/50 text-gray-300"
          }`}
        >
          {seg.decision} · {seg.decision_source}
        </span>
      </div>
      <div className="mt-2 text-xs text-gray-400">
        {seg.start.toFixed(2)}s → {seg.end.toFixed(2)}s · score {seg.interest_score.toFixed(2)}
      </div>
      {seg.reasons.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-2 text-xs">
          {seg.reasons.map((r, i) => (
            <li
              key={i}
              className="rounded bg-gray-800 px-2 py-0.5 text-gray-300"
            >
              {r}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}


function StatsBar({
  stats,
}: {
  stats: { keep: number; cut: number; keepSec: number; cutSec: number };
}) {
  const total = stats.keep + stats.cut;
  const totalSec = stats.keepSec + stats.cutSec;
  const pct = totalSec > 0 ? Math.round((stats.keepSec / totalSec) * 100) : 0;
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-gray-400">
      <span className="text-gray-200">
        {stats.keep} keep · {stats.cut} cut
      </span>
      <span>
        {stats.keepSec.toFixed(1)}s of {totalSec.toFixed(1)}s ({pct}%)
      </span>
      <span className="text-gray-500">· {total} segments total</span>
    </div>
  );
}


function summarise(segments: SegmentDict[]) {
  let keep = 0;
  let cut = 0;
  let keepSec = 0;
  let cutSec = 0;
  for (const s of segments) {
    const d = s.end - s.start;
    if (s.decision === "keep") {
      keep += 1;
      keepSec += d;
    } else {
      cut += 1;
      cutSec += d;
    }
  }
  return { keep, cut, keepSec, cutSec };
}
