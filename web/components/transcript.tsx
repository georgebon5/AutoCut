"use client";

import { useMemo, useState } from "react";

import { ApiError } from "@/lib/api";
import { useSegmentToggle } from "@/lib/use-segment-toggle";
import { useWordEdit } from "@/lib/use-word-edit";
import type { Decision, Edl, SegmentDict, Transcript as TranscriptT, WordTimestamp } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  edl: Edl;
  /** Index into edl.segments of the currently-selected segment (optional). */
  selectedIndex?: number | null;
  onSelect?: (index: number) => void;
  /** Called after a successful toggle so the parent can flag "needs re-render". */
  onDirty?: () => void;
}

/** Carries the original word position in `transcript.words` for PATCH targeting. */
interface WordAssignment {
  segIndex: number;
  word: WordTimestamp;
  wordIndex: number;
  clipId: string;
}

/** O(words + segments·log) assignment of each word to the segment that covers it. */
function buildWordToSegment(edl: Edl): WordAssignment[] {
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

  const out: WordAssignment[] = [];
  for (const t of edl.transcripts) {
    const segs = byClip.get(t.clip_id) ?? [];
    t.words.forEach((w, wordIndex) => {
      const segIndex = findContainingSegment(segs, w.start);
      if (segIndex < 0) return;    // transcript word outside any segment: skip
      out.push({ segIndex, word: w, wordIndex, clipId: t.clip_id });
    });
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


type Mode = "toggle" | "edit";

export default function Transcript({ jobId, preset, edl, selectedIndex, onSelect, onDirty }: Props) {
  const toggleMut = useSegmentToggle(jobId, preset);
  const editMut = useWordEdit(jobId, preset);
  const [mode, setMode] = useState<Mode>("toggle");
  // Which word is currently being edited: (clipId, wordIndex) tuple.
  const [editing, setEditing] = useState<{ clipId: string; wordIndex: number } | null>(null);

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
        Δεν υπάρχει κείμενο για αυτό το μοντάζ.
      </p>
    );
  }

  const commitEdit = (clipId: string, wordIndex: number, next: string) => {
    setEditing(null);
    const t = edl.transcripts.find((x) => x.clip_id === clipId);
    const current = t?.words[wordIndex]?.word ?? "";
    const trimmed = next.trim();
    if (!trimmed || trimmed === current) return;    // no-op: nothing changed
    editMut.mutate({ clipId, wordIndex, word: trimmed });
    onDirty?.();
  };

  const headerHint =
    mode === "edit"
      ? "Κείμενο · πάτα μία λέξη για να τη διορθώσεις"
      : "Κείμενο · πάτα μία λέξη για να κόψεις/κρατήσεις το κομμάτι της";

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
          {headerHint}
        </h2>
        <ModeToggle value={mode} onChange={(m) => { setMode(m); setEditing(null); }} />
      </div>

      <div className="glass-card flex max-h-80 flex-col gap-4 overflow-y-auto rounded-2xl p-5">
        {edl.transcripts.map((t) => (
          <ClipTranscript
            key={t.clip_id}
            transcript={t}
            segments={edl.segments}
            assignments={groupedByClip.get(t.clip_id) ?? []}
            selectedIndex={selectedIndex ?? null}
            mode={mode}
            editing={editing}
            onStartEdit={(clipId, wordIndex) => setEditing({ clipId, wordIndex })}
            onCommitEdit={commitEdit}
            onCancelEdit={() => setEditing(null)}
            onSelect={(i) => {
              onSelect?.(i);
              const seg = edl.segments[i];
              if (seg) {
                toggleMut.mutate({
                  index: i,
                  decision: flip(seg.decision),
                });
                onDirty?.();
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
      {editMut.isError ? (
        <p className="text-sm text-red-400">
          {(editMut.error as ApiError | Error).message}
        </p>
      ) : null}
    </section>
  );
}


function ModeToggle({ value, onChange }: { value: Mode; onChange: (m: Mode) => void }) {
  return (
    <div className="inline-flex shrink-0 gap-1 rounded-xl border border-white/10 bg-black/30 p-0.5 text-xs">
      <button
        type="button"
        onClick={() => onChange("toggle")}
        className={`rounded-lg px-3 py-1 transition ${
          value === "toggle"
            ? "btn-primary-gradient font-semibold"
            : "text-white/60 hover:bg-white/5 hover:text-white"
        }`}
      >
        Κόψιμο
      </button>
      <button
        type="button"
        onClick={() => onChange("edit")}
        className={`rounded-lg px-3 py-1 transition ${
          value === "edit"
            ? "bg-cyan-400 font-semibold text-cyan-950"
            : "text-white/60 hover:bg-white/5 hover:text-white"
        }`}
      >
        Διόρθωση κειμένου
      </button>
    </div>
  );
}


function ClipTranscript({
  transcript,
  segments,
  assignments,
  selectedIndex,
  mode,
  editing,
  onStartEdit,
  onCommitEdit,
  onCancelEdit,
  onSelect,
}: {
  transcript: TranscriptT;
  segments: SegmentDict[];
  assignments: WordAssignment[];
  selectedIndex: number | null;
  mode: Mode;
  editing: { clipId: string; wordIndex: number } | null;
  onStartEdit: (clipId: string, wordIndex: number) => void;
  onCommitEdit: (clipId: string, wordIndex: number, next: string) => void;
  onCancelEdit: () => void;
  onSelect: (segIndex: number) => void;
}) {
  if (assignments.length === 0) return null;

  return (
    <div>
      <div className="mb-1 font-mono text-xs text-white/35">
        {transcript.clip_id} · {transcript.language}
      </div>
      <p className="text-sm leading-relaxed">
        {assignments.map((a, i) => {
          const seg = segments[a.segIndex];
          if (!seg) return null;
          const keep = seg.decision === "keep";
          const active = selectedIndex === a.segIndex;
          const isEditing =
            editing !== null
            && editing.clipId === a.clipId
            && editing.wordIndex === a.wordIndex;

          if (isEditing) {
            return (
              <WordEditor
                key={i}
                initial={a.word.word.trim()}
                onCommit={(next) => onCommitEdit(a.clipId, a.wordIndex, next)}
                onCancel={onCancelEdit}
              />
            );
          }
          return (
            <button
              key={i}
              type="button"
              onClick={() => {
                if (mode === "edit") {
                  onStartEdit(a.clipId, a.wordIndex);
                } else {
                  onSelect(a.segIndex);
                }
              }}
              className={[
                "inline rounded px-0.5 transition",
                keep
                  ? "text-white/90 hover:bg-cyan-400/15"
                  : "text-white/35 line-through hover:bg-white/5",
                active ? "ring-1 ring-fuchsia-400/70" : "",
                mode === "edit" ? "cursor-text hover:bg-cyan-400/20" : "",
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


function WordEditor({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  // Rough-width autosize: scales with current content so the input doesn't
  // flex the surrounding line too much.
  const width = Math.max(4, value.length + 1);
  return (
    <input
      type="text"
      autoFocus
      value={value}
      size={width}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit(value);
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
      onBlur={() => onCommit(value)}
      className="inline rounded-md border border-cyan-400/60 bg-black/60 px-1.5 text-sm text-cyan-100 outline-none"
    />
  );
}


function flip(d: Decision): Decision {
  return d === "keep" ? "cut" : "keep";
}
