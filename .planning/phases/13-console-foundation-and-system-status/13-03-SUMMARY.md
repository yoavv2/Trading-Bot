---
phase: 13-console-foundation-and-system-status
plan: 03
subsystem: ui
tags: [nextjs, react, typescript, vitest, fetch]

# Dependency graph
requires:
  - phase: 13-01
    provides: "GET /api/v1/system/kill-switch HTTP route"
  - phase: 13-02
    provides: "Next.js app shell at console/ with /backend proxy and layout mount placeholder"
provides:
  - "Typed fetchApi<T>() client returning a discriminated ApiResult<T> (success/HTTP-error/network-error), vitest-covered"
  - "useApiQuery<T>() client hook: loading, result, asOf, refetch()"
  - "Shared ErrorState and FetchMeta components — the only approved way screens render failures and freshness/refresh controls"
  - "KillSwitchBanner mounted globally in root layout with honest tri-state rendering (tripped/red, fetch-failed/amber unknown, armed/hidden), refetching on route change"
affects: [13-04-system-status-screen, 14-runs-and-strategies, 15-orders-and-fills, 16-analytics-and-charting]

# Tech tracking
tech-stack:
  added: [vitest@4.1.10]
  patterns:
    - "All console data fetching goes through fetchApi()/useApiQuery() — no component calls fetch() directly (CONS-02/CONS-03 shared instrument, enforced by grep check in plan verification)."
    - "ApiFailure always carries endpoint + status (null = unreachable) + message + asOf; ErrorState is the only approved renderer for it."
    - "Safety indicators (kill-switch banner) render an explicit 'unknown' state on fetch failure rather than going silent or assuming armed."

key-files:
  created:
    - console/src/lib/api.ts
    - console/src/lib/api.test.ts
    - console/src/lib/useApiQuery.ts
    - console/src/components/ErrorState.tsx
    - console/src/components/FetchMeta.tsx
    - console/src/components/KillSwitchBanner.tsx
    - console/vitest.config.ts
  modified:
    - console/src/app/layout.tsx
    - console/package.json

key-decisions:
  - "fetchApi never throws: network failures, HTTP errors, and non-JSON error bodies all resolve to a typed ApiFailure rather than a rejected promise, so callers never need try/catch."
  - "useApiQuery's mount/endpoint-change effect calls a helper that synchronously sets loading=true before awaiting fetchApi; this trips eslint-plugin-react-hooks' experimental set-state-in-effect rule (tuned for external-store sync patterns), so a single narrowly-scoped, commented eslint-disable was added rather than restructuring the hook with request-id/version state indirection, which would contradict the plan's explicit 'deliberate minimal instrument, no data-fetching library' scope."

patterns-established:
  - "Pattern: shared-fetch-instrument — every future console screen (14/15/16) fetches via useApiQuery + renders failures via ErrorState + renders freshness via FetchMeta, never a bespoke fetch/error path."

requirements-completed: [CONS-02, CONS-03, KILL-01]

# Metrics
duration: 16min
completed: 2026-07-08
---

# Phase 13 Plan 03: Shared Fetch Client, Error/Freshness Components & Kill-Switch Banner Summary

**Typed `fetchApi`/`useApiQuery` client (vitest-covered, never throws) plus shared `ErrorState`/`FetchMeta` components and a globally-mounted `KillSwitchBanner` with an honest tripped/unknown/hidden tri-state, refetching on every route change.**

## Performance

- **Duration:** 16 min
- **Started:** 2026-07-08T09:22:49Z
- **Completed:** 2026-07-08T09:39:11Z
- **Tasks:** 2
- **Files modified/created:** 9

## Accomplishments
- `fetchApi<T>(endpoint)` prefixes `/backend`, classifies every outcome (success, HTTP error with JSON body, HTTP error with non-JSON body, network/proxy failure) into a discriminated `ApiResult<T>`, and never throws — verified by 4 vitest tests (TDD RED then GREEN)
- `useApiQuery<T>(endpoint)` client hook: fetches on mount/endpoint change, exposes `{loading, result, refetch}`, guards against post-unmount and stale (superseded) request state updates
- `ErrorState` renders endpoint + status (`HTTP 503` or `unreachable`) + message verbatim — the only approved failure render per CONS-02
- `FetchMeta` renders `as of HH:MM:SS` + a Refresh button that disables/labels itself while a fetch is in flight — the CONS-03 manual-refresh primitive
- `KillSwitchBanner` consumes `/api/v1/system/kill-switch` via `useApiQuery`, refetches whenever `usePathname()` changes, and renders three honest states: red "TRIPPED" banner with last-changed metadata, amber "state UNKNOWN" banner naming the failed endpoint/status when the fetch itself fails, or nothing when armed
- `<KillSwitchBanner />` mounted in `console/src/app/layout.tsx` above `{children}`, replacing the plan-13-02 placeholder comment, so every current and future screen inherits it (KILL-01)
- `grep -rnE "(^|[^a-zA-Z])fetch\(" src/app src/components | grep -v fetchApi` returns nothing — confirmed no component bypasses the shared client

## Task Commits

