"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { bundleDownloadUrl, fetchOutputText, listOutputs, outputDownloadUrl } from "@/lib/api";
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
  const outputs = outputsQ.data?.outputs ?? [];
  const videos = outputs.filter((o) => o.kind === "video");
  const thumbnails = outputs.filter((o) => o.kind === "thumbnail");
  const chapterFiles = outputs.filter((o) => o.kind === "chapters_txt");
  const fcpxmlFiles = outputs.filter((o) => o.kind === "fcpxml");
  const cmxFiles = outputs.filter((o) => o.kind === "cmx_edl");
  const srtFiles = outputs.filter((o) => o.kind === "captions_srt");
  // Show the bundle button when there's at least one NLE file alongside the video.
  const hasBundle = videos.length > 0 && (fcpxmlFiles.length > 0 || cmxFiles.length > 0 || srtFiles.length > 0);

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
          Κατέβασμα βίντεο
        </h2>
        {hasBundle && (
          <a
            href={bundleDownloadUrl(jobId)}
            download={`autocut_${jobId.slice(0, 8)}.zip`}
            className="shrink-0 rounded-xl border border-white/10 bg-white/5 px-3 py-1 text-xs font-medium text-white/60 transition hover:border-white/20 hover:bg-white/10 hover:text-white/90"
          >
            Κατέβασε όλα (.zip)
          </a>
        )}
      </div>

      {thumbnails.length > 0 && (
        <ThumbnailStrip jobId={jobId} thumbnails={thumbnails} cacheKey={cacheKey} />
      )}

      <div className="glass-card rounded-2xl p-4">
        {outputsQ.isLoading ? (
          <p className="text-sm text-white/40">Φόρτωση…</p>
        ) : videos.length === 0 ? (
          <p className="text-sm text-white/40">
            Δεν έχει βγει τίποτα ακόμα. Πάτα "Ξαναφτιάξε βίντεο" πιο πάνω.
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
            {fcpxmlFiles.map((o) => (
              <OutputRow
                key={o.name}
                file={o}
                href={`${outputDownloadUrl(jobId, o.name)}?v=${encodeURIComponent(cacheKey)}`}
                label="FCP XML"
              />
            ))}
            {cmxFiles.map((o) => (
              <OutputRow
                key={o.name}
                file={o}
                href={`${outputDownloadUrl(jobId, o.name)}?v=${encodeURIComponent(cacheKey)}`}
                label="CMX EDL"
              />
            ))}
            {srtFiles.map((o) => (
              <OutputRow
                key={o.name}
                file={o}
                href={`${outputDownloadUrl(jobId, o.name)}?v=${encodeURIComponent(cacheKey)}`}
                label="SRT"
              />
            ))}
          </ul>
        )}
      </div>

      {chapterFiles.map((f) => (
        <ChaptersCard key={f.name} jobId={jobId} file={f} cacheKey={cacheKey} />
      ))}
    </section>
  );
}


function ChaptersCard({
  jobId,
  file,
  cacheKey,
}: {
  jobId: string;
  file: OutputFile;
  cacheKey: string;
}) {
  const [copied, setCopied] = useState(false);

  const textQ = useQuery({
    queryKey: ["chapters-txt", jobId, file.name, cacheKey],
    queryFn: () => fetchOutputText(jobId, file.name),
  });

  const lines = (textQ.data ?? "").trim().split("\n").filter(Boolean);

  const handleCopy = async () => {
    if (!textQ.data) return;
    await navigator.clipboard.writeText(textQ.data);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
          Chapters
        </h2>
        <button
          onClick={handleCopy}
          disabled={!textQ.data}
          className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] font-medium text-white/60 transition hover:border-white/20 hover:text-white/80 disabled:opacity-40"
        >
          {copied ? "Copied!" : "Copy for YouTube"}
        </button>
      </div>
      <div className="glass-card rounded-2xl p-4">
        {textQ.isLoading ? (
          <p className="text-sm text-white/40">Φόρτωση…</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {lines.map((line, i) => {
              const spaceIdx = line.indexOf(" ");
              const ts = spaceIdx > -1 ? line.slice(0, spaceIdx) : line;
              const title = spaceIdx > -1 ? line.slice(spaceIdx + 1) : "";
              return (
                <li key={i} className="flex items-baseline gap-2.5">
                  <span className="shrink-0 font-mono text-xs text-cyan-400/80">{ts}</span>
                  <span className="text-sm text-white/70">{title}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}


function ThumbnailStrip({
  jobId,
  thumbnails,
  cacheKey,
}: {
  jobId: string;
  thumbnails: OutputFile[];
  cacheKey: string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {thumbnails.map((t) => {
        const src = `${outputDownloadUrl(jobId, t.name)}?v=${encodeURIComponent(cacheKey)}`;
        return (
          <a
            key={t.name}
            href={src}
            download={t.name}
            title={t.name}
            className="group relative overflow-hidden rounded-xl border border-white/10 transition hover:border-cyan-400/40"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={src}
              alt={t.name}
              className="h-24 w-auto object-cover"
            />
            <span className="absolute inset-x-0 bottom-0 bg-black/60 px-2 py-1 text-[10px] text-white/60 opacity-0 transition group-hover:opacity-100">
              {t.name}
            </span>
          </a>
        );
      })}
    </div>
  );
}


function OutputRow({ file, href, label = "Download" }: { file: OutputFile; href: string; label?: string }) {
  return (
    <li className="flex items-center justify-between gap-3 rounded-xl border border-white/5 bg-black/30 px-3 py-2.5">
      <div className="min-w-0">
        <p className="truncate font-mono text-sm text-white/80" title={file.name}>
          {file.name}
        </p>
        <p className="text-[11px] text-white/40">{formatBytes(file.size)}</p>
      </div>
      <a
        href={href}
        download={file.name}
        className="shrink-0 rounded-xl border border-cyan-400/20 bg-cyan-400/10 px-3 py-1.5 text-xs font-semibold text-cyan-200 transition hover:border-cyan-400/40 hover:bg-cyan-400/20"
      >
        {label}
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
