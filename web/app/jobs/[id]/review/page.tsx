"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import Timeline from "@/components/timeline";
import { ApiError, getEdl, getJob, postRender } from "@/lib/api";
import type { Job, JobConfig } from "@/lib/api.types";

const PRESET_OPTIONS: JobConfig["preset"][] = ["tight", "medium", "loose"];

function usePollWhileRunning(job: Job | undefined): number | false {
  return job && (job.status === "running" || job.status === "pending") ? 1_500 : false;
}

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const qc = useQueryClient();

  const [preset, setPreset] = useState<string | null>(null);

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
    mutationFn: () => postRender(jobId, edlPreset ?? undefined),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", jobId] }),
  });

  const isAll = job?.config?.preset === "all";

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-8">
      <header className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <Link href="/" className="text-sm text-gray-400 hover:text-white">
            ← Jobs
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold tracking-tight">
            Review · {jobId.slice(0, 8)}…
          </h1>
          {job ? (
            <p className="text-xs text-gray-500">
              status {job.status} · stage {job.stage}
            </p>
          ) : null}
        </div>
        <RerenderButton
          busy={renderMut.isPending || job?.status === "running"}
          disabled={job?.status !== "done"}
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
          Waiting for the job to finish before you can review.
        </p>
      ) : isAll && preset === null ? (
        <p className="text-sm text-gray-400">
          This job rendered three presets — pick one to review.
        </p>
      ) : edlQuery.isLoading ? (
        <p className="text-sm text-gray-500">Loading EDL…</p>
      ) : edlQuery.isError ? (
        <p className="text-sm text-red-400">
          {(edlQuery.error as ApiError | Error).message}
        </p>
      ) : edlQuery.data ? (
        <Timeline jobId={jobId} preset={edlPreset ?? undefined} edl={edlQuery.data} />
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
  onClick,
}: {
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className="shrink-0 rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-emerald-950 transition disabled:cursor-not-allowed disabled:bg-gray-700 disabled:text-gray-400"
    >
      {busy ? "Rendering…" : "Re-render"}
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
            {p}
          </button>
        );
      })}
    </div>
  );
}
