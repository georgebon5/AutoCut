# AutoCut Web

Next.js 15 + React 19 + TypeScript + Tailwind frontend for the AutoCut API.

## Running locally

```bash
# 1. Start the FastAPI backend from the repo root
uvicorn autocut_api.main:app --reload

# 2. In another terminal, start the Next.js dev server
cd web
npm install          # first time only
npm run dev          # http://localhost:3000
```

The dev server rewrites `/api/*` → the backend at `http://127.0.0.1:8000`
(see `next.config.mjs`). Override with `AUTOCUT_API_URL`:

```bash
AUTOCUT_API_URL=http://192.168.1.10:8000 npm run dev
```

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Hot-reloading dev server |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run typecheck` | `tsc --noEmit` |

## Project structure

```
web/
├── app/               # Next.js App Router pages
│   ├── layout.tsx     # root layout + Providers wrapper
│   ├── page.tsx       # landing: /health + job list
│   └── globals.css    # Tailwind entry
├── components/
│   └── providers.tsx  # TanStack Query client
├── lib/
│   ├── api.ts         # typed fetch wrappers for every API endpoint
│   └── api.types.ts   # TS mirror of autocut_api/schemas.py
├── next.config.mjs    # /api/* rewrite → FastAPI
├── tailwind.config.ts
└── tsconfig.json      # strict TS
```

Keep `lib/api.types.ts` in sync with `autocut_api/schemas.py` by hand for
now — a future task will auto-generate from the OpenAPI schema.
