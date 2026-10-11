# AutoCut

AI rough-cut tool for a Greek TikTok "spend a day with me" vlog creator.
Produces a strong **first rough cut** she then polishes in her own editor;
automates silence removal, filler/repetition cuts, caption generation, and
preset-driven selection. Local-first, no third-party upload of footage.

> Measured by *editing-time saved* and *what fraction of our cut she keeps*,
> **not** by auto-generating a finished edit.

## Repo layout

```
autocut/            # Pipeline library + CLI. No HTTP, no React.
autocut_api/        # FastAPI backend wrapping the library.
web/                # Next.js 15 frontend (TypeScript + Tailwind + TanStack Query).
tests/              # Pytest for autocut + autocut_api.
config/default.yaml # All thresholds / weights / presets live here.
```

Running the three layers:

```bash
# 1. CLI only
autocut ingest clip.mp4 --preset medium --hook --pacing --zoom

# 2. API server
uvicorn autocut_api.main:app --reload

# 3. Web dev server (proxies /api → backend)
cd web && npm run dev
```

## Pipeline flow (library, `autocut/pipeline.py`)

Entry: `run_pipeline(clips, output_dir, cfg, options, reporter)`.
Reporter abstracts progress output — `_ClickReporter` for CLI, `JobReporter`
for API, `NullReporter` for tests. Any caller plugs their own sink without
touching pipeline logic.

```
ingest.py       → ingest_clips          # VFR → CFR H.264 proxy + 16kHz mono WAV
vad.py          → detect_speech         # Silero VAD
transcribe.py   → transcribe_clip       # faster-whisper, VAD-gated chunks only
fillers.py      → detect_fillers        # isolated Greek fillers (εε, λοιπόν…)
takes.py        → detect_repeated_takes # rapidfuzz near-duplicate sentences
silence.py      → apply_silence_removal # snap cuts to word boundaries + pad
features.py     → enrich_segments       # RMS, pitch (parselmouth), speaking rate
motion.py       → enrich_segments_motion# OpenCV frame diff
keywords.py     → enrich_segments_keywords # exclamations / prices / proper names
scenes.py       → detect_scene_boundaries + enrich_segments_scenes
scoring.py      → score_segments / score_segments_with_model
hook.py         → find_hook_segment     # top-score 1-3s teaser
select.py       → select_segments       # preset-driven greedy picker + pacing
render.py       → render                # ffmpeg concat + crossfades + optional zoom
captions.py     → write_captions        # SRT + ASS (TikTok style preset)
edl.py          → save_edl / load_edl   # JSON decision list
fcpxml.py       → edl_to_fcpxml         # NLE handoff (FCPXML 1.11)
eval.py         → evaluate_cut          # audio-correlation precision/recall vs editor
trainer.py      → LogisticModel + train_and_evaluate # Phase-5 classifier
```

Every segment carries `features`, `interest_score`, `decision`,
`decision_source` (auto|user), and `reasons: list[str]`. The EDL is the
single source of truth — render and FCPXML both read from it.

## API surface (`autocut_api/`)

Factory: `create_app(cfg, pipeline_fn, max_workers)`. Lifespan context
cleanly shuts down the ThreadPoolExecutor on app teardown.

| Route | Purpose |
|---|---|
| `GET  /health` | liveness + version |
| `POST /uploads` → `PUT /uploads/{id}/chunks/{i}` → `GET /uploads/{id}` → `POST /uploads/{id}/complete` | chunked / resumable upload (5MB chunks) |
| `POST /jobs` / `GET /jobs` / `GET /jobs/{id}` | create pipeline job + poll |
| `GET  /jobs/{id}/edl[?preset=X]` | load EDL as JSON |
| `PATCH /jobs/{id}/segments/{i}[?preset=X]` | flip keep/cut, log to `SegmentOverride` |
| `GET  /jobs/{id}/outputs` / `GET /jobs/{id}/outputs/{filename}` | whitelist-filtered downloads |
| `POST /jobs/{id}/render[?preset=X]` | **cached re-render** — reads overridden EDL, skips all analysis |
| `GET  /jobs/{id}/export/fcpxml[?preset=X]` | NLE handoff |
| `GET  /jobs/{id}/overrides` | training-data export |

