import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col items-center justify-center gap-6 px-4 py-14 text-center">
      <div className="font-mono text-[10rem] leading-none text-white/10">404</div>
      <div className="space-y-2">
        <h1 className="bg-gradient-to-br from-white to-white/60 bg-clip-text text-2xl font-light tracking-tight text-transparent">
          Η σελίδα δεν βρέθηκε
        </h1>
        <p className="text-sm text-white/50">
          Μάλλον το link είναι παλιό ή το job έχει σβηστεί.
        </p>
      </div>
      <Link
        href="/"
        className="btn-primary-gradient rounded-2xl px-5 py-3 text-sm font-semibold tracking-wide shadow-xl shadow-cyan-500/15 transition"
      >
        ← Πίσω στο main menu
      </Link>
    </main>
  );
}
