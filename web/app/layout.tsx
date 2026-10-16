import type { Metadata } from "next";
import { Great_Vibes } from "next/font/google";
import type { ReactNode } from "react";

import Providers from "@/components/providers";
import SplashScreen from "@/components/splash-screen";

import "./globals.css";

// Calligraphic signature font — loaded as a CSS variable so only the splash
// screen uses it (keeps the main UI's typography untouched).
// Great Vibes is Latin-only (and most calligraphic Google Fonts are). The
// splash uses Latin initials to stay in the signature style; swap to any
// Greek-capable face if you'd rather use "ΓΜ".
const signatureFont = Great_Vibes({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-signature",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AutoCut",
  description: "AI rough-cut tool for vertical vlogs",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="el" className={signatureFont.variable}>
      <body>
        {/* Change this prop to swap the splash signature initials. */}
        <SplashScreen initials="GB" />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
