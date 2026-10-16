"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import DownloadsPanel from "@/components/downloads-panel";
import PreviewPlayer from "@/components/preview-player";
import Timeline from "@/components/timeline";
import Transcript from "@/components/transcript";
import { ApiError, getEdl, getJob, postRender } from "@/lib/api";
import type { Job, JobConfig } from "@/lib/api.types";

const PRESET_OPTIONS: JobConfig["preset"][] = ["tight", "medium", "loose"];

const PRESET_LABEL: Record<string, string> = {
  tight: "Σφιχτό",
  medium: "Μέτριο",
  loose: "Χαλαρό",
};

const STATUS_LABEL: Record<"pending" | "running" | "done" | "failed", string> = {
  pending: "αναμονή",
  running: "τρέχει",
  done: "έτοιμο",
  failed: "απέτυχε",
};

function usePollWhileRunning(job: Job | undefined): number | false {
  return job && (job.status === "running" || job.status === "pending") ? 1_500 : false;
}

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const qc = useQueryClient();

  const [preset, setPreset] = useState<string | null>(null);
  // Burn subtitles into the mp4 on next render. Off by default so the base
  // output stays clean for NLE import; user opts in when they want a
  // standalone video with hard-coded captions.
  const [burnSubs, setBurnSubs] = useState(false);
  // "dirty" = user toggled a segment since the last render. The preview mp4
  // reflects the previous render only; we gate on this to tell the user when
  // their edits aren't visible yet.
  const [dirty, setDirty] = useState(false);
  // Reset dirty when the user switches preset — each preset has its own EDL
  // + rendered mp4, so dirty-against-preset-A doesn't apply to preset B.
  useEffect(() => {
    setDirty(false);
  }, [preset]);

  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (q) => usePollWhileRunning(q.state.data as Job | undefined),
  });
  const job = jobQuery.data;

  const edlPreset = resolvePreset(job?.config?.preset, preset);
  const edlQuery = useQuery({
    queryKey: ["edl", jobId, edlPreset],
    queryFn: () => getEdl(jobId, edlPreset ?? undefined),
    // Only fetch after we know the job is done and we've resolved a preset.
    enabled: job?.status === "done" && (edlPreset !== null || job?.config?.preset !== "all"),
    staleTime: 0,
  });

  const renderMut = useMutation({
    mutationFn: () =>
      postRender(jobId, edlPreset ?? undefined, { burnSubtitles: burnSubs }),
    onSuccess: () => {
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["job", jobId] });
    },
  });

  const isAll = job?.config?.preset === "all";
  // Re-render transiently flips the job back to "running"; capture that here
  // because the narrowed branch below loses the "running" possibility.
  const rendering = renderMut.isPending || job?.status === "running";

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-8">
      <header className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <Link href="/" className="text-sm text-gray-400 hover:text-white">
            ← Jobs
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold tracking-tight">
            Έλεγχος · {jobId.slice(0, 8)}…
          </h1>
          {job ? (
            <p className="text-xs text-gray-500">
              {STATUS_LABEL[job.status]} · {job.stage}
            </p>
          ) : null}
          {job?.config?.model_used ? (
            <p className="mt-0.5 text-xs text-sky-400">
              Σκοράρισμα από εκπαιδευμένο μοντέλο
            </p>
          ) : null}
          <Link
            href={`/jobs/${jobId}/overrides`}
            className="mt-1 inline-block text-xs text-sky-400 hover:text-sky-300"
          >
            Οι αλλαγές μου →
          </Link>
        </div>
        <RerenderButton
          busy={rendering}
          disabled={job?.status !== "done"}
          dirty={dirty}
          onClick={() => renderMut.mutate()}
        />
      </header>

      {renderMut.isError ? (
        <p className="text-sm text-red-400">
          {(renderMut.error as ApiError | Error).message}
        </p>
      ) : null}

      {isAll ? (
        <PresetPicker value={preset} onChange={setPreset} />
      ) : null}

      {job?.status !== "done" ? (
        <p className="text-sm text-gray-400">
          Περιμένουμε να τελειώσει το job για να το δεις.
        </p>
      ) : isAll && preset === null ? (
        <p className="text-sm text-gray-400">
          Αυτό το job έβγαλε τρεις διάρκειες. Διάλεξε μία για να δεις.
        </p>
      ) : edlQuery.isLoading ? (
        <p className="text-sm text-gray-500">Φόρτωση EDL…</p>
      ) : edlQuery.isError ? (
        <p className="text-sm text-red-400">
          {(edlQuery.error as ApiError | Error).message}
        </p>
      ) : edlQuery.data ? (
        <>
          {dirty ? (
            <div className="flex items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              <span>
                Έχεις αλλαγές που δεν φαίνονται ακόμη στο preview — πάτα{" "}
                <span className="font-semibold">«Ξαναφτιάξε βίντεο»</span> για να εφαρμοστούν.
              </span>
              <button
                type="button"
                onClick={() => renderMut.mutate()}
                disabled={renderMut.isPending}
                className="shrink-0 rounded-md bg-amber-400 px-2.5 py-1 text-xs font-semibold text-amber-950 transition disabled:cursor-not-allowed disabled:opacity-60"
              >
                {renderMut.isPending ? "Φτιάχνει…" : "Εφάρμοσε"}
              </button>
            </div>
          ) : null}
          <PreviewPlayer
            jobId={jobId}
            preset={edlPreset ?? undefined}
            cacheKey={job.updated_at}
            rendering={rendering}
          />
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={burnSubs}
              onChange={(e) => {
                setBurnSubs(e.target.checked);
                setDirty(true);
              }}
              className="h-4 w-4 accent-sky-500"
            />
            <span>
              Υπότιτλοι μέσα στο βίντεο
              <span className="ml-1 text-xs text-gray-500">
                (μόνο για τις λέξεις που κρατάς· χρειάζεται re-render)
              </span>
            </span>
          </label>
          <Timeline
            jobId={jobId}
            preset={edlPreset ?? undefined}
            edl={edlQuery.data}
            onDirty={() => setDirty(true)}
          />
          <Transcript
            jobId={jobId}
            preset={edlPreset ?? undefined}
            edl={edlQuery.data}
            onDirty={() => setDirty(true)}
          />
          <DownloadsPanel
            jobId={jobId}
            preset={edlPreset ?? undefined}
            cacheKey={job.updated_at}
          />
        </>
      ) : null}
    </main>
  );
}

