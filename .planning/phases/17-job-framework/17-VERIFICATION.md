---
phase: 17-job-framework
verified: 2026-07-20T08:42:25Z
status: passed
score: 6/6 must-haves verified (JOB-06 accepted at framework level for Phase 17; operator surface assigned to Phase 18)
overrides_applied: 1
override_note: >
  Developer decision (2026-07-20): JOB-06's cancellation FRAMEWORK MECHANISM is
  accepted as satisfying Phase 17's backend scope (ROADMAP explicitly scopes Phase 17
  as "no operator-visible surface yet"). The orphaned operator-invocable cancel surface
  is no longer orphaned — it is assigned to Phase 18 via a new success criterion 5
  ("mutating cancellation endpoint"), and JOB-06 is marked Complete in REQUIREMENTS.md
  with dual-phase traceability. No Phase 17 rework required.
gaps:
  - truth: "Operator can cancel a queued or running Job, transitioning it to CANCELLED with an audit record (JOB-06 — operator-invocable surface)"
    status: partial
    reason: >
      The cancellation FRAMEWORK MECHANISM is fully built, tested, and exercised
      end-to-end (request_cancellation for QUEUED, cooperative RUNNING cancel via
      acknowledge_cancellation, grace-period timeout sweep, full D-10 audit fields).
      However, no operator-invocable entry point exists anywhere in the codebase.
      src/trading_platform/api/routes/jobs.py is explicitly read-only ("No POST, PUT,
      PATCH, or DELETE handler belongs here... cancellation endpoints are Phase 18").
      grep across src/ (excluding definitions and tests) shows request_cancellation()
      has zero callers outside tests/test_job_cancellation.py — no CLI command, no API
      route, nothing an operator could invoke today calls it. REQUIREMENTS.md itself
      marks JOB-06 "[ ] Pending", which is authoritative and matches this finding.
      This is presented as an escalation, not a rework order: ROADMAP.md's Phase
      17 phase-ordering note explicitly states Phase 17 is "pure backend infrastructure
      with no operator-visible surface yet" — so the absence of a CLI/API cancel
      endpoint is consistent with the phase's own stated scope. The problem is that
      neither Phase 18's nor Phase 19's success criteria in ROADMAP.md explicitly
      claim ownership of an operator-facing cancel surface (Phase 18 = idempotency/
      observation; Phase 19 = trigger/retry/kill-switch — neither names "cancel"),
      so JOB-06's operator surface is currently orphaned between phases rather than
      cleanly deferred with explicit tracking.
    artifacts:
      - path: "src/trading_platform/jobs/cancellation.py"
        issue: "Fully implemented and tested framework mechanism — not itself a gap"
      - path: "src/trading_platform/api/routes/jobs.py"
        issue: "Deliberately read-only by design (D-15); no cancel endpoint, by Phase 17 scope"
    missing:
      - "A ROADMAP.md decision that explicitly assigns the operator-invocable cancel surface (CLI command or mutating API endpoint) to a named future phase (most likely Phase 18's mutating endpoints or Phase 19's console controls), closing the current traceability hole"
      - "OR an explicit override in this VERIFICATION.md accepting framework-level completion as satisfying JOB-06 for Phase 17, with REQUIREMENTS.md updated to reflect that decision"
---

# Phase 17: Job Framework Verification Report

**Phase Goal:** A generic, extensible, restart-safe DB-backed Job framework exists in PostgreSQL — every long-running operation can run as a Job with a closed lifecycle, explicit dependencies, cancellation, progress, and structured logs, with zero Redis/Celery infrastructure.
**Verified:** 2026-07-20T08:42:25Z
**Status:** gaps_found (escalation — see "JOB-06 Crux" section below; does not indicate Phase 17 rework and does not block starting Phase 18)
**Re-verification:** No — initial verification

## JOB-06 Crux — Read This First

The task brief for this verification specifically flagged JOB-06 (`Pending` in REQUIREMENTS.md) as requiring an honest, non-rubber-stamped judgment. Summary of findings:

- **Framework mechanism: VERIFIED.** `src/trading_platform/jobs/cancellation.py` implements atomic QUEUED cancellation (D-07), cooperative RUNNING cancellation with handler acknowledgement (D-08), grace-period timeout-as-FAILED (D-09), and full D-10 audit fields (requester, reason, `requested_at`, `acknowledged_at`, terminal cause). `tests/test_job_cancellation.py` (433 lines) exercises all paths; the runner (`jobs/runner.py`) exercises `acknowledge_cancellation` end-to-end when a handler observes and honors a cancellation checkpoint. All 136 job-framework tests pass, including this file.
- **Operator-invocable surface: ABSENT.** `grep -rn "request_cancellation(" src/` (excluding the definition and tests) returns zero results — nothing in the shipped codebase can call it except test code. `src/trading_platform/api/routes/jobs.py` is explicitly documented as read-only, with a code comment stating cancellation endpoints belong to Phase 18. No CLI command exists either.
- **This is consistent with, not a violation of, Phase 17's own stated scope.** ROADMAP.md line 14: "Phase 17 builds the generic Job framework in isolation ... pure backend infrastructure with no operator-visible surface yet." Read this way, Success Criterion 5's "Operator can cancel" describes a framework capability that is exercisable (and is exercised, by the runner/tests), not a literal HTTP/CLI entry point.
- **The genuine gap is a roadmap traceability hole, not missing Phase 17 work.** Neither Phase 18's success criteria (idempotency, transport-agnostic observation, CLI-thin-wrapper enforcement) nor Phase 19's (trigger operations, retry, strategy/kill-switch control) explicitly name "cancel a Job" as a deliverable. So JOB-06's operator surface is not cleanly deferred with a named owner — it is simply not committed to any specific future phase yet. Per the verification-overrides guidance, deferred items must have "clear, specific evidence in a later phase's goal or success criteria" to auto-defer; that evidence does not currently exist, so this is reported as a gap rather than silently deferred.
- **REQUIREMENTS.md's own `[ ] Pending` marking for JOB-06 is authoritative** and this verifier will not unilaterally override it to Complete. Doing so would contradict the project's own tracked state without a recorded human decision.

**Recommended resolution (either is acceptable, developer's call):**
1. Add a ROADMAP.md phase-ownership note (e.g., in Phase 18 or 19) explicitly assigning the operator cancel surface, then treat JOB-06 as still-open until that phase ships; or
2. Add a verification override here accepting framework-level completion as satisfying JOB-06's Phase 17 obligation, and update REQUIREMENTS.md's checkbox/status to reflect that explicit decision.

This finding does **not** block starting Phase 18 — the framework Phase 18 depends on (Job persistence, lifecycle, registry, dependencies, cancellation mechanics, progress/logs, read API) is solid and fully tested. It blocks only a silent "JOB-06 fully complete" claim.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A Job's state is always one of QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED, no other state representable, proven by enforcement test (JOB-01) | VERIFIED | `db/models/job.py` `JobStatus(StrEnum)` backed by Postgres `Enum`; `jobs/lifecycle.py` closed `_LEGAL_TRANSITIONS` table; `tests/test_job_lifecycle.py` (374 lines) passes |
| 2 | A queued Job survives worker restart and executes after; a running Job whose worker crashes is detected and moved to a terminal state, never silently lost or duplicated (JOB-02) | VERIFIED | `jobs/queue.py` `claim_next_job`/`SKIP LOCKED`, `find_lost_job_ids`/`reclaim_lost_jobs`; `jobs/runner.py` `run_worker_loop`; `tests/test_stale_job_reclaim.py` (491 lines) and `tests/test_job_runner.py` (431 lines) pass |
| 3 | Registering a new Job type touches zero existing queue-framework modules; import-boundary test proves handlers invoke only domain services (JOB-03, JOB-04) | VERIFIED | `jobs/registry.py` register/resolve + `UnknownJobTypeError`; `tests/test_job_registry.py`, `tests/test_job_import_boundary.py` pass |
| 4 | A Job with dependencies starts only after all succeed; a failed dependency moves dependents to a terminal non-executed state (JOB-05) | VERIFIED | `jobs/dependencies.py` (`DependencyCycleError`, `cascade_dependency_outcome`); `tests/test_job_dependencies.py` (465 lines) passes |
| 5a | Cancellation framework mechanism: QUEUED→CANCELLED atomic, RUNNING cooperative-with-acknowledgement, timeout→FAILED, full audit record (JOB-06 mechanism) | VERIFIED | `jobs/cancellation.py` (325 lines); `tests/test_job_cancellation.py` (433 lines) passes; exercised by `jobs/runner.py` |
| 5b | Operator can invoke cancellation (CLI or mutating API endpoint) (JOB-06 operator surface) | **FAILED** | No caller of `request_cancellation()` exists in `src/` outside tests; `api/routes/jobs.py` is explicitly read-only by design; no CLI command exists. See "JOB-06 Crux" above. |
| 6 | Every Job's progress and structured logs are queryable via the API during and after execution (JOB-07) | VERIFIED | `api/routes/jobs.py` (`/progress`, `/logs`, `/events`); `services/job_reads.py`; `tests/test_job_api.py` (541 lines) passes |

**Score:** 5/6 truths verified (JOB-06 partially verified — framework mechanism yes, operator surface no)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/trading_platform/db/models/job.py` | Job ORM + closed enums | VERIFIED | 175 lines, `JobStatus`/`JobFailureReason`/`JobCancellationCause` StrEnums |
| `src/trading_platform/db/models/job_dependency.py` | Dependency edge model | VERIFIED | 52 lines, self-dependency CHECK constraint present |
| `src/trading_platform/db/models/job_event.py` | Append-only audit model | VERIFIED | 99 lines, `JobEventType(StrEnum)` |
| `src/trading_platform/db/models/job_log.py` | Append-only structured log model | VERIFIED | 63 lines, `sequence` column present |
| `alembic/versions/0018_phase17_job_framework.py` | Phase 17 migration | VERIFIED | 259 lines; `tests/test_db_migrations.py` (13 tests) passes |
| `src/trading_platform/jobs/contracts.py` | JobHandler/JobContext Protocols | VERIFIED | 124 lines |
| `src/trading_platform/jobs/registry.py` | JobRegistry | VERIFIED | 67 lines |
| `src/trading_platform/jobs/lifecycle.py` | Guarded transition table | VERIFIED | 273 lines, `_LEGAL_TRANSITIONS` |
| `src/trading_platform/jobs/progress.py` | ProgressSnapshot | VERIFIED | 125 lines |
| `src/trading_platform/jobs/context.py` | DatabaseJobContext | VERIFIED | 181 lines |
| `src/trading_platform/jobs/dependencies.py` | Dependency validation/cascade | VERIFIED | 382 lines |
| `src/trading_platform/jobs/cancellation.py` | Cancellation mechanism | VERIFIED | 325 lines |
| `src/trading_platform/jobs/queue.py` | Claim/lease/reclaim queue | VERIFIED | 223 lines, `skip_locked` present |
| `src/trading_platform/jobs/runner.py` | execute_job / run_worker_loop | VERIFIED | 346 lines |
| `src/trading_platform/worker/commands/run_jobs.py` | Thin CLI wrapper | VERIFIED | 43 lines, no queue/lease/lifecycle logic present |
| `src/trading_platform/services/job_reads.py` | Transport-agnostic read layer | VERIFIED | 292 lines |
| `src/trading_platform/api/routes/jobs.py` | Read-only Job routes | VERIFIED | 88 lines, no POST/PUT/PATCH/DELETE handler (confirmed) |

All 9 plan-level test files present and passing (see Behavioral Spot-Checks).

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `db/models/__init__.py` | `db/models/job.py` | barrel export | WIRED | `from trading_platform.db.models.job import Job, JobCancellationCause, JobFailureReason, JobStatus` present |
| `jobs/lifecycle.py` | `db/models/job_event.py` | append-only audit row per transition | WIRED | Confirmed via passing `test_job_lifecycle.py` |
| `jobs/cancellation.py` | `jobs/lifecycle.py` | `apply_job_transition` | WIRED | All status changes route through it (confirmed by code read + tests) |
| `jobs/queue.py` | `jobs/dependencies.py` | reuse of readiness/cascade functions | WIRED | Confirmed by passing `test_stale_job_reclaim.py` |
| `jobs/runner.py` | `jobs/registry.py` | `.resolve()` | WIRED | Confirmed by code read + `test_job_runner.py` |
| `worker/commands/__init__.py` | `worker/commands/run_jobs.py` | DISPATCH entry `"run-jobs"` | WIRED | `run_jobs_command` registered in `DISPATCH` dict |
| `api/app.py` | `api/routes/jobs.py` | `include_router` | WIRED | `app.include_router(jobs_router)` confirmed |
| `api/routes/jobs.py` | `services/job_reads.py` | `get_job_read_service` dependency | WIRED | Confirmed by code read + `test_job_api.py` |
| (absent) | `jobs/cancellation.py` `request_cancellation()` | operator-facing caller | **NOT WIRED** | Zero callers in `src/` outside the function definition and tests — this is the JOB-06 gap |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full job-framework test suite | `python -m pytest tests/test_job_api.py tests/test_job_cancellation.py tests/test_job_context.py tests/test_job_dependencies.py tests/test_job_import_boundary.py tests/test_job_lifecycle.py tests/test_job_registry.py tests/test_job_runner.py tests/test_stale_job_reclaim.py -q` | `136 passed in 31.37s` | PASS |
| Migration enforcement suite | `python -m pytest tests/test_db_migrations.py -q` | `13 passed in 4.55s` | PASS |
| Operator cancel caller search | `grep -rn "request_cancellation(" src/` (excl. definition) | zero matches | Confirms gap (see JOB-06 Crux) |

Both suites ran against a live local PostgreSQL instance (`pg_isready` confirmed accepting connections on `localhost:5432`), not skipped or mocked.

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|----------------|--------------|--------|----------|
| JOB-01 | 17-01, 17-03 | Closed lifecycle enum, no state outside it representable | SATISFIED | DB enum + `lifecycle.py` transition table + enforcement tests |
| JOB-02 | 17-07, 17-09 | Restart-safe persistence, crash detection, no silent loss/duplication | SATISFIED | `queue.py`/`runner.py` + `test_stale_job_reclaim.py`/`test_job_runner.py` |
| JOB-03 | 17-02, 17-09 | Registry-based extensibility, zero queue-framework modules touched | SATISFIED | `registry.py` + `test_job_registry.py` |
| JOB-04 | 17-02 | Import-boundary: handlers invoke only domain services | SATISFIED | `test_job_import_boundary.py` |
| JOB-05 | 17-01, 17-05, 17-08 | Explicit dependencies, cycle rejection, cascade cancellation | SATISFIED | `dependencies.py` + `test_job_dependencies.py` |
| JOB-06 | 17-01, 17-03, 17-04, 17-06, 17-08 | Operator can cancel a queued/running Job; transitions to CANCELLED; audited | **PARTIAL — mechanism SATISFIED, operator surface BLOCKED** | See "JOB-06 Crux" |
| JOB-07 | 17-01, 17-04, 17-08 | Progress and structured logs observable via API | SATISFIED | `api/routes/jobs.py` + `services/job_reads.py` + `test_job_api.py` |

No orphaned requirement IDs: all plan `requirements:` frontmatter entries across 17-01 through 17-09 cover exactly JOB-01 through JOB-07, matching ROADMAP.md's declared Phase 17 requirement list with no gaps or extras.

### Anti-Patterns Found

None. Scanned all Phase 17 source files (`jobs/*.py`, `services/job_reads.py`, `api/routes/jobs.py`, `worker/commands/run_jobs.py`, `db/models/job*.py`) for `TBD`, `FIXME`, `XXX`, `TODO`, `HACK`, `PLACEHOLDER`, "not yet implemented", "coming soon" — zero matches. No stub patterns, no empty handlers, no hardcoded-empty return values feeding rendering/output.

### Human Verification Required

None. All truths and artifacts for this backend-only, non-UI phase were verifiable programmatically via code inspection and a live test run against PostgreSQL.

### Gaps Summary

Phase 17 delivers a genuinely solid, fully-tested generic Job framework: closed lifecycle enum enforced at both the DB and code level, restart-safe claim/lease/reclaim queue, registry-based extensibility with import-boundary enforcement, explicit dependency graphs with cycle rejection and cascade cancellation, a complete cooperative-cancellation mechanism with full audit trail, and a read-only observation API for progress/logs/events. 136 job-framework tests plus 13 migration tests all pass against a live PostgreSQL instance — no shortcuts, no stubs, no debt markers found anywhere in the phase's files.

The one gap is JOB-06's operator-invocable surface: nothing in the shipped codebase — no CLI command, no mutating API endpoint — lets an actual operator trigger cancellation today. The cancellation *mechanism* the operator action would eventually call is complete and tested. This is consistent with Phase 17's own declared scope ("no operator-visible surface yet" per ROADMAP.md), but it surfaces a real traceability hole: neither Phase 18 nor Phase 19's ROADMAP success criteria currently name "operator cancels a Job" as a deliverable, so this requirement currently has no committed future owner. REQUIREMENTS.md's own `[ ] Pending` marking for JOB-06 agrees with this finding. This is reported as an escalation for a developer decision (assign an explicit future-phase owner, or record an override accepting framework-level completion) — not as a defect in Phase 17's delivered code, and it does not block proceeding to Phase 18.

---

*Verified: 2026-07-20T08:42:25Z*
*Verifier: Claude (gsd-verifier)*
