"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listOutputs, outputDownloadUrl } from "@/lib/api";
import type { Edl, OutputFile, RenderChunk } from "@/lib/api.types";


interface Props {
  jobId: string;
  preset: string | undefined;
  /** job.updated_at — included in the video URL so re-renders bust the cache. */
  cacheKey: string;
  /** True while the backend is re-rendering; preview is stale until it finishes. */
  rendering?: boolean;
  /** Current EDL — drives live preview skipping without re-render. */
  edl?: Edl;
  /** When set, the player seeks to this output-timeline position (seconds).
   *  Uses a {time, seq} tuple so repeated seeks to the same position still fire. */
  seekRequest?: { time: number; seq: number };
  /** Called with the video's current output-time on every timeupdate event. */
  onPlaybackTime?: (t: number) => void;
}

export default function PreviewPlayer({ jobId, preset, cacheKey, rendering = false, edl, seekRequest, onPlaybackTime }: Props) {
  const outputsQ = useQuery({
    queryKey: ["outputs", jobId, cacheKey],
    queryFn: () => listOutputs(jobId),
  });

  // All hooks must run on every render in a stable order — compute these
  // BEFORE any conditional return below. Otherwise React tracks a different
  // number of hooks per render and raises "change in the order of Hooks".
  const cutChunks = useMemo(() => {
    if (!edl) return [];
    return edl.render_chunks.filter((c) => {
      if (c.segment_index < 0) return false;   // hook stays
      const seg = edl.segments[c.segment_index];
      return seg?.decision === "cut";
    });
  }, [edl]);
  const pendingAdds = useMemo(() => {
    if (!edl) return 0;
    const rendered = new Set(
      edl.render_chunks.map((c) => c.segment_index).filter((i) => i >= 0),
    );
    return edl.segments.filter(
      (s, i) => s.decision === "keep" && !rendered.has(i),
    ).length;
  }, [edl]);

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
          cutChunks={cutChunks}
          seekRequest={seekRequest}
          onPlaybackTime={onPlaybackTime}
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
      {pendingAdds > 0 ? (
        <p className="text-xs text-amber-200/80">
          {pendingAdds} segment{pendingAdds === 1 ? "" : "s"} που ξανά-επέλεξες δεν φαίνονται ακόμη —
          πάτα «Ξαναφτιάξε βίντεο» για να μπουν στο preview.
        </p>
      ) : null}
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
const SPEED_STEPS = [0.5, 1, 1.5, 2] as const;

function fmtPt(s: number): string {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

function VideoWithSpaceToggle({
  src,
  rendering,
  cutChunks,
  seekRequest,
  onPlaybackTime,
}: {
  src: string;
  rendering: boolean;
  cutChunks: RenderChunk[];
  seekRequest?: { time: number; seq: number };
  onPlaybackTime?: (t: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [speed, setSpeed] = useState<number>(1);
  const [loopIn, setLoopIn] = useState<number | null>(null);
  const [loopOut, setLoopOut] = useState<number | null>(null);
  const [looping, setLooping] = useState(false);

  // Refs so timeupdate closure always sees current loop values without churn.
  const loopRef = useRef({ looping: false, loopIn: null as number | null, loopOut: null as number | null });
  useEffect(() => { loopRef.current = { looping, loopIn, loopOut }; }, [looping, loopIn, loopOut]);

  const clearLoop = useCallback(() => { setLoopIn(null); setLoopOut(null); setLooping(false); }, []);

  // Apply speed changes to the video element.
  useEffect(() => {
    const v = videoRef.current;
    if (v) v.playbackRate = speed;
  }, [speed]);

  // Keep the latest cutChunks in a ref so the timeupdate handler always sees
  // the current set — attaching a new listener on every change would churn.
  const cutRef = useRef<RenderChunk[]>(cutChunks);
  useEffect(() => { cutRef.current = cutChunks; }, [cutChunks]);

  // Seek when a segment is clicked in the Timeline. The {time, seq} shape
  // means clicking the same segment twice still triggers the effect.
  useEffect(() => {
    if (!seekRequest) return;
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = seekRequest.time;
    // Briefly pause-then-play so the seek is visible even if already playing.
    if (!v.paused) {
      v.pause();
      void v.play();
    }
  }, [seekRequest]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const inInput = !!(t && (
        t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT"
        || t.tagName === "BUTTON" || t.tagName === "A" || t.isContentEditable
      ));
      const v = videoRef.current;
      if (!v) return;

      if (e.key === " " || e.code === "Space") {
        if (inInput) return;
        if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
        if (typeof document !== "undefined" && !document.hasFocus()) return;
        const rect = v.getBoundingClientRect();
        if (rect.bottom <= 0 || rect.top >= window.innerHeight) return;
        e.preventDefault();
        if (v.paused) void v.play(); else v.pause();
      } else if ((e.key === "i" || e.key === "I") && !inInput && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setLoopIn(v.currentTime);
      } else if ((e.key === "o" || e.key === "O") && !inInput && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setLoopOut(v.currentTime);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Live-skip "cut" chunks. On every timeupdate, if the playhead is inside a
  // chunk whose segment is now cut, jump to that chunk's end. ~250ms update
  // rate is enough to be imperceptible; a tiny 0.001s nudge past the end
  // avoids re-landing in the same chunk due to float rounding.
  const onPlaybackTimeRef = useRef(onPlaybackTime);
  useEffect(() => { onPlaybackTimeRef.current = onPlaybackTime; }, [onPlaybackTime]);

  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    const now = v.currentTime;
    // Loop region: jump back to in-point when past out-point.
    const { looping: l, loopIn: li, loopOut: lo } = loopRef.current;
    if (l && li !== null && lo !== null && lo > li && now >= lo) {
      v.currentTime = li;
      return;
    }
    // Live-skip cut chunks.
    for (const c of cutRef.current) {
      if (now >= c.output_start && now < c.output_end) {
        v.currentTime = c.output_end + 0.001;
        return;
      }
    }
    onPlaybackTimeRef.current?.(now);
  };

  const loopReady = loopIn !== null && loopOut !== null && loopOut > loopIn;

  return (
    <>
      <video
        ref={videoRef}
        key={src}            /* force reload on cache-key change */
        controls
        playsInline
        preload="metadata"
        onTimeUpdate={onTimeUpdate}
        className={`w-full bg-black transition duration-300 ${
          rendering ? "opacity-30 blur-sm" : ""
        }`}
        src={src}
      />
      {/* Playback speed bar */}
      <div className="flex items-center gap-1 px-1 pt-0.5">
        <span className="mr-1 text-[10px] text-white/35">Ταχύτητα</span>
        {SPEED_STEPS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSpeed(s)}
            className={[
              "rounded px-2 py-0.5 text-[11px] font-medium transition",
              speed === s
                ? "bg-cyan-400/20 text-cyan-200 ring-1 ring-cyan-400/40"
                : "text-white/40 hover:bg-white/5 hover:text-white/70",
            ].join(" ")}
          >
            {s}×
          </button>
        ))}
      </div>
      {/* Loop in/out row */}
      <div className="flex items-center gap-2 px-1 pb-1">
        <span className="text-[10px] text-white/35">Loop</span>
        <button
          type="button"
          title="Ορισμός αρχής (I)"
          onClick={() => setLoopIn(videoRef.current?.currentTime ?? 0)}
          className="rounded border border-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/40 transition hover:border-cyan-400/40 hover:text-cyan-200"
        >
          I
        </button>
        <span className={`font-mono text-[10px] tabular-nums ${loopIn !== null ? "text-cyan-300" : "text-white/20"}`}>
          {loopIn !== null ? fmtPt(loopIn) : "--:--"}
        </span>
        <span className="text-[10px] text-white/20">—</span>
        <span className={`font-mono text-[10px] tabular-nums ${loopOut !== null ? "text-cyan-300" : "text-white/20"}`}>
          {loopOut !== null ? fmtPt(loopOut) : "--:--"}
        </span>
        <button
          type="button"
          title="Ορισμός τέλους (O)"
          onClick={() => setLoopOut(videoRef.current?.currentTime ?? 0)}
          className="rounded border border-white/10 px-1.5 py-0.5 font-mono text-[10px] text-white/40 transition hover:border-cyan-400/40 hover:text-cyan-200"
        >
          O
        </button>
        <button
          type="button"
          disabled={!loopReady}
          onClick={() => setLooping((v) => !v)}
          title={looping ? "Απενεργοποίηση loop" : "Ενεργοποίηση loop"}
          className={[
            "rounded border px-1.5 py-0.5 text-[10px] font-medium transition disabled:opacity-25",
            looping
              ? "border-amber-400/50 bg-amber-400/15 text-amber-200"
              : "border-white/10 text-white/40 hover:border-white/20 hover:text-white/70",
          ].join(" ")}
        >
          ∞
        </button>
        {(loopIn !== null || loopOut !== null) && (
          <button
            type="button"
            onClick={clearLoop}
            title="Καθαρισμός"
            className="text-[10px] text-white/25 transition hover:text-white/60"
          >
            ✕
          </button>
        )}
      </div>
    </>
  );
}
