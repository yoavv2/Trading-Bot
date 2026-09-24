---
phase: 19-job-operations-vertical-slice
reviewed: 2026-09-24T17:14:29Z
depth: standard
files_reviewed: 79
files_reviewed_list:
  - .env.example
  - alembic/versions/0020_phase19_job_operations.py
  - console/src/app/jobs/[jobId]/page.tsx
  - console/src/app/jobs/new/page.tsx
  - console/src/app/jobs/page.tsx
  - console/src/app/layout.tsx
  - console/src/components/jobs/AutoRefreshIndicator.tsx
  - console/src/components/jobs/CancelJobDialog.test.tsx
  - console/src/components/jobs/CancelJobDialog.tsx
  - console/src/components/jobs/JobFilters.tsx
  - console/src/components/jobs/JobsTable.test.tsx
  - console/src/components/jobs/JobsTable.tsx
  - console/src/components/jobs/detail/JobDetailView.test.tsx
  - console/src/components/jobs/detail/JobDetailView.tsx
  - console/src/components/jobs/detail/JobEventsPanel.test.tsx
  - console/src/components/jobs/detail/JobEventsPanel.tsx
  - console/src/components/jobs/detail/JobHeaderPanel.test.tsx
  - console/src/components/jobs/detail/JobHeaderPanel.tsx
  - console/src/components/jobs/detail/JobLogsPanel.test.tsx
  - console/src/components/jobs/detail/JobLogsPanel.tsx
  - console/src/components/jobs/detail/JobProgressPanel.tsx
  - console/src/components/jobs/detail/JobResourcesPanel.tsx
  - console/src/components/jobs/detail/JobResultSummaryPanel.tsx
  - console/src/components/jobs/new/BacktestJobForm.test.tsx
  - console/src/components/jobs/new/BacktestJobForm.tsx
  - console/src/components/jobs/new/NewJobView.test.tsx
  - console/src/components/jobs/new/NewJobView.tsx
  - console/src/components/jobs/types.ts
  - console/src/components/runs/detail/RunHeaderPanel.test.tsx
  - console/src/components/runs/detail/RunHeaderPanel.tsx
  - console/src/components/strategy/StrategyOverviewPanel.tsx
  - console/src/lib/api.test.ts
  - console/src/lib/api.ts
  - console/src/lib/cancellationLabel.test.ts
  - console/src/lib/cancellationLabel.ts
  - console/src/lib/consoleBoundaries.test.ts
  - console/src/lib/jobStatus.ts
  - console/src/lib/jobTypeForms.ts
  - console/src/lib/resourceRoutes.ts
  - console/src/lib/useApiQuery.test.tsx
  - console/src/lib/useApiQuery.ts
  - console/src/lib/useMutationCapability.ts
  - console/vitest.config.ts
  - docker-compose.yml
  - render.yaml
  - src/trading_platform/api/app.py
  - src/trading_platform/api/dependencies.py
  - src/trading_platform/api/routes/job_types.py
  - src/trading_platform/api/routes/jobs.py
  - src/trading_platform/core/settings.py
  - src/trading_platform/db/models/job.py
  - src/trading_platform/db/models/strategy_run.py
  - src/trading_platform/jobs/handlers/__init__.py
  - src/trading_platform/jobs/handlers/backtest.py
  - src/trading_platform/jobs/handlers/backtest_submission.py
  - src/trading_platform/jobs/registry.py
  - src/trading_platform/jobs/runner.py
  - src/trading_platform/services/backtesting.py
  - src/trading_platform/services/config/validation.py
  - src/trading_platform/services/job_reads.py
  - src/trading_platform/services/operator_reads.py
  - src/trading_platform/worker/commands/run_jobs.py
  - tests/test_backtest_job_link.py
  - tests/test_backtest_job_type.py
  - tests/test_db_migrations.py
  - tests/test_deploy_config.py
  - tests/test_job_catalog.py
  - tests/test_job_mutation_api.py
  - tests/test_job_mutation_e2e.py
  - tests/test_job_mutation_migration.py
  - tests/test_job_operations_e2e.py
  - tests/test_job_orchestration.py
  - tests/test_job_registry.py
  - tests/test_job_resources_read.py
  - tests/test_job_runner_preflight.py
  - tests/test_mutation_guard.py
  - tests/test_orchestration_boundaries.py
  - tests/test_phase19_job_operations_migration.py
  - tests/test_startup_validation.py
