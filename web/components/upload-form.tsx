"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ApiError, createJob } from "@/lib/api";
import { uploadFileChunked } from "@/lib/upload";
import type { JobConfig } from "@/lib/api.types";

type ItemState = "pending" | "uploading" | "complete" | "failed";

interface UploadItem {
  file: File;
  state: ItemState;
  uploaded: number;      // bytes
  uploadId?: string;
  error?: string;
}

const PRESETS: { value: JobConfig["preset"]; label: string }[] = [
  { value: "none", label: "Κανένα, όλο το κομμάτι χωρίς σιωπές" },
  { value: "tight", label: "Σφιχτό, ~45s" },
  { value: "medium", label: "Μέτριο, ~90s" },
  { value: "loose", label: "Χαλαρό, ~3 λεπτά" },
  { value: "all", label: "Και τα τρία (45s + 90s + 3 λεπτά)" },
];

export default function UploadForm() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const voiceoverInputRef = useRef<HTMLInputElement | null>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [preset, setPreset] = useState<JobConfig["preset"]>("medium");
  const [hook, setHook] = useState(false);
  const [pacing, setPacing] = useState(false);
  const [zoom, setZoom] = useState(false);
  const [creatingJob, setCreatingJob] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);

  // Voiceover-vlog mode: a separate audio file (narration) is uploaded
  // alongside the clips. When present, the clips become muted B-roll and
  // the voiceover drives the audio + cut points.
  const [voiceoverMode, setVoiceoverMode] = useState(false);
  const [voiceover, setVoiceover] = useState<UploadItem | null>(null);

  // Post-production polish applied at render time (both default off).
  const [audioEnhance, setAudioEnhance] = useState(false);
  const [audioDenoise, setAudioDenoise] = useState(false);
  const [outputAspect, setOutputAspect] = useState<"source" | "9:16" | "16:9">("source");

  // Reorder by file identity, not index, so an in-flight upload tracked by
  // `addFiles` keeps writing to the right slot after a reorder.
  const moveItem = (file: File, direction: -1 | 1) =>
    setItems((prev) => {
      const idx = prev.indexOf(prev.find((it) => it.file === file)!);
      const target = idx + direction;
      if (idx < 0 || target < 0 || target >= prev.length) return prev;
      const next = prev.slice();
      [next[idx], next[target]] = [next[target]!, next[idx]!];
      return next;
    });

  const removeItem = (file: File) =>
    setItems((prev) => prev.filter((it) => it.file !== file));

  // Patch an item by file identity — reorder/remove may shift its index.
  const patchByFile = (file: File, patch: Partial<UploadItem>) =>
    setItems((prev) => prev.map((it) => (it.file === file ? { ...it, ...patch } : it)));

  const addFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const starting = Array.from(files).map(
      (f): UploadItem => ({ file: f, state: "pending", uploaded: 0 }),
    );
    setItems((prev) => [...prev, ...starting]);

    // Upload in parallel across files, sequentially within each.
    await Promise.allSettled(
      starting.map(async (it) => {
        patchByFile(it.file, { state: "uploading" });
        try {
          const uploadId = await uploadFileChunked(it.file, {
            onProgress: (bytes) => patchByFile(it.file, { uploaded: bytes }),
          });
          patchByFile(it.file, { state: "complete", uploadId });
        } catch (e) {
          const msg = e instanceof ApiError ? e.detail : String(e);
          patchByFile(it.file, { state: "failed", error: msg });
        }
      }),
    );
  };

  const completedIds = items
    .filter((it) => it.state === "complete" && it.uploadId)
    .map((it) => it.uploadId!) as string[];

  const anyInFlight =
    items.some((it) => it.state === "uploading")
    || voiceover?.state === "uploading";

  // Voiceover file upload: single file, same chunked upload machinery.
  const addVoiceover = async (file: File) => {
    const starting: UploadItem = { file, state: "pending", uploaded: 0 };
    setVoiceover(starting);
    const patch = (p: Partial<UploadItem>) =>
      setVoiceover((prev) => (prev ? { ...prev, ...p } : prev));
    patch({ state: "uploading" });
    try {
      const uploadId = await uploadFileChunked(file, {
        onProgress: (bytes) => patch({ uploaded: bytes }),
      });
      patch({ state: "complete", uploadId });
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e);
      patch({ state: "failed", error: msg });
    }
  };

  const voiceoverReady =
    !voiceoverMode || (voiceover?.state === "complete" && !!voiceover.uploadId);

  // Pacing is only meaningful when a preset drives selection.
  const pacingDisabled = preset === "none";

  const canCreateJob =
    completedIds.length > 0 && !anyInFlight && !creatingJob && voiceoverReady;

  const handleCreateJob = async () => {
    setCreatingJob(true);
    setJobError(null);
    try {
      await createJob({
        upload_ids: completedIds,
        preset,
        hook,
        pacing: pacingDisabled ? false : pacing,
        zoom,
        voiceover_upload_id:
          voiceoverMode && voiceover?.uploadId ? voiceover.uploadId : undefined,
        audio_enhance: audioEnhance,
        audio_denoise: audioDenoise,
        output_aspect: outputAspect,
      });
      router.push("/");
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e);
      setJobError(msg);
      setCreatingJob(false);
    }
  };

  return (
    <section className="flex flex-col gap-6">
      <div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="video/*"
          className="hidden"
          onChange={(e) => {
            void addFiles(e.target.files);
            if (inputRef.current) inputRef.current.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="group relative w-full overflow-hidden rounded-2xl border border-dashed border-white/15 bg-white/[0.02] px-4 py-10 text-center text-sm text-white/70 transition hover:border-cyan-400/40 hover:bg-white/[0.04] hover:text-white"
        >
          <span className="relative z-10 font-light tracking-wide">
            Πάτα για να διαλέξεις βίντεο
          </span>
          <span className="pointer-events-none absolute inset-0 opacity-0 transition group-hover:opacity-100" style={{
            background: "radial-gradient(400px circle at var(--x,50%) var(--y,50%), rgba(34,211,238,0.08), transparent 60%)",
          }} />
        </button>
      </div>

      {items.length > 0 ? (
        <>
          <p className="text-xs text-white/50">
            Η σειρά παρακάτω είναι αυτή που θα μπουν στο τελικό cut.
            Σύρε με τα βελάκια για αναδιάταξη (π.χ. πρωί → μεσημέρι → απόγευμα).
          </p>
          <ul className="flex flex-col gap-2">
            {items.map((it, i) => (
              <UploadItemRow
                key={it.file.name + ":" + it.file.lastModified + ":" + it.file.size}
                item={it}
                index={i}
                isFirst={i === 0}
                isLast={i === items.length - 1}
                onMoveUp={() => moveItem(it.file, -1)}
                onMoveDown={() => moveItem(it.file, 1)}
                onRemove={() => removeItem(it.file)}
              />
            ))}
          </ul>
        </>
      ) : null}

      <div className="glass-card flex flex-col gap-3 rounded-2xl p-4">
        <label className="flex items-center gap-3 text-sm">
          <input
            type="checkbox"
            checked={voiceoverMode}
            onChange={(e) => {
              setVoiceoverMode(e.target.checked);
              if (!e.target.checked) setVoiceover(null);
            }}
            className="h-4 w-4 accent-cyan-400"
          />
          <span>
            Voiceover vlog
            <span className="ml-1 text-xs text-white/50">
              (τα clips μπαίνουν muted σαν B-roll, η φωνή της αφήγησης είναι ο ήχος)
            </span>
          </span>
        </label>

        {voiceoverMode ? (
          <>
            <input
              ref={voiceoverInputRef}
              type="file"
              accept="audio/*,.mp3,.wav,.m4a,.aac"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void addVoiceover(f);
                if (voiceoverInputRef.current) voiceoverInputRef.current.value = "";
              }}
            />
            {!voiceover ? (
              <button
                type="button"
                onClick={() => voiceoverInputRef.current?.click()}
                className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] px-4 py-5 text-center text-sm text-white/70 transition hover:border-cyan-400/40 hover:text-white"
              >
                Πάτα για να διαλέξεις αρχείο αφήγησης (mp3 / wav / m4a)
              </button>
            ) : (
              <VoiceoverRow
                item={voiceover}
                onRemove={() => setVoiceover(null)}
              />
            )}
          </>
        ) : null}
      </div>

      <fieldset
        className="glass-card flex flex-col gap-4 rounded-2xl p-5"
        disabled={!canCreateJob}
      >
        <legend className="px-2 text-[11px] font-medium uppercase tracking-[0.2em] text-white/40">
          Επιλογές
        </legend>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="text-white/60">Διάρκεια</span>
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value as JobConfig["preset"])}
            className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm transition focus:border-cyan-400/50 focus:outline-none"
          >
            {PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <Checkbox
          label="Opening hook (το καλύτερο 1-3s στην αρχή σαν teaser)"
          checked={hook}
          onChange={setHook}
        />
        <Checkbox
          label={`Γρήγορο ξεκίνημα${pacingDisabled ? " (θέλει διάρκεια)" : ""}`}
          checked={pacing && !pacingDisabled}
          disabled={pacingDisabled}
          onChange={setPacing}
        />
        <Checkbox label="Μικρό zoom στα highlights" checked={zoom} onChange={setZoom} />
      </fieldset>

      <fieldset
        className="glass-card flex flex-col gap-4 rounded-2xl p-5"
        disabled={!canCreateJob}
      >
        <legend className="px-2 text-[11px] font-medium uppercase tracking-[0.2em] text-white/40">
          Ποιότητα εξόδου
        </legend>

        <label className="flex flex-col gap-1.5 text-sm">
          <span className="text-white/60">Format</span>
          <select
            value={outputAspect}
            onChange={(e) => setOutputAspect(e.target.value as typeof outputAspect)}
            className="rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm transition focus:border-cyan-400/50 focus:outline-none"
          >
            <option value="source">Όπως το αρχικό</option>
            <option value="9:16">Κάθετο 9:16 (TikTok / Reels)</option>
            <option value="16:9">Οριζόντιο 16:9 (YouTube)</option>
          </select>
        </label>

        <Checkbox
          label="Καθαρισμός φωνής (compressor + loudness normalisation)"
          checked={audioEnhance}
          onChange={setAudioEnhance}
        />
        <Checkbox
          label="Μείωση θορύβου περιβάλλοντος (αέρας, βουή, hiss)"
          checked={audioDenoise}
          onChange={setAudioDenoise}
        />
      </fieldset>

      {jobError ? (
        <p className="text-sm text-red-300/90">{jobError}</p>
      ) : null}

      <button
        type="button"
        onClick={handleCreateJob}
        disabled={!canCreateJob}
        className="btn-primary-gradient rounded-2xl px-5 py-4 text-sm font-semibold tracking-wide shadow-xl shadow-cyan-500/15 transition disabled:cursor-not-allowed disabled:bg-none disabled:bg-white/5 disabled:text-white/30 disabled:shadow-none"
      >
        {creatingJob
          ? "Ξεκινάει…"
          : `Ξεκίνα με ${completedIds.length} clip${completedIds.length === 1 ? "" : "s"}`}
      </button>
    </section>
  );
}

