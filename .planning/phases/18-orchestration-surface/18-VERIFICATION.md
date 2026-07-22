---
phase: 18-orchestration-surface
verified: 2026-07-21T19:24:48Z
status: passed
score: 33/33 must-haves verified
overrides_applied: 0
---

# Phase 18: Orchestration Surface Verification Report

**Phase Goal:** The HTTP API becomes the single orchestration surface for manual operations — every mutating endpoint is idempotent, returns a transport-agnostic Job reference, and CLI worker commands are proven to be thin wrappers over the identical service layer.
**Verified:** 2026-07-21T19:24:48Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Every manual operation is HTTP-only; no direct business-logic/CLI-only path remains (ORCH-01). | VERIFIED | `worker/parser.py` exposes only `serve`, `run-jobs`, and read/report commands; all 11 removed mutators are rejected. `test_orchestration_boundaries.py` pins parser, dispatch, AST route, and runtime route sets. |
| 2 | API/CLI adapters contain no duplicated business logic (ORCH-02). | VERIFIED | `jobs.py` only maps request/typed outcomes to `JobOrchestrationService`; `run_jobs.py` only invokes `run_worker_loop`; AST boundary tests prohibit ORM/lifecycle/domain-service logic in adapters. |
| 3 | Same-key resubmission returns the original Job without duplicate execution (ORCH-03). | VERIFIED | `JobOrchestrationService` uses endpoint/key lookup, canonical fingerprint, nested savepoint, and the database named unique constraint. Real-PostgreSQL sequential and two-thread race tests pass. |
| 4 | Mutation responses are transport-agnostic Job references and observation is available through API reads (ORCH-04). | VERIFIED | `_reference()` returns only `job_id`, `job_type`, `status`, and four fixed relative links. E2E follows all links after worker completion. |
| 5 | Queued/running cancellation reaches the Phase 17 cancellation primitive and returns the updated reference. | VERIFIED | `JobOrchestrationService.cancel()` calls `request_cancellation(..., session=session)` for eligible states; API and orchestration tests cover queued/running/replay/terminal outcomes. |
| 6 | Concurrent requests cannot persist two mutation identities for one endpoint/key. | VERIFIED | `JobMutation` has `uq_job_mutations_endpoint_key`; migration test holds one PostgreSQL transaction and verifies the competing insert raises `IntegrityError`. |
| 7 | One key is independently reusable on submit and cancel endpoint templates. | VERIFIED | Uniqueness is `(endpoint_id, idempotency_key)`; real-DB test persists the same literal key for both route IDs. |
| 8 | Each mutation identity permanently links to its original Job. | VERIFIED | Non-null `job_id` FK uses `ondelete="RESTRICT"`; migration test verifies FK rejection. |
| 9 | Job creation participates in a caller-owned transaction without changing standalone submission behavior. | VERIFIED | `submit_job(..., session=...)` flushes through `_submit_job_in_session`; the no-session branch retains `session_scope`. Focused rollback/standalone regressions pass. |
| 10 | Eligible cancellation participates atomically with its key/audit mutation through the existing primitive. | VERIFIED | `request_cancellation(..., session=...)` is caller-session aware, flushes, and still routes status changes through `apply_job_transition`. |
| 11 | Queued cancellation is immediate, running cancellation cooperative, and first request facts are immutable. | VERIFIED | `_request_cancellation_in_session` preserves first running request facts; cancellation regression suite passed. |
| 12 | Registered valid submission creates one queued Job; replay returns it; changed identity conflicts with original ID. | VERIFIED | Service tests prove one Job/JobMutation/JobEvent for replay, conflict identity, and concurrent candidate rollback. |
| 13 | Public submission specs validate type-specific payloads before transaction entry and invalid input writes nothing. | VERIFIED | `submit()` resolves the spec and calls `validate_payload()` before `session_scope`; tests patch `session_scope` and assert zero Job/JobMutation/JobEvent rows. |
| 14 | Invalid keys/types/reasons/targets/fresh terminal cancellations are rejected before prohibited mutation. | VERIFIED | Service validation precedes `session_scope` where applicable; API/service tests cover missing/blank/oversized key, unknown type, invalid reason, absent target, and terminal target. |
| 15 | Cancellation replay/repeats preserve audit facts and return current references. | VERIFIED | Existing endpoint/key replay is read before terminal checks; pending/CANCELLED fresh-key paths avoid `request_cancellation`; service/API tests assert unchanged first facts/events. |
| 16 | Every successful service mutation has one compact current reference with relative observation links only. | VERIFIED | Single `_reference()`/`_relative_links()` builder has exact four-link shape; no embedded progress/log/event content. |
| 17 | `POST /api/v1/jobs` accepts only a registered generic Job and unsupported types receive 422 before persistence. | VERIFIED | `SubmitJobRequest` supplies generic type/payload; route maps `UnknownJobTypeForSubmissionError` to 422; API contract tests assert no writes. |
| 18 | Registered but invalid payload returns 422 `invalid_job_payload` with zero persistence. | VERIFIED | Route maps `InvalidJobPayloadError` exactly; API and E2E tests assert status/detail plus zero rows. |
| 19 | Both mutation routes require `Idempotency-Key` and missing keys return 400 without mutation. | VERIFIED | Optional aliased header reaches service validation; route maps `MissingIdempotencyKeyError` to 400; API tests pass. |
| 20 | New submission is 202; exact replay is 200 with `Idempotency-Replayed: true`; mismatch is typed 409. | VERIFIED | `_mutation_response()` applies the replay header and `submit_job()` maps conflict; API/E2E tests assert all three outcomes. |
| 21 | The cancel POST route exposes the Phase 17 lifecycle outcomes with the shared reference. | VERIFIED | `cancel_job()` delegates only to orchestration; API tests cover normalization, no-op/replay, absent, and terminal paths. |
| 22 | Mutation bodies are compact references; GET routes remain the observation mechanism. | VERIFIED | Mutation routes return `result.reference.to_dict()` unchanged; E2E follows `self`, `progress`, `logs`, and `events`. |
| 23 | No manual mutator is invocable from the worker CLI while infrastructure/read/report commands remain. | VERIFIED | Exact parser/dispatch allowlists and removed-command argparse tests pass. |
| 24 | `run-jobs` is a thin worker-loop adapter and the production registry has no Phase 19 handlers. | VERIFIED | `run_jobs_command()` performs startup/configuration then calls `run_worker_loop`; `build_default_registry()` returns empty and boundary/E2E tests assert it. |
| 25 | Structural enforcement covers parser, dispatch, entrypoint, routes, orchestration, and domain boundaries. | VERIFIED | `test_orchestration_boundaries.py` AST/runtime checks passed, including the exact two POST routes and adapter import restrictions. |
| 26 | The existing `services -> jobs/api/worker` prohibition remains strict; orchestration is outside services. | VERIFIED | Orchestration imports jobs/db but not API/worker/services; unchanged service-boundary suite passed with at least 30 scanned modules. |
| 27 | Test-only handler/spec completes HTTP POST → queued → worker → terminal linked API reads. | VERIFIED | `test_job_mutation_e2e.py` injects one registry into app and `run_worker_loop`, then proves one execution, succeeded detail, 100% progress, log, and lifecycle events. |
| 28 | The E2E type rejects `{message: goodbye}` before session/persistence. | VERIFIED | E2E asserts HTTP 422 `invalid_job_payload` and `(Job, JobMutation, JobEvent) == (0,0,0)`. |
| 29 | The production registry remains free of operation handlers until Phase 19. | VERIFIED | `build_default_registry()` constructs a fresh empty `JobRegistry`; E2E and boundary tests assert no Phase 19 literals/registrations. |
| 30 | Mutation API startup requires PostgreSQL before boot/registry construction. | VERIFIED | `lifespan()` calls `enforce_startup_config(..., require_database=True)` before state initialization; startup tests passed. |
| 31 | Submission/replay/reference links remain idempotent and transport-neutral through worker lifecycle. | VERIFIED | E2E proves 202 create, 200 exact pre-execution replay, one handler execution, and usable relative GET links after completion. |
| 32 | Phase 18 made no console/push/polling implementation change. | VERIFIED | `git diff --name-only f33e62c...HEAD` contains no `console/` path; fixed-base boundary test passed. |
| 33 | Runtime API/worker/jobs/orchestration code has no direct schema mutation or Alembic upgrade/downgrade call. | VERIFIED | Closed AST denylist in `test_orchestration_boundaries.py` scanned all four runtime roots and passed. |

