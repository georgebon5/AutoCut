"use client";

import { useQuery } from "@tanstack/react-query";

import { getHealth, listJobs } from "@/lib/api";
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

      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-gray-400">
          Jobs
        </h2>
        {jobs.isLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : jobs.isError ? (
          <p className="text-sm text-red-400">
            Failed to load jobs. Is the API running?
          </p>
        ) : jobs.data && jobs.data.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {jobs.data.map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-500">
            No jobs yet. Upload clips to get started.
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
      {ok ? `API v${version ?? "?"}` : "API offline"}
    </span>
  );
}

function JobRow({ job }: { job: Job }) {
  const pct = Math.round(job.progress * 100);
  return (
    <li className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" title={job.id}>
            {job.id.slice(0, 8)}…
          </p>
          <p className="text-xs text-gray-500">
            {job.stage} · {new Date(job.created_at).toLocaleString()}
          </p>
        </div>
        <StatusChip status={job.status} />
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
    </li>
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
      {status}
    </span>
  );
}
