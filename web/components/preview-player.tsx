"use client";

import { useQuery } from "@tanstack/react-query";

import { listOutputs, outputDownloadUrl } from "@/lib/api";
import type { OutputFile } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  /** job.updated_at — included in the video URL so re-renders bust the cache. */
  cacheKey: string;
}

export default function PreviewPlayer({ jobId, preset, cacheKey }: Props) {
  const outputsQ = useQuery({
    queryKey: ["outputs", jobId, cacheKey],
    queryFn: () => listOutputs(jobId),
  });

  const videoName = pickVideo(outputsQ.data?.outputs ?? [], preset);

  if (outputsQ.isLoading) {
    return <PlayerSkeleton label="Loading outputs…" />;
  }
  if (!videoName) {
    return <PlayerSkeleton label="No rendered video yet" />;
  }

  // Cache-bust the download URL so the browser fetches the fresh render after
  // the user hits "Re-render"; ApiError isn't possible here (static URL).
  const src = `${outputDownloadUrl(jobId, videoName)}?v=${encodeURIComponent(cacheKey)}`;

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-medium uppercase tracking-wide text-gray-400">
        Preview · {videoName}
      </h2>
      <video
        key={src}            /* force reload on cache-key change */
        controls
        playsInline
        preload="metadata"
        className="w-full rounded-lg border border-gray-800 bg-black"
        src={src}
      />
    </section>
  );
}


function pickVideo(outputs: OutputFile[], preset: string | undefined): string | null {
  const videos = outputs.filter((o) => o.kind === "video").map((o) => o.name);
  if (videos.length === 0) return null;

  // Prefer the file that matches the requested preset.
  const wanted = preset ? `rough_cut_${preset}.mp4` : "rough_cut.mp4";
  if (videos.includes(wanted)) return wanted;

  // Fallbacks: anything named rough_cut.mp4, else first available.
  if (videos.includes("rough_cut.mp4")) return "rough_cut.mp4";
  return videos[0] ?? null;
}


function PlayerSkeleton({ label }: { label: string }) {
  return (
    <div className="flex aspect-video items-center justify-center rounded-lg border border-gray-800 bg-gray-900/60 text-sm text-gray-500">
      {label}
    </div>
  );
}
