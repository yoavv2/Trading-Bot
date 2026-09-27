---
phase: 19
slug: job-operations-vertical-slice
status: verified
threats_open: 0
asvs_level: 1
created: 2026-09-27
---

# Phase 19 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

> **Note on provenance:** an untracked `19-SECURITY.md` (dated 2026-09-26) already existed at audit start, claiming 48/48 closed. Per the security-auditor's read-only/documentation-is-not-evidence rule, that draft was treated only as a lead list of citations, not as proof. Every citation below was independently re-verified against the actual PLAN `<threat_model>` mitigation text and the current implementation/test files (grep + direct reads), including a targeted re-check of the weakest closures (T-19-05-03, T-19-07-03, T-19-10-01, T-19-03-03, T-19-10-02, T-19-09-02, and all code-only closures with no cited test). No discrepancies were found between the prior draft's citations and the live code; this file reflects independently confirmed evidence, not a copy-forward of the draft.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| migration runner -> Postgres | Operator-controlled Alembic DDL | schema only, no user input |
| public internet -> FastAPI (render.yaml deploy) | Unauthenticated callers can reach every route | HTTP JSON |
| env/config -> Settings | Operator-controlled configuration decides mutation capability | config flags, secrets |
| HTTP client -> GET /api/v1/jobs/{id}, /api/v1/runs/{id} | Read-only public routes | DB-derived job/run data |
| public client -> GET /api/v1/job-types | Unauthenticated read | catalog metadata |
| spec.submission_defaults() -> DB | Read-time DB query that may fail | prefill defaults |
| env/config files -> worker | Operator config incl. broker secrets, read at boot and per dispatch | secrets |
| worker -> jobs table (failure_message) | Persisted text later rendered publicly via GET /api/v1/jobs/{id} | failure text |
| HTTP payload -> BacktestSubmissionSpec.validate_payload | Untrusted JSON before persistence | job submission payload |
| Job payload (DB) -> handler | Payload already normalized by the spec | normalized payload |
| test process -> temp Postgres | Isolated per-test database | none (test-only) |
| browser -> Next.js /backend proxy -> FastAPI | Console mutation requests | JSON, Idempotency-Key |
| API JSON -> React render | Response strings rendered in console | untrusted text/JSON |
| operator input -> POST cancel | Free-text reason | cancellation reason |
| API log/event text -> React | Handler-authored log/event text | untrusted text |
| operator form input -> POST /api/v1/jobs | Untrusted job submission payload | strategy_id/dates |
| URL query params -> form pre-fill | ?type and ?strategy_id are attacker-controllable via links | query params |
| operator click -> cancel mutation | Destructive action on a public-capable UI | mutation trigger |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation (verified) | Status |
|-----------|----------|-----------|-------------|------------------------|--------|
| T-19-01-01 | Tampering | strategy_runs.job_id | mitigate | `uq_strategy_runs_job_id` UNIQUE + FK `ondelete="SET NULL"` present at `alembic/versions/0020_phase19_job_operations.py:31,34`; pinning test `test_strategy_runs_job_id_unique_rejects_second_run_for_same_job` exists at `tests/test_phase19_job_operations_migration.py:153` | closed |
| T-19-01-02 | Repudiation | Job -> run provenance | mitigate | Provenance is DB FK only; grep across `src/` for `StrategyRun(` construction confirms no non-orchestration write site sets `job_id` (verified under T-19-03-02) | closed |
| T-19-01-03 | Denial of Service | ALTER TYPE ADD VALUE locking | accept | Rationale matches plan text: single-operator deploy, migration runs before traffic (`Dockerfile:32`, `alembic upgrade head && exec uvicorn ...`) | closed (accepted) |
| T-19-01-04 | Tampering | ON DELETE SET NULL | accept | Rationale matches plan text: app code never deletes Job rows; SET NULL guards manual DB cleanup only | closed (accepted) |
| T-19-02-01 | Elevation of Privilege | POST /api/v1/jobs, POST /api/v1/jobs/{id}/cancel | mitigate | `require_mutations_enabled` dependency raises 403 when disabled, `src/trading_platform/api/dependencies.py:56-66`; default `False` (`core/settings.py:267`); `render.yaml:66` sets `"false"`; `tests/test_mutation_guard.py::test_every_mutating_route_requires_mutation_guard:261-289` walks **all** `app.routes` generically (not a fixed list), asserting `checked >= 2` non-vacuous — confirmed this would catch any future mutating route | closed |
| T-19-02-02 | Tampering | DB rows on disabled deploy | mitigate | Guard is a route-level dependency, runs before body/orchestration deps; `_counts()` helper (`tests/test_mutation_guard.py:133`) covers jobs/job_mutations/job_events/strategy_runs; zero-row equality asserted at lines 175 and 202 | closed |
| T-19-02-03 | Information Disclosure | ordering of 400/422 vs 403 | mitigate | `test_disabled_guard_precedes_idempotency_and_schema_validation:210-243` confirmed: missing-key, empty-body/schema-invalid, and bad-UUID-path cases all return 403 with only `{"code": "mutations_disabled"}` | closed |
| T-19-02-04 | Information Disclosure | 422 reason field | accept | `reason` is a closed-enum machine code (`BacktestPayloadRejection`), no payload echo — matches plan rationale | closed (accepted) |
| T-19-02-05 | Spoofing | no authentication | accept | Matches plan rationale: out of scope per roadmap; ORCH-07 flag is the only control | closed (accepted) |
| T-19-03-01 | Information Disclosure | resources[] | accept | Matches plan rationale: exposes only run id/status/relative link, already public via `/api/v1/runs` | closed (accepted) |
| T-19-03-02 | Tampering | job_id write path | mitigate | `job_id` written only inside `_create_backtest_run`'s transaction, `src/trading_platform/services/backtesting.py:112-133,192-198`; grep of all `StrategyRun(` construction sites (`bootstrap.py`, `risk.py`, `operator_controls.py` x2, `reconciliation/report.py`, `execution/submit_orders.py`) confirms none pass `job_id`; UNIQUE constraint (T-19-01-01) blocks relinking | closed |
| T-19-03-03 | Repudiation | provenance inference | mitigate | `resources[]` derived solely from `strategy_runs.job_id` FK, `src/trading_platform/services/job_reads.py:120-160`; confirmed `tests/test_job_resources_read.py::test_detail_resources_visible_while_running:175` and `::test_detail_resources_survive_terminal_states:205` exist and their seed helper creates no JobLog rows — resources render correctly with zero logs, proving no log/timestamp dependence | closed |
| T-19-04-01 | Information Disclosure | catalog payload | mitigate | Entry keys restricted to `{job_type, description, cancellation_mode, submission_defaults}`, `src/trading_platform/api/routes/job_types.py:43-58`; `tests/test_job_catalog.py:131-134` asserts key-set subset | closed |
| T-19-04-02 | Information Disclosure | defaults failure logging | mitigate | `job_types.py:48-54` logs only `job_type` and `type(exc).__name__`, `str(exc)` never referenced in the log call — confirmed by direct read | closed |
| T-19-04-03 | Denial of Service | defaults DB failure | mitigate | try/except per catalog entry, `job_types.py:47-58`; `tests/test_job_catalog.py:150-154` confirms 200 with `items` present, `submission_defaults` omitted on failure | closed |
| T-19-04-04 | Tampering | registration of malformed public types | mitigate | `register()` in `src/trading_platform/jobs/registry.py:92-118` validates description (nonblank, <=200 chars), `cancellation_mode` (must be `JobCancellationMode`), and `submission_defaults` (must be callable) — all checks run and raise `ValueError` before `self._handlers`/`self._submission_specs` are mutated (confirmed by direct read) | closed |
| T-19-05-01 | Information Disclosure | config_invalid failure_message | mitigate | `config_failure_message` returns only sorted dotted field paths, never expected-shape text or values, `src/trading_platform/services/config/validation.py:127-140` (confirmed by direct read); sentinel-secret absence asserted `tests/test_job_runner_preflight.py:202-223` | closed |
| T-19-05-02 | Denial of Service | worker crash on bad config | mitigate | Preflight failure is a terminal Job transition (`FAILED`/`CONFIG_INVALID`), not a process exit, `src/trading_platform/jobs/runner.py:157-181`; `tests/test_job_runner_preflight.py::test_worker_continues_after_config_invalid:226` | closed |
| T-19-05-03 | Elevation of Privilege | handler running under wrong mode | mitigate | Plan requires fail-closed when no valid `ExecutionMode` is declared (not cross-mode matching); confirmed `required_mode_preflight` in `src/trading_platform/worker/commands/run_jobs.py:23-36` checks `isinstance(mode, ExecutionMode)` and fails closed otherwise; the sole production caller (`run_jobs.py:60`) passes this preflight through `run_worker_loop`; `tests/test_backtest_job_type.py:392-394` (`test_handler_declares_backtest_mode`) confirms `BacktestJobHandler` declares `ExecutionMode.BACKTEST` and passes preflight | closed |
| T-19-05-04 | Tampering | placeholder serve loop in deploy | mitigate | `tests/test_deploy_config.py::test_no_deploy_config_starts_serve_loop:42-62` parses `docker-compose.yml`, `render.yaml`, `Dockerfile` | closed |
| T-19-06-01 | Tampering | backtest payload | mitigate | `_BacktestPayload` pydantic model `extra="forbid"`, `StrictStr` strategy_id (1-64 chars), ISO-date regex validator, `src/trading_platform/jobs/handlers/backtest_submission.py:59-81` (confirmed by direct read); registry lookup/date-order/future-date checks in `validate_payload`; called before insert at `src/trading_platform/orchestration/job_mutations.py:214` | closed |
| T-19-06-02 | Denial of Service | oversized date range | accept | Matches plan rationale: single operator, window bounded by persisted sessions, no rate limiting in scope | closed (accepted) |
| T-19-06-03 | Repudiation | Job -> run provenance | mitigate | `job_id=context.job_id` and `trigger_source="job"` passed to `run_backtest`, `src/trading_platform/jobs/handlers/backtest.py:71-72` | closed |
| T-19-06-04 | Information Disclosure | handler log context | mitigate | Handler logs only `strategy_id`/dates/`run_id`, `src/trading_platform/jobs/handlers/backtest.py:56-78`; `DatabaseJobContext.log` routes through `trading_platform.core.log_sanitizer.sanitize`, `src/trading_platform/jobs/context.py:131-132` | closed |
| T-19-06-05 | Tampering | run status rewrite after cancel | mitigate | `tests/test_backtest_job_type.py::test_handler_never_writes_run_status:396-401` asserts `"StrategyRunStatus" not in source` and `"_update_backtest_run" not in source` for the handler module (confirmed present) | closed |
| T-19-07-01 | Tampering | untrusted payload over HTTP | mitigate | `tests/test_job_operations_e2e.py::test_backtest_payload_rejections_write_nothing:256-273` asserts `after == before` on `_counts()` for each D-09 rejection case | closed |
| T-19-07-02 | Repudiation | cancel audit facts | mitigate | `tests/test_job_operations_e2e.py:308-378` asserts `cancellation_cause`, `cancellation_reason`, `cancellation_requested_at`, `cancellation_acknowledged_at` all persisted | closed |
| T-19-07-03 | Information Disclosure | broker secrets in worker boot | mitigate | Plan's stated property is exactly "worker boots with empty broker credentials" (D-22), not a secrets-scanning claim; `job_operations_env` fixture sets empty Alpaca API key/secret, `tests/test_job_operations_e2e.py:53-64`; worker boots and completes a backtest job in that fixture — matches the declared mitigation precisely | closed |
| T-19-08-01 | Tampering | duplicate submits on retry | mitigate | `postJson` requires a non-optional `idempotencyKey: string` param and sends `Idempotency-Key` header on every call, `console/src/lib/api.ts:152-166` (confirmed by direct read) | closed |
| T-19-08-02 | Information Disclosure / XSS | error copy rendering | mitigate | `console/src/lib/consoleBoundaries.test.ts` scans all non-test console source files for `dangerouslySetInnerHTML` and asserts zero matches (confirmed present at lines ~227-235) | closed |
| T-19-08-03 | Elevation of Privilege | assuming mutations enabled | mitigate | `useMutationCapability` returns `"unknown"` on catalog failure, `console/src/lib/useMutationCapability.ts:33-48`; consumers (JobsTable, BacktestJobForm, JobHeaderPanel) all gate on `state === "enabled"`, not merely "not disabled" | closed |
| T-19-08-04 | Tampering | ad-hoc fetch sites bypassing the client | mitigate | `consoleBoundaries.test.ts::"no source file other than src/lib/api.ts calls fetch("` (lines 41-58 confirmed) forbids `/\bfetch\(/` outside `src/lib/api.ts` | closed |
| T-19-08-05 | Denial of Service | polling load | mitigate | `console/src/lib/useApiQuery.ts:63-204` implements non-overlapping `setTimeout` chain, pauses on `document.hidden`, resumes on `visibilitychange` (confirmed by direct read) | closed |
| T-19-09-01 | Information Disclosure / XSS | JobsTable cells | mitigate | No `dangerouslySetInnerHTML` in `JobsTable.tsx` (grep confirmed); covered by the repo-wide boundary scan (T-19-08-02) | closed |
| T-19-09-02 | Tampering | filter values in query string | mitigate | `buildJobsEndpoint` uses `URLSearchParams` (percent-encodes equivalently to `encodeURIComponent` — functionally equivalent, plan names the latter but the control is the same class of escaping), `console/src/components/jobs/JobsTable.tsx:20-24`; backend confirmed: `status: JobStatus | None = Query(None)` at `src/trading_platform/api/dependencies.py:97` — FastAPI/pydantic returns 422 on an invalid enum value | closed |
| T-19-09-03 | Elevation of Privilege | New Job on disabled deploy | mitigate | "New Job" control gated on `capability.state === "enabled"`, else disabled, `console/src/components/jobs/JobsTable.tsx:68-85`; backend 403 guard (T-19-02-01) is authoritative | closed |
| T-19-10-01 | Information Disclosure / XSS | JobLogsPanel, JobEventsPanel | mitigate | `JobLogsPanel.test.tsx:144-160` renders an `<img onerror>` payload and asserts no `<img>` reaches the DOM. `JobEventsPanel.tsx:46` confirmed to render `event_type` as a plain `<span>{event.event_type}</span>` React text node (no interpolation into markup, no dangerouslySetInnerHTML); `JobEventsPanel.test.tsx` (96 lines, confirmed read) has no dedicated payload-injection test — its safety rests on React's default text-node escaping plus the repo-wide boundary scan (T-19-08-02) rather than a targeted assertion. The declared control (safe rendering, no HTML sink) is present and enforced structurally for both panels; evidence trail is thinner for JobEventsPanel specifically (noted, not a gap) | closed |
| T-19-10-02 | Tampering | cancel reason | mitigate | `maxLength={500}` and trim-to-null client-side, `console/src/components/jobs/CancelJobDialog.tsx:82-121`; server re-validates independently: `InvalidCancellationReasonError` raised from `src/trading_platform/orchestration/job_mutations.py:282` inside `cancel()`, surfaced as 422 `invalid_cancellation_reason` at `src/trading_platform/api/routes/jobs.py:138` — confirmed server-side check is independent of the client, not merely echoing the client's limit | closed |
| T-19-10-03 | Tampering | duplicate cancel on retry | mitigate | `CancelJobDialog.test.tsx::"reuses the same Idempotency-Key across a network-failure retry...":197-251` | closed |
| T-19-10-04 | Denial of Service | runaway has_more loop | mitigate | `MAX_PAGES_PER_TICK = 20` and the bounded `while (hasMore && pagesFetched < MAX_PAGES_PER_TICK)` loop confirmed at `console/src/components/jobs/detail/JobLogsPanel.tsx:12,97-100` by direct read | closed |
| T-19-11-01 | Tampering | payload | mitigate | Server-side `extra="forbid"` is authoritative (T-19-06-01); form sends only `{strategy_id, from_date, to_date}`, `console/src/components/jobs/new/BacktestJobForm.tsx:89-94` | closed |
| T-19-11-02 | Tampering | duplicate submissions | mitigate | `BacktestJobForm.test.tsx::"reuses the same Idempotency-Key across a network-failure retry with an identical payload, and rotates it when the payload changes":299-335` | closed |
| T-19-11-03 | Spoofing / XSS | ?type / ?strategy_id rendering | mitigate | Rendered as React text; confirmed `encodeURIComponent(item.job_type)` at `console/src/components/jobs/new/NewJobView.tsx:93` and `encodeURIComponent(strategyId)` at `console/src/components/strategy/StrategyOverviewPanel.tsx:82` | closed |
| T-19-11-04 | Elevation of Privilege | submit on disabled deploy | mitigate | Submit disabled unless `capability.state === "enabled"`, `console/src/components/jobs/new/BacktestJobForm.tsx:82,191-192`; backend 403 guard (T-19-02-01) authoritative | closed |
| T-19-12-01 | Information Disclosure / XSS | JobResultSummaryPanel, JobHeaderPanel failure_message | mitigate | React text + `JSON.stringify` for nested values, `console/src/components/jobs/detail/JobResultSummaryPanel.tsx:50`; repo-wide boundary scan (T-19-08-02) confirms no `dangerouslySetInnerHTML` anywhere in non-test console files | closed |
| T-19-12-02 | Spoofing | resource links | mitigate | `resourceHref` built only from the closed `RESOURCE_ROUTES` map, `console/src/lib/resourceRoutes.ts:9-15`; `JobResourcesPanel.tsx:33` uses `resourceHref(resource.kind, resource.id)`, never `links.self` | closed |
| T-19-12-03 | Elevation of Privilege | cancel trigger on disabled deploy | mitigate | Cancel button `disabled={capability.state !== "enabled"}`, `console/src/components/jobs/detail/JobHeaderPanel.tsx:74`; backend 403 guard (T-19-02-01) authoritative | closed |
| T-19-12-04 | Denial of Service | polling | mitigate | `pollIntervalMs: 3000`, `console/src/components/jobs/detail/JobDetailView.tsx:32`; `JobDetailView.test.tsx::"polls every 3s while non-terminal and stops the instant status becomes terminal":277-316` | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| R-19-01 | T-19-01-03 | Single-operator deploy; `ALTER TYPE ADD VALUE` migration runs at container start (`Dockerfile:32`) before traffic is served, so the lock window has no concurrent readers/writers to contend with | plan-time (19-01-PLAN.md threat_model) | 2026-09-26 |
| R-19-02 | T-19-01-04 | Job rows are never deleted by application code (cancellation never deletes, Phase 18 D-11); `ON DELETE SET NULL` only protects against manual DB cleanup, an operator-controlled action | plan-time (19-01-PLAN.md threat_model) | 2026-09-26 |
| R-19-03 | T-19-02-04 | `reason` in the 422 response is a stable machine code drawn from a closed enum (no payload echo, no secret values) | plan-time (19-02-PLAN.md threat_model) | 2026-09-26 |
| R-19-04 | T-19-02-05 | No authentication is out of scope per roadmap; the ORCH-07 `mutations_enabled` flag is the only deploy-exposure control for this phase | plan-time (19-02-PLAN.md threat_model) | 2026-09-26 |
| R-19-05 | T-19-03-01 | `resources[]` exposes only run id/status/relative link — data already public via the existing `/api/v1/runs` endpoint | plan-time (19-03-PLAN.md threat_model) | 2026-09-26 |
| R-19-06 | T-19-06-02 | Single operator; the date-range window is bounded by persisted trading sessions; the roadmap has no rate limiting in scope for this phase | plan-time (19-06-PLAN.md threat_model) | 2026-09-26 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-09-26 | 48 | 48 | 0 | gsd-security-auditor (prior draft, superseded — treated as unverified lead list per this run's adversarial re-check) |
| 2026-09-27 | 48 | 48 | 0 | gsd-security-auditor |

---

## Informational Notes (non-blocking)

- **Unregistered flags:** none. No `19-*-SUMMARY.md` contains a `## Threat Flags` section. `19-REVIEW.md`'s `files_reviewed_list` (79 files) was cross-checked against the twelve `19-*-SUMMARY.md` key-files declarations during this run; no attack surface was found outside the threat register.
- **T-19-09-02 evidence caveat:** the plan's mitigation text names `encodeURIComponent`; the implementation instead builds the query string with `URLSearchParams.set()/.toString()`, which percent-encodes equivalently. Treated as closed on functional-equivalence grounds; the control class differs from the literal pattern named in the plan.
- **T-19-10-01 evidence caveat:** `JobLogsPanel` has a dedicated XSS-payload render test (`<img onerror>` -> no `<img>` in DOM). `JobEventsPanel` has no equivalent dedicated test (confirmed by direct read of its 96-line test file); its safety rests on React's default text-node escaping (confirmed: `<span>{event.event_type}</span>`, no interpolation into markup) plus the repo-wide `dangerouslySetInnerHTML` boundary scan (T-19-08-02). The declared control is present and enforced structurally for both panels; the evidence trail is thinner for JobEventsPanel specifically. Not treated as a gap because the structural mitigation (no HTML sink, text-only rendering) is verified in code, not merely inferred.
- **`.env.example:18`** sets `TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED=true` for local development. Intentional (local dev needs mutations to exercise the feature); does not affect the production default (`Settings.orchestration.mutations_enabled: bool = False`; `render.yaml` sets `"false"` explicitly for the deploy). Flagged for awareness only.
- **Methodology note:** this audit independently re-derived evidence from the twelve `19-*-PLAN.md` `<threat_model>` blocks (the authoritative mitigation text) rather than trusting the pre-existing untracked `19-SECURITY.md` draft found at session start. All 48 threats were classified by disposition, and every `mitigate` threat's cited file/line and named test (where the plan names one) was independently opened and read. Weak/ambiguous closures (T-19-05-03, T-19-07-03, T-19-10-01, T-19-03-03, T-19-10-02, T-19-09-02, and code-only closures with no cited test: T-19-08-01/03/05, T-19-09-03, T-19-10-04, T-19-11-04, T-19-12-03, T-19-04-02, T-19-06-04) received targeted re-reads against the plan's exact stated property, not the prior draft's paraphrase. No discrepancy between the prior draft and the live code was found.

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-09-27
