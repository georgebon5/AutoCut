"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import CaptionStylePicker from "@/components/caption-style-picker";
import DownloadsPanel from "@/components/downloads-panel";
import PreviewPlayer from "@/components/preview-player";
import Timeline from "@/components/timeline";
import Transcript from "@/components/transcript";
import { ApiError, getEdl, getJob, patchJobLabel, postRender } from "@/lib/api";
import { useJobEvents } from "@/lib/use-job-events";
import type { CaptionStyle, Edl, Job, JobConfig } from "@/lib/api.types";
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
  // Seek request: when the user clicks a segment in the Timeline, the player
  // jumps to that segment's position in the output video.  The `seq` counter
  // lets us re-seek to the same position on a second click.
  const [seekRequest, setSeekRequest] = useState<{ time: number; seq: number } | undefined>();
  const seekSeq = useRef(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [editingLabel, setEditingLabel] = useState(false);
  const [labelDraft, setLabelDraft] = useState("");
  // Reset dirty when the user switches preset — each preset has its own EDL
  // + rendered mp4, so dirty-against-preset-A doesn't apply to preset B.
  useEffect(() => {
    setDirty(false);
  }, [preset]);

  const jobQuery = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
    // SSE drives fast invalidation; keep a slow fallback for reconnect gaps.
    refetchInterval: 10_000,
  });
  const job = jobQuery.data;

  const isLive = job?.status === "pending" || job?.status === "running";
  useJobEvents(
    jobId,
    isLive ?? false,
    () => qc.invalidateQueries({ queryKey: ["job", jobId] }),
    () => {
      qc.invalidateQueries({ queryKey: ["job", jobId] });
      qc.invalidateQueries({ queryKey: ["edl", jobId] });
    },
  );

  const edlPreset = resolvePreset(job?.config?.preset, preset);
  const edlQuery = useQuery({
    queryKey: ["edl", jobId, edlPreset],
    queryFn: () => getEdl(jobId, edlPreset ?? undefined),
    // Only fetch after we know the job is done and we've resolved a preset.
    enabled: job?.status === "done" && (edlPreset !== null || job?.config?.preset !== "all"),
    staleTime: 0,
  });

  const labelMut = useMutation({
    mutationFn: (next: string | null) => patchJobLabel(jobId, next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", jobId] }),
  });

  const commitLabel = useCallback(() => {
    setEditingLabel(false);
    const trimmed = labelDraft.trim();
    const current = job?.label ?? "";
    if (trimmed === current) return;
    labelMut.mutate(trimmed || null);
  }, [labelDraft, job?.label, labelMut]);

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
        <div className="min-w-0 flex-1">
          <Link
            href="/"
            className="text-xs font-medium uppercase tracking-[0.2em] text-white/40 transition hover:text-white/70"
          >
            ← Jobs
          </Link>
          {/* Inline-editable label */}
          <div className="mt-2">
            {editingLabel ? (
              <input
                autoFocus
                type="text"
                maxLength={120}
                value={labelDraft}
                onChange={(e) => setLabelDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); commitLabel(); }
                  if (e.key === "Escape") { e.preventDefault(); setEditingLabel(false); }
                }}
                onBlur={commitLabel}
                placeholder="Πρόσθεσε τίτλο…"
                className="w-full max-w-xs rounded-xl border border-cyan-400/40 bg-black/40 px-3 py-1.5 text-xl font-light text-white/90 outline-none placeholder-white/20 focus:border-cyan-400/70"
              />
            ) : (
              <button
                type="button"
                onClick={() => { setLabelDraft(job?.label ?? ""); setEditingLabel(true); }}
                className="group flex items-center gap-2 text-left"
                title="Κλικ για επεξεργασία τίτλου"
              >
                {job?.label ? (
                  <h1 className="bg-gradient-to-br from-white to-white/60 bg-clip-text text-2xl font-light tracking-tight text-transparent">
                    {job.label}
                  </h1>
                ) : (
                  <h1 className="text-2xl font-light tracking-tight text-white/90">
                    Έλεγχος{" "}
                    <span className="font-mono text-lg text-white/40">· {jobId.slice(0, 8)}</span>
                  </h1>
                )}
                <span className="text-xs text-white/20 opacity-0 transition group-hover:opacity-100">
                  ✎
                </span>
              </button>
            )}
          </div>
          {job ? (
            <p className="mt-1 text-xs text-white/40">
              {STATUS_LABEL[job.status]} · {job.stage}
              {job.label ? (
                <span className="ml-2 font-mono text-white/25">{jobId.slice(0, 8)}</span>
              ) : null}
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
        <JobProgressCard job={job} />
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
            edl={edlQuery.data}
            seekRequest={seekRequest}
            onPlaybackTime={setCurrentTime}
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
            onSeekRequest={(t) => {
              seekSeq.current += 1;
              setSeekRequest({ time: t, seq: seekSeq.current });
            }}
          />
          <DurationBar edl={edlQuery.data} />
          <Transcript
            jobId={jobId}
            preset={edlPreset ?? undefined}
            edl={edlQuery.data}
            onDirty={() => setDirty(true)}
            currentTime={currentTime}
            onSeekRequest={(t) => {
              seekSeq.current += 1;
              setSeekRequest({ time: t, seq: seekSeq.current });
            }}
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


// ---------------------------------------------------------------------------
// Live progress card shown while the job is running / pending
// ---------------------------------------------------------------------------

const STAGE_LABEL: Record<string, string> = {
  created:         "Αρχικοποίηση…",
  ingesting:       "Εισαγωγή clips…",
  vad:             "Ανίχνευση ομιλίας…",
  transcribing:    "Μεταγραφή…",
  fillers:         "Εντοπισμός fillers…",
  takes:           "Ανάλυση takes…",
  silence_removal: "Αφαίρεση σιωπών…",
  features:        "Ανάλυση περιεχομένου…",
  hook:            "Εύρεση hook…",
  semantic:        "Semantic matching…",
  rendering:       "Δημιουργία βίντεο…",
  captions:        "Υπότιτλοι…",
  complete:        "Ολοκληρώθηκε",
  error:           "Σφάλμα",
};

function JobProgressCard({ job }: { job: Job | undefined }) {
  if (!job) {
    return (
      <div className="glass-card rounded-2xl p-6 text-center text-sm text-white/40">
        Φόρτωση…
      </div>
    );
  }

  if (job.status === "failed") return null;   // error card is shown separately above

  const pct = Math.round(job.progress * 100);
  const stageLabel = STAGE_LABEL[job.stage] ?? job.stage;

  return (
    <div className="glass-card rounded-2xl p-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent" />
          <div>
            <p className="text-sm font-medium text-white/90">{stageLabel}</p>
            <p className="text-xs text-white/40">
              {pct}% · {STATUS_LABEL[job.status]}
            </p>
          </div>
        </div>
        <span className="font-mono text-lg font-light text-white/50">{pct}%</span>
      </div>
      <div className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-cyan-400 via-indigo-400 to-fuchsia-400 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Duration summary bar shown above the transcript when EDL is loaded
// ---------------------------------------------------------------------------

function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function DurationBar({ edl }: { edl: Edl | undefined }) {
  if (!edl) return null;

  const totalSource = edl.clips.reduce((acc, c) => acc + c.duration, 0);
  const kept = edl.segments
    .filter((s) => s.decision === "keep")
    .reduce((acc, s) => acc + (s.end - s.start), 0);

  if (totalSource === 0) return null;

  const pct = Math.min(100, (kept / totalSource) * 100);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-white/50">
          Κρατάς{" "}
          <span className="font-medium text-white/80">{fmtDur(kept)}</span>
        </span>
        <span className="text-white/35">
          {fmtDur(totalSource)} σύνολο · {Math.round(pct)}%
        </span>
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-white/5">
        <div
          className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-cyan-400 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
