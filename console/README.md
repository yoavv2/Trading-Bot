# Research Console

Next.js console for the strategy research platform: author strategy
specifications (YAML), validate and approve immutable versions, search the
asset catalog and keep saved lists, define studies, check readiness, run them,
compare the evidence per asset, freeze a candidate and run the final test. The
generic Jobs pages (`/jobs`, `/jobs/new`, `/jobs/<id>`) list and submit the
research Jobs the studies run on.

The former operator console (system status, runs, paper trading, controls,
kill-switch banner) is no longer served: `/`, `/strategy`, `/runs`, `/paper`
and `/controls` redirect to the research pages (`src/lib/legacyRedirects.ts`,
applied by `next.config.ts`). The trading components stay in the tree, frozen.
Every research page gates on `GET /health` reporting `"mode": "research"`;
against a trading-mode or unreachable API it shows what to start instead.

## Prerequisites

- Node.js 22+
- The repo's canonical local development setup (see the root `README.md`):
  `make dev` from the repo root runs the API, the Job worker and this console
  together.

## Setup

```bash
cd console
npm install         # first time only (or: make console-install from repo root)
cp .env.example .env.local
```

Edit `.env.local` if the API is not at the default `http://127.0.0.1:8000`:

```
TRADING_CONSOLE_API_BASE_URL=http://127.0.0.1:8000
```

## Start

From the repo root:

```bash
make dev
```

Then open http://localhost:3000 (it lands on Research → Studies; `127.0.0.1`
works too — both hosts are in `allowedDevOrigins`). `make console` (equivalent to
`cd console && npm run dev -- --port 3000`) starts the console alone when the
API and worker are already running.

## Proxy design

The FastAPI backend has no CORS middleware and none is planned — instead, every
browser call the console makes goes through Next.js rewrites under `/backend/*`
(configured in `next.config.ts`), which the Next.js server forwards to
`TRADING_CONSOLE_API_BASE_URL`. Because the browser only ever talks to the
Next.js origin, no CORS configuration is needed on the backend.