**Score:** 33/33 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `src/trading_platform/db/models/job_mutation.py` | Durable endpoint/key identity | VERIFIED | Concrete ORM model, named uniqueness/index, non-null RESTRICT FK. |
| `alembic/versions/0019_phase18_job_idempotency.py` | Reversible schema | VERIFIED | Exact 0019 revision/down-revision; creates/drops only `job_mutations`. |
| `tests/test_job_mutation_migration.py` | DB migration proof | VERIFIED | Fresh PostgreSQL shape, race, FK, downgrade/re-upgrade tests passed. |
| `src/trading_platform/jobs/dependencies.py` | Caller-session submission | VERIFIED | Shared `_submit_job_in_session`; standalone/session branches are substantive and tested. |
| `src/trading_platform/jobs/cancellation.py` | Caller-session cancellation | VERIFIED | Shared session path preserves guarded lifecycle semantics and is tested. |
| `tests/test_job_dependencies.py` | Submission transaction tests | VERIFIED | Included in 176 passing focused regressions. |
| `tests/test_job_cancellation.py` | Cancellation transaction tests | VERIFIED | Included in 176 passing focused regressions. |
| `src/trading_platform/orchestration/job_mutations.py` | Transport-independent mutation service | VERIFIED | 342-line service implements validation, identity, savepoint recovery, cancellation, and references. |
| `src/trading_platform/jobs/registry.py` | Submission validation contract | VERIFIED | Concrete `JobSubmissionSpec`, typed payload error, and atomic paired registration. |
| `tests/test_job_orchestration.py` | Service/race invariants | VERIFIED | Real PostgreSQL sequential/concurrent service tests passed. |
| `src/trading_platform/api/routes/jobs.py` | Thin POST adapters plus reads | VERIFIED | Two adapters only call orchestration; existing GET readers remain. |
| `src/trading_platform/api/dependencies.py` | Registry/service providers | VERIFIED | Reads injected registry or builds default, then constructs orchestration service. |
| `tests/test_job_mutation_api.py` | HTTP contract tests | VERIFIED | Real-PostgreSQL new/replay/conflict/no-write/cancel coverage passed. |
| `tests/test_job_api.py` | Route-method fence | VERIFIED | Exact GET/POST Job route surface included in focused pass. |
| `src/trading_platform/worker/parser.py` | Retained CLI surface | VERIFIED | Five-command explicit parser whitelist. |
| `src/trading_platform/worker/commands/__init__.py` | Dispatch whitelist | VERIFIED | Four non-serve dispatch entries, no mutating command. |
| `tests/test_orchestration_boundaries.py` | Architecture/scope gates | VERIFIED | Substantive 387-line AST/runtime suite passed. |
| `src/trading_platform/api/app.py` | DB-gated startup/registry lifecycle | VERIFIED | Required database gate precedes boot state; injected registry is retained. |
| `tests/test_job_mutation_e2e.py` | Full submit/execute/observe proof | VERIFIED | Uses test-only handler/spec and live migrated PostgreSQL. |
| `tests/test_app_boot.py` | Boot invariant | VERIFIED | Included in focused pass. |
| `tests/test_startup_validation.py` | API/run-jobs entrypoint gates | VERIFIED | Included in focused pass. |

### Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| `JobMutation` | `jobs` | non-null FK | WIRED | `ForeignKey("jobs.id", ondelete="RESTRICT")`. |
| 0019 migration | 0018 migration | `down_revision` | WIRED | Exact `down_revision = "0018_phase17_job_framework"`. |
| `submit_job` | session scope | standalone/session split | WIRED | Session branch flushes; fallback opens `session_scope`. |
| `request_cancellation` | lifecycle | guarded transition | WIRED | Queued path calls `apply_job_transition`. |
| orchestration | registry/spec | pre-session validation | WIRED | `resolve`, `resolve_submission_spec`, then `validate_payload` occur before line 225 `session_scope`. |
| orchestration | `JobMutation` | transaction/savepoint | WIRED | Endpoint/key lookup, nested insert, named-constraint recovery. |
| orchestration | `submit_job` | caller session | WIRED | Calls `submit_job(..., session=session)`. |
| orchestration | `request_cancellation` | caller session | WIRED | Calls `request_cancellation(..., session=session)`. |
| Job POST routes | orchestration | FastAPI dependency | WIRED | `Depends(get_job_orchestration_service)` and only `submit`/`cancel` mutations. |
| response links | Job GET routes | fixed relative paths | WIRED | Service emits all existing route shapes; E2E dereferences each. |
| worker entrypoint | dispatch | routing lookup | WIRED | `DISPATCH.get(args.command)` after sole `serve` special case. |
| boundary suite | import-boundary suite | unchanged services fence | WIRED | Explicit `SERVICE_MODULES` floor plus focused suite pass. |
| API lifespan | registry | injected/default lifecycle | WIRED | `app.state.job_registry` injection/default construction. |
| E2E registry | submission spec | paired test registration | WIRED | `registry.register(handler, submission_spec=...)`. |
| E2E registry | runner | same instance | WIRED | Same `registry` passes to `create_app` and `run_worker_loop`. |
| response links | linked GETs | TestClient requests | WIRED | E2E invokes returned self/progress/logs/events links. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| --- | --- | --- | --- | --- |
| `job_mutations.py` | `JobReference` | PostgreSQL `Job` retrieved through session | Yes — live migrated-DB service/E2E tests | FLOWING |
| `api/routes/jobs.py` | response reference | `JobOrchestrationService` result | Yes — API tests create/replay persisted jobs | FLOWING |
| `tests/test_job_mutation_e2e.py` | linked observations | API GET routes after `run_worker_loop` | Yes — succeeded status/progress/log/events asserted | FLOWING |
| `api/app.py` | `job_registry` | injected registry or `build_default_registry` | Yes — exact injected object reaches API and runner in E2E | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| --- | --- | --- | --- |
| Phase 18 migration/service/API/worker contracts | `.venv/bin/python -m pytest` over the 15 focused Phase 18/Job modules | `176 passed, 1 warning in 30.96s` | PASS |
| Declared artifact/link checks | `gsd-sdk query verify.artifacts/verify.key-links` for all six plans | 22/22 artifacts pass; manually verified false-negative path-pattern links above | PASS |
| Owned lint/type checks | Ruff owned paths; configured mypy scope | Ruff passed; mypy: `Success: no issues found in 51 source files` | PASS |
| Fresh migration schema shape | Focused migration suite | Passed against fresh upgraded PostgreSQL databases | PASS |