SQLite (WAL) + SQLAlchemy 2.0 sync sessions. Jobs are self-describing:
`config_json` carries every option so a run is reproducible from the row
alone. `SegmentOverride` is append-only; the first row for a given
`(job, preset, segment)` captures the auto baseline (its `previous_decision`
is the pipeline's original decision).

**Phase 4 acceptance** verified by `test_render_does_not_invoke_analysis_functions`:
the re-render path tripwires `ingest_clips / detect_speech / transcribe_clip /
enrich_segments / enrich_segments_motion / score_segments / run_pipeline` —
any firing fails the test. That's the "no re-analysis" guarantee enforced
in code.

## Web (`web/`)

Next.js 15 App Router. `next.config.mjs` rewrites `/api/*` → FastAPI, so no
CORS during dev. `AUTOCUT_API_URL` env var overrides the backend address.

```
app/page.tsx                  # job list + health badge, polls while a job runs
app/upload/page.tsx           # multi-file picker + options + chunked upload
app/jobs/[id]/review/page.tsx # preview + timeline + transcript + downloads
components/
  upload-form.tsx             # per-file state, parallel across files
  timeline.tsx                # px-per-second horizontal strip, tap to toggle
  transcript.tsx              # click a word to toggle its segment
  preview-player.tsx          # cache-busts via ?v=job.updated_at
  downloads-panel.tsx         # outputs list + FCPXML export button
lib/
  api.ts        # typed fetch wrappers for every endpoint
  api.types.ts  # hand-written mirrors of Pydantic schemas
  upload.ts     # chunked uploader with progress callback
  use-segment-toggle.ts  # optimistic PATCH shared by timeline + transcript
```

## Tests

```bash
pytest tests/                 # 571 tests, ~55s on CPU
pytest tests/test_pipeline.py # specific module
pytest -m slow                # real Silero + Whisper, downloads models
cd web && npm run typecheck   # strict TS + noUncheckedIndexedAccess
cd web && npm run build       # verifies prod bundling
```

Fakes injected via `monkeypatch.setattr("autocut.pipeline.load_vad_model", ...)`
etc. — all heavy I/O goes through module-level references so tests can swap
them without downloading models.

## Key design decisions (and why)

- **OpenCV HSV frame diff instead of PySceneDetect.**
  PySceneDetect's decode thread assertion-fails on this machine's OpenCV 5
  (returns `cv2.Mat` where it expects `np.ndarray`). We port the same
  algorithm (HSV mean diff + min scene length) directly, using the same
  `np.array(cv2.cvtColor(...))` workaround `motion.py` already relies on.

- **Pure-numpy logistic regression instead of LightGBM.**
  `pip install lightgbm` succeeds but at runtime libomp is missing; the
  scipy ABI also broke numpy 1.26 compatibility. `trainer.LogisticModel`
  has the same `fit / predict_proba / save / load` surface, serialises as
  JSON, and swapping to LightGBM later is a one-file change in `trainer.py`.

- **Thread pool + FastAPI BackgroundTasks instead of Redis + RQ.**
  Spec said "RQ or Celery"; for a single-tenant local-first app with one
  concurrent job, a bounded `ThreadPoolExecutor` + SQLite WAL is enough.
  The job progress feed is identical in shape to what RQ would produce,
  so the swap is a `runner.py` rewrite when scale demands.

- **FCPXML as the one NLE export.** DaVinci Resolve, Final Cut Pro X,
  Premiere Pro, iMovie and Motion all import it. The alternative was
  Premiere's older XML + a separate EDL + FCPXML — three formats for one
  feature. `<asset src>` points at the ORIGINAL footage so finishing is
  full-quality.

- **Audio cross-correlation for evaluation.** Visual re-encodes mangle
  frame hashes; audio passes through NLEs effectively lossless, so
  FFT-based normalised cross-correlation gives sample-accurate match
  positions for a few ms of compute per segment.

- **Caching = reusing the EDL + proxies on disk.** `rerender_from_edl`
  reads the user-edited EDL (`decision` already flipped by PATCH), reuses
  the proxy files produced at ingest, and jumps straight to the ffmpeg
  concat. The "no re-analysis" invariant is enforced by a tripwire test.

- **Hook + original decision preserved separately.** The EDL stores
  `hook: Optional[Segment]`. The `SegmentOverride` table captures user
  toggles in append-only form; `features_json` snapshots live features at
  override time so training data stays valid even after re-renders rewrite
  the EDL.

## Environment quirks

- **scipy 1.14.1 pinned** (`pip install 'scipy<1.15'`) — newer scipy expects
  numpy ≥2.0 but we're on 1.26; `scipy.signal` fails to import otherwise.
  `autocut.eval` ships its own FFT cross-correlation to avoid depending on
  `scipy.signal` at all; but `tests/test_eval.py` uses `scipy.io.wavfile`
  for test fixtures, so keep scipy present.
- **PySceneDetect installed but unused.** The dependency stays in
  `pyproject.toml` as a safety net — if someone fixes the OpenCV 5
  conflict, swapping `autocut.scenes` back is trivial.
- **libomp absent → no LightGBM runtime.** `brew install libomp` on this
  machine; or stay on the numpy logistic model.

## Config knobs

Everything thresholdable lives in `config/default.yaml`:

- `vad.threshold`, `vad.min_speech_duration_ms`, `vad.min_silence_duration_ms`
- `silence.min_silence`, `silence.padding`
- `fillers.words`, `fillers.min_isolation_ms`, `fillers.padding_ms`
- `takes.similarity_threshold`, `takes.min_gap_s`, `takes.min_words`
- `captions.max_words`, `captions.max_duration_s`, `captions.style` (tiktok|default)
- `scenes.threshold`, `scenes.boundary_window_s`
- `keywords.exclamations`, `keywords.price_pattern`, etc.
- `scoring.weights`, `scoring.feature_max`
- `hook.min_duration_s`, `hook.max_duration_s`
- `pacing.*` (opt-in short-opener bias)
- `zoom.*` (opt-in subtle push-in)
- `presets.targets` (tight 45s / medium 90s / loose 180s)

Env vars (API):

- `AUTOCUT_API_DATA_DIR` — where jobs/uploads/db live
- `AUTOCUT_API_DATABASE_URL` — override SQLite path
- `AUTOCUT_API_UPLOAD_CHUNK_SIZE` — bytes per chunk (default 5 MB)
- `AUTOCUT_API_MODEL_PATH` — admin-set LogisticModel for every job

## Known limitations

- Scene boundaries use mean HSV diff instead of PySceneDetect's weighted
  luma/sat/hue formula. Fine for phone footage, less accurate on
  color-graded content.
- Caption style picker is config-only (`captions.style`); no UI exposure yet.
- No way to upload a trained model via API — admin must drop the JSON on the
  server filesystem and set the env var.
- Overrides have no UI readback; training data is export-only.
- No model manages its own stage/progress in the re-render path — the UI
  sees "rendering" then "complete", no stage subdivisions.
- `--pacing` only applies when a preset is active; the backend rejects
  pacing + preset=none at job creation.

## Commit style

Scanning `git log --oneline`:

```
feat: Phase 3 Task 14 target-duration preset selection
feat: Phase 4 Task 21 resumable chunked upload endpoints
feat: Phase 5 Task 33 FCPXML export for NLE handoff
```

Phase + Task tag when applicable, lowercase after the colon, specific
rather than generic. Follow the pattern.
