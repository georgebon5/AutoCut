# AutoCut

Built this for a friend who makes vertical vlogs on her phone and spends
ages editing them. The idea isn't to replace her, just to hand her a strong
first rough cut she can polish.

What it does, basically: normalize the clips, cut silences and filler words
("εε", "λοιπόν", etc.), spot repeated takes and keep the last one, score
each remaining segment, pick enough to hit a target length, generate
captions, render. Optional opening hook, optional subtle punch-in zoom on
the good moments. Everything runs locally.

It's not a one-click "AI editor". It's a rough-cut assistant. Her final
timeline is still her job.

## Running it

You need Python 3.11+, ffmpeg on PATH, and (for the web UI) Node 20+.

```bash
pip install -e '.[dev]'
```

### CLI

Simplest case, one clip:

```bash
autocut ingest clip.mp4 --output-dir out/
```

Three preset lengths plus an opening hook:

```bash
autocut ingest *.mp4 --preset all --hook --zoom --output-dir out/
```

### Web app (with Docker — easiest)

If you have Docker installed, this is one command:

```bash
docker compose up --build
```

First build pulls Python + Node, grabs ffmpeg, and compiles the frontend;
takes a few minutes. After that, open http://localhost:3000.

Everything the app writes (SQLite, uploaded clips, proxies, rendered mp4s,
plus the Silero/Whisper weights it downloads the first time it runs) lives
in two Docker volumes: `autocut_data` and `autocut_cache`. Nothing hits
your home directory.

### Web app (without Docker)

Two terminals. Backend:

```bash
uvicorn autocut_api.main:app --reload
```

Frontend:

```bash
cd web && npm install && npm run dev
```

Then open http://localhost:3000. You can upload clips from a phone browser
too; the chunked upload resumes if you lose signal.

## Config

Pretty much every threshold and weight is in `config/default.yaml`. Copy
it, tweak it, point the CLI at it:

```bash
autocut -c my_config.yaml ingest clip.mp4
```

## Tests

```bash
pytest                 # ~55s on CPU
cd web && npm run build
```

There are a couple of slow tests gated behind `-m slow` that actually
download Silero + Whisper. Not run by default.

## Learning loop (optional)

After you've used it on a few videos and have her final published cuts, you
can score how well the heuristic did:

```bash
autocut eval out1/edl.json her_final_cut.mp4 --verbose
```

And train a classifier on the logged features:

```bash
autocut train out1/edl.json cut1.mp4 out2/edl.json cut2.mp4 --out model.json
autocut ingest new_clip.mp4 --model model.json --preset medium
```

If you're running the API, set `AUTOCUT_API_MODEL_PATH=/path/to/model.json`
and every new job uses it. No code change, no restart of the frontend.

## More detail

`ARCHITECTURE.md` has the module map, the HTTP endpoints, and the "why
did we do it this way" notes (why I ditched PySceneDetect, why the
trainer is numpy and not LightGBM, how caching works, etc.).

## What this doesn't try to do

- Predict virality
- Auto-pick music or beat-sync
- Color, transitions, stickers

For that stuff, export the FCPXML and finish in Final Cut / DaVinci / Premiere.
