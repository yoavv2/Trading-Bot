---
phase: 20-complete-operation-migration-safety-controls
part: A-jobs
reviewed: 2026-09-28T00:00:00Z
depth: standard
files_reviewed: 22
files_reviewed_list:
  - alembic/versions/0021_phase20_operations_safety.py
  - src/trading_platform/jobs/contracts.py
  - src/trading_platform/jobs/dependencies.py
  - src/trading_platform/jobs/handlers/broker_order_sync.py
  - src/trading_platform/jobs/handlers/broker_order_sync_submission.py
  - src/trading_platform/jobs/handlers/domain_conflicts.py
  - src/trading_platform/jobs/handlers/ingest_bars.py
  - src/trading_platform/jobs/handlers/ingest_bars_submission.py
  - src/trading_platform/jobs/handlers/paper_session.py
  - src/trading_platform/jobs/handlers/paper_session_submission.py
  - src/trading_platform/jobs/handlers/payload_fields.py
  - src/trading_platform/jobs/handlers/reconciliation.py
  - src/trading_platform/jobs/handlers/reconciliation_submission.py
  - src/trading_platform/jobs/handlers/risk_evaluation.py
  - src/trading_platform/jobs/handlers/risk_evaluation_submission.py
  - src/trading_platform/jobs/handlers/sync_market_sessions.py
  - src/trading_platform/jobs/handlers/sync_market_sessions_submission.py
  - src/trading_platform/jobs/handlers/sync_symbol_metadata.py
  - src/trading_platform/jobs/handlers/sync_symbol_metadata_submission.py
  - src/trading_platform/jobs/registry.py
  - src/trading_platform/jobs/runner.py
  - src/trading_platform/orchestration/job_mutations.py
findings:
  critical: 1
  warning: 5
  info: 5
  total: 11
status: issues_found
---

# Phase 20 (Part A: Jobs framework, handlers, orchestration, migration): Code Review Report

**Reviewed:** 2026-09-28
**Depth:** standard (diff `961cdab..HEAD` plus full-file context; call sites in `services/`, `api/routes/jobs.py` and `jobs/queue.py`, `jobs/cancellation.py` read to judge behavior)
**Files Reviewed:** 22
**Status:** issues_found

## Summary

The core mechanisms are sound. Checked and found correct:

- **Cancel vs. claim race (D-02):** `cancel()` row-locks the Job (`FOR UPDATE`). `claim_next_job` uses `FOR UPDATE SKIP LOCKED`. The QUEUED_ONLY rejection happens before any write, so a rejected cancel writes zero rows.
- **Retry lineage uniqueness (D-17):** enforced by `uq_jobs_retry_of_job_id`. The naming convention in `db/base.py` matches the migration's constraint names.
- **Retry-block predicate (D-19):** `Job.completed_at` is the correct column for the context's "finished_at".
- **Handlers:** all call services only, and none hold a DB transaction across `handler.run`.
- **`external_` log:** for the paper-session and broker-sync handlers it is written before the service call.

The main defect is a validation gap that violates D-25. Session-scoped payloads with dates outside the exchange calendar's window escape as untyped exceptions, which is a 500 on submit and on retry. Beyond that there is:

- one idempotency race in retry;
- an `ingest-bars` failure-semantics regression against the retired CLI, and an inconsistency with `sync-symbol-metadata`;
- unbounded date ranges that fail only at run time;
- a non-reversible migration downgrade;
- a runner-level hard-coded certainty claim.

No security vulnerabilities were found. SQL uses bound parameters, and the payload JSON path comparison is parameterized.

## Critical Issues

### CR-A-01: Out-of-calendar `as_of_session` escapes `validate_payload` as an untyped exception (500), on both submit and retry

**File:** `src/trading_platform/jobs/handlers/payload_fields.py:187` (also `src/trading_platform/orchestration/job_mutations.py:519-524`)
**Issue:** `require_trading_session_not_future` calls `is_trading_session()`. `exchange_calendars` only knows a rolling window: currently 2006-09-28 through 2027-09-28. Outside it, `cal.is_session()` raises `DateOutOfBounds` (a `ValueError`), and for years before ~1677 it raises `OverflowError`. Neither is an `InvalidJobPayloadError`.

