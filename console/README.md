# Operator Console

Read-only Next.js operator console for the trading platform. This is a
debugging instrument for a single operator, not a customer-facing dashboard —
every screen it will grow shows what the running system is doing right now
and lets the operator halt it if needed.

## Prerequisites

- Node.js 22+
- The FastAPI backend running and reachable, e.g.:
  ```bash
  PYTHONPATH=src .venv/bin/python -m uvicorn trading_platform.api.app:app --port 8000
  ```
  or via `docker compose up` (the `api` service listens on port 8000 the same way).

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
make console
```

(equivalent to `cd console && npm run dev`). Then open http://localhost:3000.

## Proxy design

The FastAPI backend has no CORS middleware and none is planned — instead, every
browser call the console makes goes through Next.js rewrites under `/backend/*`
(configured in `next.config.ts`), which the Next.js server forwards to
`TRADING_CONSOLE_API_BASE_URL`. Because the browser only ever talks to the
Next.js origin, no CORS configuration is needed on the backend.
