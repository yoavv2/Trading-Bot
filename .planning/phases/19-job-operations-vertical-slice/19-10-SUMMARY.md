---
phase: 19-job-operations-vertical-slice
plan: 10
subsystem: ui
tags: [nextjs, react, typescript, vitest, polling, job-orchestration, xss-safety]

# Dependency graph
requires:
  - phase: 19-job-operations-vertical-slice/19-06
    provides: backtest Job type registration (job_type/status vocab exercised by the panels)
  - phase: 19-job-operations-vertical-slice/19-08
    provides: console Job primitives (types.ts JobLogsPage/JobEventsPage/JobReference, fetchApi/cancelJob/mutationErrorMessage, useApiQuery polling)
provides:
  - console/src/components/jobs/detail/JobLogsPanel.tsx — cursor-based (after_sequence) structured log tail, bounded has_more drain, 3s poll while non-terminal, terminal-drain continuation past the per-tick page cap, stale-response discard on jobId change
  - console/src/components/jobs/detail/JobEventsPanel.tsx — job-type-agnostic lifecycle events list over useApiQuery polling
  - console/src/components/jobs/CancelJobDialog.tsx — accessible React-state confirmation overlay for POST /api/v1/jobs/{id}/cancel (D-15), one Idempotency-Key per opening reused across transport retries
