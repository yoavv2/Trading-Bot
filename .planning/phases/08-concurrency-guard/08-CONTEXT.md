# Phase 8: Concurrency Guard - Context

**Gathered:** 2026-07-12
**Status:** Ready for planning

<domain>
## Phase Boundary

At most one active run per `(strategy_id, session_date)` can execute side effects. A PostgreSQL advisory lock keyed on that tuple is acquired BEFORE any broker call or state-affecting DB write, is released on all exit paths including crash, and stale runs (a `running` row past a declared timeout) are detectable via a single query and cleanly reclaimed.

Requirements: LOCK-01, LOCK-02, LOCK-03, LOCK-04, LOCK-05, LOCK-06.

This phase is concurrency control only. It does not rewrite reconciliation (Phase 9), harden startup (Phase 10), or add new product capabilities. Reconciliation is leaned on as-is.

</domain>

<decisions>
## Implementation Decisions

### Stale detection (LOCK-04)
- **Mechanism:** fixed wall-clock timeout on `started_at`. No heartbeat column, no periodic liveness writes. Stale = `status = running AND now() - started_at > threshold` — a single SQL predicate.
- **Threshold:** 30 minutes. A daily paper session finishes in minutes; 30m is a generous margin over Polygon/Alpaca latency + retries so a live run is never falsely flagged.
- **Configuration:** externalized in the execution/safety settings block (alongside `repeated_failure_threshold`), not a hardcoded constant. Matches the config-externalization principle; tunable without a code change.
- **Representation:** add a `STALE` value to the closed `StrategyRunStatus` enum (currently `pending/running/succeeded/failed`). Run liveness stays a single authoritative closed-enum column. Requires a DB enum migration.

### Stale-run handling (LOCK-05)
- When the lock is free but a stale `running` row exists: flip it to `STALE`, record a durable `ExecutionEvent` noting the reclaim (explicit over silent, per Phase 7), then the new run continues.
- **Residual broker state:** do NOT special-case it in Phase 8. The reclaiming run reuses the same `client_order_id`s (Phase 7 idempotency) so it cannot double-submit; the existing paper-session reconcile step syncs broker truth. No active re-sync of the stale run's orders in this phase.
- **Detection timing:** lazy — staleness is resolved when a new run for the tuple starts and finds the old row. No background job.
- **Multiplicity:** mark ALL past-threshold `running` rows for the tuple `STALE` (idempotent cleanup), not just the most recent.

### Lock-denied path (LOCK-01, LOCK-02)
- **Acquisition:** non-blocking try-lock (`pg_try_advisory_lock`). If held, return false immediately and exit — a scheduled job never hangs; "exits cleanly" is literal.
- **Loser persists NO DB row.** The losing attempt writes zero rows, keeping success-criterion 1 a crisp testable invariant ("second process performs no DB writes before the lock is confirmed"). The denial is auditable via a WARNING structured log naming the tuple and that another session holds the lock.
- **Typed denial:** a dedicated exception class (e.g. `ConcurrentRunLockedError`) carrying `strategy_id` + `session_date` and a human-readable message. Callers/tests assert the class, not a string.
- **Exit code:** a dedicated non-zero exit code (distinct from generic failure), no traceback — so a scheduler/operator can tell "another run holds it" apart from a real crash.

### Lock scope & crash-release (LOCK-01, LOCK-02, LOCK-06)
- **Boundary:** acquire at the submission entrypoint `run_paper_order_submission` — the single path that makes broker calls + order writes. `run-paper-session` inherits the guard (it calls submission) and a direct `submit-paper-orders` is guarded too. One acquisition site, no re-entrancy juggling.
- **Hold mechanism:** session-level `pg_advisory_lock` on ONE dedicated connection held open for the whole guarded run; broker calls happen OUTSIDE any DB transaction. Crash → connection drops → Postgres auto-releases the lock (satisfies LOCK-06). This replaces the current "new `session_scope` per operation" structure inside the guarded region.
- **NOT** transaction-level (`pg_advisory_xact_lock`) — that would force slow broker network calls to sit inside one open DB transaction.
- **Read flows excluded:** reconciliation and `sync-paper-state` do NOT take the lock. They are read-mostly and Phase 9 rewrites reconciliation; keeping them lock-free avoids coupling Phase 8 to that rewrite and keeps the guarded region tight around actual side effects.

