---
phase: 19-job-operations-vertical-slice
plan: 02
subsystem: api
tags: [fastapi, pydantic-settings, mutation-guard, security]

# Dependency graph
requires:
  - phase: 18-orchestration-surface
    provides: idempotent POST /api/v1/jobs and POST /api/v1/jobs/{id}/cancel routes, JobOrchestrationService
provides:
  - OrchestrationSettings.mutations_enabled (default False) on Settings and EnvironmentOverrides
  - require_mutations_enabled FastAPI route-dependency guard (403 mutations_disabled)
  - Both mutating Job routes guarded; zero-row-write proof (jobs/job_mutations/job_events/strategy_runs)
  - Guard-before-idempotency/schema ordering pinned by test
  - render.yaml explicit disabled flag; .env.example explicit enabled flag for local dev
  - 422 invalid_job_payload body now carries InvalidJobPayloadError.reason (D-09 transport half)
affects: [19-04 (job-type catalog reads mutations_enabled), 20 (OperatorControlService routes reuse require_mutations_enabled)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Mutation guard lives at the HTTP route-decorator dependency layer (api/dependencies.py), not inside the orchestration service, so ordering is correct and Phase 20 control routes can reuse it unchanged."
    - "FastAPI 0.131 route-walk tests must use route.effective_candidates() (not isinstance(route, APIRoute)) to see through _IncludedRouter wrapping — same pattern test_orchestration_boundaries.py::_effective_routes() already established."

key-files:
  created:
    - tests/test_mutation_guard.py
  modified:
    - src/trading_platform/core/settings.py
    - src/trading_platform/api/dependencies.py
    - src/trading_platform/api/routes/jobs.py
    - render.yaml
    - .env.example
    - tests/test_job_mutation_api.py
    - tests/test_job_mutation_e2e.py

key-decisions:
  - "Guard ordering: 403 fires before Idempotency-Key/schema validation (route-decorator dependencies resolve before body-schema validation), pinned by test_disabled_guard_precedes_idempotency_and_schema_validation. Malformed/non-JSON bodies are architecturally always 422/400 first (FastAPI parses the body before any dependency runs) and were deliberately not tested against that ordering."
  - "require_mutations_enabled reads get_settings(request).orchestration.mutations_enabled directly rather than living inside JobOrchestrationService, because the service also accepts a bare DatabaseSettings in some call sites and because Phase 20 reuses this exact dependency on OperatorControlService routes."

patterns-established:
  - "Route-walk boundary test (test_every_mutating_route_requires_mutation_guard) asserts every non-GET/HEAD route in create_app() carries the guard dependency, so a future mutating route added without the guard fails CI automatically."

requirements-completed: [ORCH-07]

# Metrics
duration: 25min
completed: 2026-09-24
---

# Phase 19 Plan 02: ORCH-07 Mutation Guard Summary

**Config-flag mutation guard (default disabled) wired onto every mutating Job route via a FastAPI route-decorator dependency, with a route-walk test proving future mutating routes cannot skip it, and the D-09 `reason` field added to the 422 invalid-payload body.**

## Performance

- **Duration:** 25 min
- **Started:** 2026-09-24T08:52:00Z
- **Completed:** 2026-09-24T09:17:00Z
- **Tasks:** 2
- **Files modified:** 8 (5 modified in Task 1, 2 modified + 1 created in Task 2)

## Accomplishments
- `OrchestrationSettings.mutations_enabled: bool = False` added to both `Settings` and `EnvironmentOverrides` (dual-declaration pattern), so `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED` maps to it; model-level default is `False` independent of any `.env`.
- `require_mutations_enabled` dependency in `api/dependencies.py` raises `HTTPException(403, {"code": "mutations_disabled"})`; wired onto `POST /api/v1/jobs` and `POST /api/v1/jobs/{id}/cancel` via `dependencies=[Depends(...)]`.
- `test_every_mutating_route_requires_mutation_guard` walks `create_app()`'s effective routes and asserts every route with a non-GET/HEAD method carries the guard — a future mutating route without it fails this test, not just a code review.
- `test_disabled_guard_precedes_idempotency_and_schema_validation` pins the ordering: missing `Idempotency-Key` → 403 (not 400); schema-invalid JSON body `{}` → 403 (not 422); malformed path param → 403.
- Zero-row-write proof extended to four tables (`jobs`, `job_mutations`, `job_events`, `strategy_runs`) across the disabled-submit and disabled-cancel tests.
- `render.yaml` sets the flag `"false"` explicitly and its header comment now states mutating routes exist but are guarded-disabled, rather than claiming every route is read-only. `.env.example` enables the flag for local development.
- 422 `invalid_job_payload` body now includes `"reason": <InvalidJobPayloadError.reason>` (D-09 transport contract); both Phase 18 test files updated to assert the extended body.
- Every Phase 18 HTTP mutation test now explicitly enables the flag (`monkeypatch.setenv(...)`) so their submit/cancel expectations stay accurate against the new disabled-by-default guard.

## Task Commits

1. **Task 1: Settings flag, guard dependency, route wiring, reason in 422 body, deploy config** - `5e40720` (feat)
2. **Task 2: Enable flag in Phase 18 HTTP tests + dedicated ORCH-07 tests** - `a585767` (test)

**Plan metadata:** committed below (docs: complete plan)

## Files Created/Modified
- `src/trading_platform/core/settings.py` - `OrchestrationSettings` class; `orchestration` field on `Settings` and `EnvironmentOverrides`
- `src/trading_platform/api/dependencies.py` - `require_mutations_enabled(request) -> None`
- `src/trading_platform/api/routes/jobs.py` - guard wired onto both mutating decorators; `reason=exc.reason` added to the 422 mapping
- `render.yaml` - explicit `MUTATIONS_ENABLED=false` envVar; reworded read-only-route comment
- `.env.example` - explicit `MUTATIONS_ENABLED=true` for local dev
- `tests/test_job_mutation_api.py` - `client` fixture enables the flag; invalid-payload assertion includes `reason`
- `tests/test_job_mutation_e2e.py` - `migrated_job_mutation_e2e_db` fixture enables the flag; invalid-payload assertion includes `reason`
- `tests/test_mutation_guard.py` (new) - 6 tests: model default, submit-disabled, cancel-disabled, guard-ordering, submit-enabled happy path, route-walk

## Decisions Made

- Guard lives at the HTTP dependency layer, not inside `JobOrchestrationService` — matches the plan's explicit instruction and the 19-PATTERNS.md placement analysis (ordering correctness, Phase 20 reuse, and `JobOrchestrationService` accepting a bare `DatabaseSettings` in some call sites that lack `.orchestration`).
- `test_every_mutating_route_requires_mutation_guard` uses `route.effective_candidates()` rather than `isinstance(route, APIRoute)` — this FastAPI version (0.131) wraps `app.include_router(...)` results as `_IncludedRouter` placeholders on `app.routes`, so a naive `isinstance` walk finds zero routes. This mirrors the existing `_effective_routes()` helper pattern in `tests/test_orchestration_boundaries.py`.
- `tests/test_mutation_guard.py` copies the throwaway-Postgres-database fixture pattern locally (own `_admin_connection_settings`/`_set_database_env`/`migrated_mutation_guard_db` fixture) rather than importing private helpers from `tests/test_job_mutation_api.py`, per the plan's explicit instruction.

## Deviations from Plan

None — plan executed exactly as written. The route-walk test required using `effective_candidates()` instead of the more naive `isinstance(route, APIRoute)` approach to work correctly under this FastAPI version, but this is an implementation detail within Task 2's stated acceptance criteria (the test still asserts what the plan specified), not a scope deviation.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. Local `.env` files created from `.env.example` will now include `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true`, but this is documentation/config only, not a manual step.

## Next Phase Readiness

- ORCH-07 is fully satisfied by this plan's literal requirement text (configuration flag, default disabled, typed 403 + zero rows when disabled, `render.yaml` sets it disabled explicitly, no authentication introduced) — marked Complete in REQUIREMENTS.md.
- `require_mutations_enabled` is ready for Phase 20 to attach to `OperatorControlService` HTTP routes unchanged.
- Plan 19-04 (job-type catalog, D-20) can read `settings.orchestration.mutations_enabled` directly for its `mutations_enabled` top-level catalog field — no new plumbing needed.
- No blockers for subsequent Phase 19 plans (backtest handler, worker wiring, console Job UI).

---
*Phase: 19-job-operations-vertical-slice*
*Completed: 2026-09-24*

## Self-Check: PASSED

All 9 claimed files found on disk; all 3 claimed commit hashes (5e40720, a585767, 658a6cd) found in git log.
