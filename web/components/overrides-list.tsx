"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, listOverrides } from "@/lib/api";
import type { Decision, OverrideRecord } from "@/lib/api.types";


const DECISION_LABEL: Record<Decision, string> = {
  keep: "μέσα",
  cut: "έξω",
};


export default function OverridesList({ jobId }: { jobId: string }) {
  const q = useQuery({
    queryKey: ["overrides", jobId],
    queryFn: () => listOverrides(jobId),
  });

  if (q.isLoading) {
    return <p className="text-sm text-gray-500">Φόρτωση…</p>;
  }
  if (q.isError) {
    return (
      <p className="text-sm text-red-400">
        {(q.error as ApiError | Error).message}
      </p>
    );
  }

  const rows = q.data?.overrides ?? [];
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-6 text-center">
        <p className="text-sm text-gray-400">
          Δεν έχεις αλλάξει τίποτα ακόμα σε αυτό το μοντάζ.
        </p>
        <p className="mt-1 text-xs text-gray-500">
          Κάθε φορά που κρατάς ή κόβεις ένα κομμάτι, η αλλαγή σου καταγράφεται εδώ.
        </p>
      </div>
    );
  }

  const summary = summarise(rows);

  return (
    <section className="flex flex-col gap-4">
      <SummaryBar
        flips={summary.flips}
        touchedSegments={summary.touchedSegments}
        kept={summary.kept}
        cut={summary.cut}
      />

      <ul className="flex flex-col gap-2">
        {rows.map((r) => (
          <OverrideRow key={r.id} row={r} />
        ))}
      </ul>
    </section>
  );
}


function OverrideRow({ row }: { row: OverrideRecord }) {
  const when = new Date(row.created_at);
  const changed = row.previous_decision !== row.new_decision;

  return (
    <li className="rounded-md border border-gray-800 bg-gray-900/60 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-gray-200">
            Κομμάτι #{row.segment_index} · {row.clip_id}
          </p>
          <p className="text-xs text-gray-500">
            {row.segment_start.toFixed(2)}s → {row.segment_end.toFixed(2)}s ·
            βαθμός {row.interest_score.toFixed(2)}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <div className="flex items-center justify-end gap-1 text-xs">
            <DecisionChip decision={row.previous_decision} muted />
            <span className="text-gray-500">→</span>
            <DecisionChip decision={row.new_decision} />
          </div>
          <p className="mt-1 text-[10px] text-gray-500">
            {when.toLocaleString("el-GR", {
              month: "short", day: "numeric",
              hour: "2-digit", minute: "2-digit",
            })}
          </p>
        </div>
      </div>
      {!changed ? (
        // Rare but possible: user tapped twice and the second PATCH restated
        // the same decision — surface it so the log is honest.
        <p className="mt-1 text-[10px] text-gray-500">
          (ίδια απόφαση — επανεκλογή)
        </p>
      ) : null}
    </li>
  );
}


function DecisionChip({
  decision, muted = false,
}: { decision: Decision; muted?: boolean }) {
  const base = decision === "keep"
    ? muted
      ? "bg-emerald-500/10 text-emerald-500/70"
      : "bg-emerald-500/20 text-emerald-300"
    : muted
      ? "bg-gray-700/40 text-gray-500"
      : "bg-gray-700/60 text-gray-200";
  return (
    <span className={`rounded-full px-2 py-0.5 ${base}`}>
      {DECISION_LABEL[decision]}
    </span>
  );
}


function SummaryBar({
  flips, touchedSegments, kept, cut,
}: { flips: number; touchedSegments: number; kept: number; cut: number }) {
  // Two separate thoughts: how much the user clicked (flips) and what the
  // final state looks like (kept/cut). Mixing them produced the "5 total,
  // 0 kept, 1 cut" confusion where the numbers didn't add up.
  const segWord = touchedSegments === 1 ? "κομμάτι" : "κομμάτια";
  return (
    <div className="flex flex-col gap-1 text-xs text-gray-400">
      <div>
        <span className="text-gray-200">
          {flips} tap{flips === 1 ? "" : "s"}
        </span>{" "}
        σε {touchedSegments} {segWord}
      </div>
      <div>
        <span className="text-gray-500">Τελικά:</span>{" "}
        <span className="text-emerald-300">{kept} μέσα</span>
        {" · "}
        <span className="text-gray-300">{cut} έξω</span>
      </div>
    </div>
  );
}


function summarise(rows: OverrideRecord[]) {
  // The LAST row per segment is what currently applies — history before that
  // is editing noise. We summarise the final state across segments; the raw
  // flip count stays visible as its own line so the user can see "I tapped
  // the same thing 5 times" alongside "the end result is 1 cut segment".
  const final = new Map<string, Decision>();
  for (const r of rows) {
    final.set(`${r.preset}:${r.segment_index}`, r.new_decision);
  }
  let kept = 0;
  let cut = 0;
  for (const d of final.values()) {
    if (d === "keep") kept += 1;
    else cut += 1;
  }
  return {
    flips: rows.length,
    touchedSegments: final.size,
    kept,
    cut,
  };
}
