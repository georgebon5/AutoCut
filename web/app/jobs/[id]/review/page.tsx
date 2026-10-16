"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import CaptionStylePicker from "@/components/caption-style-picker";
import DownloadsPanel from "@/components/downloads-panel";
import PreviewPlayer from "@/components/preview-player";
import Timeline from "@/components/timeline";
import Transcript from "@/components/transcript";
import { ApiError, getEdl, getJob, postRender } from "@/lib/api";
import type { CaptionStyle, Job, JobConfig } from "@/lib/api.types";
import { usePersistedState } from "@/lib/use-persisted-state";

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
  // UI preferences are persisted per-job in localStorage so a page reload
  // (or coming back later) keeps the user's picks around. Keyed by jobId so
  // different videos remember their own styling. Preset is intentionally
  // NOT persisted — a persisted "tight" from one job would misroute to a
  // non-existent EDL on a medium-only job.
  const [burnSubs, setBurnSubs] = usePersistedState<boolean>(
    `autocut:${jobId}:burnSubs`, false,
  );
  const [captionStyle, setCaptionStyle] = usePersistedState<CaptionStyle>(
    `autocut:${jobId}:captionStyle`, {},
  );
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
      postRender(jobId, edlPreset ?? undefined, {
        burnSubtitles: burnSubs,
        captionStyle: burnSubs ? captionStyle : undefined,
      }),
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
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-12">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/"
            className="text-xs font-medium uppercase tracking-[0.2em] text-white/40 transition hover:text-white/70"
          >
            ← Jobs
          </Link>
          <h1 className="mt-2 bg-gradient-to-br from-white to-white/60 bg-clip-text text-2xl font-light tracking-tight text-transparent">
            Έλεγχος <span className="font-mono text-lg text-white/40">· {jobId.slice(0, 8)}</span>
          </h1>
          {job ? (
            <p className="mt-1 text-xs text-white/40">
              {STATUS_LABEL[job.status]} · {job.stage}
            </p>
          ) : null}
          {job?.config?.model_used ? (
            <p className="mt-1 text-xs text-cyan-300/80">
              Σκοράρισμα από εκπαιδευμένο μοντέλο
            </p>
          ) : null}
          <Link
            href={`/jobs/${jobId}/overrides`}
            className="mt-2 inline-block text-xs text-white/50 transition hover:text-cyan-300"
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

      {/* Backend render errors (background job) land on job.error even when
          status is "done" — surface them so the user sees why the last
          re-render didn't apply. */}
      {job?.error && !rendering ? (
        <div className="rounded-2xl border border-red-400/20 bg-red-500/5 p-4 text-sm text-red-100/90 backdrop-blur">
          <div className="font-medium">Αποτυχία τελευταίας εφαρμογής αλλαγών</div>
          <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-xs text-red-200/70">
            {job.error}
          </pre>
        </div>
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
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-amber-400/20 bg-amber-400/5 px-4 py-3 text-sm text-amber-100/90 backdrop-blur">
              <span>
                Έχεις αλλαγές που δεν φαίνονται ακόμη στο preview — πάτα{" "}
                <span className="font-semibold">«Ξαναφτιάξε βίντεο»</span> για να εφαρμοστούν.
              </span>
              <button
                type="button"
                onClick={() => renderMut.mutate()}
                disabled={renderMut.isPending}
                className="shrink-0 rounded-xl bg-amber-400 px-3 py-1.5 text-xs font-semibold text-amber-950 transition hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-60"
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
          <div className="glass-card flex flex-col gap-4 rounded-2xl p-4">
            <label className="flex items-center gap-3 text-sm text-white/80">
              <input
                type="checkbox"
                checked={burnSubs}
                onChange={(e) => {
                  setBurnSubs(e.target.checked);
                  setDirty(true);
                }}
                className="h-4 w-4 accent-cyan-400"
              />
              <span>
                Υπότιτλοι μέσα στο βίντεο
                <span className="ml-1 text-xs text-white/40">
                  (μόνο για τις λέξεις που κρατάς · χρειάζεται re-render)
                </span>
              </span>
            </label>
            {burnSubs ? (
              <CaptionStylePicker
                value={captionStyle}
                onChange={(next) => {
                  setCaptionStyle(next);
                  setDirty(true);
                }}
              />
            ) : null}
          </div>
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
  const base = "shrink-0 rounded-xl px-4 py-2.5 text-sm font-semibold tracking-wide transition disabled:cursor-not-allowed disabled:text-white/30";
  const color = dirty
    ? "bg-amber-400 text-amber-950 shadow-xl shadow-amber-400/25 animate-pulse"
    : "btn-primary-gradient shadow-xl shadow-cyan-500/15";
  const disabledLook = "disabled:bg-none disabled:bg-white/5 disabled:shadow-none";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className={`${base} ${color} ${disabledLook}`}
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
    <div className="glass-card inline-flex gap-1 rounded-2xl p-1">
      {PRESET_OPTIONS.map((p) => {
        const active = value === p;
        return (
          <button
            key={p}
            type="button"
            onClick={() => onChange(p)}
            className={`rounded-xl px-4 py-1.5 text-sm transition ${
              active
                ? "btn-primary-gradient font-semibold shadow-lg shadow-cyan-500/20"
                : "text-white/60 hover:bg-white/5 hover:text-white"
            }`}
          >
            {PRESET_LABEL[p] ?? p}
          </button>
        );
      })}
    </div>
  );
}
