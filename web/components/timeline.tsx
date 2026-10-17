"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiError } from "@/lib/api";
import { useSegmentToggle } from "@/lib/use-segment-toggle";
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
  /** Called after a successful toggle so the parent can flag "needs re-render". */
  onDirty?: () => void;
}

export default function Timeline({ jobId, preset, edl, onDirty }: Props) {
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

  const toggleMut = useSegmentToggle(jobId, preset);

  const stats = useMemo(() => summarise(edl.segments), [edl.segments]);
  const selectedSeg = selected !== null ? edl.segments[selected] ?? null : null;

  // Keyboard shortcuts for high-volume editing. Skip when the user is typing
  // in an input/button/link so word-edits and buttons keep their keystrokes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t && (
          t.tagName === "INPUT"
          || t.tagName === "TEXTAREA"
          || t.tagName === "SELECT"
          || t.tagName === "BUTTON"
          || t.tagName === "A"
          || t.isContentEditable
        )
      ) return;
      if (document.visibilityState !== "visible" || !document.hasFocus()) return;

      const total = edl.segments.length;
      if (total === 0) return;

      // j/k navigate; d/x/Delete/Backspace toggle keep/cut of current.
      if (e.key === "j" || e.key === "J" || e.key === "ArrowLeft") {
        e.preventDefault();
        setSelected((s) => (s === null ? 0 : Math.max(0, s - 1)));
      } else if (e.key === "l" || e.key === "L" || e.key === "ArrowRight") {
        e.preventDefault();
        setSelected((s) => (s === null ? 0 : Math.min(total - 1, s + 1)));
      } else if (
        e.key === "d" || e.key === "D"
        || e.key === "x" || e.key === "X"
        || e.key === "Delete" || e.key === "Backspace"
      ) {
        if (selected === null) return;
        const seg = edl.segments[selected];
        if (!seg) return;
        e.preventDefault();
        const next: Decision = seg.decision === "keep" ? "cut" : "keep";
        toggleMut.mutate({ index: selected, decision: next });
        onDirty?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [edl.segments, selected, toggleMut, onDirty]);

  return (
    <section className="flex flex-col gap-4">
      <StatsBar stats={stats} />

      <div className="glass-card overflow-x-auto rounded-2xl">
        <div className="flex min-w-full flex-col divide-y divide-white/5">
          {edl.clips.map((clip) => (
            <ClipRow
              key={clip.clip_id}
              clip={clip}
              items={bySegmentIndex.get(clip.clip_id) ?? []}
              selectedIndex={selected}
              onSelect={setSelected}
              onToggle={(index, decision) => {
                toggleMut.mutate({ index, decision });
                onDirty?.();
              }}
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
        <p className="text-xs text-white/40">
          Πάτα ένα κομμάτι για να το κρατήσεις ή να το κόψεις.
        </p>
      )}

      <p className="text-[11px] text-white/35">
        Shortcuts: <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">J</kbd>
        /<kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">L</kbd> προηγούμενο/επόμενο ·{" "}
        <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">D</kbd> κόψε/κράτα ·{" "}
        <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">Space</kbd> play/pause
      </p>
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
      <div className="w-24 shrink-0 text-xs text-white/50">
        <div className="truncate font-mono text-white/80" title={clip.clip_id}>
          {clip.clip_id}
        </div>
        <div className="font-mono text-white/40">{clip.duration.toFixed(1)}s</div>
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
                "absolute top-0 rounded-md border transition duration-200",
                keep
                  ? "border-cyan-400/60 bg-cyan-400/30 hover:bg-cyan-400/50"
                  : "border-dashed border-white/15 bg-white/[0.03] hover:bg-white/10",
                active ? "ring-2 ring-fuchsia-400/70" : "",
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
    <div className="glass-card rounded-2xl p-4 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-white/90">
          Κομμάτι <span className="font-mono text-white/50">#{index}</span> · <span className="font-mono text-white/60">{seg.clip_id}</span>
        </span>
        <span
          className={`rounded-full border px-2 py-0.5 text-xs ${
            seg.decision === "keep"
              ? "border-cyan-400/30 bg-cyan-400/10 text-cyan-200"
              : "border-white/10 bg-white/5 text-white/60"
          }`}
        >
          {seg.decision === "keep" ? "μέσα" : "έξω"} · {seg.decision_source === "user" ? "εσύ" : "αυτόματο"}
        </span>
      </div>
      <div className="mt-2 font-mono text-xs text-white/50">
        {seg.start.toFixed(2)}s → {seg.end.toFixed(2)}s · βαθμός {seg.interest_score.toFixed(2)}
      </div>
      {seg.reasons.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-1.5 text-xs">
          {seg.reasons.map((r, i) => (
            <li
              key={i}
              className="rounded-md border border-white/5 bg-white/5 px-2 py-0.5 text-white/70"
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
    <div className="flex flex-wrap items-center gap-3 text-xs text-white/50">
      <span className="font-medium text-white/80">
        {stats.keep} μέσα · {stats.cut} έξω
      </span>
      <span className="font-mono">
        {stats.keepSec.toFixed(1)}s από {totalSec.toFixed(1)}s ({pct}%)
      </span>
      <span className="text-white/35">· {total} κομμάτια σύνολο</span>
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