Reproduced with `RiskEvaluationSubmissionSpec(load_settings()).validate_payload({"strategy_id": "trend_following_daily", "as_of_session": "2001-01-02"})`, which raises `DateOutOfBounds`. The value `"0001-01-01"` raises `OverflowError`.

The future check runs first, so only past dates are affected. This hits all four session-scoped types: `risk-evaluation`, `paper-session`, `reconciliation` and `broker-order-sync`. `api/routes/jobs.py` catches only `InvalidJobPayloadError`, so `POST /api/v1/jobs` returns an unhandled 500. `JobOrchestrationService.retry()` catches only `InvalidJobPayloadError` around `spec.validate_payload(original.payload)`, so the retry path has the same escape. This violates D-25 ("every rejection has a stable machine-readable reason") and D-18 (typed 422).

The calendar window also rolls forward with time, so a payload that was valid when stored can become invalid at retry time. That case is exactly what D-18 says must return a typed 422.

**Fix:** Add a closed rejection value (e.g. `AS_OF_SESSION_OUT_OF_CALENDAR_RANGE`) to `PayloadFieldRejection` and every session-scoped spec's rejection enum. Map the exception in the shared helper:
```python
try:
    is_session = is_trading_session(as_of_session, settings.market_data.calendar.exchange)
except (ValueError, OverflowError) as exc:  # DateOutOfBounds subclasses ValueError
    raise InvalidJobPayloadError(
        job_type=job_type,
        reason=PayloadFieldRejection.AS_OF_SESSION_NOT_TRADING_SESSION.value,  # or a new dedicated value
    ) from exc
if not is_session:
    raise InvalidJobPayloadError(...)
```
Add a parametrized test per session-scoped type for `2001-01-02` and `0001-01-01`, and a retry-path test.

## Warnings

### WR-A-01: Concurrent same-key retry returns `retry_exists` (409) instead of an idempotent replay (200)

**File:** `src/trading_platform/orchestration/job_mutations.py:492-513`
**Issue:** `retry()` checks `_existing_outcome` before it takes the row lock on the original Job (`_require_job(..., lock=True)` at line 501). Take two concurrent requests with the same `Idempotency-Key`, for example a double-click:

1. Request B passes the pre-lock replay check, because A has not committed.
2. B then blocks on A's row lock.
3. A commits the retry Job and its `JobMutation`.
4. B acquires the lock, finds `existing_retry_id`, and raises `RetryAlreadyExistsError`.

B gets 409 `retry_exists` for what D-16 requires to be an exact replay (`200` plus `Idempotency-Replayed: true`). The `uq_jobs_retry_of_job_id` handler further down is unreachable for this race because the lock serializes the two requests. `cancel()` does not have this problem: its post-lock path falls through to the `JobMutation` unique violation and replays.

**Fix:** Re-run the replay check immediately after acquiring the lock, before the status and existing-retry checks:
```python
original = self._require_job(session, job_id, lock=True)
existing = self._existing_outcome(session, endpoint_id=RETRY_ENDPOINT_ID, key=key, fingerprint=fingerprint)
if existing is not None:
    return existing
```
Add a two-connection test that races two same-key retries and asserts one 202 and one 200 replay.

### WR-A-02: `ingest-bars` records a fully or partially failed ingestion as SUCCEEDED, unlike the retired CLI and unlike `sync-symbol-metadata`

**File:** `src/trading_platform/jobs/handlers/ingest_bars.py:89-96` (compare `sync_symbol_metadata.py:73`)
**Issue:** The retired `scripts/ingest_polygon_bars.py` (`git show 961cdab:scripts/ingest_polygon_bars.py`) ended with `if not result.succeeded: sys.exit(1)`. `ingest_daily_bars` swallows each per-symbol exception into `failed_symbols`. When every symbol fails, for example a bad or expired Polygon key, the Job is still `SUCCEEDED` with `ingestion_succeeded: false` and `bars_upserted: 0`.

