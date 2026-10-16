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
  const [items, setItems] = useState<UploadItem[]>([]);
  const [preset, setPreset] = useState<JobConfig["preset"]>("medium");
  const [hook, setHook] = useState(false);
  const [pacing, setPacing] = useState(false);
  const [zoom, setZoom] = useState(false);
  const [creatingJob, setCreatingJob] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);

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

  const anyInFlight = items.some((it) => it.state === "uploading");

  // Pacing is only meaningful when a preset drives selection.
  const pacingDisabled = preset === "none";

  const canCreateJob = completedIds.length > 0 && !anyInFlight && !creatingJob;

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
          className="w-full rounded-lg border border-dashed border-gray-700 px-4 py-6 text-center text-sm text-gray-300 transition hover:border-gray-500 hover:text-white"
        >
          Πάτα για να διαλέξεις βίντεο
        </button>
      </div>

      {items.length > 0 ? (
        <>
          <p className="text-xs text-gray-400">
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

      <fieldset
        className="flex flex-col gap-3 rounded-lg border border-gray-800 p-4"
        disabled={!canCreateJob}
      >
        <legend className="px-1 text-xs uppercase tracking-wide text-gray-400">
          Επιλογές
        </legend>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-300">Διάρκεια</span>
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value as JobConfig["preset"])}
            className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm"
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

      {jobError ? (
        <p className="text-sm text-red-400">{jobError}</p>
      ) : null}

      <button
        type="button"
        onClick={handleCreateJob}
        disabled={!canCreateJob}
        className="rounded-lg bg-emerald-500 px-4 py-3 text-sm font-semibold text-emerald-950 transition disabled:cursor-not-allowed disabled:bg-gray-700 disabled:text-gray-400"
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
    <li className="flex items-stretch gap-2 rounded-md border border-gray-800 bg-gray-900/60 p-3">
      <div className="flex w-6 shrink-0 items-center justify-center text-xs font-semibold text-gray-500">
        {index + 1}
      </div>
      <div className="flex min-w-0 flex-1 flex-col justify-center">
        <div className="flex items-center justify-between gap-2 text-sm">
          <span className="truncate font-medium" title={item.file.name}>
            {item.file.name}
          </span>
          <span className="shrink-0 text-xs text-gray-400">
            {formatBytes(item.file.size)} · {labelFor(item.state)}
          </span>
        </div>
        <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-gray-800">
          <div className={`h-full ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        {item.error ? (
          <p className="mt-2 text-xs text-red-400">{item.error}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col items-center justify-center gap-1">
        <OrderButton label="Πάνω" symbol="↑" disabled={isFirst} onClick={onMoveUp} />
        <OrderButton label="Κάτω" symbol="↓" disabled={isLast} onClick={onMoveDown} />
        <button
          type="button"
          aria-label="Αφαίρεση"
          onClick={onRemove}
          className="rounded-md px-1.5 py-0.5 text-xs text-gray-500 transition hover:text-red-400"
        >
          ×
        </button>
      </div>
    </li>
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
      className="rounded-md border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-xs text-gray-300 transition hover:border-gray-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-30"
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
