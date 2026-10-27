"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { ApiError, deleteJob, getTrainStatus, listJobs, triggerTrain } from "@/lib/api";
import type { Job, TrainStatus } from "@/lib/api.types";

/** Only running jobs need live polling; terminal jobs are checked less often. */
function usePollingInterval(jobs: Job[] | undefined): number | false {
  if (!jobs) return false;
  const live = jobs.some((j) => j.status === "pending" || j.status === "running");
  return live ? 1_500 : false;
}

export default function HomePage() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: (q) => usePollingInterval(q.state.data as Job[] | undefined),
  });

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-8 px-4 py-14">
      <header>
        <h1 className="bg-gradient-to-br from-white to-white/60 bg-clip-text text-3xl font-light tracking-tight text-transparent">
          AutoCut
        </h1>
      </header>

      <Link
        href="/upload"
        className="btn-primary-gradient rounded-2xl px-5 py-4 text-center text-sm font-semibold tracking-wide shadow-xl shadow-cyan-500/15 transition"
      >
        + Ανέβασε clips
      </Link>

      <IntelligencePanel />

      <section>
        <h2 className="mb-4 text-xs font-medium uppercase tracking-[0.2em] text-white/40">
          Jobs
        </h2>
        {jobs.isLoading ? (
          <p className="text-sm text-white/40">Φόρτωση…</p>
        ) : jobs.isError ? (
          <p className="text-sm text-red-300">
            Δεν φορτώθηκαν τα jobs. Τρέχει το API;
          </p>
        ) : jobs.data && jobs.data.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {jobs.data.map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-white/40">
            Κανένα job ακόμα. Πάτα “Ανέβασε clips” για να ξεκινήσεις.
          </p>
        )}
      </section>
    </main>
  );
}

