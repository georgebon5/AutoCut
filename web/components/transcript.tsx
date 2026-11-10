"use client";

import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, cutWord, patchWord } from "@/lib/api";
import { useSegmentToggle } from "@/lib/use-segment-toggle";
import { useWordCut } from "@/lib/use-word-cut";
import { useWordEdit } from "@/lib/use-word-edit";
import type { Decision, Edl, SegmentDict, Transcript as TranscriptT, WordTimestamp } from "@/lib/api.types";


const DEFAULT_FILLERS = ["um", "uh", "hmm", "εε", "αα", "ααα", "εεε", "μμ", "μμμ"];

interface Props {
  jobId: string;
  preset: string | undefined;
  edl: Edl;
  /** Index into edl.segments of the currently-selected segment (optional). */
  selectedIndex?: number | null;
  onSelect?: (index: number) => void;
  /** Called after a successful toggle so the parent can flag "needs re-render". */
  onDirty?: () => void;
  /** Output-timeline position from the video player — drives live word highlight. */
  currentTime?: number;
  /** Called with an output-timeline position when the user clicks a word. */
  onSeekRequest?: (outputTime: number) => void;
}

/** Carries the original word position in `transcript.words` for PATCH targeting. */
interface WordAssignment {
  segIndex: number;
  word: WordTimestamp;
  wordIndex: number;
  clipId: string;
}

/** Map a source position (clipId + sourceTime) forward to an output-timeline position. */
function sourceToOutput(clipId: string, sourceTime: number, edl: Edl): number | null {
  for (const chunk of edl.render_chunks) {
    if (chunk.segment_index < 0) continue;
    const seg = edl.segments[chunk.segment_index];
    if (!seg || seg.clip_id !== clipId) continue;
    if (sourceTime >= seg.start && sourceTime < seg.end) {
      return chunk.output_start + (sourceTime - seg.start);
    }
  }
  return null;
}

