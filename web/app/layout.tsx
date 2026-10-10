import type { Metadata } from "next";
import type { ReactNode } from "react";

import Providers from "@/components/providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "AutoCut",
  description: "AI rough-cut tool for vertical vlogs",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="el">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
