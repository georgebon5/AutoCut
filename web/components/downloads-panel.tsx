"use client";

import { useQuery } from "@tanstack/react-query";

import { exportFcpxmlUrl, listOutputs, outputDownloadUrl } from "@/lib/api";
import type { OutputFile } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  /** ``job.updated_at`` — cache-busts stale video/EDL after a re-render. */
  cacheKey: string;
}


const KIND_LABEL: Record<OutputFile["kind"], string> = {
  video: "Βίντεο",
  edl: "EDL (JSON)",
  captions_srt: "Υπότιτλοι (SRT)",
  captions_ass: "Υπότιτλοι (ASS)",
};


export default function DownloadsPanel({ jobId, preset, cacheKey }: Props) {
  const outputsQ = useQuery({
    queryKey: ["outputs", jobId, cacheKey],
    queryFn: () => listOutputs(jobId),
  });

  const outputs = outputsQ.data?.outputs ?? [];
  const grouped = groupByKind(outputs);

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-sm font-medium uppercase tracking-wide text-gray-400">
        Κατεβάσματα
      </h2>

      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        {outputsQ.isLoading ? (
          <p className="text-sm text-gray-500">Φόρτωση…</p>
        ) : outputs.length === 0 ? (
          <p className="text-sm text-gray-500">
            Δεν έχει βγει τίποτα ακόμα. Πάτα “Ξαναφτιάξε βίντεο” πιο πάνω.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {(Object.keys(grouped) as OutputFile["kind"][]).map((kind) => (
              <li key={kind}>
                <div className="mb-1 text-xs uppercase tracking-wide text-gray-500">
                  {KIND_LABEL[kind]}
                </div>
                <ul className="flex flex-col gap-1.5">
                  {grouped[kind].map((o) => (
                    <OutputRow
                      key={o.name}
                      file={o}
                      href={`${outputDownloadUrl(jobId, o.name)}?v=${encodeURIComponent(cacheKey)}`}
                    />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ExportBlock jobId={jobId} preset={preset} />
    </section>
  );
}


function ExportBlock({ jobId, preset }: { jobId: string; preset: string | undefined }) {
  const href = exportFcpxmlUrl(jobId, preset);
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
      <div className="mb-2 text-xs uppercase tracking-wide text-gray-500">
        Για να το τελειώσεις στον editor σου
      </div>
      <p className="mb-3 text-xs text-gray-500">
        Το FCPXML ανοίγει σε Final Cut Pro, DaVinci Resolve, Premiere Pro και iMovie.
        Το timeline δείχνει στο <strong>αρχικό</strong> βίντεο, άρα τελειώνεις
        σε πλήρη ποιότητα.
      </p>
      <a
        href={href}
        className="inline-flex rounded-md bg-sky-500 px-3 py-2 text-sm font-semibold text-sky-950 transition hover:bg-sky-400"
      >
        Κατέβασμα FCPXML
      </a>
    </div>
  );
}


function OutputRow({ file, href }: { file: OutputFile; href: string }) {
  return (
    <li className="flex items-center justify-between gap-3 rounded-md border border-gray-800 bg-gray-950/40 px-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-gray-200" title={file.name}>
          {file.name}
        </p>
        <p className="text-xs text-gray-500">{formatBytes(file.size)}</p>
      </div>
      <a
        href={href}
        download={file.name}
        className="shrink-0 rounded-md bg-emerald-500/15 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/25"
      >
        Κατέβασμα
      </a>
    </li>
  );
}


function groupByKind(outputs: OutputFile[]): Record<OutputFile["kind"], OutputFile[]> {
  const empty: Record<OutputFile["kind"], OutputFile[]> = {
    video: [], edl: [], captions_srt: [], captions_ass: [],
  };
  for (const o of outputs) {
    empty[o.kind].push(o);
  }
  // Drop empty groups so we don't render blank sections.
  const nonEmpty: Record<OutputFile["kind"], OutputFile[]> = {} as Record<OutputFile["kind"], OutputFile[]>;
  for (const k of ["video", "edl", "captions_srt", "captions_ass"] as const) {
    if (empty[k].length > 0) nonEmpty[k] = empty[k];
  }
  return nonEmpty;
}


function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
