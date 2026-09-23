# Phase 19: Job Operations Vertical Slice - Context

**Gathered:** 2026-09-23
**Status:** Ready for planning

<domain>
## Phase Boundary

A backtest submitted from the console travels the full production path — `POST /api/v1/jobs` → `JobOrchestrationService` → registered `backtest` handler → production worker (`run-jobs`) → existing `services.backtesting.run_backtest` — and its progress, logs, events, linked domain result, failure state, and cancellation are observable in generic, job-type-agnostic Job UI.

Delivers: the first production Job type (`backtest`), an explicit persisted Job → StrategyRun relationship exposed through the Job API, the read-only job-type catalog (ORCH-06), the configuration-driven mutation guard (ORCH-07), the compose worker switched to `run-jobs` (ORCH-05), generic Job list/detail/logs/events/cancel screens with auto-refresh (JOBUI-01..05), and one submission form (backtest).

Out of scope (roadmap-fixed): every operation other than backtest; safety controls; retry; `scripts/` retirement; history view; JSON-Schema-driven form generation; SSE/WebSockets; auth; `submitted_by`/identity fields.

</domain>

<decisions>
## Implementation Decisions

### Job → domain result contract (user-added area)
- **D-01:** The Job ↔ StrategyRun relationship is persisted as `strategy_runs.job_id`: nullable FK → `jobs.id` with a UNIQUE constraint (one Job produces at most one run; pre-Phase-19 and non-Job runs have `NULL`). Enforced by DB constraint, not convention.
- **D-02:** `job_id` is written in the **same transaction that creates the run** — `run_backtest` accepts an opaque originating `job_id` and passes it to `_create_backtest_run`. The link therefore exists from the moment the run exists (RUNNING) and survives every Job terminal state (SUCCEEDED, FAILED, CANCELLED). No link is ever inferred from logs, timestamps, or `trigger_source`.
- **D-03:** `JobContext` is NOT extended (frozen Phase 17 contract stays frozen). The handler passes `context.job_id` to the service; the framework never writes domain tables.
- **D-04:** `GET /api/v1/jobs/{job_id}` gains a generic `resources` array: `[{kind, id, status, links: {self}}]`. `kind` is a closed enum; Phase 19 defines exactly one value, `strategy_run`, whose `links.self` is `/api/v1/runs/{id}`. `resources` is derived at read time from the FK (`job_reads` query), not stored as JSON. Empty array when no linked resource exists.
- **D-05:** `resources[]` is visible as soon as the run exists — while the Job is RUNNING and after any terminal state. A stuck, timed-out, or cancelled Job never hides its run.
- **D-06:** `resources[]` is the **authoritative** link. The backtest handler's success `result_summary` also contains `run_id` plus a few metric totals for display; a test asserts `result_summary.run_id == resources[0].id` whenever both exist. The console navigates **only** from `resources[]`; `result_summary` is rendered as a generic key/value view with no special-cased keys. Roadmap SC1 and REQUIREMENTS JOBUI-02 wording was amended during this discussion to reference `resources[]` (done; no planner action).
- **D-07:** Back-link: the run-detail API response adds nullable `job_id`; the console run header (`RunHeaderPanel`) shows "Created by Job <id>" linking to `/jobs/{id}` when non-null, nothing otherwise.

### Backtest payload & validation
- **D-08:** Backtest payload is `{strategy_id, from_date, to_date}` — all three **required**. Dates are never defaulted inside `validate_payload` (the idempotency fingerprint is computed over the normalized payload; resolving "latest session" at submit time would turn a same-key replay after the next session close into a 409). The Job payload records exactly what ran.
- **D-09:** Submit-time validation (typed HTTP 422, zero rows written, before Job persistence — consistent with Phase 18 D-05): unknown `strategy_id` (checked against the strategy registry); `from_date > to_date`; `to_date` in the future; unknown payload keys (strict schema, `extra="forbid"`). Each rejection has a stable machine-readable reason and a test.
- **D-10:** Console pre-fill comes from the catalog: each `GET /api/v1/job-types` entry may carry an optional generic `submission_defaults` object computed at read time by that type's submission spec. Backtest supplies `{from_date, to_date}` using the same logic as `resolve_backtest_window` (latest completed session; lookback from `settings.market_data.ingest.default_lookback_days`). No backtest-specific read endpoint.
- **D-11:** Job-created StrategyRuns record `trigger_source = "job"` (single value for every Job-originated run; provenance lives on the `job_id` FK).

