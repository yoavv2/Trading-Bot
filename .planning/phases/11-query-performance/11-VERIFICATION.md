---
phase: 11-query-performance
verified: 2026-07-14T12:29:27Z
status: passed
score: "3/3 must-haves verified; 3/3 requirements satisfied"
re_verification: true
previous_status: gaps_found
previous_verified: 2026-07-14T07:44:48Z
requirements:
  PERF-01: passed
  PERF-02: passed
  PERF-03: passed
gaps: []
human_verification: []
review_followups:
  - "Non-blocking: scope count_queries(Session/Connection) to the supplied connection rather than the shared Engine to reduce false failures under concurrent same-process DB activity."
  - "Non-blocking: extend broker-fill EXPLAIN coverage from two IDs to representative 500- and 1,000-ID predicates to strengthen proof at the production chunk boundary."
---

# Phase 11: Query Performance Verification Report

**Phase Goal:** Paper preflight issues at most 2 queries regardless of portfolio size, reconciliation scales linearly with entity count, and every critical query path has a named covering index confirmed by EXPLAIN.

**Status:** PASSED
**Re-verification:** Yes — the initial verification found the unconditional broker-fill history scan; plan 11-04 implemented and proved the required selective lookup.

## Goal Achievement

| # | Observable truth | Status | Evidence |
|---|---|---|---|
| 1 | Paper preflight issues at most 2 queries and does not scale with candidate count. | VERIFIED | `tests/test_paper_preflight_query_count.py:87-130` measures the real auto-resolve path, asserts `<= 2`, and asserts equal counts for 1 versus 25 candidates. `paper_execution.py:1218-1251` performs folded candidate/run resolution (Q1), `paper_execution.py:1286-1300` performs one batched order load (Q2), and the candidate loop at `paper_execution.py:1345-1364` resolves from in-memory indexes without SQL. |
| 2 | Reconciliation matching scales linearly over positions, orders, and fills through the public matching surface. | VERIFIED | `reconciliation_matcher.py:127-131,149-181,194-229,244-258` builds dict/set indexes and performs single passes. `tests/test_reconciliation_matcher.py:378-518` asserts hard comparison-count bounds for positions, orders, fills, and `match_snapshots_with_comparisons`; the public `match_snapshots` delegates to that same path. |
| 3 | Critical operator-read, reconciliation, and order-lifecycle query paths use named indexes under realistic-volume EXPLAIN, with no large-table sequential scan. | VERIFIED | `tests/test_query_index_usage.py:123-356` seeds and ANALYZEs roughly 120k rows, compiles the service statement shapes, requires Index/Index-Only Scan, and rejects Seq Scan on each large table. The five path assertions at lines 365-457 pass. The former broker-fill gap is closed by the bound-parameter `WHERE broker_fill_id IN (...)` lookup at `paper_execution.py:2032-2050`; its EXPLAIN assertion names `uq_paper_fills_broker_fill_id` and rejects `Seq Scan on paper_fills`. |

**Score:** 3/3 truths verified.

## Gap-Closure Verification

The initial report correctly found that `_ingest_paper_fills` read every historical `broker_fill_id`, making PostgreSQL's full scan unavoidable. Commits `6b238c0`, `8437484`, and `19cc65c` close that exact gap:

- Distinct current-batch IDs are sorted and queried in deterministic 1,000-ID chunks; empty input executes no dedup SELECT.
- Regression tests prove lookup statement/bind work is independent of historical table size, crossing 1,000 IDs produces exactly two bounded queries, and historical plus same-response duplicates remain idempotent.
- The old accepted-Seq-Scan test was replaced with a selective EXPLAIN test over the realistic seeded history. It confirms the existing named unique index `uq_paper_fills_broker_fill_id` and no `paper_fills` Seq Scan.
- No schema or model change was needed; schema-drift verification is clean.

## Requirements Coverage

| Requirement | Status | Verification |
|---|---|---|
| PERF-01 | SATISFIED | Real PostgreSQL query-count assertions pin the auto-resolve preflight to at most two statements and equal counts for K=1/K=25. |
| PERF-02 | SATISFIED | Deterministic comparison-count benchmarks cover positions, orders, fills, and the public aggregate entry point with linear upper bounds. |
| PERF-03 | SATISFIED | All five seeded-and-ANALYZEd EXPLAIN assertions pass; critical fact tables avoid Seq Scan, and broker-fill lookup explicitly names `uq_paper_fills_broker_fill_id`. |

`REQUIREMENTS.md` marks PERF-01/02/03 Complete, and `ROADMAP.md` records Phase 11 as 4/4 complete. No Phase 11 requirement is orphaned.

## Automated Evidence

Fresh verifier run against local PostgreSQL:

```text
PYTHONPATH=src .venv/bin/pytest \
  tests/test_paper_preflight_query_count.py \
  tests/test_reconciliation_matcher.py \
  tests/test_query_index_usage.py \
  tests/test_paper_execution.py -q

61 passed in 13.19s
```

Independent orchestrator gates also reported:

- Focused Phase 11 query-index + paper-execution gate: 34 passed.
- Prior-phase regression gate across 13 files: 173 passed.
- Schema drift: false.

## Review Findings Assessment

The standard code review found no critical issue and two warnings. Neither warning disproves a phase must-have:

1. The query counter listens on the shared Engine. Concurrent unrelated SQL could inflate the count and cause a false failure, but cannot hide preflight statements or create a false passing `<= 2` result. The focused isolated test is green and the implementation contains exactly the two statements described above. This is a test-isolation improvement, not an unmet PERF-01 behavior.
2. The broker-fill EXPLAIN assertion uses two IDs while production supports 500-ID pages and 1,000-ID chunks. The exact selective production statement shape is proven at realistic historical volume, and separate tests prove deterministic chunking through 1,001 IDs. Larger-predicate EXPLAIN cases would strengthen confidence in planner choice at the boundary, but the literal requirement does not require every supported predicate cardinality and no contrary Seq Scan evidence exists.

The stale migration narrative noted by review is documentation-only and does not affect execution, schema, tests, or goal achievement.

## Human Verification

None required. All phase outcomes are mechanically verified through statement counting, deterministic comparison counters, source inspection, PostgreSQL EXPLAIN, migration/schema checks, and automated regression tests.

## Re-verification History

- **2026-07-14T07:44:48Z — `gaps_found` (2/3):** PERF-01 and PERF-02 passed; PERF-03 failed because broker-fill dedup performed an unconditional historical-table read and EXPLAIN correctly selected a Seq Scan.
- **2026-07-14T12:29:27Z — `passed` (3/3):** plan 11-04 scoped broker-fill dedup to current-batch IDs, added empty/chunk/idempotency regressions, replaced the accepted-Seq-Scan assertion with named-index proof, and passed the fresh 61-test verifier gate.

---

_Verified: 2026-07-14T12:29:27Z_
_Verifier: Codex (gsd-verifier)_
