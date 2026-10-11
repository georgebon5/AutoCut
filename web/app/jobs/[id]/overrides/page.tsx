"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import OverridesList from "@/components/overrides-list";

export default function OverridesPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col gap-6 px-4 py-8">
      <header className="min-w-0">
        <Link
          href={`/jobs/${jobId}/review`}
          className="text-sm text-gray-400 hover:text-white"
        >
          ← Έλεγχος
        </Link>
        <h1 className="mt-1 truncate text-xl font-semibold tracking-tight">
          Οι αλλαγές μου
        </h1>
        <p className="mt-1 text-xs text-gray-500">
          Κάθε φορά που κρατάς ή κόβεις ένα κομμάτι, καταγράφεται εδώ.
          Το API αντλεί από αυτό το log για να εκπαιδεύσει ένα μοντέλο που
          θυμάται τι σου αρέσει.
        </p>
      </header>

      <OverridesList jobId={jobId} />
    </main>
  );
}