### Running-cancel honesty
- **D-12:** The backtest handler has cancellation checkpoints (`raise_if_cancelled`) **before** and **after** the single `run_backtest` call. A cancel arriving mid-call is acknowledged at the post-call boundary (Job → `CANCELLED`) or, if the call outlives the grace period, the Job lands `FAILED`/`cancellation_timeout` with `outcome_uncertain` (Phase 17 D-09). A running cancel never yields `SUCCEEDED`.
- **D-13:** The StrategyRun stays truthful: when the Job is CANCELLED after the service completed, the run keeps its real domain status (SUCCEEDED/FAILED). No handler or framework code rewrites run status; the linked run remains reachable via `resources[]` (D-05).
- **D-14:** The console composes an honest cancellation/outcome label with **one job-type-agnostic function** over generic fields only: `status`, `cancellation_requested_at`, `cancellation_acknowledged_at`, `cancellation_cause`, `failure_reason`, `outcome_uncertain`, `resources[].status`. Examples: "Cancelled before start — never executed"; "Cancelled — linked run had already completed (SUCCEEDED)"; "Cancellation timed out — outcome uncertain; run still RUNNING". Unit-tested against a case table covering every reachable combination.
- **D-15:** Cancel confirmation dialog: reason is **optional** (matches Phase 18 D-12: ≤500 chars, trimmed, blank → null). The dialog shows Job type/id and, for a RUNNING Job, states that cancellation takes effect only at the next step boundary.
- **D-16:** Backtest progress: step text only (e.g. `resolving strategy` → `running backtest` → `recording result`), `percent = null` until the framework sets 100 on SUCCEEDED. The UI renders an indeterminate bar whenever `percent` is null. No fabricated percentages.

### Submission entry & mutation guard UX
- **D-17:** Generic entry: `/jobs` list has a "New Job" action → type picker built from the catalog → `/jobs/new?type=<job_type>` renders the type's form via a single `job_type → form component` map. The console has exactly two lookup maps, each in one module: (1) this `job_type → form` map, and (2) the `resources[].kind → route` map (D-04). List/detail/logs/events components contain no `job_type` branches and never import map (1); Job detail renders `resources[]` through a generic resource-link component that uses map (2), which is keyed by resource kind, not job type. Phase 20 adds forms to map (1).
- **D-18:** Shortcut: `/strategy` gets a "Run backtest" button that deep-links to `/jobs/new?type=backtest&strategy_id=<id>` (same form, pre-selected strategy). After a successful submit, navigate to `/jobs/{job_id}`.
- **D-19:** ORCH-07 mutation flag defaults to **disabled** when unset. `docker-compose.yml` / `.env.example` enable it for local use; `render.yaml` sets it disabled explicitly. When disabled every mutating route returns a typed 403 with a stable code and writes zero rows.
- **D-20:** `GET /api/v1/job-types` response shape is `{mutations_enabled: bool, items: [{job_type, description, cancellation_mode, submission_defaults?}]}` — single source for the console's mutation capability signal.
- **D-21:** When `mutations_enabled` is false, submit/cancel/"New Job"/"Run backtest" controls stay **visible but disabled** with an inline reason ("Mutations disabled on this deployment"). All read-only Job screens work fully.