findings:
  critical: 0
  warning: 5
  info: 3
  total: 8
status: issues_found
---

# Phase 19: Code Review Report

**Reviewed:** 2026-09-24T17:14:29Z
**Depth:** standard
**Files Reviewed:** 79
**Status:** issues_found

## Summary

This phase's backend (job routes, mutation guard, preflight/config_invalid
path, backtest handler/submission spec, migration 0020, job read service) is
careful and well-tested: the ORCH-07 mutation guard defaults to disabled, is
applied to both mutating routes (`POST /api/v1/jobs`, `POST
/api/v1/jobs/{id}/cancel`) and nothing else, `render.yaml` sets it
explicitly to `false`, and `test_mutation_guard.py` proves guard-before-
schema-validation ordering (including a malformed-body request still
returning 403, not 422) plus a route-walk asserting every mutating route
carries the guard dependency. The preflight/`config_invalid` path (D-22)
correctly fails closed on an exception inside the preflight callable, never
invokes `handler.run`, writes zero `job_logs` rows, and lets the worker loop
continue to the next Job — all covered by `test_job_runner_preflight.py`.
The `backtest` handler's two cancellation checkpoints, `run_backtest`'s
`job_id` linkage inside the creating transaction, and migration 0020's
FK/unique-constraint/enum-add are correct and directly tested.

I initially flagged a Critical finding assuming Next.js does not remount a
dynamic-segment page (`/jobs/[jobId]`) when only the route param changes,
which would let a stale `Job` object leak the wrong `job.id` into the Cancel
control across a same-route navigation. I verified this against this
project's actual Next.js version (16.2.10) rather than relying on general
recollection: `node_modules/next/dist/client/components/router-reducer/
create-router-cache-key.js` includes the dynamic segment's param value in
its cache key, and `layout-router.js` passes that key as the React `key` of
the segment's rendered subtree (`stateKey` at `layout-router.js:604`) — so
navigating `/jobs/A` -> `/jobs/B` *does* remount `JobDetailView` and reset
its state. That specific scenario is not reachable in this app. The same
root cause (`useApiQuery` never clears `result` when `endpoint` changes) is
still reachable and still a real bug, but only within a single mounted
instance whose `endpoint` changes via local state rather than route
navigation — which happens in `JobsTable` when a filter changes. Downgraded
to Warning accordingly (see WR-06).

## Warnings

### WR-01: `JobResultSummaryPanel`'s "no result summary" copy is unreachable in production — every non-SUCCEEDED Job shows a blank panel

**File:** `console/src/components/jobs/detail/JobResultSummaryPanel.tsx:41-42`

**Issue:** The panel only renders `emptyCopy(...)` when `resultSummary === null`:

```tsx
{resultSummary === null ? (
  <p className="text-zinc-500">{emptyCopy(status, resourceCount)}</p>
) : (
  <dl ...>{Object.entries(resultSummary).map(...)}</dl>
)}
```

But `Job.result_summary` is declared `nullable=False, default=dict`
(`src/trading_platform/db/models/job.py:127`) and is only ever overwritten
on a SUCCEEDED transition (`src/trading_platform/jobs/lifecycle.py:224-225`:
`if request.result_summary is not None: job.result_summary = dict(...)`).
`JobReadService.get_job_detail` passes it through unchanged
(`src/trading_platform/services/job_reads.py:148`). So a QUEUED, RUNNING,
FAILED, or CANCELLED Job's `result_summary` is always `{}`, never `null` —
the shape this component actually receives from the real API. The
`resultSummary === null` check is therefore false, and the code falls into
the `<dl>` branch with zero entries: an empty, unlabeled box instead of the
carefully-worded D-06/D-14 empty-state copy (`emptyCopy` even branches on
`status === "cancelled"` vs `"failed"` vs non-terminal — none of that copy
ever displays in production). `JobDetailView.test.tsx`'s "cancelled-before-
start" case only exercises this via a hand-constructed
`jobDetail({ result_summary: null, ... })` override, which does not reflect
the real backend contract, so the bug is invisible to the suite.