function UploadItemRow({
  item,
  index,
  isFirst,
  isLast,
  onMoveUp,
  onMoveDown,
  onRemove,
}: {
  item: UploadItem;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
}) {
  const pct = item.file.size > 0
    ? Math.round((item.uploaded / item.file.size) * 100)
    : 0;
  const barColor =
    item.state === "failed" ? "bg-red-500"
      : item.state === "complete" ? "bg-emerald-500"
        : "bg-sky-500";

  return (
    <li className="glass-card flex items-stretch gap-3 rounded-2xl p-3">
      <div className="flex w-7 shrink-0 items-center justify-center font-mono text-xs text-white/40">
        {String(index + 1).padStart(2, "0")}
      </div>
      <div className="flex min-w-0 flex-1 flex-col justify-center">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span className="truncate font-medium text-white/90" title={item.file.name}>
            {item.file.name}
          </span>
          <span className="shrink-0 text-xs text-white/40">
            {formatBytes(item.file.size)} · {labelFor(item.state)}
          </span>
        </div>
        <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-white/5">
          <div className={`h-full ${barColor} transition-all duration-300`} style={{ width: `${pct}%` }} />
        </div>
        {item.error ? (
          <p className="mt-2 text-xs text-red-300/90">{item.error}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col items-center justify-center gap-1">
        <OrderButton label="Πάνω" symbol="↑" disabled={isFirst} onClick={onMoveUp} />
        <OrderButton label="Κάτω" symbol="↓" disabled={isLast} onClick={onMoveDown} />
        <button
          type="button"
          aria-label="Αφαίρεση"
          onClick={onRemove}
          className="rounded-md px-1.5 py-0.5 text-xs text-white/40 transition hover:text-red-300"
        >
          ×
        </button>
      </div>
    </li>
  );
}

function VoiceoverRow({
  item,
  onRemove,
}: {
  item: UploadItem;
  onRemove: () => void;
}) {
  const pct =
    item.file.size > 0 ? Math.round((item.uploaded / item.file.size) * 100) : 0;
  const barColor =
    item.state === "failed" ? "bg-red-500"
      : item.state === "complete" ? "bg-emerald-500"
        : "bg-sky-500";
  return (
    <div className="flex items-stretch gap-3 rounded-xl border border-white/10 bg-black/30 p-3">
      <div className="flex min-w-0 flex-1 flex-col justify-center">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span className="truncate font-medium text-white/90" title={item.file.name}>
            🎙 {item.file.name}
          </span>
          <span className="shrink-0 text-xs text-white/40">
            {formatBytes(item.file.size)} · {labelFor(item.state)}
          </span>
        </div>
        <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-white/5">
          <div
            className={`h-full ${barColor} transition-all duration-300`}
            style={{ width: `${pct}%` }}
          />
        </div>
        {item.error ? (
          <p className="mt-2 text-xs text-red-300/90">{item.error}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label="Αφαίρεση voiceover"
        className="shrink-0 self-start rounded-md px-2 py-1 text-xs text-white/40 transition hover:text-red-300"
      >
        ×
      </button>
    </div>
  );
}


function OrderButton({
  label,
  symbol,
  disabled,
  onClick,
}: {
  label: string;
  symbol: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-xs text-white/70 transition hover:border-cyan-400/40 hover:text-white disabled:cursor-not-allowed disabled:opacity-30"
    >
      {symbol}
    </button>
  );
}

function Checkbox({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-center gap-2 text-sm ${disabled ? "opacity-50" : ""}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-emerald-500"
      />
      <span>{label}</span>
    </label>
  );
}

function labelFor(state: ItemState): string {
  switch (state) {
    case "pending":   return "αναμονή";
    case "uploading": return "ανεβαίνει…";
    case "complete":  return "έτοιμο";
    case "failed":    return "απέτυχε";
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