### Worker configuration
- **D-22:** `run-jobs` boots with **base (BACKTEST-level)** config validation — no broker credentials required to start. Each registered Job type declares the `ExecutionMode` it needs (backtest → `BACKTEST`). Immediately before a handler runs, that mode's config is validated; on failure the Job lands `FAILED` with a new closed `JobFailureReason` value `config_invalid` and a message naming the failing fields, and the worker keeps running. Phase 20's paper-family types will declare `PAPER`.
- **D-23:** The catalog does NOT expose a type's required mode (kept minimal: description, cancellation_mode, submission_defaults, plus top-level mutations_enabled). Config problems surface as FAILED Jobs with `config_invalid`.

### Claude's Discretion
- `cancellation_mode` is a closed enum containing only values needed now (backtest: cooperative at handler step boundaries); naming is Claude's. Phase 20 extends it (e.g. "before broker submission").
- Guard ordering on mutating routes (ORCH-07 403 vs missing-`Idempotency-Key` 400 vs 422) — choose one order and pin it with a test; recommended: 403 first so a disabled deployment reveals nothing else.
- Polling: extend `useApiQuery` (no SWR/TanStack — carried-forward decision) with an optional interval that runs only while the Job is non-terminal and stops at a terminal state (JOBUI-05); cadence and pause-when-tab-hidden behavior are Claude's choice.
- Log tail UX: cursor-based via `after_sequence`; auto-scroll/“follow” behavior is Claude's choice.
- Idempotency-Key lifecycle in the console: generated once per form instance, reused on transport-level retry, regenerated when the payload changes; a `200` + `Idempotency-Replayed: true` response shows "Already submitted — opening existing Job" and navigates.
- Add a "Jobs" nav link in `layout.tsx` now.
- "`to_date` in the future" (D-09) is judged against the exchange-calendar date obtained through an injectable clock, never the host's local date; pinned by a deterministic test.
- Catalog resilience: if `submission_defaults` cannot be computed (e.g. DB unavailable or no sessions), the catalog still returns `mutations_enabled` and `items`, omitting `submission_defaults` for that entry; pinned by a test.
- Mutating client lives in `console/src/lib/api.ts` (SC6: no `fetch(` elsewhere); exact function names are Claude's.
- Flag/env var name for ORCH-07, typed 403 error code string, migration naming.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Milestone contracts
- `.planning/ROADMAP.md` § Phase 19 — goal, 9 success criteria, out-of-scope list (SC1 / SC9 wording notes in D-06)
- `.planning/REQUIREMENTS.md` — OPS-01, ORCH-05, ORCH-06, ORCH-07, JOBUI-01..05 (JOBUI-02 wording amendment per D-06)
- `.planning/PROJECT.md` — architecture invariant (two mutation paths), Jobs orchestrate / services own domain semantics
- `.planning/STATE.md` — current position

### Prior phase decisions
- `.planning/phases/17-job-framework/17-CONTEXT.md` — lifecycle, cancellation (D-07..D-10), progress (D-11/D-12), log (D-13) semantics
- `.planning/phases/18-orchestration-surface/18-CONTEXT.md` — idempotency (D-06..D-10), cancel API (D-11..D-15), Job reference shape (D-16..D-20)
- `.planning/phases/18-orchestration-surface/deferred-items.md` — pre-existing ruff violations outside scope