1. **Task 1 (RED): Add failing fetchApi classification tests** - `07db80f` (test)
2. **Task 1 (GREEN): Implement fetchApi client and useApiQuery hook** - `d2c9a8d` (feat)
3. **Task 2: Shared ErrorState/FetchMeta components and global KillSwitchBanner** - `a6b8fab` (feat)

**Plan metadata:** (this commit, docs)

_No refactor step was needed for Task 1 — the GREEN implementation was already clean._

## Files Created/Modified
- `console/src/lib/api.ts` - `fetchApi<T>()` typed client; `ApiSuccess`/`ApiFailure`/`ApiResult` types
- `console/src/lib/api.test.ts` - 4 vitest classification tests (success, HTTP error + JSON body, HTTP error + non-JSON body, network failure)
- `console/src/lib/useApiQuery.ts` - `useApiQuery<T>()` hook: loading/result/refetch, unmount- and stale-request-safe
- `console/src/components/ErrorState.tsx` - shared failure renderer (endpoint + status + message)
- `console/src/components/FetchMeta.tsx` - shared as-of timestamp + Refresh control
- `console/src/components/KillSwitchBanner.tsx` - global tri-state kill-switch banner, refetches on route change
- `console/src/app/layout.tsx` - mounted `<KillSwitchBanner />`, replacing the 13-02 placeholder comment
- `console/vitest.config.ts` - node-environment vitest config, `src/**/*.test.ts` include
- `console/package.json` - added `vitest` devDependency and `test` script

## Decisions Made
- Kept `fetchApi` exception-free by design (network errors caught and converted to `ApiFailure`, JSON parse failures caught and treated as a non-JSON body rather than thrown) so every call site can rely on the discriminated union alone.
- Preserved the un-prefixed API path (e.g. `/api/v1/system`) as the `endpoint` field in results — the `/backend` prefix is an implementation detail of the Next.js proxy, not something the operator should see.
- Added a single, narrowly-scoped, commented `eslint-disable-next-line react-hooks/set-state-in-effect` in `useApiQuery`'s mount effect rather than restructuring the hook to satisfy an experimental compiler-lint rule whose recommended alternatives (external-store subscription, or request-id/version state indirection) would add exactly the caching/state-machine complexity the plan explicitly says to avoid ("no polling, no caching layer, no SWR/TanStack dependency ... deliberate minimal instrument").

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Lint/code-quality] eslint-plugin-react-hooks flagged synchronous setState in useApiQuery's mount effect**
- **Found during:** Task 2 (ran `npm run lint` as an extra safety check beyond the plan's stated `<verify>` commands, which only require `npx vitest run` and `npm run build` — neither of which runs ESLint)
- **Issue:** `eslint-config-next`'s `core-web-vitals` preset (present in this Next.js scaffold since plan 13-02, not something this plan configured) includes an experimental `react-hooks/set-state-in-effect` rule that flags the mount effect's direct call to the fetch-triggering `runFetch()` helper (which synchronously sets `loading=true` before the async fetch settles). This is a standard, idiomatic effect-driven-fetch pattern; the rule's own suggested alternatives (external-store subscription, or version/request-id state indirection) would add meaningful complexity contrary to the plan's explicit "deliberate minimal instrument, no data-fetching library" scope.
- **Fix:** Added one narrowly-scoped, commented `eslint-disable-next-line react-hooks/set-state-in-effect` at the single flagged call site, explaining the tradeoff. Loading always resolves correctly via the guarded `.then()` regardless of which caller (mount, endpoint change, or manual refetch) triggered it.
- **Files modified:** console/src/lib/useApiQuery.ts
- **Verification:** `npm run lint` is clean (no errors, no warnings); `npx vitest run` and `npm run build` both still pass.
- **Committed in:** a6b8fab (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 lint/code-quality, 0 architectural)
**Impact on plan:** No behavior change and no scope creep — the fix is a documented, targeted lint suppression for a single line, not a functional change. Both plan-mandated verification commands (`npx vitest run`, `npm run build`) passed before and after; `npm run lint` (not required by the plan but run proactively) is now also clean.

## Issues Encountered
None beyond the deviation above. RED step confirmed all 4 tests failed against a stubbed `fetchApi` that threw `"not implemented"`; GREEN step passed all 4 on first run.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `console/src/lib/api.ts`, `console/src/lib/useApiQuery.ts`, `console/src/components/ErrorState.tsx`, and `console/src/components/FetchMeta.tsx` are the exact shared building blocks plans 13-04 and phases 14-16 must consume — no bespoke fetch/error handling is expected downstream.
- `KillSwitchBanner` is live in the root layout; its actual "tripped" and "amber unknown" visual states still need live verification against a running FastAPI backend, which is explicitly deferred to plan 13-04's checkpoint per this plan's `<verification>` section.
- No blockers for 13-04 (System Status screen), which can now build on `useApiQuery`/`ErrorState`/`FetchMeta` directly.

---
*Phase: 13-console-foundation-and-system-status*
*Completed: 2026-07-08*

## Self-Check: PASSED

All created files verified present on disk (console/src/lib/api.ts, api.test.ts, useApiQuery.ts, console/src/components/ErrorState.tsx, FetchMeta.tsx, KillSwitchBanner.tsx, console/vitest.config.ts, this SUMMARY.md). All three task commits (07db80f, d2c9a8d, a6b8fab) verified present in git log.