affects: [19-12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hand-rolled cursor-tail polling (JobLogsPanel): a setTimeout-chained tick loop with its own bounded has_more drain, a generation counter (genRef) to discard stale in-flight responses across prop-driven resets, and a deferred-flag (finalFetchPendingRef) to avoid overlapping requests on a state transition mid-flight -- the same non-overlapping-tick discipline as useApiQuery, reimplemented by hand because this panel's pagination (cursor + bounded drain loop) doesn't fit useApiQuery's single-fetch-per-tick shape"
    - "React-state modal overlay (role=dialog, aria-modal=true) instead of the native <dialog> element, since jsdom's HTMLDialogElement has no showModal/close behavior -- first modal/dialog pattern in the console"
    - "Idempotency-Key generated once per dialog opening (open false->true transition) and reused across confirm attempts within that opening, so a network-failure retry doesn't risk double-executing the mutation"

key-files:
  created:
    - console/src/components/jobs/detail/JobLogsPanel.tsx
    - console/src/components/jobs/detail/JobLogsPanel.test.tsx
    - console/src/components/jobs/detail/JobEventsPanel.tsx
    - console/src/components/jobs/detail/JobEventsPanel.test.tsx
    - console/src/components/jobs/CancelJobDialog.tsx
    - console/src/components/jobs/CancelJobDialog.test.tsx

key-decisions:
  - "JobLogsPanel's dedupe sentinel is -1, not 0, even though the backend numbers JobLog.sequence from 1 (verified in src/trading_platform/jobs/context.py: next_sequence = (max_sequence or 0) + 1) -- defensive only, costs nothing, removes an assumption about backend numbering from the frontend"
  - "A terminal Job whose log volume exceeds the 20-page/tick cap (T-19-10-04 bound) triggers a same-virtual-time setTimeout(0) continuation rather than stopping: because a terminal Job never gets a next scheduled poll tick, stopping at the cap would silently and permanently truncate that Job's log tail with no operator-visible indication -- inconsistent with the codebase's CappedDisclosure honesty norm (runs/detail). The continuation is still bounded per invocation, so a runaway/buggy has_more can only ever fetch in finite (if repeated) chunks, never a single unbounded loop"
  - "JobLogsPanel tracks a generation counter (genRef, bumped every mount/jobId-change) so a slow in-flight page request from a previous jobId is discarded rather than appended to (or moving the cursor of) a different Job's panel -- mirrors useApiQuery's requestIdRef guard from Plan 08"
  - "A non-terminal->terminal jobIsTerminal transition that arrives while a tick is already in flight defers to a finalFetchPendingRef flag instead of starting a second, overlapping fetch; the in-flight tick consumes the flag with one fresh fetch once it resolves -- same overlap-safety concern Plan 08's advisor review caught in useApiQuery, applied here to the hand-rolled tail"
  - "JobEventsPanel is a thin useApiQuery consumer (no hand-rolled polling) since its endpoint returns a single page with no cursor -- only JobLogsPanel needed the hand-rolled tick loop"
  - "CancelJobDialog keeps the operator's typed reason text and the same Idempotency-Key after a failed attempt (per D-15/plan spec: one key per opening, reused on retry) -- a known, non-blocking edge case documented below, not fixed in this plan"

patterns-established:
  - "React-state dialog overlay (role=dialog/aria-modal=true, no native <dialog>) as the console's confirmation-dialog pattern for any future destructive-action UI"

requirements-completed: []  # JOBUI-03/JOBUI-04 stay Pending: this plan ships three self-contained, unit-tested building blocks with no route mounting any of them yet (per the plan's own frontmatter note: "Plan 12 composes them into the detail page"). Neither requirement's literal text ("Operator can view...", "Operator can cancel... from the console") is satisfiable until Plan 12 wires these components into an actual operator-visible Job detail screen -- same precedent as 19-08 (console primitives, no screen) not overclaiming a plan's frontmatter requirements list.

# Metrics
duration: 21min
completed: 2026-09-24
---

# Phase 19 Plan 10: Job Detail Building Blocks — Logs, Events, Cancel Summary

**Cursor-based JobLogsPanel log tail (bounded has_more drain, 3s non-terminal poll, terminal-drain continuation past the per-tick cap, stale-response/overlap-safety guards), a generic JobEventsPanel over useApiQuery polling, and an accessible React-state CancelJobDialog (D-15) with a stable per-opening Idempotency-Key — three self-contained, job-type-agnostic components for Plan 12 to compose onto the Job detail page.**

## Performance

- **Duration:** 21 min
- **Started:** 2026-09-24T12:49:17Z
- **Completed:** 2026-09-24T13:10:00Z
- **Tasks:** 2 (+ 1 post-review fix)
- **Files modified:** 6

## Accomplishments
- `JobLogsPanel.tsx` fetches `/api/v1/jobs/{id}/logs?limit=100`, drains subsequent `after_sequence` pages while `has_more` is true (bounded to 20 pages/tick, T-19-10-04), appends only lines whose `sequence` exceeds the highest seen, and polls every 3s only while non-terminal
- A terminal Job whose logs exceed the per-tick page cap is fully drained via a same-virtual-time continuation rather than silently truncated — verified with a 25-page fixture (20 in the first tick, 5 in the continuation, 25 total requests, no further requests after)
- A slow in-flight page response from a previous `jobId` is discarded via a generation counter rather than appended to a different Job's panel — verified with a deferred-promise fixture
- A `jobIsTerminal` transition mid-tick defers to a pending-flag instead of starting a second, overlapping request — verified with a deferred-promise fixture
- Exact UI-SPEC empty-state copy (non-terminal vs terminal) and untrusted log message/context text rendered as plain React text only — verified with an `<img onerror>` payload rendering as literal text with zero `<img>` elements in the DOM
- `JobEventsPanel.tsx` lists `event_type`, `from_status → to_status`, `outcome`, `event_at`, and `requested_by`/`reason`/`terminal_cause` when present, defensively rendering "No events recorded yet." for an empty list, polling every 3s only while non-terminal via `useApiQuery`
- `CancelJobDialog.tsx` — React-state overlay (`role="dialog"`, `aria-modal="true"`, no native `<dialog>`) with exact D-15 heading/body/button copy, `Reason (optional)` field (`maxLength={500}`), one `Idempotency-Key` per opening reused across a transport-failure retry (a fresh key after reopening), and honest `mutationErrorMessage` copy on failure (dialog stays open, e.g. 409 `job_not_cancellable`)
- `consoleBoundaries.test.ts`'s D-17 checks (no `"backtest"` literal, no `job_type` equality) now also cover these three new `components/jobs/` files, non-vacuously, and stay green

## Task Commits

Each task was committed atomically:

1. **Task 1: JobLogsPanel (cursor tail) and JobEventsPanel with tests** - `01d7249` (feat)
2. **Task 2: CancelJobDialog with tests** - `e38668a` (feat)

**Post-review fix:** `9a33d04` (fix) — stale-response guard, terminal drain-past-cap, overlap-safe terminal transition, found via advisor review after Task 2

**Plan metadata:** commit pending (this SUMMARY + STATE/ROADMAP/REQUIREMENTS tracking commit)

## Files Created/Modified
- `console/src/components/jobs/detail/JobLogsPanel.tsx` - cursor-based log tail with bounded drain, stale-response/overlap guards
- `console/src/components/jobs/detail/JobLogsPanel.test.tsx` - 9 component tests
- `console/src/components/jobs/detail/JobEventsPanel.tsx` - generic lifecycle events list
- `console/src/components/jobs/detail/JobEventsPanel.test.tsx` - 3 component tests
- `console/src/components/jobs/CancelJobDialog.tsx` - accessible confirmation overlay for cancel mutation
- `console/src/components/jobs/CancelJobDialog.test.tsx` - 8 component tests

## Decisions Made
See `key-decisions` in frontmatter above (dedupe sentinel, terminal drain-past-cap continuation, generation-counter stale-response guard, deferred-flag overlap safety, JobEventsPanel's thin useApiQuery-only design, and the per-opening Idempotency-Key reuse-on-retry behavior).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] JobLogsPanel truncated a terminal Job's log tail past the 20-page/tick cap with no operator-visible indication**
- **Found during:** advisor review after Task 2, before declaring the plan complete
- **Issue:** `runTick`'s while loop is bounded to `MAX_PAGES_PER_TICK` (20) per the plan's T-19-10-04 mitigation. For a non-terminal Job this is harmless — the next scheduled 3s tick picks up the remainder. But a terminal Job never gets a next scheduled tick (`scheduleNextTick` returns early when `jobIsTerminalRef.current` is true), so any terminal Job whose log volume exceeds 2,000 lines (20 pages × 100/page) would silently show only its first 2,000 lines forever, with no error, no truncation notice, and no way to see the rest — inconsistent with the codebase's `CappedDisclosure` honesty norm (`runs/detail/CappedDisclosure.tsx`) for exactly this class of "response was capped" situation.
- **Fix:** When the loop exits with `hasMore` still true and the Job is terminal, schedule a same-virtual-time (`setTimeout(..., 0)`) continuation that runs another bounded drain pass, repeating until `has_more` is actually false. Each continuation is still capped at 20 pages, so a pathological/buggy `has_more` can only ever fetch in finite (if repeated) chunks, never a single unbounded loop — the DoS mitigation is preserved.
- **Files modified:** `console/src/components/jobs/detail/JobLogsPanel.tsx`
- **Verification:** New regression test — a 25-page fixture (`has_more: true` for pages 1-24, `false` for 25) against a terminal Job: exactly 25 requests total, all 25 lines rendered, and zero further requests after advancing 10,000ms.
- **Committed in:** `9a33d04` (separate `fix(19-10)` commit, after the two task commits)

**2. [Rule 1 - Bug] JobLogsPanel could append a stale in-flight response from a previous `jobId` to the new Job's panel**
- **Found during:** advisor review after Task 2
- **Issue:** The mount effect resets local state (`cursorRef`, `lastSequenceRef`, `lines`) whenever `jobId` changes and immediately starts a new fetch, but nothing prevented an already-in-flight request for the *previous* `jobId` from resolving afterward and appending its (now-wrong-Job) data via the shared `setLines`/`cursorRef` mutations. `useApiQuery` (Plan 08) already solved this exact class of bug with a `requestIdRef` guard; this hand-rolled panel had not been given the equivalent.
- **Fix:** Added `genRef`, incremented every time the mount/jobId-change effect (re-)initializes state. `runTick` captures the generation at start and bails immediately after every `await` if it no longer matches the current generation — discarding the stale response instead of applying it.
- **Files modified:** `console/src/components/jobs/detail/JobLogsPanel.tsx`
- **Verification:** New regression test — a deferred (never-resolving-until-manually-triggered) first fetch, a `jobId` change while it's still pending, the new Job's own (immediately-resolved) fetch rendering correctly, then manually resolving the stale first fetch and asserting its data never appears.
- **Committed in:** `9a33d04`

**3. [Rule 1 - Bug] A terminal transition mid-tick could start a second, overlapping fetch**
- **Found during:** advisor review after Task 2
- **Issue:** The non-terminal→terminal transition effect unconditionally called `tickRef.current()` for the "one final fetch" behavior, without checking whether a tick was already in flight (e.g. a background poll mid-request at the exact moment `jobIsTerminal` flips true — precisely the scenario Plan 12's detail-page polling will produce). Plan 08's advisor review found and fixed the identical class of bug in `useApiQuery`; this hand-rolled loop had not inherited that guard.
- **Fix:** Added `finalFetchPendingRef`. The transition effect only starts an immediate tick if nothing is in flight; otherwise it sets the pending flag, which the in-flight tick consumes (running one fresh fetch) once it finishes, instead of two concurrent loops mutating the shared cursor/lines state.
- **Files modified:** `console/src/components/jobs/detail/JobLogsPanel.tsx`
- **Verification:** New regression test — a deferred first tick, a terminal transition while it's pending (asserts no second request yet), then resolving the first tick and asserting exactly one more (deferred) request fires, with none after advancing 10,000ms.
- **Committed in:** `9a33d04`

**4. [Rule 1 - Bug, defensive] Dedupe sentinel changed from 0 to -1**
- **Found during:** advisor review after Task 2
- **Issue:** The append-dedupe filter (`item.sequence > lastSequenceRef.current`) started `lastSequenceRef` at 0. Verified against `src/trading_platform/jobs/context.py` (`next_sequence = (max_sequence or 0) + 1`) that `JobLog.sequence` is always ≥ 1 in the current backend, so this was not an active bug against real data — but it silently assumed 1-based numbering with no enforcement.
- **Fix:** Changed the sentinel to -1, which is correct regardless of whether sequence numbering is ever 0-based in the future. Zero behavior change against the actual (1-based) backend.
- **Files modified:** `console/src/components/jobs/detail/JobLogsPanel.tsx`
- **Verification:** Existing test suite unaffected (all sequences used in tests are ≥ 1); no new test needed since the current backend contract makes sequence 0 unreachable.
- **Committed in:** `9a33d04`

---

**Total deviations:** 4 auto-fixed (4 bugs, 3 with new regression coverage)
**Impact on plan:** No scope creep — all four are correctness fixes to code shipped in this same plan (Task 1), caught before the plan was declared complete. All originally-specified test cases pass unchanged; three new regression cases added, one existing test strengthened (strict per-row ordering assertion).

## Issues Encountered

**Known, non-blocking limitation (not fixed — inherent to the plan's own spec):** `CancelJobDialog` reuses the same `Idempotency-Key` for every confirm attempt within one dialog opening (per D-15/the plan's explicit requirement, so a network-failure retry cannot double-execute the cancel). If an operator edits the reason text between a failed attempt and a retry, and the *first* attempt actually reached the server before failing client-side (e.g. a dropped response after a successful write), the retry — same key, different body — will be rejected with `idempotency_key_conflict`, whose copy ("Idempotency key reused with a different payload — this is a console bug, not an operator error") incorrectly blames the console. This is a pre-existing tension in the `mutationErrorMessage`/idempotency design from Plan 08, not something this plan's scope can resolve without changing that copy or the key-reuse contract; documented here for Plan 12/future consideration, not fixed.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Plan 12 (Job detail page) can compose `JobLogsPanel`, `JobEventsPanel`, and `CancelJobDialog` directly — all three take exactly the props specified in this plan's interfaces block (`{jobId, jobIsTerminal}` / the `CancelJobDialog` open/onClose/onCancelled contract) with no further wiring needed.
- `JobLogsPanel`'s generation-counter and deferred-transition guards mean Plan 12 can safely re-render the panel with a changing `jobIsTerminal` prop (driven by the detail page's own poll) without needing to key the panel by `jobId` or worry about overlapping/stale requests.
- No blockers. Baseline before this plan: 8 files / 75 tests. After this plan: 11 files / 95 tests, all green (`npm test`); `npx tsc --noEmit` clean; `npm run lint` clean.

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*

## Self-Check: PASSED

- All 6 created/modified files under `console/src/` and this SUMMARY.md confirmed present on disk.
- All 3 commits (`01d7249`, `e38668a`, `9a33d04`) confirmed present in `git log --oneline --all`.
- `npm test` (console/): 11 files / 95 tests passed.
- `npx tsc --noEmit` (console/): clean.
- `npm run lint` (console/): clean.