/** Map an output-timeline position back to { clipId, sourceTime } via render_chunks. */
function outputToSource(t: number, edl: Edl): { clipId: string; sourceTime: number } | null {
  for (const chunk of edl.render_chunks) {
    if (chunk.segment_index < 0) continue;
    if (t >= chunk.output_start && t < chunk.output_end) {
      const seg = edl.segments[chunk.segment_index];
      if (!seg) continue;
      return { clipId: seg.clip_id, sourceTime: seg.start + (t - chunk.output_start) };
    }
  }
  return null;
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


type WordMenu = {
  clipId: string;
  wordIndex: number;
  word: WordTimestamp;
  segIndex: number;
  x: number;
  y: number;
};

export default function Transcript({ jobId, preset, edl, selectedIndex, onSelect, onDirty, currentTime, onSeekRequest }: Props) {
  const qc = useQueryClient();
  const edlKey = ["edl", jobId, preset ?? null] as const;

  const toggleMut = useSegmentToggle(jobId, preset);
  const editMut = useWordEdit(jobId, preset);
  const cutMut = useWordCut(jobId, preset);
  const [editing, setEditing] = useState<{ clipId: string; wordIndex: number } | null>(null);
  const [wordMenu, setWordMenu] = useState<WordMenu | null>(null);

  // ── Search + Replace state ────────────────────────────────────────────────
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeMatchIdx, setActiveMatchIdx] = useState(0);
  const [showReplace, setShowReplace] = useState(false);
  const [replaceValue, setReplaceValue] = useState("");

  // ── Filler word state ─────────────────────────────────────────────────────
  const [showFillers, setShowFillers] = useState(false);
  const [fillerList, setFillerList] = useState<string[]>(DEFAULT_FILLERS);
  const [cuttingFillers, setCuttingFillers] = useState(false);

  // Find which word is currently playing based on the video's output time.
  const activeWord = useMemo(() => {
    if (currentTime === undefined) return null;
    const src = outputToSource(currentTime, edl);
    if (!src) return null;
    const transcript = edl.transcripts.find((t) => t.clip_id === src.clipId);
    if (!transcript) return null;
    const idx = transcript.words.findIndex(
      (w) => src.sourceTime >= w.start && src.sourceTime < w.end,
    );
    if (idx < 0) return null;
    return { clipId: src.clipId, wordIndex: idx };
  }, [currentTime, edl]);

  // All assignments that match the current search query, in transcript order.
  const matchList = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [];
    const result: { clipId: string; wordIndex: number; word: WordTimestamp }[] = [];
    for (const t of edl.transcripts) {
      t.words.forEach((w, wordIndex) => {
        if (w.word.toLowerCase().includes(q)) {
          result.push({ clipId: t.clip_id, wordIndex, word: w });
        }
      });
    }
    return result;
  }, [searchQuery, edl.transcripts]);

  // Key of the currently focused match: "clipId:wordIndex".
  const currentMatchKey =
    matchList.length > 0
      ? `${matchList[activeMatchIdx]?.clipId}:${matchList[activeMatchIdx]?.wordIndex}`
      : null;

  // Navigate to a match: update index and seek the player.
  const goToMatch = useCallback(
    (idx: number) => {
      const match = matchList[idx];
      if (!match) return;
      setActiveMatchIdx(idx);
      const ot = sourceToOutput(match.clipId, match.word.start, edl);
      if (ot !== null) onSeekRequest?.(ot);
    },
    [matchList, edl, onSeekRequest],
  );

  // Ctrl/Cmd+F opens the transcript search bar.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        const target = e.target as HTMLElement | null;
        // Don't intercept when user is typing in a word editor.
        if (target?.closest("[data-transcript]")) {
          e.preventDefault();
          setShowSearch(true);
        }
      }
      if (e.key === "Escape" && showSearch) {
        setShowSearch(false);
        setSearchQuery("");
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [showSearch]);

  // Reset match index when query changes.
  useEffect(() => { setActiveMatchIdx(0); }, [searchQuery]);

  // Close word menu on click-outside or Escape.
  useEffect(() => {
    if (!wordMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setWordMenu(null); };
    const onClick = () => setWordMenu(null);
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick, { capture: true });
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick, { capture: true });
    };
  }, [wordMenu]);

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

  const fillerSet = useMemo(
    () => new Set(fillerList.map((f) => f.trim().toLowerCase()).filter(Boolean)),
    [fillerList],
  );

  // Filler word occurrences in *kept* segments only — these are the ones we can cut.
  const fillerMatches = useMemo(() => {
    if (!showFillers || fillerSet.size === 0) return [];
    const result: { clipId: string; word: WordTimestamp }[] = [];
    for (const t of edl.transcripts) {
      t.words.forEach((w) => {
        if (!fillerSet.has(w.word.trim().toLowerCase())) return;
        // Only count it if the segment it falls in is currently kept.
        const ot = sourceToOutput(t.clip_id, w.start, edl);
        if (ot !== null) result.push({ clipId: t.clip_id, word: w });
      });
    }
    return result;
  }, [showFillers, fillerSet, edl]);

  const handleCutAllFillers = async () => {
    if (fillerMatches.length === 0 || cuttingFillers) return;
    setCuttingFillers(true);
    try {
      // Sequential calls: each cut can split a segment, so the next call
      // needs the updated structure to find its segment correctly.
      for (const m of fillerMatches) {
        await cutWord(jobId, m.clipId, m.word.start, m.word.end, preset);
      }
      onDirty?.();
    } finally {
      void qc.invalidateQueries({ queryKey: edlKey });
      setCuttingFillers(false);
    }
  };

  const handleReplace = () => {
    const match = matchList[activeMatchIdx];
    if (!match || !replaceValue.trim()) return;
    editMut.mutate({ clipId: match.clipId, wordIndex: match.wordIndex, word: replaceValue.trim() });
    onDirty?.();
  };

  const handleReplaceAll = async () => {
    const matches = matchList;
    if (matches.length === 0 || !replaceValue.trim()) return;
    const replacement = replaceValue.trim();
    await qc.cancelQueries({ queryKey: edlKey });
    const prev = qc.getQueryData<Edl>(edlKey);
    if (prev) {
      const matchSet = new Set(matches.map((m) => `${m.clipId}:${m.wordIndex}`));
      qc.setQueryData<Edl>(edlKey, {
        ...prev,
        transcripts: prev.transcripts.map((t) => ({
          ...t,
          words: t.words.map((w, i) =>
            matchSet.has(`${t.clip_id}:${i}`) ? { ...w, word: replacement } : w,
          ),
        })),
      });
    }
    try {
      await Promise.all(
        matches.map((m) => patchWord(jobId, m.clipId, m.wordIndex, replacement, preset)),
      );
      onDirty?.();
    } catch {
      if (prev) qc.setQueryData(edlKey, prev);
    } finally {
      void qc.invalidateQueries({ queryKey: edlKey });
    }
  };

  return (
    <section className="flex flex-col gap-3" data-transcript>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
          Κείμενο · κλικ σε λέξη για επεξεργασία ή κοπή
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowFillers((v) => !v)}
            title="Λέξεις γεμίσματος (um, uh, εε…)"
            className={[
              "rounded border px-1.5 py-0.5 text-xs transition",
              showFillers
                ? "border-amber-400/50 bg-amber-500/15 text-amber-200"
                : "border-white/10 bg-white/5 text-white/35 hover:border-white/20 hover:text-white/60",
            ].join(" ")}
          >
            ✂ fillers
          </button>
          <button
            type="button"
            onClick={() => setShowSearch((v) => !v)}
            title="Αναζήτηση στο κείμενο (Ctrl+F)"
            className={[
              "rounded border px-1.5 py-0.5 font-mono text-xs transition",
              showSearch
                ? "border-fuchsia-400/50 bg-fuchsia-500/15 text-fuchsia-200"
                : "border-white/10 bg-white/5 text-white/35 hover:border-white/20 hover:text-white/60",
            ].join(" ")}
          >
            ⌕
          </button>
        </div>
      </div>

      {showFillers ? (
        <FillerBar
          fillerList={fillerList}
          fillerCount={fillerMatches.length}
          busy={cuttingFillers}
          onChangeList={setFillerList}
          onCutAll={() => { void handleCutAllFillers(); }}
        />
      ) : null}

      {showSearch ? (
        <SearchBar
          query={searchQuery}
          matchCount={matchList.length}
          activeIdx={activeMatchIdx}
          onQueryChange={(q) => setSearchQuery(q)}
          onPrev={() => goToMatch((activeMatchIdx - 1 + matchList.length) % matchList.length)}
          onNext={() => goToMatch((activeMatchIdx + 1) % matchList.length)}
          onClose={() => { setShowSearch(false); setSearchQuery(""); setShowReplace(false); setReplaceValue(""); }}
          showReplace={showReplace}
          replaceValue={replaceValue}
          onToggleReplace={() => setShowReplace((v) => !v)}
          onReplaceValueChange={setReplaceValue}
          onReplace={handleReplace}
          onReplaceAll={() => { void handleReplaceAll(); }}
        />
      ) : null}

      <div className="glass-card flex max-h-80 flex-col gap-4 overflow-y-auto rounded-2xl p-5">
        {edl.transcripts.map((t) => (
          <ClipTranscript
            key={t.clip_id}
            transcript={t}
            segments={edl.segments}
            assignments={groupedByClip.get(t.clip_id) ?? []}
            selectedIndex={selectedIndex ?? null}
            activeWord={activeWord}
            searchQuery={searchQuery}
            currentMatchKey={currentMatchKey}
            editing={editing}
            fillerSet={showFillers ? fillerSet : null}
            onCommitEdit={commitEdit}
            onCancelEdit={() => setEditing(null)}
            onSeekToWord={(clipId, sourceTime) => {
              const ot = sourceToOutput(clipId, sourceTime, edl);
              if (ot !== null) onSeekRequest?.(ot);
            }}
            onWordClick={(a, x, y) => {
              // Seek + select segment on every word click.
              const ot = sourceToOutput(a.clipId, a.word.start, edl);
              if (ot !== null) onSeekRequest?.(ot);
              onSelect?.(a.segIndex);
              // Open the action menu.
              setWordMenu({ clipId: a.clipId, wordIndex: a.wordIndex, word: a.word, segIndex: a.segIndex, x, y });
            }}
          />
        ))}
      </div>

      {/* Word action popover — rendered in a portal to avoid overflow/z-index issues */}
      {wordMenu && typeof document !== "undefined" && createPortal(
        <div
          className="fixed z-50 flex flex-col overflow-hidden rounded-xl border border-white/10 bg-black/90 shadow-xl backdrop-blur-sm"
          style={{ left: wordMenu.x, top: wordMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="px-4 py-2 text-left text-xs text-white/80 transition hover:bg-white/8 hover:text-white"
            onClick={() => {
              setEditing({ clipId: wordMenu.clipId, wordIndex: wordMenu.wordIndex });
              setWordMenu(null);
            }}
          >
            ✏ Επεξεργασία κειμένου
          </button>
          <button
            type="button"
            className="px-4 py-2 text-left text-xs text-red-300/80 transition hover:bg-red-500/10 hover:text-red-200"
            onClick={() => {
              cutMut.mutate({ clipId: wordMenu.clipId, start: wordMenu.word.start, end: wordMenu.word.end });
              setWordMenu(null);
              onDirty?.();
            }}
          >
            ✂ Κόψε από βίντεο
          </button>
        </div>,
        document.body,
      )}

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


// ---------------------------------------------------------------------------
// Filler word bar
// ---------------------------------------------------------------------------

function FillerBar({
  fillerList,
  fillerCount,
  busy,
  onChangeList,
  onCutAll,
}: {
  fillerList: string[];
  fillerCount: number;
  busy: boolean;
  onChangeList: (list: string[]) => void;
  onCutAll: () => void;
}) {
  const [inputVal, setInputVal] = useState("");

  const addFiller = () => {
    const word = inputVal.trim().toLowerCase();
    if (!word || fillerList.includes(word)) { setInputVal(""); return; }
    onChangeList([...fillerList, word]);
    setInputVal("");
  };

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-400/25 bg-amber-500/8 px-3 py-2">
      <span className="shrink-0 text-xs text-amber-300/60">✂</span>
      {fillerList.map((f) => (
        <span
          key={f}
          className="inline-flex items-center gap-1 rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[11px] text-amber-200"
        >
          {f}
          <button
            type="button"
            onClick={() => onChangeList(fillerList.filter((x) => x !== f))}
            className="text-amber-400/60 transition hover:text-amber-200"
            aria-label={`Αφαίρεση ${f}`}
          >
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        value={inputVal}
        placeholder="+ προσθήκη…"
        onChange={(e) => setInputVal(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); addFiller(); }
        }}
        onBlur={addFiller}
        className="w-24 min-w-0 bg-transparent text-[11px] text-white/70 placeholder-white/25 outline-none"
      />
      <button
        type="button"
        onClick={onCutAll}
        disabled={fillerCount === 0 || busy}
        className="ml-auto shrink-0 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-xs font-medium text-amber-200 transition hover:border-amber-400/50 hover:bg-amber-400/20 disabled:opacity-30"
      >
        {busy ? "Κόβει…" : `Κόψε όλα (${fillerCount})`}
      </button>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Search bar
// ---------------------------------------------------------------------------

function SearchBar({
  query,
  matchCount,
  activeIdx,
  onQueryChange,
  onPrev,
  onNext,
  onClose,
  showReplace = false,
  replaceValue = "",
  onToggleReplace,
  onReplaceValueChange,
  onReplace,
  onReplaceAll,
}: {
  query: string;
  matchCount: number;
  activeIdx: number;
  onQueryChange: (q: string) => void;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  showReplace?: boolean;
  replaceValue?: string;
  onToggleReplace?: () => void;
  onReplaceValueChange?: (v: string) => void;
  onReplace?: () => void;
  onReplaceAll?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);

  const hasQuery = query.trim() !== "";
  const countLabel = hasQuery
    ? matchCount === 0
      ? "0 αποτελέσματα"
      : `${activeIdx + 1} / ${matchCount}`
    : "";

  return (
    <div className="flex flex-col gap-1 rounded-xl border border-fuchsia-400/25 bg-fuchsia-500/10 px-3 py-2">
      {/* Search row */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onToggleReplace}
          title={showReplace ? "Απόκρυψη αντικατάστασης" : "Εμφάνιση αντικατάστασης"}
          className="shrink-0 font-mono text-[10px] text-fuchsia-300/40 transition hover:text-fuchsia-200"
        >
          {showReplace ? "▼" : "▶"}
        </button>
        <span className="font-mono text-xs text-fuchsia-300/60">⌕</span>
        <input
          ref={inputRef}
          type="search"
          value={query}
          placeholder="Αναζήτηση λέξης…"
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.shiftKey ? onPrev() : onNext();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onClose();
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-sm text-white/90 placeholder-white/25 outline-none"
        />
        {hasQuery ? (
          <span className="shrink-0 font-mono text-[11px] text-fuchsia-300/60">
            {countLabel}
          </span>
        ) : null}
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={onPrev}
            disabled={matchCount === 0}
            title="Προηγούμενο (Shift+Enter)"
            className="rounded p-1 text-white/40 transition hover:text-white/80 disabled:opacity-25"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={matchCount === 0}
            title="Επόμενο (Enter)"
            className="rounded p-1 text-white/40 transition hover:text-white/80 disabled:opacity-25"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Κλείσιμο (Esc)"
            className="ml-1 rounded p-1 text-white/30 transition hover:text-white/70"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Replace row */}
      {showReplace ? (
        <div className="flex items-center gap-2 pl-5">
          <span className="shrink-0 font-mono text-xs text-fuchsia-300/40">↩</span>
          <input
            ref={replaceRef}
            type="text"
            value={replaceValue}
            placeholder="Αντικατάσταση με…"
            onChange={(e) => onReplaceValueChange?.(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); onReplace?.(); }
              else if (e.key === "Escape") { e.preventDefault(); onClose(); }
            }}
            className="min-w-0 flex-1 bg-transparent text-sm text-white/90 placeholder-white/25 outline-none"
          />
          <button
            type="button"
            onClick={onReplace}
            disabled={matchCount === 0 || !replaceValue.trim()}
            className="shrink-0 rounded-lg border border-white/10 px-2.5 py-0.5 text-xs text-white/50 transition hover:border-cyan-400/40 hover:text-cyan-200 disabled:opacity-30"
          >
            Αντικατάσταση
          </button>
          <button
            type="button"
            onClick={onReplaceAll}
            disabled={matchCount === 0 || !replaceValue.trim()}
            className="shrink-0 rounded-lg border border-white/10 px-2.5 py-0.5 text-xs text-white/50 transition hover:border-fuchsia-400/40 hover:text-fuchsia-200 disabled:opacity-30"
          >
            Όλα ({matchCount})
          </button>
        </div>
      ) : null}
    </div>
  );
}


function ClipTranscript({
  transcript,
  segments,
  assignments,
  selectedIndex,
  activeWord,
  searchQuery,
  currentMatchKey,
  editing,
  fillerSet,
  onCommitEdit,
  onCancelEdit,
  onSeekToWord,
  onWordClick,
}: {
  transcript: TranscriptT;
  segments: SegmentDict[];
  assignments: WordAssignment[];
  selectedIndex: number | null;
  activeWord: { clipId: string; wordIndex: number } | null;
  searchQuery: string;
  currentMatchKey: string | null;
  editing: { clipId: string; wordIndex: number } | null;
  fillerSet: Set<string> | null;
  onCommitEdit: (clipId: string, wordIndex: number, next: string) => void;
  onCancelEdit: () => void;
  onSeekToWord: (clipId: string, sourceTime: number) => void;
  onWordClick: (a: WordAssignment, x: number, y: number) => void;
}) {
  const activeRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [activeWord?.clipId, activeWord?.wordIndex]);

  // Scroll to current search match when it lands in this transcript.
  useEffect(() => {
    if (!currentMatchKey) return;
    const [matchClipId] = currentMatchKey.split(":");
    if (matchClipId !== transcript.clip_id) return;
    searchRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [currentMatchKey, transcript.clip_id]);
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
          const isPlaying =
            activeWord?.clipId === a.clipId && activeWord?.wordIndex === a.wordIndex;
          const wordKey = `${a.clipId}:${a.wordIndex}`;
          const isCurrentMatch = currentMatchKey === wordKey;
          const isMatch =
            searchQuery.trim() !== "" &&
            a.word.word.toLowerCase().includes(searchQuery.trim().toLowerCase());
          const prob = a.word.probability ?? 1;
          const isFiller = keep && !!fillerSet?.has(a.word.word.trim().toLowerCase());
          const confidenceClass =
            keep && !isPlaying && !isCurrentMatch && !isFiller
              ? prob < 0.6
                ? "underline decoration-orange-400/60 decoration-dotted underline-offset-2"
                : prob < 0.8
                ? "underline decoration-amber-400/50 decoration-dotted underline-offset-2"
                : ""
              : "";
          return (
            <button
              key={i}
              ref={isPlaying ? activeRef : isCurrentMatch ? searchRef : undefined}
              type="button"
              title={
                isFiller
                  ? "Λέξη γεμίσματος — κλικ για κοπή"
                  : prob < 0.8
                  ? `Πιθανότητα: ${(prob * 100).toFixed(0)}%`
                  : undefined
              }
              onClick={(e) => {
                e.stopPropagation();
                onSeekToWord(a.clipId, a.word.start);
                const rect = e.currentTarget.getBoundingClientRect();
                onWordClick(a, rect.left, rect.bottom + 4);
              }}
              className={[
                "inline rounded px-0.5 transition",
                keep
                  ? "text-white/90 hover:bg-cyan-400/15"
                  : "text-white/35 line-through hover:bg-white/5",
                active ? "ring-1 ring-fuchsia-400/70" : "",
                isFiller && !isPlaying && !isCurrentMatch
                  ? "bg-amber-400/20 text-amber-200 ring-1 ring-amber-400/40"
                  : "",
                isPlaying ? "bg-amber-400/30 ring-1 ring-amber-400/60 text-amber-100" : "",
                isCurrentMatch
                  ? "bg-fuchsia-400/40 ring-1 ring-fuchsia-400/70 text-fuchsia-100"
                  : isMatch
                  ? "bg-fuchsia-400/15 text-fuchsia-200"
                  : "",
                confidenceClass,
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