**Fix:**
```tsx
const isEmpty = resultSummary === null || Object.keys(resultSummary).length === 0;
{isEmpty ? (
  <p className="text-zinc-500">{emptyCopy(status, resourceCount)}</p>
) : (
  <dl ...>{Object.entries(resultSummary!).map(...)}</dl>
)}
```

### WR-02: `JobEventsPanel` can permanently miss the terminal lifecycle event, and has no manual-refresh path to recover

**File:** `console/src/components/jobs/detail/JobEventsPanel.tsx:21-26`

**Issue:** Unlike `JobLogsPanel` (which explicitly watches the
non-terminal -> terminal edge and fires an immediate final fetch — see
`JobLogsPanel.tsx:207-217`, and is directly tested by
`JobLogsPanel.test.tsx`'s "defers a terminal transition ... then fetches
once more and stops"), `JobEventsPanel` relies entirely on `useApiQuery`'s
generic interval chain:

```tsx
const { result } = useApiQuery<JobEventsPage>(endpoint, {
  pollIntervalMs: 3000,
  shouldPoll: () => !jobIsTerminal,
});
```

Trace the race: tick N's `fetchApi` call is sent while `jobIsTerminal` is
still `false`. Before it resolves, `JobDetailView`'s own poll observes the
Job going terminal and re-renders, so `JobEventsPanel`'s `optionsRef`
(updated unconditionally every render) now holds `jobIsTerminal = true`.
Tick N's response — issued *before* the terminal write, so it does not yet
contain the terminal event — then resolves; its `.then()` calls
`scheduleNextTick()`, which reads the now-updated `shouldPoll()` as `false`
and stops the chain permanently. No further tick is ever scheduled. Because
`JobEventsPanel` renders no `FetchMeta`/manual-refresh control at all
(compare `JobsTable`/`JobDetailView`/`StrategyOverviewPanel`, which all wire
up `onRefresh`), there is no way to recover the missing terminal event short
of a full page reload. `JobEventsPanel.test.tsx` has no test for this
transition race at all (only a static `jobIsTerminal={true}` case and a
static `jobIsTerminal={false}` case), so this gap is untested.

**Fix:** Track the previous `jobIsTerminal` value in a ref and trigger one
extra fetch immediately on the false->true edge (mirroring `JobLogsPanel`'s
pattern), and/or add a manual refresh control to this panel as a safety
net.

### WR-03: `BacktestJobForm` gives no error feedback when `GET /api/v1/strategies` fails

**File:** `console/src/components/jobs/new/BacktestJobForm.tsx:52-76`

**Issue:**
```tsx
const { result: strategiesResult } = useApiQuery<StrategiesResponse>("/api/v1/strategies");
...
const strategies = strategiesResult?.ok ? strategiesResult.data.strategies : [];
```
A failed fetch (`strategiesResult.ok === false`) is silently collapsed to an
empty array — the `<select>` renders with only the placeholder option and no
indication anything is wrong. Every other fetch-consuming component in this
phase (`JobsTable`, `JobDetailView`, `JobEventsPanel`, `JobLogsPanel`,
`NewJobView`, `StrategyOverviewPanel`) renders `ErrorState` or an equivalent
explicit message on a failed `ApiResult`; this is the only place in the Job
UI that swallows a fetch failure without any visible signal, inconsistent
with this codebase's otherwise-consistent "honest unknown" (D-21)
discipline.

**Fix:** Render an `ErrorState`/inline message when `strategiesResult` is
non-null and `!strategiesResult.ok`, matching the pattern used elsewhere in
this phase.

### WR-04: Migration 0020's downgrade leaves a read-crashing trap for a coordinated schema+code rollback

**File:** `alembic/versions/0020_phase19_job_operations.py:40-51`

**Issue:** `downgrade()` correctly drops the new FK/unique constraint/
column, but (as its own comment documents) cannot remove the
`'config_invalid'` value it added to the `job_failure_reason` Postgres enum,
since Postgres cannot drop a single enum value without rewriting every
dependent column. The comment frames this only as "`ADD VALUE IF NOT
EXISTS` keeps re-upgrading idempotent" and does not mention the actual
operational hazard: if a deployment is ever rolled back past this migration
*and* the application code is also rolled back to a pre-Phase-19 version
(whose `JobFailureReason` `StrEnum`/SQLAlchemy
`Enum(..., validate_strings=True)` does not declare `CONFIG_INVALID`), any
`SELECT` that touches a pre-existing `failure_reason = 'config_invalid'` row
(e.g. any Job list/detail read, `JobReadService.list_jobs`) will raise at
the ORM layer — a broken read path, not just a display/staleness glitch.

**Fix:** Expand the downgrade docstring to name this specific failure mode
(SELECT crash, not just semantic staleness) so a future coordinated
schema+code rollback knows to check for/backfill `config_invalid` rows
before downgrading application code below this migration.

### WR-05: `JobsTable` shows stale, unfiltered/old-filter rows while a filter change is in flight

**File:** `console/src/lib/useApiQuery.ts:141-159`, consumed by
`console/src/components/jobs/JobsTable.tsx:41-50`

**Issue:** `runFetch` sets `loading` to `true` on every call (mount,
`endpoint` change, and manual `refetch()`) but never clears `result`:

```ts
const runFetch = useCallback(() => {
  ...
  setLoading(true);                 // <-- loading flips
  fetchApi<T>(endpoint).then((next) => {
    ...
    setResult(next);                // <-- but the previous `result` isn't cleared first
    ...
  });
}, [endpoint, clearTimer, scheduleNextTick]);
```

This is intentional and correct for a same-endpoint manual refresh (keep
showing the last good data while refreshing, matching the background-tick
contract documented at the top of the file). It is not correct when
`endpoint` itself changes: `JobsTable` rebuilds `endpoint` from `filters`
state (`buildJobsEndpoint(filters.status, filters.jobType)`), and the
mount/endpoint-change effect (`useEffect(..., [runFetch, clearTimer])`) does
re-run `runFetch` when `endpoint`'s identity changes, but that effect does
not reset `result` either. So changing the Status or Job type filter leaves
the previous (now-mismatched) rows on screen — with `FetchMeta`'s
`loading` spinner active — until the new, filtered response lands. This is
not just a rendering flicker: a table whose column-filter selects "failed"
can transiently keep showing "running"/"succeeded" rows from the old query.
No test in `JobsTable.test.tsx` observes the interim render — the filter
test only asserts on the final outgoing request URL, not on what is
rendered while it is in flight.

**Note:** the analogous concern for `JobDetailView`/`app/jobs/[jobId]/
page.tsx` (stale `Job` object surviving a same-route jobId navigation,
including a mistargeted `CancelJobDialog`) does *not* apply: this Next.js
version (16.2.10) keys the dynamic-segment subtree by the param value
(`create-router-cache-key.js` folds the segment's param into the cache key;
`layout-router.js` passes that key as the subtree's React `key`), so
navigating between two Job detail URLs remounts `JobDetailView` and resets
all of `useApiQuery`'s state. Verified directly against
`node_modules/next/dist/client/components/{layout-router.js,router-reducer/
create-router-cache-key.js}` rather than assumed.

**Fix:** Reset `result` (and `hasSuccessRef`/`lastSuccessDataRef`) whenever
`endpoint` itself changes (as opposed to a same-endpoint manual/background
refetch) — e.g. track the previous `endpoint` in a ref and `setResult(null)`
when it differs, inside the effect keyed on `[runFetch, clearTimer]`.

## Info

### IN-01: `cancellationLabel.ts`'s "never executed" label is coupled to today's one Job type's side-effect ordering

**File:** `console/src/lib/cancellationLabel.ts:60-63`

**Issue:** `status === "cancelled" && resources.length === 0` renders
`"Cancelled before start — never executed"`. This is accurate today because
`BacktestJobHandler.run`'s linked `StrategyRun` (the sole `resources[]`
entry) is created as the very first action inside `run_backtest`, so
`resources.length === 0` really does mean "never executed." The module's
own docstring states this function is designed to describe "any future
operation type unchanged" — but a future Job type whose first step performs
a real side effect (e.g. a broker call) before creating any linked resource
would be mislabeled "never executed" by a Job that, in fact, started and
possibly caused external effects. Not a bug in the current phase; flagging
as a latent precision gap given how explicitly this file's docstring claims
generality.

**Fix:** None required for Phase 19. When Phase 20 adds a Job type whose
first side effect precedes its first linked resource, revisit this label
against `outcome_uncertain`/`started_at` rather than `resources.length`
alone.

### IN-02: `JobLogsPanel`'s stale-generation guard can clear `inFlightRef` while a live generation's fetch is genuinely in flight (latent — not reachable via normal navigation)

**File:** `console/src/components/jobs/detail/JobLogsPanel.tsx:91-113`

**Issue:** `runTick` captures `gen = genRef.current` at entry and, on
detecting a mismatch after an `await`, does:

```ts
if (!mountedRef.current || gen !== genRef.current) {
  inFlightRef.current = false;   // <-- shared across generations, not scoped to `gen`
  return;
}
```

If `jobId` changed while a previous generation's `fetchApi` call was still
in flight, that stale tick's eventual resolution clears the *shared*
`inFlightRef` even though the new generation's own tick (which set
`inFlightRef.current = true` at its own start) may still be genuinely in
flight. A subsequent `visibilitychange` event during that window would then
incorrectly start a second, truly overlapping request for the live
generation, defeating the single-flight guard this file otherwise takes
care to implement. As documented in WR-05, this specific file's `jobId`
prop cannot actually change on a live, mounted instance via normal
navigation in this app (the whole page remounts first), so this is latent/
defensive-code-only under the current call sites — but `JobLogsPanel.test.
tsx` already has a dedicated test ("discards a stale in-flight response
after jobId changes...") demonstrating the component's authors intended
`jobId` to be safely changeable on a live instance, so the guard bug is
real relative to that stated contract even though no current caller
triggers it.

**Fix:** Make the in-flight tracking generation-scoped (store the in-flight
generation number instead of a bare boolean, and only clear it when
`gen === genRef.current`), or simply don't touch `inFlightRef` on the
stale-generation early-return path at all.

### IN-03: Two-microtask `flush()` used for multi-fetch resolution instead of `findBy*`/`waitFor`

**File:** `console/src/components/jobs/new/NewJobView.test.tsx:88-93` (used
by the D-21 catalog-failure test at 145-159 and the `StrategyOverviewPanel`
tests at 162-187)

**Issue:** These tests resolve two independent `useApiQuery` fetches
(`/api/v1/job-types` and `/api/v1/strategies/trend_following_daily`) with a
fixed `await act(async () => { await Promise.resolve(); await Promise.resolve(); })`
instead of `findByText`/`waitFor`. Unlike the vacuous-pass pattern fixed in
`df60600` (`JobHeaderPanel.test.tsx`), these tests do assert on
resolved-state *text* (e.g. `"Mutations disabled on this deployment"`), so a
timing shortfall would make them fail (flake), not silently pass — but a
fixed two-tick flush racing two independently-resolving `fetch()` promises
is inherently more timing-fragile than an explicit wait, and would flake
first if either hook gained an extra microtask hop in the future.

**Fix:** Prefer `findByText`/`waitFor` (as `JobHeaderPanel.test.tsx` was
fixed to do in `df60600`) over a fixed microtask count.

---

_Reviewed: 2026-09-24T17:14:29Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
