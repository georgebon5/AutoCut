"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { ApiError, getWaveform } from "@/lib/api";
import { useBatchToggle } from "@/lib/use-batch-toggle";
import { useSegmentToggle } from "@/lib/use-segment-toggle";
import type { Clip, Decision, Edl, SegmentDict } from "@/lib/api.types";

// Base pixels per second — multiplied by the zoom factor to get the live value.
const BASE_PX_PER_SECOND = 50;
// Zoom steps: each Ctrl+scroll step multiplies by this factor.
const ZOOM_STEP = 1.25;
const ZOOM_MIN = 0.3;
const ZOOM_MAX = 8;
const MIN_BLOCK_PX = 6;    // keep tiny segments tappable
const ROW_HEIGHT_PX = 56;


interface Props {
  jobId: string;
  preset: string | undefined;
  edl: Edl;
  /** Current output-video playback position in seconds (from the preview player). */
  currentTime?: number;
  /** Called after a successful toggle so the parent can flag "needs re-render". */
  onDirty?: () => void;
  /** Called with the output-timeline position when a segment is selected,
   *  so the preview player can seek to it without a full re-render. */
  onSeekRequest?: (outputTime: number) => void;
}

type Filter = "all" | "keep" | "cut";

export default function Timeline({ jobId, preset, edl, currentTime, onDirty, onSeekRequest }: Props) {
  const [selected, setSelected] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [showHelp, setShowHelp] = useState(false);
  const [zoom, setZoom] = useState(1);
  const pxPerSec = BASE_PX_PER_SECOND * zoom;

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

  // Ordered list of segment indices that pass the current filter (clip order,
  // then position within clip). Used to scope J/L keyboard navigation.
  const filteredSegmentOrder = useMemo(() => {
    const result: number[] = [];
    for (const clip of edl.clips) {
      for (const { seg, index } of (bySegmentIndex.get(clip.clip_id) ?? [])) {
        if (filter === "all" || seg.decision === filter) result.push(index);
      }
    }
    return result;
  }, [edl.clips, bySegmentIndex, filter]);

  const toggleMut = useSegmentToggle(jobId, preset);
  const batchMut = useBatchToggle(jobId, preset);

  const stats = useMemo(() => summarise(edl.segments), [edl.segments]);
  const selectedSeg = selected !== null ? edl.segments[selected] ?? null : null;

  // Map output-video currentTime to a source position within one clip, so we
  // can draw a playback cursor on the matching ClipRow / HookRow.
  const activeCursor = useMemo((): { clipId: string; sourceX: number; isHook: boolean } | null => {
    if (currentTime === undefined || currentTime < 0) return null;
    for (const chunk of edl.render_chunks) {
      if (currentTime < chunk.output_start || currentTime >= chunk.output_end) continue;
      if (chunk.segment_index === -1) {
        if (!edl.hook) continue;
        const src = edl.hook.start + (currentTime - chunk.output_start);
        return { clipId: edl.hook.clip_id, sourceX: src * pxPerSec, isHook: true };
      }
      const seg = edl.segments[chunk.segment_index];
      if (!seg) continue;
      const src = seg.start + (currentTime - chunk.output_start);
      return { clipId: seg.clip_id, sourceX: src * pxPerSec, isHook: false };
    }
    return null;
  }, [currentTime, edl.render_chunks, edl.segments, edl.hook, pxPerSec]);

  // Map (clipId, sourceTime) → output time, used for waveform background clicks.
  // Returns null when the source position falls inside a cut segment.
  const sourceToOutput = useCallback(
    (clipId: string, sourceTime: number): number | null => {
      for (const [idx, outputStart] of outputStartBySegment) {
        const seg = edl.segments[idx];
        if (!seg || seg.clip_id !== clipId) continue;
        if (sourceTime >= seg.start && sourceTime <= seg.end) {
          return outputStart + (sourceTime - seg.start);
        }
      }
      return null;
    },
    [outputStartBySegment, edl.segments],
  );

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

      if (filteredSegmentOrder.length === 0) return;

      // j/l navigate within the current filter; d/x/Delete/Backspace toggle.
      if (e.key === "j" || e.key === "J" || e.key === "ArrowLeft") {
        e.preventDefault();
        setSelected((s) => {
          const pos = s === null ? 0 : Math.max(0, filteredSegmentOrder.indexOf(s) - 1);
          const next = filteredSegmentOrder[pos] ?? filteredSegmentOrder[0]!;
          const ot = outputStartBySegment.get(next);
          if (ot !== undefined) onSeekRequest?.(ot);
          return next;
        });
      } else if (e.key === "l" || e.key === "L" || e.key === "ArrowRight") {
        e.preventDefault();
        setSelected((s) => {
          const cur = s === null ? -1 : filteredSegmentOrder.indexOf(s);
          const pos = Math.min(filteredSegmentOrder.length - 1, cur + 1);
          const next = filteredSegmentOrder[pos] ?? filteredSegmentOrder[filteredSegmentOrder.length - 1]!;
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
      } else if (e.key === "?" || e.key === "h" || e.key === "H") {
        e.preventDefault();
        setShowHelp((v) => !v);
      } else if (e.key === "Escape") {
        setShowHelp(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [edl.segments, filteredSegmentOrder, selected, toggleMut, onDirty, handleUndo, pushUndo]);

  // Ctrl+scroll zooms the waveform timeline. Attached to the section so it
  // only fires while the pointer is inside the timeline, not the whole page.
  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setZoom((z) => {
      const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z * factor));
    });
  }, []);

  return (
    <section className="flex flex-col gap-4" onWheelCapture={handleWheel}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <StatsBar stats={stats} />
          <FilterToggle value={filter} stats={stats} onChange={setFilter} />
        </div>
        <div className="flex items-center gap-2">
          <ZoomControl zoom={zoom} onChange={setZoom} />
          <BulkActions
            segments={edl.segments}
            batchMut={batchMut}
            undoCount={undoCount}
            onUndo={handleUndo}
            onDirty={onDirty}
          />
        </div>
      </div>

      <div className="glass-card overflow-x-auto rounded-2xl">
        <div className="flex min-w-full flex-col divide-y divide-white/5">
          {edl.hook ? (
            <HookRow
              hook={edl.hook}
              clip={edl.clips.find((c) => c.clip_id === edl.hook!.clip_id) ?? null}
              outputTime={
                edl.render_chunks.find((c) => c.segment_index === -1)?.output_start ?? 0
              }
              jobId={jobId}
              pxPerSec={pxPerSec}
              cursorX={
                activeCursor?.isHook && activeCursor.clipId === edl.hook.clip_id
                  ? activeCursor.sourceX
                  : null
              }
              onSeekRequest={onSeekRequest}
            />
          ) : null}
          {edl.clips.map((clip) => {
            const allItems = bySegmentIndex.get(clip.clip_id) ?? [];
            const visibleItems = allItems.filter(
              ({ seg, index }) =>
                filter === "all" || seg.decision === filter || index === selected,
            );
            if (visibleItems.length === 0) return null;
            return (
            <ClipRow
              key={clip.clip_id}
              jobId={jobId}
              clip={clip}
              items={visibleItems}
              selectedIndex={selected}
              pxPerSec={pxPerSec}
              cursorX={
                activeCursor && !activeCursor.isHook && activeCursor.clipId === clip.clip_id
                  ? activeCursor.sourceX
                  : null
              }
              onSelect={selectAndSeek}
              onWaveformClick={(srcTime) => {
                const ot = sourceToOutput(clip.clip_id, srcTime);
                if (ot !== null) onSeekRequest?.(ot);
              }}
              onToggle={(index, decision) => {
                const seg = edl.segments[index];
                if (seg) pushUndo(index, seg.decision as Decision);
                toggleMut.mutate({ index, decision });
                onDirty?.();
              }}
            />
            );
          })}
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

      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={() => setShowHelp((v) => !v)}
          title="Keyboard shortcuts (H / ?)"
          className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-xs text-white/35 transition hover:border-white/20 hover:text-white/60"
        >
          ?
        </button>
      </div>

      {showHelp ? (
        <ShortcutHelp onClose={() => setShowHelp(false)} />
      ) : null}
    </section>
  );
}