function JobRow({ job }: { job: Job }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const removeMut = useMutation({
    mutationFn: () => deleteJob(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const pct = Math.round(job.progress * 100);
  const canDelete = job.status === "done" || job.status === "failed";

  const inner = (
    <div className="glass-card rounded-2xl p-5 transition hover:border-white/15 hover:bg-white/[0.05]">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-mono text-sm tracking-tight text-white/90" title={job.id}>
            {job.id.slice(0, 8)}<span className="text-white/30">…</span>
          </p>
          <p className="mt-0.5 text-xs text-white/40">
            {job.stage} · {new Date(job.created_at).toLocaleString()}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {job.config?.model_used ? <MlBadge /> : null}
          <StatusChip status={job.status} />
          {canDelete ? (
            <DeleteButton
              confirming={confirming}
              busy={removeMut.isPending}
              onFirstClick={(e) => { e.preventDefault(); e.stopPropagation(); setConfirming(true); }}
              onConfirm={(e) => { e.preventDefault(); e.stopPropagation(); removeMut.mutate(); }}
              onCancel={(e) => { e.preventDefault(); e.stopPropagation(); setConfirming(false); }}
            />
          ) : null}
        </div>
      </div>
      <div className="mt-4 h-1 w-full overflow-hidden rounded-full bg-white/5">
        <div
          className={`h-full transition-all duration-500 ${
            job.status === "failed"
              ? "bg-gradient-to-r from-red-500 to-rose-400"
              : "bg-gradient-to-r from-cyan-400 via-indigo-400 to-fuchsia-400"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {job.error ? (
        <p className="mt-3 text-xs text-red-300/90">{job.error}</p>
      ) : null}
      {removeMut.isError ? (
        <p className="mt-3 text-xs text-red-300/90">
          {(removeMut.error as ApiError | Error).message}
        </p>
      ) : null}
    </div>
  );

  // Only completed jobs have an EDL to review; link only then so stale
  // navigation into an unfinished job can't happen.
  if (job.status === "done") {
    return (
      <li>
        <Link href={`/jobs/${job.id}/review`}>{inner}</Link>
      </li>
    );
  }
  return <li>{inner}</li>;
}

function DeleteButton({
  confirming, busy, onFirstClick, onConfirm, onCancel,
}: {
  confirming: boolean;
  busy: boolean;
  onFirstClick: (e: React.MouseEvent) => void;
  onConfirm: (e: React.MouseEvent) => void;
  onCancel: (e: React.MouseEvent) => void;
}) {
  if (busy) {
    return (
      <span className="text-xs text-gray-400">Σβήνει…</span>
    );
  }
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={onFirstClick}
        className="rounded-md border border-gray-700 px-2 py-0.5 text-xs text-gray-400 transition hover:border-red-500 hover:text-red-300"
        aria-label="Delete job"
      >
        σβήσε
      </button>
    );
  }
  return (
    <span className="flex items-center gap-1 text-xs">
      <button
        type="button"
        onClick={onConfirm}
        className="rounded-md bg-red-500 px-2 py-0.5 font-medium text-red-950 transition hover:bg-red-400"
      >
        σίγουρα;
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="rounded-md border border-gray-700 px-2 py-0.5 text-gray-400 transition hover:border-gray-500"
      >
        άκυρο
      </button>
    </span>
  );
}

const STATUS_LABEL: Record<Job["status"], string> = {
  pending: "αναμονή",
  running: "τρέχει",
  done: "έτοιμο",
  failed: "απέτυχε",
};

function MlBadge() {
  return (
    <span
      title="Σκοράρισμα από εκπαιδευμένο μοντέλο"
      className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-sky-300"
    >
      ML
    </span>
  );
}

function StatusChip({ status }: { status: Job["status"] }) {
  const colors: Record<Job["status"], string> = {
    pending: "border-white/10 bg-white/5 text-white/60",
    running: "border-cyan-400/20 bg-cyan-400/5 text-cyan-200/90",
    done: "border-emerald-400/20 bg-emerald-400/5 text-emerald-200/90",
    failed: "border-red-400/20 bg-red-400/5 text-red-200/90",
  };
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] tracking-wide ${colors[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Intelligence panel — shows adaptive model status + manual retrain trigger
// ---------------------------------------------------------------------------

function IntelligencePanel() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["train-status"],
    queryFn: getTrainStatus,
    refetchInterval: 30_000,
  });

  const trainMut = useMutation({
    mutationFn: triggerTrain,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["train-status"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const s: TrainStatus | undefined = status.data;
  if (!s) return null;

  const pct = s.auto_train_threshold > 0
    ? Math.min(100, Math.round((s.n_overrides % s.auto_train_threshold) / s.auto_train_threshold * 100))
    : 0;
  const nextIn = s.auto_train_threshold - (s.n_overrides % s.auto_train_threshold);

  return (
    <section className="glass-card rounded-2xl p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-white/50">
            Intelligence
          </p>
          <p className="mt-0.5 text-sm text-white/80">
            {s.model_active ? (
              <>
                Μοντέλο ενεργό
                {s.model_f1 != null && (
                  <span className="ml-2 text-xs text-white/40">
                    F1 {s.model_f1.toFixed(2)} vs {s.heuristic_f1?.toFixed(2)} heuristic
                  </span>
                )}
              </>
            ) : (
              <span className="text-white/40">Χωρίς εκπαιδευμένο μοντέλο</span>
            )}
          </p>
        </div>
        <button
          type="button"
          disabled={trainMut.isPending || s.n_overrides < 4}
          onClick={() => trainMut.mutate()}
          title={s.n_overrides < 4 ? "Χρειάζονται τουλάχιστον 4 overrides για εκπαίδευση" : "Εκπαίδευσε τώρα"}
          className="rounded-xl border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs font-medium text-indigo-300 transition hover:bg-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {trainMut.isPending ? "Εκπαιδεύεται…" : "Εκπαίδευσε"}
        </button>
      </div>

      <div className="mt-4 space-y-1.5">
        <div className="flex justify-between text-[11px] text-white/30">
          <span>{s.n_overrides} overrides συνολικά</span>
          {s.n_overrides >= 4 && (
            <span>επόμενη αυτόματη εκπαίδευση σε {nextIn}</span>
          )}
        </div>
        <div className="h-1 w-full overflow-hidden rounded-full bg-white/5">
          <div
            className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all duration-700"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {trainMut.isSuccess && (
        <p className={`mt-3 text-xs ${trainMut.data.deployed ? "text-emerald-300" : "text-white/40"}`}>
          {trainMut.data.deployed
            ? `Αναπτύχθηκε — F1 ${trainMut.data.model_f1.toFixed(2)}`
            : `Δεν αναπτύχθηκε (heuristic καλύτερο)`}
        </p>
      )}
      {trainMut.isError && (
        <p className="mt-3 text-xs text-red-300">
          {(trainMut.error as ApiError | Error).message}
        </p>
      )}
    </section>
  );
}
