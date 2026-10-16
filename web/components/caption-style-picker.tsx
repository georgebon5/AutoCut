"use client";

import type { CaptionStyle } from "@/lib/api.types";

/** Font list is small on purpose — libass can only use fonts that ffmpeg finds
 *  on the system; these are commonly available on macOS/Linux/Windows. */
const FONTS = [
  "Arial",
  "Helvetica",
  "Impact",
  "Georgia",
  "Verdana",
  "Comic Sans MS",
  "Times New Roman",
  "Courier New",
] as const;

const POSITIONS: { value: NonNullable<CaptionStyle["position"]>; label: string }[] = [
  { value: "top", label: "Πάνω" },
  { value: "middle", label: "Μέση" },
  { value: "bottom", label: "Κάτω" },
];

interface Props {
  value: CaptionStyle;
  onChange: (next: CaptionStyle) => void;
  disabled?: boolean;
}

function ColorField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-black/30 px-3 py-2">
      <div className="relative h-7 w-7 shrink-0 overflow-hidden rounded-full border border-white/15">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="absolute inset-0 h-10 w-10 cursor-pointer -translate-x-1 -translate-y-1 appearance-none border-0 bg-transparent"
        />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-white/70">{label}</div>
        {hint ? <div className="truncate text-[11px] text-white/40">{hint}</div> : null}
      </div>
      <div className="font-mono text-[11px] uppercase text-white/50">{value}</div>
    </div>
  );
}

export default function CaptionStylePicker({ value, onChange, disabled }: Props) {
  const patch = (p: Partial<CaptionStyle>) => onChange({ ...value, ...p });

  return (
    <fieldset
      className="flex flex-col gap-4 rounded-xl border border-white/10 bg-black/20 p-4 text-sm"
      disabled={disabled}
    >
      <legend className="px-2 text-[11px] font-medium uppercase tracking-[0.2em] text-white/40">
        Στυλ υποτίτλων
      </legend>

      <div className="grid grid-cols-2 gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-wider text-white/40">Γραμματοσειρά</span>
          <select
            value={value.font ?? ""}
            onChange={(e) => patch({ font: e.target.value || undefined })}
            className="rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-sm transition focus:border-cyan-400/50 focus:outline-none"
          >
            <option value="">Προεπιλογή</option>
            {FONTS.map((f) => (
              <option key={f} value={f} style={{ fontFamily: f }}>
                {f}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] uppercase tracking-wider text-white/40">Μέγεθος</span>
          <input
            type="number"
            min={20}
            max={200}
            step={4}
            value={value.size ?? ""}
            placeholder="80"
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              patch({ size: Number.isFinite(n) ? n : undefined });
            }}
            className="rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-sm transition focus:border-cyan-400/50 focus:outline-none"
          />
        </label>

        <ColorField
          label="Γραμμάτων"
          hint="Το κύριο χρώμα των υποτίτλων"
          value={value.text_color ?? "#ffffff"}
          onChange={(c) => patch({ text_color: c })}
        />
        <ColorField
          label="Περιγράμματος"
          hint="Για να διαβάζονται σε φωτεινά φόντα"
          value={value.outline_color ?? "#000000"}
          onChange={(c) => patch({ outline_color: c })}
        />
        <div className="col-span-2">
          <ColorField
            label="Ενεργής λέξης"
            hint="Ανάβει λέξη-λέξη καθώς τη λες (karaoke highlight)"
            value={value.highlight_color ?? "#ffff00"}
            onChange={(c) => patch({ highlight_color: c })}
          />
        </div>
      </div>

      <label className="flex items-center gap-3">
        <span className="text-[11px] uppercase tracking-wider text-white/40">Θέση</span>
        <div className="inline-flex gap-1 rounded-xl border border-white/10 bg-black/30 p-0.5">
          {POSITIONS.map((p) => {
            const active = (value.position ?? "bottom") === p.value;
            return (
              <button
                key={p.value}
                type="button"
                onClick={() => patch({ position: p.value })}
                className={`rounded-lg px-3 py-1 text-xs transition ${
                  active
                    ? "btn-primary-gradient font-semibold"
                    : "text-white/60 hover:bg-white/5 hover:text-white"
                }`}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </label>
    </fieldset>
  );
}
