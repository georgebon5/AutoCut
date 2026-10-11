import Link from "next/link";

import UploadForm from "@/components/upload-form";

export const metadata = { title: "AutoCut · Ανέβασμα" };

export default function UploadPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-6 px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Ανέβασμα clips</h1>
        <Link href="/" className="text-sm text-gray-400 hover:text-white">
          ← Jobs
        </Link>
      </header>
      <UploadForm />
    </main>
  );
}
