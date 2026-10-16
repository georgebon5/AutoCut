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

export default function CaptionStylePicker({ value, onChange, disabled }: Props) {
  const patch = (p: Partial<CaptionStyle>) => onChange({ ...value, ...p });

  return (
    <fieldset
      className="flex flex-col gap-3 rounded-md border border-gray-800 bg-gray-900/60 p-3 text-sm"
      disabled={disabled}
    >
      <legend className="px-1 text-xs uppercase tracking-wide text-gray-400">
        Στυλ υποτίτλων
      </legend>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-gray-400">Γραμματοσειρά</span>
          <select
            value={value.font ?? ""}
            onChange={(e) => patch({ font: e.target.value || undefined })}
            className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5"
          >
            <option value="">Προεπιλογή</option>
            {FONTS.map((f) => (
              <option key={f} value={f} style={{ fontFamily: f }}>
                {f}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-xs text-gray-400">Μέγεθος</span>
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
            className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1.5"
          />
        </label>

        <label className="flex items-center gap-2">
          <span className="text-xs text-gray-400">Χρώμα γραμμάτων</span>
          <input
            type="color"
            value={value.text_color ?? "#ffffff"}
            onChange={(e) => patch({ text_color: e.target.value })}
            className="h-7 w-10 cursor-pointer rounded border border-gray-700 bg-transparent"
          />
        </label>

        <label className="flex items-center gap-2">
          <span className="text-xs text-gray-400">Χρώμα περιγράμματος</span>
          <input
            type="color"
            value={value.outline_color ?? "#000000"}
            onChange={(e) => patch({ outline_color: e.target.value })}
            className="h-7 w-10 cursor-pointer rounded border border-gray-700 bg-transparent"
          />
        </label>

        <label className="col-span-2 flex items-center gap-2">
          <span className="text-xs text-gray-400">
            Χρώμα ενεργής λέξης
            <span className="ml-1 text-[10px] text-gray-500">
              (ανάβει λέξη-λέξη καθώς τη λες)
            </span>
          </span>
          <input
            type="color"
            value={value.highlight_color ?? "#ffff00"}
            onChange={(e) => patch({ highlight_color: e.target.value })}
            className="h-7 w-10 cursor-pointer rounded border border-gray-700 bg-transparent"
          />
        </label>
      </div>

      <label className="flex items-center gap-2">
        <span className="text-xs text-gray-400">Θέση</span>
        <div className="flex gap-1">
          {POSITIONS.map((p) => {
            const active = (value.position ?? "bottom") === p.value;
            return (
              <button
                key={p.value}
                type="button"
                onClick={() => patch({ position: p.value })}
                className={`rounded-md px-2 py-1 text-xs transition ${
                  active
                    ? "bg-emerald-500 text-emerald-950"
                    : "bg-gray-800 text-gray-300 hover:bg-gray-700"
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
