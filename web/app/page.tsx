"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import { ApiError, deleteJob, getTrainStatus, listJobs, patchJobLabel, triggerTrain } from "@/lib/api";
import { useJobEvents } from "@/lib/use-job-events";
import type { Job, TrainStatus } from "@/lib/api.types";

type StatusFilter = "all" | "active" | "done" | "failed";

export default function HomePage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [bulkConfirm, setBulkConfirm] = useState(false);

  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: 10_000,
  });

  const allJobs = jobs.data ?? [];

  const filteredJobs = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allJobs.filter((j) => {
      if (statusFilter === "active" && j.status !== "pending" && j.status !== "running") return false;
      if (statusFilter === "done" && j.status !== "done") return false;
      if (statusFilter === "failed" && j.status !== "failed") return false;
      if (!q) return true;
      return (
        j.label?.toLowerCase().includes(q) ||
        j.id.toLowerCase().startsWith(q)
      );
    });
  }, [allJobs, search, statusFilter]);

  const finishedCount = allJobs.filter(
    (j) => j.status === "done" || j.status === "failed",
  ).length;

  const bulkDeleteMut = useMutation({
    mutationFn: () =>
      Promise.all(
        allJobs
          .filter((j) => j.status === "done" || j.status === "failed")
          .map((j) => deleteJob(j.id)),
      ),
    onSuccess: () => {
      setBulkConfirm(false);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const statusTabs: { f: StatusFilter; label: string }[] = [
    { f: "all",    label: "Όλα" },
    { f: "active", label: "Ενεργά" },
    { f: "done",   label: "Ολοκλ." },
    { f: "failed", label: "Απέτ." },
  ];

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
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
            Jobs
          </h2>
          {finishedCount > 0 && (
            <span className="flex items-center gap-1.5">
              {bulkConfirm ? (
                <>
                  <span className="text-xs text-white/50">Σβήνεις {finishedCount};</span>
                  <button
                    type="button"
                    disabled={bulkDeleteMut.isPending}
                    onClick={() => bulkDeleteMut.mutate()}
                    className="rounded-md bg-red-500/80 px-2 py-0.5 text-xs font-medium text-red-950 transition hover:bg-red-400"
                  >
                    {bulkDeleteMut.isPending ? "…" : "ναι"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setBulkConfirm(false)}
                    className="rounded-md border border-white/10 px-2 py-0.5 text-xs text-white/50 transition hover:border-white/20"
                  >
                    άκυρο
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => setBulkConfirm(true)}
                  className="rounded-md border border-white/8 px-2 py-0.5 text-[11px] text-white/35 transition hover:border-red-500/30 hover:text-red-300/70"
                >
                  σβήσε ολοκληρωμένα ({finishedCount})
                </button>
              )}
            </span>
          )}
        </div>

        {/* Search + status filter */}
        {allJobs.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[160px]">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Αναζήτηση…"
                className="w-full rounded-lg border border-white/8 bg-white/5 py-1 pl-3 pr-7 text-xs text-white/80 placeholder:text-white/25 focus:border-cyan-500/40 focus:outline-none"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-white/30 hover:text-white/70"
                  aria-label="Καθαρισμός"
                >
                  ✕
                </button>
              )}
            </div>
            <div className="inline-flex gap-0.5 rounded-lg border border-white/8 bg-black/20 p-0.5">
              {statusTabs.map(({ f, label }) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setStatusFilter(f)}
                  className={[
                    "rounded-md px-2.5 py-0.5 text-[11px] font-medium transition",
                    statusFilter === f
                      ? "bg-white/12 text-white/90"
                      : "text-white/40 hover:text-white/70",
                  ].join(" ")}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        {jobs.isLoading ? (
          <p className="text-sm text-white/40">Φόρτωση…</p>
        ) : jobs.isError ? (
          <p className="text-sm text-red-300">
            Δεν φορτώθηκαν τα jobs. Τρέχει το API;
          </p>
        ) : filteredJobs.length > 0 ? (
          <ul className="flex flex-col gap-3">
            {filteredJobs.map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </ul>
        ) : allJobs.length > 0 ? (
          <p className="text-sm text-white/40">Κανένα job ταιριάζει.</p>
        ) : (
          <p className="text-sm text-white/40">
            Κανένα job ακόμα. Πάτα "Ανέβασε clips" για να ξεκινήσεις.
          </p>
        )}
      </section>
    </main>
  );
}

function JobRow({ job }: { job: Job }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [editingLabel, setEditingLabel] = useState(false);
  const isLive = job.status === "pending" || job.status === "running";
  useJobEvents(
    job.id,
    isLive,
    () => qc.invalidateQueries({ queryKey: ["jobs"] }),
    () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  );
  const [labelDraft, setLabelDraft] = useState(job.label ?? "");
  const labelInputRef = useRef<HTMLInputElement | null>(null);

  const removeMut = useMutation({
    mutationFn: () => deleteJob(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const labelMut = useMutation({
    mutationFn: (next: string | null) => patchJobLabel(job.id, next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const commitLabel = () => {
    setEditingLabel(false);
    const trimmed = labelDraft.trim();
    const current = job.label ?? "";
    if (trimmed === current) return;
    labelMut.mutate(trimmed || null);
  };

  const pct = Math.round(job.progress * 100);
  const canDelete = job.status === "done" || job.status === "failed";
  const timeAgo = fmtRelTime(job.created_at);

  const inner = (
    <div className="glass-card rounded-2xl p-5 transition hover:border-white/15 hover:bg-white/[0.05]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {editingLabel ? (
            <input
              ref={labelInputRef}
              autoFocus
              type="text"
              maxLength={120}
              value={labelDraft}
              onChange={(e) => setLabelDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); commitLabel(); }
                if (e.key === "Escape") { e.preventDefault(); setEditingLabel(false); setLabelDraft(job.label ?? ""); }
              }}
              onBlur={commitLabel}
              onClick={(e) => e.preventDefault()}
              placeholder="Πρόσθεσε τίτλο…"
              className="w-full rounded-lg border border-cyan-400/40 bg-black/40 px-2 py-0.5 text-sm text-white/90 outline-none placeholder-white/25 focus:border-cyan-400/70"
            />
          ) : (
            <button
              type="button"
              onClick={(e) => { e.preventDefault(); setEditingLabel(true); setLabelDraft(job.label ?? ""); }}
              className="group flex items-center gap-1.5 text-left"
              title="Κλικ για επεξεργασία τίτλου"
            >
              {job.label ? (
                <span className="truncate text-sm font-medium text-white/90">{job.label}</span>
              ) : (
                <span className="text-sm text-white/25 opacity-0 transition group-hover:opacity-100">+ τίτλος</span>
              )}
            </button>
          )}
          <p className="mt-0.5 font-mono text-xs text-white/35" title={job.id}>
            {job.id.slice(0, 8)}<span className="text-white/20">…</span>
            <span className="ml-2 not-italic text-white/30">{job.stage}</span>
            <span className="ml-2 not-italic text-white/25">{timeAgo}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
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

function fmtRelTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return "μόλις τώρα";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}λ πριν`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}ω πριν`;
  const d = Math.floor(h / 24);
  if (d === 1) return "χθες";
  if (d < 7) return `${d}μ πριν`;
  return new Date(iso).toLocaleDateString("el-GR", { day: "numeric", month: "short" });
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
