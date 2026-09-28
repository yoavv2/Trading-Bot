---
phase: 20-complete-operation-migration-safety-controls
reviewed: 2026-09-28
depth: standard
diff_base: 961cdab
status: issues_found
files_reviewed: 81
scope_note: "81 changed source files (tests excluded; 57 test files pass: 956 pytest / 262 vitest). Split into 3 parallel reviewer parts."
parts:
  - 20-REVIEW-part-A-jobs.md
  - 20-REVIEW-part-B-services-api.md
  - 20-REVIEW-part-C-console.md
findings:
  critical: 2
  warning: 19
  info: 18
  total: 39
---

# Phase 20 Code Review (merged)

Standard-depth review of every source file changed since `961cdab` (phase start), split across three reviewers. Full findings with file:line, rationale and suggested fixes live in the part files; this file is the index and severity roll-up.

| Part | Scope | Files | Critical | Warning | Info |
|------|-------|-------|----------|---------|------|
| A | Job framework, handlers, orchestration, migration 0021 | 22 | 1 | 5 | 5 |
| B | Services, HTTP API (controls, jobs), DB models, worker CLI, Makefile/README | 29 | 1 | 6 | 7 |
| C | Console (controls UI, retry UI, Job forms, api.ts) | 30 | 0 | 8 | 6 |
| **Total** | | **81** | **2** | **19** | **18** |

## Critical

### CR-A-01: `as_of_session` outside the exchange-calendar window escapes validation as a 500
`jobs/handlers/payload_fields.py:187` — `is_trading_session` raises `DateOutOfBounds` (dates before ~2006-09-28 or beyond the rolling window) or `OverflowError` (year 1). Submit and `retry()` catch only `InvalidJobPayloadError`, so the API returns 500 instead of a typed 422 (D-25, D-18). Affects risk-evaluation, paper-session, reconciliation and broker-order-sync. Reproduced by the reviewer. Fix: translate calendar bound errors into a closed payload rejection.

### CR-B-01: `ingest_daily_bars` failure path rolls back its own audit row
`services/ingestion.py:216-283` — the run row, the bar upserts and `_finish_run` share one `session_scope`. The outer `except` writes `failed` and then re-raises, which rolls back the failed run row together with its `job_id`. A failed `ingest-bars` Job therefore shows empty `resources[]` and has no ingestion audit row, which conflicts with D-08/D-09.
*Orchestrator note:* the single-transaction structure predates Phase 20; 20-05 only threaded `job_id` through it. Phase 20 is what makes the gap visible through the Job read model. Fix: commit the run row first and finalize it in its own transaction.

## Warnings (summary — details in part files)

- **WR-A-01:** a concurrent same-key retry can return 409 `retry_exists` instead of a 200 replay, because the replay check runs before the row lock.
- **WR-A-02:** `ingest-bars` reports SUCCEEDED even when every symbol fails, for example with a bad Polygon key.
- **WR-A-03:** `ingest-bars` and `sync-market-sessions` date ranges have no bounds; dates before 2006 fail only at run time.
- **WR-A-04:** migration 0021 `downgrade()` fails once any Job links two runs, and it silently drops the ingestion-run links.
- **WR-A-05:** the runner pins `outcome_uncertain=False` for every `domain_conflict`; the flag should be carried on the exception.
- **WR-B-01:** a no-op kill-switch PUT still overwrites `last_changed_at`, actor, reason and run id.
- **WR-B-02:** the control paths take no `FOR UPDATE`, so concurrent PUTs both report `changed:true`. A first-use `ensure_strategy_record` race can return 500.
- **WR-B-03:** ARCHIVED is reported as "disabled", and `PUT enabled` silently un-archives.
- **WR-B-04:** a NUL byte in `reason`, deeply nested JSON, a missing kill-switch row and DB errors all produce non-JSON 500s.
- **WR-B-05:** kill-switch trip, including the break-glass CLI, depends on the strategy registry loading `trend_following_daily`.
- **WR-B-06:** for a strategy with no DB row, `get_strategy_state` defaults to ACTIVE while analytics uses `metadata.enabled`. Also logged in deferred-items.md.
- **WR-C-01:** Escape and "Keep Current State" stay active while a control PUT is in flight, and a late response can apply to a reopened dialog.
- **WR-C-02:** after a transport failure or 5xx on a control PUT, no change event fires, so the banner may be stale if the server committed the change.
- **WR-C-03:** a 2xx response with an unparseable body becomes `ok:true, data:null`, and callers then throw.
- **WR-C-04:** control 5xx and non-JSON failures show "Request rejected — check the input…". This matches the UI-SPEC literally but misleads during an outage, so the spec row may need an amendment.
- **WR-C-05:** the dialogs have no focus management or focus trap, no `role="alert"` on errors, and no `aria-describedby`.
- **WR-C-06:** an open "Already X" notice unmounts if the follow-up state refetch fails.
- **WR-C-07:** a `?strategy_id=` deep link with no matching option still enables Submit and sends the untrimmed value.
- **WR-C-08:** `crypto.randomUUID()` is unguarded, so it crashes outside secure contexts, and it is evaluated on every render.

## Info

18 items (IN-A-01..05, IN-B-01..07, IN-C-01..06) — stale docstrings/README lines, dead code, duplicated boilerplate, unused Makefile vars, minor a11y/UX polish. See part files.

## Next steps

```
/gsd:code-review 20 --fix   — auto-fix Critical + Warning
```
