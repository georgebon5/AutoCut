import Link from "next/link";

import UploadForm from "@/components/upload-form";

export const metadata = { title: "AutoCut · Ανέβασμα" };

export default function UploadPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-8 px-4 py-14">
      <header className="flex items-center justify-between">
        <div>
          <Link
            href="/"
            className="text-xs font-medium uppercase tracking-[0.2em] text-white/40 transition hover:text-white/70"
          >
            ← Jobs
          </Link>
          <h1 className="mt-2 bg-gradient-to-br from-white to-white/60 bg-clip-text text-3xl font-light tracking-tight text-transparent">
            Ανέβασμα clips
          </h1>
        </div>
      </header>
      <UploadForm />
    </main>
  );
}
