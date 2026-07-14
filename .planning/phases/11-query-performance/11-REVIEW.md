---
status: issues_found
phase: 11
depth: standard
files_reviewed: 9
critical: 0
warning: 2
info: 1
total: 3
reviewed_at: 2026-07-14
---

# Phase 11 Code Review

## Scope

Reviewed the Phase 11 behavior and cross-file call chains in exactly these nine files:

- `alembic/versions/0017_phase11_query_performance_indices.py`
- `src/trading_platform/services/paper_execution.py`
- `src/trading_platform/services/reconciliation_matcher.py`
- `tests/support/__init__.py`
- `tests/support/query_counter.py`
- `tests/test_paper_execution.py`
- `tests/test_paper_preflight_query_count.py`
- `tests/test_query_index_usage.py`
- `tests/test_reconciliation_matcher.py`

The independently supplied PostgreSQL gate passed 34/34:

```text
PYTHONPATH=src .venv/bin/pytest tests/test_query_index_usage.py tests/test_paper_execution.py -q
```

Focused bytecode compilation over the Python files also completed without errors. The findings below concern reliability and proof coverage rather than a currently reproduced functional failure.

## Findings

### W1 — Query counting is engine-wide even when the caller supplies one Session

- **Severity:** Warning
- **Introduced by:** Phase 11 (`6685daf`)
- **Evidence:** `tests/support/query_counter.py:28-38` deliberately resolves every accepted bind to its underlying `Engine`, and `tests/support/query_counter.py:49-60` installs `before_cursor_execute` on that engine. `tests/test_paper_preflight_query_count.py:90-99` passes a single Session and treats the resulting count as that preflight's exact statement count.
- **Impact:** Any other Session using the cached engine in the same process while the context is open contributes statements to the counter. The helper's API and docstring say it counts against the supplied bind, but a Session/Connection call is not isolated to that bind. Concurrent test execution, background database work, or nested helpers can therefore create false PERF-01 failures (and potentially mask attribution when debugging the captured SQL). The current sequential focused gate does not exercise this condition.
- **Suggested fix:** Preserve the caller's scope: for a Session, acquire/listen on its active `Connection`; for a Connection, listen/filter on that connection; retain engine-wide behavior only when the caller explicitly passes an Engine. Alternatively keep one engine listener but filter callbacks by the target connection identity. Add a regression test with two Sessions sharing one Engine and prove only the supplied Session is counted.

### W2 — The broker-fill EXPLAIN proof covers two IDs, not the production chunk boundary

- **Severity:** Warning
- **Introduced by:** Phase 11 gap closure (`19cc65c`)
- **Evidence:** Production deduplication may place up to 1,000 IDs in one predicate (`src/trading_platform/services/paper_execution.py:68`, `src/trading_platform/services/paper_execution.py:2037-2049`), and the normal broker page can contain hundreds of fills. The EXPLAIN test uses only two predicate values (`tests/test_query_index_usage.py:449-453`). PostgreSQL plan selection is cardinality/cost dependent, so identical SQL shape at two values does not prove the named index remains selected at 500 or at the configured 1,000-ID boundary. The separate chunking test (`tests/test_paper_execution.py:1337-1372`) checks statement count and bound values, but never EXPLAINs those statements.
- **Impact:** PERF-03 can remain green even if PostgreSQL switches the largest production chunk to a sequential scan. That would not break correctness, but it would contradict the phase's explicit no-large-table-Seq-Scan claim at the workload size the new chunking code permits.
- **Suggested fix:** Parameterize the scratch-database EXPLAIN test over representative production cardinalities (for example 1, 500, and `_PAPER_FILL_DEDUP_CHUNK_SIZE`), using existing and missing IDs in realistic proportions, and assert `uq_paper_fills_broker_fill_id` plus no `Seq Scan on paper_fills` for every supported cardinality. If PostgreSQL legitimately changes plans near 1,000, lower the chunk size or narrow the requirement to a measured/selective operating range.

### I1 — Migration 0017's durable audit narrative describes the pre-gap-closure query

- **Severity:** Info
- **Introduced by:** Phase 11 gap closure left earlier Phase 11 text stale (`67030e1` followed by `6b238c0`/`19cc65c`)
- **Evidence:** `alembic/versions/0017_phase11_query_performance_indices.py:19-25` still says broker-fill deduplication is an unconditional full-column read for which PostgreSQL correctly chooses a Seq Scan and points readers to the current EXPLAIN test. The production query is now selective (`src/trading_platform/services/paper_execution.py:2042-2045`), while that test now asserts the opposite plan (`tests/test_query_index_usage.py:435-457`). Lines 27-32 call the migration a durable record of the phase audit, making the contradiction especially misleading.
- **Impact:** No runtime or migration failure, but future maintainers investigating PERF-03 receive mutually inconsistent guidance from the migration and executable test.
- **Suggested fix:** Keep the historical explanation, but explicitly date it as the 11-03 finding and add that 11-04 rewrote the query to a batch-scoped predicate whose existing unique index is now EXPLAIN-verified. Remove the claim that the current test demonstrates the unconditional Seq Scan.

## Cross-file assessment

- The paper preflight's two-statement auto-resolve path preserves the existing candidate ordering and intent-decision behavior. Existing lifecycle tests cover reuse, missing orders, and versioned predecessors.
- Broker-fill lookup is parameterized through SQLAlchemy, deduplicates before deterministic chunking, performs no query for empty input, and preserves historical and same-response idempotency. The unique constraint remains the final concurrent-write integrity boundary.
- Reconciliation matching remains a single O(n) indexed code path; `match_snapshots()` delegates without changing its return contract. The new comparison-count tests are deterministic, though they measure explicit algorithmic counters rather than wall-clock performance.
- No Phase 11 security issue, unsafe SQL interpolation, swallowed exception, migration-chain error, or critical correctness defect was found in the reviewed scope.

## Pre-existing / out-of-scope observations

None promoted to findings. The review did not treat the existing database uniqueness-race behavior as Phase 11-introduced: the optimization retains the same unique constraint and transaction boundary as before.
