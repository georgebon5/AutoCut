"use client";

import { useQuery } from "@tanstack/react-query";

import { listOutputs, outputDownloadUrl } from "@/lib/api";
import type { OutputFile } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  /** ``job.updated_at`` — cache-busts stale video after a re-render. */
  cacheKey: string;
}


export default function DownloadsPanel({ jobId, cacheKey }: Props) {
  const outputsQ = useQuery({
    queryKey: ["outputs", jobId, cacheKey],
    queryFn: () => listOutputs(jobId),
  });

  // TikTok workflow: user just needs the rendered mp4. The other artifacts
  // (EDL JSON, standalone SRT/ASS, FCPXML) are still produced server-side —
  // they're useful for NLE workflows and debugging — but hidden from this UI.
  const videos = (outputsQ.data?.outputs ?? []).filter((o) => o.kind === "video");

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-sm font-medium uppercase tracking-wide text-gray-400">
        Κατέβασμα βίντεο
      </h2>

      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        {outputsQ.isLoading ? (
          <p className="text-sm text-gray-500">Φόρτωση…</p>
        ) : videos.length === 0 ? (
          <p className="text-sm text-gray-500">
            Δεν έχει βγει τίποτα ακόμα. Πάτα “Ξαναφτιάξε βίντεο” πιο πάνω.
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {videos.map((o) => (
              <OutputRow
                key={o.name}
                file={o}
                href={`${outputDownloadUrl(jobId, o.name)}?v=${encodeURIComponent(cacheKey)}`}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
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


function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