That hides operational failures from status filters and from the Phase 21 global failure indicator. It is also inconsistent within Phase 20: `sync-symbol-metadata` raises on any failed ticker and lands FAILED, citing ORCH-02 exit-1 parity. This is a Job-level failure-reason mapping (domain_conflict vs handler_error), not a domain decision like a blocked paper session (D-05), so it is not covered by invariant 2's "never reinterpret".

**Fix:** After the completion log and the post-call cancel checkpoint, raise a typed handler error when `not result.succeeded`, naming the failed symbols. This mirrors `raise_for_failures()` for metadata sync. The `MarketDataIngestionRun` already holds per-symbol detail and stays linked via `job_id`. If SUCCEEDED-with-flag is intentional, record the decision in CONTEXT and apply it to metadata sync as well so the two types are consistent.

### WR-A-03: Date ranges for `ingest-bars` and `sync-market-sessions` are unbounded, and pre-window dates fail at run time instead of being rejected at submit

**File:** `src/trading_platform/jobs/handlers/payload_fields.py:194-217`
**Issue:** `require_date_range` checks only `from_date <= to_date` and `to_date <= today`. `sync_market_sessions` reaches `sessions_in_range()`. That raises `DateOutOfBounds` for a `from_date` before 2006-09-28 and `OverflowError` for year 1, both verified. The submit therefore returns 202, and the Job later lands `FAILED/handler_error` with a raw exception string. For `ingest-bars`, an arbitrarily old `from_date` combined with up to 500 symbols becomes an unbounded number of Polygon requests. D-25 requires strict schemas where every rejection has a stable machine-readable reason.

**Fix:** In `require_date_range`, reject dates outside a sane window with a new closed value (e.g. `from_date_out_of_range`). For `sync-market-sessions` the window should be the calendar's bounds, `get_calendar(exchange).first_session`/`last_session`. For `ingest-bars`, cap it at a configured maximum lookback or span. Add rejection tests.

### WR-A-04: Migration `downgrade()` fails as soon as any paper-session Job has run, and silently discards ingestion-run linkage

**File:** `alembic/versions/0021_phase20_operations_safety.py:77`
**Issue:** `downgrade()` recreates `uq_strategy_runs_job_id`. Upgrade removed that constraint precisely so a `paper-session` Job can link two `strategy_runs` (internal reconciliation and execution) to one `job_id` (D-07/D-08). After even one such Job, `CREATE UNIQUE CONSTRAINT` fails with a duplicate-key error, so the downgrade aborts. Dropping `market_data_ingestion_runs.job_id` also discards the Job linkage with no warning. The enum no-op is documented and fine; this is not.

**Fix:** Before recreating the unique constraint, NULL out all but one `job_id` per group:
```python
op.execute("""
    UPDATE strategy_runs SET job_id = NULL
    WHERE id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY created_at, id) AS rn
            FROM strategy_runs WHERE job_id IS NOT NULL
        ) t WHERE t.rn > 1
    )
""")
```
Alternatively, raise a clear error when duplicates exist. Document the lossy linkage in the migration docstring, and add an up/down/up test that includes a two-run Job.

### WR-A-05: Runner hard-codes `outcome_uncertain=False` for every `domain_conflict` using paper-session-specific lock-ordering knowledge

**File:** `src/trading_platform/jobs/runner.py:313-332` (with `jobs/contracts.py:34-47`, `jobs/handlers/domain_conflicts.py:21`)
**Issue:** The runner justifies the pinned `False` with a comment about `run_paper_order_submission`'s advisory lock preceding broker submission. But `runner.py` is deliberately domain-agnostic (JOB-04), and `DOMAIN_CONFLICT_EXCEPTIONS` is an extensible tuple. Anyone who adds a translated exception that can fire after a broker or other external side effect gets a Job recorded as certain, so D-19's reconcile-first block never engages.

The safety property lives in a comment in the runner, but its true owner is the handler and the domain exception. Also, `translate_domain_conflicts` is applied only to `paper-session`, so this is coupled to one call site.