### Probe Execution

Step 7c: SKIPPED — no declared or conventional `probe-*.sh` files were found.

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
| --- | --- | --- | --- | --- |
| ORCH-01 | 18-04, 18-05, 18-06 | HTTP-only manual operation surface | SATISFIED | Exact two-POST route fence; removed CLI mutators; E2E HTTP submission. |
| ORCH-02 | 18-02, 18-05, 18-06 | Thin API/CLI adapters over shared layers | SATISFIED | Session-composable primitives plus AST/runtime adapter and import boundaries. |
| ORCH-03 | 18-01, 18-02, 18-03, 18-04, 18-06 | Every mutation endpoint is idempotent | SATISFIED | Durable endpoint/key constraint, canonical fingerprints, savepoint recovery, API/E2E race/replay tests. |
| ORCH-04 | 18-03, 18-04, 18-06 | Compact reference observable via API reads | SATISFIED | One relative-link reference builder and post-worker linked-read E2E proof. |

All four Phase 18 requirement IDs declared by PLAN frontmatter are present in `REQUIREMENTS.md`; no Phase 18 orphaned requirement was found.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| --- | --- | --- | --- | --- |
| `src/trading_platform/worker/parser.py` | 12 | Existing `serve` help calls its non-mutating loop “placeholder” | Info | Not a mutation path or Phase 18 stub; `run-jobs` is the retained real queue-worker adapter. |
| `src/trading_platform/jobs/dependencies.py` | 186-216 | Direct framework submission with an already terminal dependency creates a queued dependent without triggering cascade | Warning, pre-existing Phase 17 | Not reachable through Phase 18 `SubmitJobRequest` (no dependency input); can strand a direct-framework Job. Needs a separate Phase 17 framework fix, not a Phase 18 gap. |
| `src/trading_platform/jobs/dependencies.py` | 365-406 | Concurrent cascades can each select a queued shared descendant before one transition wins | Warning, pre-existing Phase 17 | `apply_job_transition` serializes writes but loser can receive `IllegalJobTransition`; Phase 18 has no dependency submission path. A concurrent dependency-graph cancellation can surface a transient API error and merits a framework race regression. |
| `src/trading_platform/jobs/cancellation.py` | 337-370 | Timeout sweep selects candidates before locking/rechecking them | Warning, pre-existing Phase 17 | Two sweeps, or sweep versus acknowledgement, can race into an illegal terminal transition; runner's terminal landing is guarded, but sweep itself lacks equivalent per-row recovery. |

No `TBD`, `FIXME`, or `XXX` markers were found in Phase 18 implementation files.

### Verification Notes

- The full suite was independently attempted rather than accepting the summary claim. It reached `300 passed` but stopped at teardown in unrelated `tests/test_market_data_access.py`: its fixture attempted to terminate a PostgreSQL superuser connection and received `InsufficientPrivilege`. This is outside Phase 18 files and does not contradict the focused Phase 18/Job result above. It is an environment/legacy-fixture warning, not a Phase 18 goal gap.
- `alembic check` against the verifier's default local database reported `Target database is not up to date`, so it could not perform an autogenerate drift comparison there. Fresh database migrations and exact model/database schema assertions passed in `test_job_mutation_migration.py`; deploy the default local database to Alembic head before using it for mutation API verification.
- The three independently reviewed concurrency concerns originate in Phase 17 commits (`da8f6d8`, `e77f837`) rather than Phase 18. They do not invalidate the Phase 18 public no-dependency submission surface, idempotency contract, or its focused E2E flow. They are not deferred by a later roadmap criterion; they require an explicit framework follow-up if accepted as work.

### Human Verification Required

None. This phase is backend-only and its externally observable HTTP/worker flow is covered by live PostgreSQL/TestClient automated tests.

---

_Verified: 2026-07-21T19:24:48Z_
_Verifier: Claude (gsd-verifier)_