function HookRow({
  hook,
  clip,
  outputTime,
  jobId,
  cursorX,
  pxPerSec,
  onSeekRequest,
}: {
  hook: SegmentDict;
  clip: Clip | null;
  outputTime: number;
  jobId: string;
  cursorX: number | null;
  pxPerSec: number;
  onSeekRequest?: (t: number) => void;
}) {
  const dur = hook.end - hook.start;
  const widthPx = Math.max(80, Math.round(dur * pxPerSec));

  const waveformQ = useQuery({
    queryKey: ["waveform", jobId, hook.clip_id],
    queryFn: () => getWaveform(jobId, hook.clip_id),
    staleTime: Infinity,
    retry: false,
  });

  return (
    <div className="flex gap-3 bg-amber-400/5 px-3 py-3">
      <div className="w-24 shrink-0 text-xs">
        <div className="font-semibold text-amber-300/90">⚡ Hook</div>
        <div className="mt-0.5 font-mono text-white/35" title={hook.clip_id}>
          {hook.clip_id}
        </div>
      </div>
      <div className="relative" style={{ width: widthPx, height: ROW_HEIGHT_PX }}>
        {waveformQ.data && (
          <WaveformBackground
            peaks={waveformQ.data.peaks}
            widthPx={widthPx}
            heightPx={ROW_HEIGHT_PX}
          />
        )}
        <button
          type="button"
          onClick={() => onSeekRequest?.(outputTime)}
          style={{ width: widthPx, height: ROW_HEIGHT_PX }}
          title={`Hook · ${hook.start.toFixed(2)}–${hook.end.toFixed(2)}s · score ${hook.interest_score.toFixed(2)}`}
          className="absolute top-0 left-0 rounded-md border border-amber-400/50 bg-amber-400/20 hover:bg-amber-400/35 transition duration-200"
        >
          {widthPx >= 60 && (
            <span className="pointer-events-none absolute top-1 left-1 font-mono text-[9px] text-amber-200/60 leading-none">
              {dur.toFixed(1)}s
            </span>
          )}
          {widthPx >= 38 && (
            <span className="pointer-events-none absolute bottom-1 right-1 font-mono text-[9px] text-amber-200/60 leading-none">
              {hook.interest_score.toFixed(2)}
            </span>
          )}
        </button>
        {cursorX !== null ? (
          <div
            aria-hidden
            className="pointer-events-none absolute top-0 z-20 w-px bg-amber-300/90"
            style={{ left: Math.round(cursorX), height: ROW_HEIGHT_PX }}
          />
        ) : null}
      </div>
    </div>
  );
}


