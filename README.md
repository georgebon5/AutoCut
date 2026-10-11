# AutoCut

AI rough-cut tool for Greek vertical vlog creators. Normalizes phone
footage, removes silences and filler words, detects repeated takes,
scores each segment, and renders a punchy first cut you can polish in
your own editor.

Local-first: your footage never leaves your machine.

## What it does

- **Removes dead time** (silences > 0.5s, filler words, repeated takes)
- **Scores segments** on prosody, motion, keyword hits, scene changes
- **Fits a target duration** via `tight` (45s) / `medium` (90s) / `loose` (3min) presets
- **Opening hook** — the top-scoring 1–3s moment prepended as a teaser
- **Punch-in zoom** on emphasis moments (optional)
- **Captions** in SRT + ASS (TikTok style preset: safe zone, karaoke highlight)
- **FCPXML export** so you can finish the edit in Final Cut, DaVinci Resolve or Premiere
- **Non-destructive**: outputs a decision list (EDL JSON) + a rendered MP4;
  nothing you can't override

## Requirements

- Python 3.11+
- ffmpeg + ffprobe (`brew install ffmpeg`)
- Node 20+ and npm (for the web app only)

## Install

```bash
pip install -e '.[dev]'
```

## Three ways to use it

### 1 — CLI (fastest)

```bash
# One clip, default heuristic
autocut ingest clip.mp4 --output-dir out/

# All three presets + hook + punch-in zoom
autocut ingest *.mp4 --preset all --hook --zoom --output-dir out/

# Score segments vs your final published edit
autocut eval out/edl.json final.mp4 --verbose

# Train a classifier that replaces the hand-tuned weights
autocut train out1/edl.json final1.mp4 out2/edl.json final2.mp4 --out model.json
autocut ingest new_clip.mp4 --model model.json --preset medium
```

### 2 — HTTP API

```bash
uvicorn autocut_api.main:app --reload
# → http://127.0.0.1:8000/docs for interactive OpenAPI
```

Chunked / resumable uploads, async job runner, EDL review, cached re-render,
FCPXML export. See `ARCHITECTURE.md` for the full endpoint map.

### 3 — Web app

```bash
# Backend
uvicorn autocut_api.main:app --reload

# Frontend (new terminal)
cd web && npm install && npm run dev
# → http://localhost:3000
```

Upload clips → wait for the job → review timeline + transcript → toggle
keep/cut → re-render (no re-analysis) → download MP4/SRT or export FCPXML.
Works on a phone browser.

## Config

Every threshold and weight lives in `config/default.yaml`. Point the CLI at
your own file:

```bash
autocut -c my_config.yaml ingest clip.mp4
```

See `ARCHITECTURE.md` for the knobs that matter most.

## Testing

```bash
pytest                     # 571 tests, ~55s
pytest -m slow             # real Silero + Whisper (model downloads)
cd web && npm run build    # frontend typecheck + bundle
```

## Deployment notes

- SQLite with WAL mode — single file at `autocut_data/autocut.db`
- Thread pool runs 2 jobs in parallel by default; swap to Redis + RQ when
  you grow past one tenant
- Admin can drop a trained model at any path and set
  `AUTOCUT_API_MODEL_PATH=/path/to/model.json` — every new job uses it
  automatically

## Deeper reading

**`ARCHITECTURE.md`** — module-by-module tour, API surface, design decisions
(why OpenCV instead of PySceneDetect, why pure-numpy LR instead of LightGBM,
why audio-correlation evaluation), environment quirks, known limitations.

## Non-goals

- Virality / engagement prediction
- Automatic music selection or beat sync
- Color grading, transitions, stickers

If you want any of those, use your NLE after exporting the FCPXML.
