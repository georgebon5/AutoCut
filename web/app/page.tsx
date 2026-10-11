"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { ApiError, deleteJob, getHealth, listJobs } from "@/lib/api";
import type { Job } from "@/lib/api.types";

/** Only running jobs need live polling; terminal jobs are checked less often. */
function usePollingInterval(jobs: Job[] | undefined): number | false {
  if (!jobs) return false;
  const live = jobs.some((j) => j.status === "pending" || j.status === "running");
  return live ? 1_500 : false;
}

export default function HomePage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: (q) => usePollingInterval(q.state.data as Job[] | undefined),
  });

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-6 px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">AutoCut</h1>
        <HealthBadge status={health.data?.status} version={health.data?.version} />
      </header>

      <Link
        href="/upload"
        className="rounded-lg bg-emerald-500 px-4 py-3 text-center text-sm font-semibold text-emerald-950 transition hover:bg-emerald-400"
      >
        + Ανέβασε clips
      </Link>

      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-gray-400">
          Jobs
        </h2>
        {jobs.isLoading ? (
          <p className="text-sm text-gray-500">Φόρτωση…</p>
        ) : jobs.isError ? (
          <p className="text-sm text-red-400">
            Δεν φορτώθηκαν τα jobs. Τρέχει το API;
          </p>
        ) : jobs.data && jobs.data.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {jobs.data.map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-500">
            Κανένα job ακόμα. Πάτα “Ανέβασε clips” για να ξεκινήσεις.
          </p>
        )}
      </section>
    </main>
  );
}

function HealthBadge({ status, version }: { status?: string; version?: string }) {
  const ok = status === "ok";
  return (
    <span
      className={`rounded-full px-3 py-1 text-xs font-medium ${
        ok ? "bg-emerald-500/15 text-emerald-300" : "bg-gray-500/15 text-gray-400"
      }`}
    >
      {ok ? `API v${version ?? "?"}` : "API εκτός"}
    </span>
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
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4 transition hover:border-gray-700">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" title={job.id}>
            {job.id.slice(0, 8)}…
          </p>
          <p className="text-xs text-gray-500">
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
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-gray-800">
        <div
          className={`h-full ${
            job.status === "failed" ? "bg-red-500" : "bg-emerald-500"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {job.error ? (
        <p className="mt-2 text-xs text-red-400">{job.error}</p>
      ) : null}
      {removeMut.isError ? (
        <p className="mt-2 text-xs text-red-400">
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
    pending: "bg-gray-500/15 text-gray-300",
    running: "bg-sky-500/15 text-sky-300",
    done: "bg-emerald-500/15 text-emerald-300",
    failed: "bg-red-500/15 text-red-300",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs ${colors[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}
