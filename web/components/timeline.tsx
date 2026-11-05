"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError, getWaveform } from "@/lib/api";
import { useBatchToggle } from "@/lib/use-batch-toggle";
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
  /** Called with the output-timeline position when a segment is selected,
   *  so the preview player can seek to it without a full re-render. */
  onSeekRequest?: (outputTime: number) => void;
}

export default function Timeline({ jobId, preset, edl, onDirty, onSeekRequest }: Props) {
  const [selected, setSelected] = useState<number | null>(null);

  // Undo stack: each entry stores the decision BEFORE a toggle so Ctrl+Z can
  // replay it. Using a ref avoids stale-closure issues in event handlers;
  // undoCount is a plain state integer used only to trigger re-renders.
  const undoStackRef = useRef<Array<{ index: number; prev: Decision }>>([]);
  const [undoCount, setUndoCount] = useState(0);

  const pushUndo = useCallback((index: number, prev: Decision) => {
    undoStackRef.current.push({ index, prev });
    setUndoCount((c) => c + 1);
  }, []);

  // Build a map from segment index → output_start for instant seek-to-segment.
  const outputStartBySegment = useMemo(() => {
    const m = new Map<number, number>();
    for (const chunk of edl.render_chunks) {
      if (chunk.segment_index >= 0) {
        m.set(chunk.segment_index, chunk.output_start);
      }
    }
    return m;
  }, [edl.render_chunks]);

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
  const batchMut = useBatchToggle(jobId, preset);

  const stats = useMemo(() => summarise(edl.segments), [edl.segments]);
  const selectedSeg = selected !== null ? edl.segments[selected] ?? null : null;

  const selectAndSeek = (index: number) => {
    setSelected(index);
    const t = outputStartBySegment.get(index);
    if (t !== undefined) onSeekRequest?.(t);
  };

  // Undo: pop from stack and restore previous decision.
  const handleUndo = useCallback(() => {
    if (undoStackRef.current.length === 0) return;
    const entry = undoStackRef.current.pop()!;
    setUndoCount((c) => c - 1);
    toggleMut.mutate({ index: entry.index, decision: entry.prev });
    onDirty?.();
  }, [toggleMut, onDirty]);

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

      // Ctrl/Cmd+Z: undo last toggle.
      if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        handleUndo();
        return;
      }

      // Modifier keys guard — don't intercept Ctrl+J etc.
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      const total = edl.segments.length;
      if (total === 0) return;

      // j/l navigate; d/x/Delete/Backspace toggle keep/cut of current.
      if (e.key === "j" || e.key === "J" || e.key === "ArrowLeft") {
        e.preventDefault();
        setSelected((s) => {
          const next = s === null ? 0 : Math.max(0, s - 1);
          const ot = outputStartBySegment.get(next);
          if (ot !== undefined) onSeekRequest?.(ot);
          return next;
        });
      } else if (e.key === "l" || e.key === "L" || e.key === "ArrowRight") {
        e.preventDefault();
        setSelected((s) => {
          const next = s === null ? 0 : Math.min(total - 1, s + 1);
          const ot = outputStartBySegment.get(next);
          if (ot !== undefined) onSeekRequest?.(ot);
          return next;
        });
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
        pushUndo(selected, seg.decision as Decision);
        toggleMut.mutate({ index: selected, decision: next });
        onDirty?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [edl.segments, selected, toggleMut, onDirty, handleUndo, pushUndo]);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatsBar stats={stats} />
        <BulkActions
          segments={edl.segments}
          batchMut={batchMut}
          undoCount={undoCount}
          onUndo={handleUndo}
          onDirty={onDirty}
        />
      </div>

      <div className="glass-card overflow-x-auto rounded-2xl">
        <div className="flex min-w-full flex-col divide-y divide-white/5">
          {edl.clips.map((clip) => (
            <ClipRow
              key={clip.clip_id}
              jobId={jobId}
              clip={clip}
              items={bySegmentIndex.get(clip.clip_id) ?? []}
              selectedIndex={selected}
              onSelect={selectAndSeek}
              onToggle={(index, decision) => {
                const seg = edl.segments[index];
                if (seg) pushUndo(index, seg.decision as Decision);
                toggleMut.mutate({ index, decision });
                onDirty?.();
              }}
            />
          ))}
        </div>
      </div>

      {(toggleMut.isError || batchMut.isError) ? (
        <p className="text-sm text-red-400">
          {((toggleMut.error ?? batchMut.error) as ApiError | Error).message}
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
        Κλικ = επιλογή + seek ·{" "}
        Διπλό κλικ = εναλλαγή keep/cut ·{" "}
        <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">J</kbd>
        /<kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">L</kbd> πλοήγηση ·{" "}
        <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">D</kbd> κόψε/κράτα ·{" "}
        <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">Ctrl+Z</kbd> undo ·{" "}
        <kbd className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px]">Space</kbd> play/pause
      </p>
    </section>
  );
}