### Implementation anchors
- `src/trading_platform/jobs/registry.py` — `build_default_registry` (currently empty), `JobSubmissionSpec`, `InvalidJobPayloadError`
- `src/trading_platform/jobs/contracts.py` — frozen `JobContext` / `JobHandler` (D-03: do not extend)
- `src/trading_platform/jobs/runner.py` — outcome handling: cancelled path persists no `result_summary`; success path sets 100%
- `src/trading_platform/orchestration/job_mutations.py` — fingerprint over normalized `validate_payload` output (reason for D-08)
- `src/trading_platform/services/backtesting.py` — `run_backtest`, `_create_backtest_run`, `resolve_backtest_window`
- `src/trading_platform/db/models/strategy_run.py` — add `job_id` FK (D-01)
- `src/trading_platform/db/models/job.py` — `JobFailureReason` (add `config_invalid`, D-22)
- `src/trading_platform/services/job_reads.py` — Job detail serialization (add `resources[]`, D-04)
- `src/trading_platform/services/operator_reads.py` — run-detail serialization (add `job_id`, D-07)
- `src/trading_platform/api/routes/jobs.py` — mutating routes to guard (ORCH-07); home for `/job-types` or sibling router
- `src/trading_platform/worker/commands/run_jobs.py` — currently `enforce_startup_config(mode=PAPER)` (D-22)
- `src/trading_platform/worker/commands/backtest.py` — existing CLI shows the service call shape
- `src/trading_platform/services/config/validation.py`, `src/trading_platform/services/config/secrets.py` — per-mode semantic validation
- `docker-compose.yml` — worker command currently `serve` (ORCH-05); enable mutations (D-19)
- `render.yaml` — set mutations disabled (ORCH-07, D-19)
- `tests/test_orchestration_boundaries.py` — Phase 18 tripwires to replace (SC9) and "exactly two mutating routes" test
- `console/src/lib/api.ts`, `console/src/lib/useApiQuery.ts` — sole fetch site; hook to extend with polling
- `console/src/app/layout.tsx` — nav
- `console/src/components/runs/detail/RunHeaderPanel.tsx` — back-link (D-07)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `JobRegistry.register(handler, submission_spec=...)`: register `backtest` here; spec provides `validate_payload` and (new) `submission_defaults`.
- `JobOrchestrationService` (`orchestration/job_mutations.py`): submission/cancel/idempotency already complete — Phase 19 adds no idempotency logic.
- `JobReadService`: list (status/job_type filters, capped limit), detail, progress, cursor logs (`after_sequence`), events — all JOBUI reads exist; detail gains `resources[]`.
- `run_backtest(...)`: single opaque service call returning `BacktestRunReport` (has `run_id`, `to_dict()`).
- Console primitives: `useApiQuery`, `FetchMeta`, `ErrorState`, `CappedDisclosure`, run-detail page at `/runs/[runId]`.

### Established Patterns
- Routes delegate to services; no business logic in adapters (enforced by boundary tests).
- Handlers call `services.*` only, never write `jobs` lifecycle rows; no DB transaction open across `handler.run`.
- Closed enums + typed exceptions + DB constraints over convention (user preference: requirements as testable invariants).
- Console: no data-fetching library; single fetch site; honest empty/error states with as-of timestamps.

### Integration Points
- New Alembic migration: `strategy_runs.job_id` (nullable, UNIQUE, FK → jobs.id) + `JobFailureReason.config_invalid` enum value.
- `run_jobs_command`: base validation at boot; per-type mode check before handler dispatch (placement — runner vs registry wrapper — for research/planning, keeping `jobs/` free of domain imports).
- New console routes: `/jobs`, `/jobs/[jobId]`, `/jobs/new`; `/strategy` shortcut button; run header back-link.
- Replace Phase 18 tripwires with a test pinning the exact registered Job-type set `{"backtest"}`.
- D-19 (mutations disabled by default) makes every existing Phase 18 submit/cancel test return 403: test fixtures and the SC1 E2E must enable the flag explicitly, and a dedicated test covers the disabled default.

</code_context>

<specifics>
## Specific Ideas

- User explicitly requires the Job → result relationship to be "explicit, persisted, and available through the Job API" — never inferred from logs or timestamp queries.
- Honesty over polish: indeterminate progress rather than fake percentages; composed outcome labels that state when a cancelled Job's run had already completed.
- Safe-by-default deployment: mutations off unless explicitly enabled.

</specifics>

<deferred>
## Deferred Ideas

- Exposing a Job type's required `ExecutionMode` in the catalog for pre-submit warnings — rejected for now (D-23); revisit if Phase 20 paper types make config failures common.
- Generic `job_resources` link table / `JobContext.record_output` — not needed while every linked resource has a natural FK; revisit if a future Job type produces multiple or non-FK-able outputs.

</deferred>

---

*Phase: 19-job-operations-vertical-slice*
*Context gathered: 2026-09-23*
