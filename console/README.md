# Research Console

Next.js console for Strategy Research: author strategy specifications (YAML
editor with inline validation and the deterministic explanation, plus the
optional assistant panel), approve immutable versions, search the asset
catalog and keep saved lists, define studies, check readiness, run them, compare
the evidence per asset, freeze a candidate and run the final test. The generic
Jobs pages (`/jobs`, `/jobs/new`, `/jobs/<id>`) list and submit the research
Jobs the studies run on.

Primary navigation (`src/lib/navigation.ts`): **Strategies · Assets · Studies
· Jobs**. `/` lands on Studies.

## What it serves

| Route | Page |
| --- | --- |
| `/research/strategies` | Families, drafts, version history |
| `/research/strategies/new`, `/research/strategies/drafts/[draftId]` | YAML editor, validation findings, explanation, assistant panel, Save / Approve / Duplicate / Delete |
| `/research/strategies/versions/[versionId]` | Immutable version: YAML as approved, explanation, lineage, assistant provenance chain |
| `/research/assets` | Catalog search (named-versus-total count, coverage dates), saved lists |
| `/research/studies`, `/research/studies/new`, `/research/studies/[id]` | Study list, wizard, study detail: revisions, readiness, progress, results, freeze, final test, exposures, export, report links |
| `/jobs`, `/jobs/new`, `/jobs/[jobId]` | Generic Job surface (research Job types only against a research-mode API) |

The former operator console (system status, runs, paper trading, controls,
kill-switch banner) is no longer served: `/`, `/strategy`, `/runs`,
`/runs/:runId`, `/paper` and `/controls` redirect permanently to the research
pages (`src/lib/legacyRedirects.ts`, applied by `next.config.ts`;
`consoleRedirects.test.ts` pins the table). The trading components stay in the
tree under `components/{status,strategy,runs,paper,controls}`, frozen.

Every research page gates on `GET /health` reporting `"mode": "research"`.
Against an unreachable API it shows the status, the configured base URL and
the command to start the stack; against a trading-mode API it names the
research start command instead of rendering empty panels.

## Workflow in the UI

1. **Author** a strategy on the editor page (or let the assistant draft one;
   its proposal is applied to a draft only by an explicit click).
2. **Approve** the draft into an immutable version (confirmation dialog).
3. **Study**: the wizard lists every approved version grouped by family (older
   ones marked); an asset combobox (catalog search by ticker or name, keyboard
   selection, removable tags, bulk paste, a saved list shown as `list` tags that
   match the stored snapshot exactly); a **Research period** (start and end date
   with shortcuts, an editable split preset — 60/20/20 by default, a convenience
   not a methodology — a three-colour timeline of the computed windows, and
   **Customize periods** for the six window dates with the backend's ordering
   rules checked beside each control); capital, quantity policy, costs,
   objective and constraint (required, never pre-filled). While you fill it in,
   a read-only preflight (`POST /api/v1/research/studies/preflight`, the same
   coverage and warm-up logic as readiness, nothing created) marks every tag
   with its catalog coverage (covers period / starts … / ends … / not in
   catalog / unknown when the check failed), groups findings by asset and
   cause with the affected versions on demand, and, when the end date is past
   an asset's catalog end, names the assets and offers "Use latest common
   catalog end" (never applied silently). Period shortcuts end on the
   exchange calendar's last completed session. Empty costs and the
   objective's constraint are flagged beside the inputs and summarised near
   Create as "can be created, but will not be ready to run" (zero is a
   value). A new revision keeps the stored dates to the day (custom periods
   preserved). An optional
   **Describe your study** assistant proposes settings for review; **Apply to
   form** fills the wizard and creation stays the wizard's own button. The
   readiness panel lists every blocking item; **Run** submits the Job graph.
4. **Compare** on the study page: inputs state, verdict, ranking by name and
   version number, per-asset tables and charts, evidence grades, benchmark
   beside each candidate, exposures from other studies.
5. **Freeze** the leading candidate with the acceptance criteria.
6. **Final test** (shown as running while its Jobs are queued), then the
   outcome block and the report/export links.

## Assistant panel

Mounted under the editor on `/research/strategies/new` and
`/research/strategies/drafts/[draftId]`. States: disabled / unconfigured (naming the
exact settings to set), exhausted allowance, loading with the elapsed time
against the deadline, timeout, refusal, invalid output after the retry,
provider error, busy, revision cap. A proposal shows editable YAML, the
assistant's note, the unsupported requests with codes, the validator's
findings and the explanation. **Use in editor** copies text; **Apply** writes
the draft. Each click sends one request with a fresh token; a failed request
keeps the typed text and the last proposal. A proposal not yet applied is not
restored after a page reload (it stays retrievable by id through the API).

Activation, model configuration, limits and accounting are described in the
root `README.md` ("Research assistant"). The default model is
`claude-haiku-5-5`; the contract version shown on provenance rows is `s5-v2`.

## Study assistant

Mounted at the top of `/research/studies/new` (collapsed by default). Same
activation, limits and allowance as the editor assistant (`GET
/api/v1/research/assistant`); one request is `POST
/api/v1/research/assistant/study-proposals`, contract version `study-v1`.
The proposal shows what the assistant understood, the information it did
**not** invent (costs, risk constraint, capital, dates are listed as missing),
the strategies resolved against approved versions (by version id or family
name; unresolved references are named, never guessed), the tickers with a
catalog flag, the periods as they would be applied, and a table of the fields
**Apply to form** will replace. Follow-up instructions revise the proposal:
the whole previous proposal (summary, missing items, unsupported requests,
settings) is sent back as the base. The follow-up counter is per page load;
the only enforced limits are the API's daily allowance and concurrency.
Applying only patches the fields the proposal filled; a range-only proposal
switches the period to the preset split (split and windows patched together),
while a proposal that repeats the unchanged range leaves custom periods alone.
Nothing is persisted but the usage ledger.

## Prerequisites

- Node.js 22+
- The repo's canonical local development setup (see the root `README.md`):
  `make research-bootstrap` once, then `make dev` from the repo root runs the
  API, the Job worker and this console together.

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

Then open http://localhost:3000 (`127.0.0.1` works too; both hosts are in
`allowedDevOrigins`). `make console` (equivalent to
`cd console && npm run dev -- --port 3000`) starts the console alone when the
API and worker are already running.

## Tests

```bash
npm test            # vitest (431 tests at the last recorded run)
npx tsc --noEmit
npm run lint
npm run build
```

Pinned inventories: `consoleRouteInventory.test.ts` (root redirect, three Job
pages, nine research pages; the five trading pages pinned as removed),
`consoleMutationInventory.test.ts` (research mutations as an exact set beside
the five trading mutations), `consoleRedirects.test.ts` (redirect table,
navigation, no trading chrome in the layout).

## Proxy design

The FastAPI backend has no CORS middleware and none is planned. Every browser
call goes through Next.js rewrites under `/backend/*` (configured in
`next.config.ts`), which the Next.js server forwards to
`TRADING_CONSOLE_API_BASE_URL`. `lib/api.ts` is the only fetch site; research
writes go through one `researchMutation(method, endpoint)` helper.