function ClipRow({
  jobId,
  clip,
  items,
  selectedIndex,
  onSelect,
  onToggle,
}: {
  jobId: string;
  clip: Clip;
  items: { seg: SegmentDict; index: number }[];
  selectedIndex: number | null;
  onSelect: (index: number) => void;
  onToggle: (index: number, next: Decision) => void;
}) {
  const widthPx = Math.max(120, Math.round(clip.duration * PX_PER_SECOND));

  const waveformQ = useQuery({
    queryKey: ["waveform", jobId, clip.clip_id],
    queryFn: () => getWaveform(jobId, clip.clip_id),
    staleTime: Infinity,
    retry: false,
  });

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
        {waveformQ.data && (
          <WaveformBackground
            peaks={waveformQ.data.peaks}
            widthPx={widthPx}
            heightPx={ROW_HEIGHT_PX}
          />
        )}
        {items.map(({ seg, index }) => {
          const left = Math.round(seg.start * PX_PER_SECOND);
          const w = Math.max(
            MIN_BLOCK_PX,
            Math.round((seg.end - seg.start) * PX_PER_SECOND),
          );
          const keep = seg.decision === "keep";
          const active = selectedIndex === index;
          const next: Decision = keep ? "cut" : "keep";
          // Score drives opacity for kept blocks: strong keeps are vivid,
          // borderline keeps are more transparent. Clamp to [0.25, 1].
          const scoreOpacity = keep
            ? Math.max(0.25, Math.min(1.0, 0.25 + seg.interest_score * 0.75))
            : 1;
          const dur = seg.end - seg.start;
          return (
            <button
              key={index}
              type="button"
              onClick={() => onSelect(index)}
              onDoubleClick={() => onToggle(index, next)}
              style={{
                left,
                width: w,
                height: ROW_HEIGHT_PX,
                // Active selection always shows at full opacity regardless of score.
                opacity: active ? 1 : scoreOpacity,
              }}
              title={`${seg.start.toFixed(2)}–${seg.end.toFixed(2)}s · score ${seg.interest_score.toFixed(2)} · ${seg.decision}`}
              className={[
                "absolute top-0 rounded-md border transition duration-200",
                keep
                  ? "border-cyan-400/60 bg-cyan-400/30 hover:bg-cyan-400/50"
                  : "border-dashed border-white/15 bg-white/[0.03] hover:bg-white/10",
                active ? "ring-2 ring-fuchsia-400/70" : "",
              ].join(" ")}
            >
              {/* Show score label inside wide-enough kept blocks */}
              {keep && w >= 38 ? (
                <span
                  className="pointer-events-none absolute bottom-1 right-1 font-mono text-[9px] text-cyan-200/70 leading-none"
                  aria-hidden
                >
                  {seg.interest_score.toFixed(2)}
                </span>
              ) : null}
              {/* Duration label for very wide blocks */}
              {w >= 60 ? (
                <span
                  className="pointer-events-none absolute top-1 left-1 font-mono text-[9px] text-white/40 leading-none"
                  aria-hidden
                >
                  {dur.toFixed(1)}s
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}


function WaveformBackground({
  peaks,
  widthPx,
  heightPx,
}: {
  peaks: number[];
  widthPx: number;
  heightPx: number;
}) {
  const n = peaks.length;
  if (n === 0) return null;
  const barW = widthPx / n;
  const mid = heightPx / 2;
  return (
    <svg
      width={widthPx}
      height={heightPx}
      className="absolute inset-0 pointer-events-none"
      style={{ opacity: 0.15 }}
      aria-hidden
    >
      {peaks.map((p, i) => {
        const h = Math.max(1, p * heightPx);
        return (
          <rect
            key={i}
            x={i * barW}
            y={mid - h / 2}
            width={Math.max(1, barW - 0.5)}
            height={h}
            fill="white"
          />
        );
      })}
    </svg>
  );
}


// Feature keys shown in the breakdown, in display order.
const FEATURE_LABELS: Record<string, string> = {
  energy:           "Ενέργεια",
  speech_rate:      "Ρυθμός ομιλίας",
  pitch_variance:   "Pitch var.",
  motion:           "Κίνηση",
  keyword_density:  "Keywords",
  scene_change:     "Scene change",
  silence_ratio:    "Σιωπή",
  filler_density:   "Fillers",
};

function SegmentDetail({ index, seg }: { index: number; seg: SegmentDict }) {
  // Pick known features present on this segment, sorted by value descending.
  const featureEntries = Object.entries(seg.features)
    .filter(([k]) => k in FEATURE_LABELS)
    .sort(([, a], [, b]) => b - a);

  const scoreColor = seg.interest_score >= 0.65
    ? "text-emerald-300"
    : seg.interest_score >= 0.35
    ? "text-amber-300"
    : "text-red-400";

  return (
    <div className="glass-card rounded-2xl p-4 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <span className="font-medium text-white/90">
            Κομμάτι <span className="font-mono text-white/50">#{index}</span>
            <span className="ml-2 font-mono text-white/40">{seg.clip_id}</span>
          </span>
          <div className="mt-1 font-mono text-xs text-white/40">
            {seg.start.toFixed(2)}s → {seg.end.toFixed(2)}s
            <span className="ml-2 text-white/30">({(seg.end - seg.start).toFixed(2)}s)</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span
            className={`rounded-full border px-2 py-0.5 text-xs ${
              seg.decision === "keep"
                ? "border-cyan-400/30 bg-cyan-400/10 text-cyan-200"
                : "border-white/10 bg-white/5 text-white/60"
            }`}
          >
            {seg.decision === "keep" ? "μέσα" : "έξω"} · {seg.decision_source === "user" ? "εσύ" : "auto"}
          </span>
          <span className={`font-mono text-lg font-light leading-none ${scoreColor}`}>
            {seg.interest_score.toFixed(2)}
          </span>
        </div>
      </div>

      {featureEntries.length > 0 ? (
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
          {featureEntries.map(([key, val]) => (
            <div key={key} className="flex flex-col gap-0.5">
              <div className="flex justify-between text-[10px] text-white/40">
                <span>{FEATURE_LABELS[key] ?? key}</span>
                <span className="font-mono">{val.toFixed(2)}</span>
              </div>
              <div className="h-1 w-full overflow-hidden rounded-full bg-white/5">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-indigo-500"
                  style={{ width: `${Math.min(100, val * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {seg.reasons.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-1.5">
          {seg.reasons.map((r, i) => (
            <li
              key={i}
              className="rounded-md border border-white/5 bg-white/5 px-2 py-0.5 text-[11px] text-white/60"
            >
              {r}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}


function BulkActions({
  segments,
  batchMut,
  undoCount,
  onUndo,
  onDirty,
}: {
  segments: SegmentDict[];
  batchMut: ReturnType<typeof useBatchToggle>;
  undoCount: number;
  onUndo: () => void;
  onDirty?: () => void;
}) {
  const busy = batchMut.isPending;

  const apply = (updates: Array<{ index: number; decision: Decision }>) => {
    if (updates.length === 0) return;
    batchMut.mutate(updates);
    onDirty?.();
  };

  const cls =
    "rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] font-medium text-white/60 transition hover:border-white/20 hover:bg-white/10 hover:text-white/80 disabled:cursor-not-allowed disabled:opacity-30";

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          apply(
            segments.flatMap((s, i) =>
              s.decision !== "keep" ? [{ index: i, decision: "keep" as Decision }] : [],
            ),
          )
        }
        className={cls}
        title="Κράτα όλα τα κομμάτια"
      >
        Κράτα όλα
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          apply(
            segments.flatMap((s, i) =>
              s.decision !== "cut" ? [{ index: i, decision: "cut" as Decision }] : [],
            ),
          )
        }
        className={cls}
        title="Κόψε όλα τα κομμάτια"
      >
        Κόψε όλα
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          apply(
            segments.map((s, i) => ({
              index: i,
              decision: (s.decision === "keep" ? "cut" : "keep") as Decision,
            })),
          )
        }
        className={cls}
        title="Αντιστρέψε όλες τις επιλογές"
      >
        Αντιστροφή
      </button>
      <button
        type="button"
        disabled={undoCount === 0 || busy}
        onClick={onUndo}
        className={cls}
        title="Undo τελευταία αλλαγή (Ctrl+Z)"
      >
        ↩{undoCount > 0 ? ` (${undoCount})` : ""}
      </button>
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