### Claude's Discretion
- Advisory-lock key derivation: hashing `(strategy_id uuid, session_date)` down to the `bigint` / two-`int4` key space Postgres advisory locks require (collision handling, hash choice).
- Exact `ConcurrentRunLockedError` module location, message wording, and the specific reserved exit-code integer.
- Exact `ExecutionEvent` action name/payload shape for the stale-reclaim event.
- Migration mechanics for adding `STALE` to the `strategy_run_status` PG enum.
- The precise dedicated-connection acquisition/teardown pattern (context manager shape) around the guarded region.

</decisions>

<specifics>
## Specific Ideas

- The LOSER of the lock is the invariant anchor: "a second process performs zero DB writes and makes zero broker calls before the lock is confirmed." Keep this literally true and directly testable.
- Prefer the guarded region to hold a dedicated connection and keep broker I/O outside any DB transaction — crash-release comes from the connection dropping, not from transaction rollback.
- Reuse Phase 7's `StrategyRun` + `ExecutionEvent` audit pattern for the stale-reclaim note; do not invent a parallel audit channel.

</specifics>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/trading_platform/services/paper_execution.py` — owns `run_paper_order_submission` (the chosen lock boundary), `run_paper_session`, `sync_paper_state`. Already loads kill-switch + control state and creates/updates the `StrategyRun` row.
- `src/trading_platform/db/models/strategy_run.py` — `StrategyRunStatus` closed enum (`pending/running/succeeded/failed`) to extend with `STALE`; `status`, `started_at` (server_default `now()`), `completed_at` already present. Existing index `ix_strategy_runs_strategy_id_status`.
- `src/trading_platform/db/models/execution_event.py` — durable audit rows for the stale-reclaim note (Phase 7 pattern).
- Phase 7 `client_order_id` material-intent idempotency (`services/order_identity.py`) — the reason a reclaiming run cannot double-submit residual orders.
- Persistent kill-switch (`load_kill_switch_state`, `system_control` model) — the guarded run must still honor it after acquiring the lock.

### Established Patterns
- Config is externalized via `core/settings.py` (`execution.safety.*`); the stale threshold belongs there.
- Auditability = `StrategyRun` root + `ExecutionEvent` rows; explicit notes over silent skips (Phase 7 principle).
- Closed enums as typed invariants (StrategyRunStatus, order lifecycle) — `STALE` extends that, no free strings.
- Worker commands: `submit-paper-orders`, `run-paper-session`, `reconcile-paper-execution` in `worker/__main__.py`.

### Integration Points — REQUIRED REORDERING
- **Current ordering is LOCK-02/LOCK-03-violating:** `run_paper_order_submission` calls `_create_paper_execution_run` (inserts a `pending` row) BEFORE the kill-switch/disabled checks and before any lock. LOCK-02 forbids state writes before the lock; LOCK-03 requires `status=running` + `started_at` to be the FIRST persisted action after lock acquisition. The planner must reorder to: **acquire advisory lock → (only then) first persisted write is the run row at `status=running`/`started_at` → then kill-switch/disabled handling.** The pre-lock `pending` insert must move or go away.
- Current code opens a fresh `session_scope(resolved_settings)` per operation (see `paper_execution.py:312`); the session-level advisory lock requires a single connection held across the guarded region — a structural change to how sessions/connections are managed inside `run_paper_order_submission`.
- No advisory-lock code exists anywhere in the repo today (greenfield for this mechanism).

</code_context>

<deferred>
## Deferred Ideas

- Active heartbeat / liveness column for sub-timeout hang detection — not needed for a serial single-operator daily job.
- Operator-visible "list currently-stale runs" query/report surface — lazy reclaim satisfies LOCK-05 without it.
- Active re-sync/reconcile of an abandoned run's orders at reclaim time — belongs with the Phase 9 reconciliation rewrite.
- Extending the advisory lock to reconciliation/sync read flows — reconsider during Phase 9.

</deferred>

---

*Phase: 08-concurrency-guard*
*Context gathered: 2026-07-12*
