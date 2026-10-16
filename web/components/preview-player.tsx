"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { listOutputs, outputDownloadUrl } from "@/lib/api";
import type { OutputFile } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  /** job.updated_at — included in the video URL so re-renders bust the cache. */
  cacheKey: string;
  /** True while the backend is re-rendering; preview is stale until it finishes. */
  rendering?: boolean;
}

export default function PreviewPlayer({ jobId, preset, cacheKey, rendering = false }: Props) {
  const outputsQ = useQuery({
    queryKey: ["outputs", jobId, cacheKey],
    queryFn: () => listOutputs(jobId),
  });

  const videoName = pickVideo(outputsQ.data?.outputs ?? [], preset);

  if (outputsQ.isLoading) {
    return <PlayerSkeleton label="Φόρτωση…" />;
  }
  if (!videoName) {
    return <PlayerSkeleton label="Δεν υπάρχει βίντεο ακόμα" />;
  }

  // Cache-bust the download URL so the browser fetches the fresh render after
  // the user hits "Re-render"; ApiError isn't possible here (static URL).
  const src = `${outputDownloadUrl(jobId, videoName)}?v=${encodeURIComponent(cacheKey)}`;

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
        Προεπισκόπηση · <span className="font-mono text-white/60">{videoName}</span>
      </h2>
      <div className="relative overflow-hidden rounded-2xl ring-glow">
        <VideoWithSpaceToggle
          src={src}
          rendering={rendering}
        />
        {rendering ? (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/50 text-center text-sm text-cyan-100 backdrop-blur-sm">
            <div className="h-7 w-7 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent" />
            <div className="font-medium tracking-wide">Φτιάχνει το νέο βίντεο…</div>
            <div className="text-xs text-cyan-100/60">
              Αυτό που βλέπεις είναι η προηγούμενη έκδοση.
            </div>
          </div>
        ) : null}
      </div>
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
    <div className="glass-card flex aspect-video items-center justify-center rounded-2xl text-sm text-white/40">
      {label}
    </div>
  );
}


/**
 * Wraps the <video> element and binds Space-to-toggle at the window level.
 * Space already works when the video has focus, but users don't naturally
 * focus the player — this makes the shortcut discoverable from anywhere on
 * the page. We skip the shortcut when an input/textarea/contenteditable has
 * focus so transcript-word edits still accept a literal space.
 */
function VideoWithSpaceToggle({
  src,
  rendering,
}: {
  src: string;
  rendering: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== " " && e.code !== "Space") return;
      const t = e.target as HTMLElement | null;
      if (
        t && (
          t.tagName === "INPUT"
          || t.tagName === "TEXTAREA"
          || t.tagName === "SELECT"
          || t.isContentEditable
        )
      ) return;
      const v = videoRef.current;
      if (!v) return;
      e.preventDefault();   // stop page from scrolling
      if (v.paused) void v.play();
      else v.pause();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <video
      ref={videoRef}
      key={src}            /* force reload on cache-key change */
      controls
      playsInline
      preload="metadata"
      className={`w-full bg-black transition duration-300 ${
        rendering ? "opacity-30 blur-sm" : ""
      }`}
      src={src}
    />
  );
}