function ClipRow({
  jobId,
  clip,
  items,
  selectedIndex,
  cursorX,
  pxPerSec,
  onSelect,
  onWaveformClick,
  onToggle,
}: {
  jobId: string;
  clip: Clip;
  items: { seg: SegmentDict; index: number }[];
  selectedIndex: number | null;
  cursorX: number | null;
  pxPerSec: number;
  onSelect: (index: number) => void;
  onWaveformClick: (sourceTime: number) => void;
  onToggle: (index: number, next: Decision) => void;
}) {
  const widthPx = Math.max(120, Math.round(clip.duration * pxPerSec));

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
        className="relative cursor-crosshair"
        style={{ width: widthPx, height: ROW_HEIGHT_PX }}
        onClick={(e) => {
          // Only fire when clicking the row background, not a child segment block.
          if (e.target !== e.currentTarget) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const srcTime = (e.clientX - rect.left) / pxPerSec;
          onWaveformClick(Math.max(0, Math.min(clip.duration, srcTime)));
        }}
      >
        {waveformQ.data && (
          <WaveformBackground
            peaks={waveformQ.data.peaks}
            widthPx={widthPx}
            heightPx={ROW_HEIGHT_PX}
          />
        )}
        {items.map(({ seg, index }) => {
          const left = Math.round(seg.start * pxPerSec);
          const w = Math.max(
            MIN_BLOCK_PX,
            Math.round((seg.end - seg.start) * pxPerSec),
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
        {/* Playback cursor — thin vertical line tracking currentTime in source space */}
        {cursorX !== null ? (
          <div
            aria-hidden
            className="pointer-events-none absolute top-0 z-20 w-px bg-cyan-300/90"
            style={{ left: Math.round(cursorX), height: ROW_HEIGHT_PX }}
          />
        ) : null}
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


function FilterToggle({
  value,
  stats,
  onChange,
}: {
  value: Filter;
  stats: { keep: number; cut: number };
  onChange: (f: Filter) => void;
}) {
  const opts: { f: Filter; label: string; count: number }[] = [
    { f: "all",  label: "Όλα",   count: stats.keep + stats.cut },
    { f: "keep", label: "Μέσα",  count: stats.keep },
    { f: "cut",  label: "Έξω",   count: stats.cut },
  ];
  return (
    <div className="inline-flex gap-0.5 rounded-lg border border-white/8 bg-black/20 p-0.5">
      {opts.map(({ f, label, count }) => (
        <button
          key={f}
          type="button"
          onClick={() => onChange(f)}
          className={[
            "rounded-md px-2.5 py-0.5 text-[11px] font-medium transition",
            value === f
              ? "bg-white/10 text-white/90"
              : "text-white/35 hover:text-white/60",
          ].join(" ")}
        >
          {label} <span className="font-mono opacity-60">{count}</span>
        </button>
      ))}
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


// ---------------------------------------------------------------------------
// Timeline zoom control
// ---------------------------------------------------------------------------

function ZoomControl({ zoom, onChange }: { zoom: number; onChange: (z: number) => void }) {
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onChange(Math.max(ZOOM_MIN, zoom / ZOOM_STEP))}
        disabled={zoom <= ZOOM_MIN}
        title="Zoom out (Ctrl+scroll)"
        className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-xs text-white/50 transition hover:border-white/20 hover:text-white/80 disabled:opacity-30"
      >
        −
      </button>
      <span className="w-10 text-center font-mono text-[10px] text-white/35">
        {Math.round(zoom * 100)}%
      </span>
      <button
        type="button"
        onClick={() => onChange(Math.min(ZOOM_MAX, zoom * ZOOM_STEP))}
        disabled={zoom >= ZOOM_MAX}
        title="Zoom in (Ctrl+scroll)"
        className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-xs text-white/50 transition hover:border-white/20 hover:text-white/80 disabled:opacity-30"
      >
        +
      </button>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Keyboard shortcut help overlay
// ---------------------------------------------------------------------------

type ShortcutRow = { keys: string[]; desc: string };

const SHORTCUTS: ShortcutRow[] = [
  { keys: ["J", "←"],        desc: "Προηγούμενο segment" },
  { keys: ["L", "→"],        desc: "Επόμενο segment" },
  { keys: ["D", "X"],        desc: "Εναλλαγή keep / cut" },
  { keys: ["Ctrl+Z"],        desc: "Undo τελευταίας αλλαγής" },
  { keys: ["Space"],         desc: "Play / Pause" },
  { keys: ["←", "→"],       desc: "Seek ±5s (στο player)" },
  { keys: ["0–9"],           desc: "Άλμα σε 0%–90% του βίντεο" },
  { keys: ["Ctrl+scroll"],   desc: "Zoom in/out στο timeline" },
  { keys: ["H", "?"],        desc: "Αυτό το παράθυρο" },
  { keys: ["Esc"],           desc: "Κλείσιμο" },
];

function ShortcutHelp({ onClose }: { onClose: () => void }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      className="glass-card rounded-2xl p-5"
    >
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-[0.15em] text-white/50">
          Keyboard shortcuts
        </p>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-2 py-0.5 text-xs text-white/40 transition hover:text-white/80"
        >
          ✕
        </button>
      </div>
      <table className="w-full text-sm">
        <tbody>
          {SHORTCUTS.map((row, i) => (
            <tr key={i} className="border-t border-white/5">
              <td className="py-1.5 pr-4">
                <span className="flex flex-wrap gap-1">
                  {row.keys.map((k) => (
                    <kbd
                      key={k}
                      className="rounded border border-white/15 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-white/60"
                    >
                      {k}
                    </kbd>
                  ))}
                </span>
              </td>
              <td className="py-1.5 text-white/55">{row.desc}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