**Fix:** Make the signal carry the claim explicitly. Give `JobDomainConflictError` an `outcome_uncertain: bool` (required or defaulting to `True`, the safe direction). Have `translate_domain_conflicts` (or the handler) set `False` for `ConcurrentRunLockedError`, and have the runner pass `exc.outcome_uncertain` through. Then adding a member to `DOMAIN_CONFLICT_EXCEPTIONS` forces a conscious decision.

## Info

### IN-A-01: `retry_prerequisite_job_type` is never checked against registered types

**File:** `src/trading_platform/jobs/registry.py:131-139`
**Issue:** `register()` validates only that the value is `None` or a non-blank string. A typo, or a prerequisite type that is later removed, makes the D-19 block permanently unsatisfiable. Existing tests pin the value `"reconciliation"` per spec but not referentially.
**Fix:** At the end of `build_default_registry`, assert every declared prerequisite is in `registry.list_job_types()`. Optionally require that the prerequisite spec is strategy-scoped.

### IN-A-02: `sync-market-sessions` rejects a future `to_date`, which the retired command allowed

**File:** `src/trading_platform/jobs/handlers/sync_market_sessions_submission.py:110`, `payload_fields.py:212-217`
**Issue:** The old `run_sync_sessions` accepted any `--to-date`. It only defaulted to yesterday. The new spec copies backtest's `to_date_in_future` rule. An operator therefore cannot pre-load upcoming trading sessions. The plan lists this rejection value, but D-25 did not, so this is an unrecorded behavior narrowing.
**Fix:** Either allow `to_date` up to the calendar's `last_session` for this type, or record the narrowing as an explicit decision and note it in the console form help text.

### IN-A-03: Seven near-identical spec/handler modules and an unused `_clock`

**File:** `src/trading_platform/jobs/handlers/*_submission.py` (`_strip_strategy_id` and `_parse_as_of_session` validators, `_default_clock`, and the try/except-to-`InvalidJobPayloadError` block repeated per spec); `sync_symbol_metadata_submission.py:56,83-85`
**Issue:** The `strategy_id` strip validator and the validation-error mapping are copied across five session-scoped specs. `SyncSymbolMetadataSubmissionSpec` stores `_clock` and defines `_default_clock` but never uses either. Drift risk is real, because a fix like CR-A-01 must be applied in five places.
**Fix:** Move the shared pydantic base (`strategy_id`/`as_of_session` fields and validators) and the try/except mapping into `payload_fields.py`. Remove the unused clock from the metadata spec if the constructor signature is not required uniform.

### IN-A-04: `retry()` revalidates the payload while holding the Job row lock and opening extra pool connections

**File:** `src/trading_platform/orchestration/job_mutations.py:519-524`
**Issue:** `validate_payload` may call `is_eligible_risk_run`, which opens its own `session_scope`. That runs inside the outer transaction that holds `FOR UPDATE` on the original Job. It cannot deadlock, since the inner call only reads other tables. It does hold two connections per retry request and lengthens the lock window. `validate_payload` may also raise anything a DB read raises, and only `InvalidJobPayloadError` is translated.
**Fix:** Revalidate before taking the lock, using a plain read of `payload` and `job_type`. Then re-check status under the lock. This also lets the post-lock section stay short.

### IN-A-05: `normalize_symbols` folds non-ASCII input into ASCII tickers via `str.upper()`

**File:** `src/trading_platform/jobs/handlers/payload_fields.py:96`
**Issue:** `"ß".upper() == "SS"`, `"ı".upper() == "I"` and `"ﬁ".upper() == "FI"`. Non-ASCII characters can therefore pass `SYMBOL_PATTERN` after upper-casing and be stored as a different ticker than the operator typed. This is low impact for an operator-only surface, but it undermines the "ticker format whitelist" intent of T-20-03-01.
**Fix:** Reject non-ASCII input before folding, e.g. `if not item.isascii(): raise PydanticCustomError(INVALID_SYMBOL, ...)`, or apply the regex to the stripped-but-not-upper-cased value with `re.IGNORECASE` and then upper-case.

---

_Reviewed: 2026-09-28_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
