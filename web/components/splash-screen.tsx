"use client";

import { useEffect, useState } from "react";

interface Props {
  /** The initials to show, e.g. "GB". */
  initials: string;
}

/**
 * Full-screen opening splash with the user's calligraphic signature in the
 * primary brand gradient. Stays visible until the user presses Enter (or
 * clicks/taps the screen for mobile), then fades out and unmounts.
 *
 * Once-per-session: sessionStorage flag so it only plays on the first visit
 * of a tab, not on every client-side route change.
 */
export default function SplashScreen({ initials }: Props) {
  const [phase, setPhase] = useState<"hidden" | "showing" | "fading" | "done">(
    "hidden",
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const KEY = "autocut:splash:shown";
    if (window.sessionStorage.getItem(KEY) === "1") {
      setPhase("done");
      return;
    }
    window.sessionStorage.setItem(KEY, "1");
    setPhase("showing");
  }, []);

  // Enter / click / tap dismisses the splash. We listen on window because the
  // splash itself has nothing focusable and the body receives keydown by
  // default on page load.
  useEffect(() => {
    if (phase !== "showing") return;
    const dismiss = () => setPhase("fading");
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        dismiss();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase]);

  // After the fade transition completes, unmount so we don't block interaction.
  useEffect(() => {
    if (phase !== "fading") return;
    const t = setTimeout(() => setPhase("done"), 600);
    return () => clearTimeout(t);
  }, [phase]);

  if (phase === "done") return null;

  return (
    <div
      aria-hidden
      onClick={() => phase === "showing" && setPhase("fading")}
      className={`fixed inset-0 z-50 flex cursor-pointer flex-col items-center justify-center bg-[#07070b] transition-opacity duration-500 ${
        phase === "fading" ? "pointer-events-none opacity-0" : "opacity-100"
      }`}
      style={{
        backgroundImage:
          "radial-gradient(1200px 600px at 10% -10%, rgba(34,211,238,0.10), transparent 60%), " +
          "radial-gradient(900px 500px at 95% 110%, rgba(167,139,250,0.10), transparent 55%)",
      }}
    >
      <span
        className="splash-letters select-none"
        style={{
          fontFamily: "var(--font-signature)",
          animation: "splashIn 900ms cubic-bezier(.2,.7,.2,1) both",
        }}
      >
        {initials}
      </span>
      <div className="splash-prompt mt-10 flex items-center gap-3 text-sm tracking-[0.3em] text-white/50">
        <kbd className="rounded-md border border-white/15 bg-white/5 px-2 py-1 font-mono text-xs text-white/80">
          Enter
        </kbd>
        <span className="uppercase">για είσοδο</span>
      </div>
      <style>{`
        .splash-letters {
          font-size: clamp(2.5rem, 7vw, 5.5rem);
          /* Cursive glyphs have tall ascenders/descenders — give them room
             so they don't get clipped at the baseline or cap height. */
          line-height: 1.4;
          padding: 0.3em 0.1em;
          background-image: linear-gradient(135deg, #22d3ee 0%, #818cf8 55%, #e879f9 100%);
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
          filter: drop-shadow(0 10px 60px rgba(129, 140, 248, 0.35));
          letter-spacing: 0.02em;
        }
        .splash-prompt {
          animation: promptPulse 2.4s ease-in-out 1.2s infinite;
          opacity: 0;
        }
        @keyframes splashIn {
          0%   { opacity: 0; transform: translateY(20px) scale(0.92); }
          60%  { opacity: 1; transform: translateY(0) scale(1.02); }
          100% { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes promptPulse {
          0%, 100% { opacity: 0.35; }
          50%      { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
