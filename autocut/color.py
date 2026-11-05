"""Color grading presets expressed as ffmpeg ``eq`` filter fragments.

Each value is a filter-graph fragment that can be inserted directly into a
``filter_complex`` chain between an input label and an output label, e.g.:

    "[vconcat]eq=brightness=0.02:contrast=1.05:saturation=1.2[vgraded]"

Only the built-in ``eq`` filter is used so no special ffmpeg build flags
(libvmaf, etc.) are needed.
"""

from __future__ import annotations

COLOR_PRESETS: dict[str, str] = {
    "warm": (
        "eq=brightness=0.02:contrast=1.05:saturation=1.2"
        ":gamma_r=1.05:gamma_b=0.92"
    ),
    "cool": (
        "eq=brightness=0.00:contrast=1.05:saturation=1.1"
        ":gamma_r=0.92:gamma_b=1.08"
    ),
    "cinematic": "eq=brightness=-0.03:contrast=1.3:saturation=0.75",
    "vibrant":   "eq=brightness=0.02:contrast=1.1:saturation=1.5",
}

VALID_PRESETS: frozenset[str] = frozenset({"none"} | COLOR_PRESETS.keys())