/** Resolve which preset's EDL to fetch. Returns null when the user must still choose. */
function resolvePreset(
  jobPreset: JobConfig["preset"] | undefined,
  userChoice: string | null,
): string | null {
  if (userChoice) return userChoice;
  if (!jobPreset || jobPreset === "all") return null;
  return jobPreset;
}

function RerenderButton({
  busy,
  disabled,
  dirty,
  onClick,
}: {
  busy: boolean;
  disabled: boolean;
  dirty: boolean;
  onClick: () => void;
}) {
  const base = "shrink-0 rounded-md px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:bg-gray-700 disabled:text-gray-400";
  const color = dirty
    ? "bg-amber-400 text-amber-950 ring-2 ring-amber-300/60 animate-pulse"
    : "bg-emerald-500 text-emerald-950";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className={`${base} ${color}`}
    >
      {busy ? "Φτιάχνει βίντεο…" : "Ξαναφτιάξε βίντεο"}
    </button>
  );
}

function PresetPicker({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex gap-2">
      {PRESET_OPTIONS.map((p) => {
        const active = value === p;
        return (
          <button
            key={p}
            type="button"
            onClick={() => onChange(p)}
            className={`rounded-md px-3 py-1.5 text-sm transition ${
              active
                ? "bg-emerald-500 text-emerald-950"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            }`}
          >
            {PRESET_LABEL[p] ?? p}
          </button>
        );
      })}
    </div>
  );
}
