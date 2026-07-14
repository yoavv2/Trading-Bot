---
phase: 11-query-performance
verified: 2026-07-14T07:44:48Z
status: gaps_found
score: 2/3 must-haves verified (truths); 2/3 requirements Complete
gaps:
  - truth: "Every critical query path (operator reads, reconciliation, order-lifecycle sync) has a named covering index confirmed by EXPLAIN — full sequential scans on large tables are absent (ROADMAP Success Criterion 3 / PERF-03)"
    status: partial
    reason: >
      4 of 5 representative critical-path queries are EXPLAIN-verified as Index/Index-Only Scans
      at realistic (~40k-120k row) scale: operator runs list, operator orders listing, the shared
      local-orders-by-strategy statement (reconciliation + order-lifecycle sync), and the shared
      open-positions-by-strategy+status statement (reconciliation + order-lifecycle sync). The 5th,
      the order-lifecycle-sync broker-fill dedup query (`select(PaperFill.broker_fill_id)`, no
      WHERE clause, in `_ingest_paper_fills`, called live from `sync_paper_state` — confirmed not
      dead code), genuinely produces a full sequential scan on `paper_fills` at ~40k rows. This was
      verified empirically (not assumed): a forced Index Only Scan over the existing unique index
      `uq_paper_fills_broker_fill_id` costs MORE (~2365 cost units) than the Seq Scan Postgres
      already chooses (~1454 cost units) — so no index addition fixes this query shape; the real
      fix is a query-scope rewrite. Success Criterion 3's literal wording ("full sequential scans
      on large tables are absent") is not met for this one path. REQUIREMENTS.md correctly leaves
      PERF-03 Pending (not Complete) for this exact reason — this is an honest, well-documented,
      correctly-diagnosed gap, not an overclaim, but it is still a gap for goal-backward
      verification purposes.
    artifacts:
      - path: "src/trading_platform/services/paper_execution.py"
        issue: "`_ingest_paper_fills` (~line 1747, called from `sync_paper_state`) runs `select(PaperFill.broker_fill_id)` with no WHERE clause, reading the entire historical paper_fills table on every sync call instead of scoping to the current broker-reported fill batch."
    missing:
      - "Rewrite the broker-fill dedup query to `select(PaperFill.broker_fill_id).where(PaperFill.broker_fill_id.in_(broker_fill_ids))` (or equivalent), scoped to the current sync batch, in `src/trading_platform/services/paper_execution.py` — this turns the existing unique index into a real, selective win (Index Only Scan) and makes runtime independent of total historical fill count, per `.planning/phases/11-query-performance/deferred-items.md`."
      - "A regression test (analogous to PERF-01's query-count/EXPLAIN-selectivity approach) asserting the dedup query scales with sync-batch size, not total historical fill count."
      - "Once fixed, remove the now-passing `test_broker_fill_dedup_query_is_a_correct_seq_scan_not_a_missing_index_gap` documentation test (or repurpose it to assert Index Only Scan) and mark PERF-03 Complete in REQUIREMENTS.md."
---

# Phase 11: Query Performance Verification Report

**Phase Goal:** Paper preflight issues at most 2 queries regardless of portfolio size, reconciliation scales linearly with entity count, and every critical query path has a named covering index confirmed by EXPLAIN.
**Verified:** 2026-07-14T07:44:48Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | An integration test asserts paper preflight issues at most 2 queries total regardless of position/candidate count — N+1 does not reappear | ✓ VERIFIED | `tests/test_paper_preflight_query_count.py` (2 tests) pass live (`2 passed in 1.42s`). Code inspection of `_build_paper_session_plan`'s auto-resolve branch (paper_execution.py:1331-1361) confirms the per-candidate loop (line 1342) contains zero `session.get(PaperOrder`/`select(PaperOrder)` calls — resolution is via in-memory `by_intent_hash`/`predecessors_by_key` dicts built by exactly 2 queries (`_load_auto_resolve_candidates`, `_load_paper_order_index`). |
| 2 | A benchmark test confirms reconciliation scales linearly (not quadratically); fails on O(n²) | ✓ VERIFIED | `tests/test_reconciliation_matcher.py -k scales_linearly` (4 tests: positions, orders, fills, public `match_snapshots_with_comparisons` entry point) pass live (`4 passed`). Full suite `25 passed`. SUMMARY documents an independent regression-teeth check (simulated O(n²) counter, confirmed tests fail, then reverted cleanly). |
| 3 | EXPLAIN output for operator reads, reconciliation, and order-lifecycle-sync queries shows named covering index used; full seq scans on large tables absent | ✗ PARTIAL | `tests/test_query_index_usage.py` (5 tests) pass live (`5 passed in 4.19s`) — but one of those 5 tests (`test_broker_fill_dedup_query_is_a_correct_seq_scan_not_a_missing_index_gap`) passes by *asserting the Seq Scan is present* on `paper_fills` at ~40k rows for the order-lifecycle-sync broker-fill dedup query. This query is live (called from `sync_paper_state` → `_ingest_paper_fills`, confirmed not dead code). 4/5 critical paths are genuinely Index-Scan-verified; the 5th genuinely seq-scans a large table. REQUIREMENTS.md correctly reflects this as PERF-03 Pending. |

**Score:** 2/3 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `tests/support/query_counter.py` | Reusable SQL statement-count context manager | ✓ VERIFIED | Exists, matches plan contract exactly (`count_queries(bind)`, `.count`, `.statements`, `before_cursor_execute`-based, engine-resolved from Session/Engine/Connection). Importable and used by test. |
| `tests/test_paper_preflight_query_count.py` | Query-count invariant integration test | ✓ VERIFIED | 2 tests present and passing; asserts `<= 2` and K=1-vs-K=25 invariance. |
| `src/trading_platform/services/paper_execution.py` | Batched, 2-query preflight (no per-candidate loop) | ✓ VERIFIED | `_load_auto_resolve_candidates` (Q1), `_load_paper_order_index` (Q2), `_resolve_paper_intent_decision_from_index` (in-memory) all present and wired into `_build_paper_session_plan`'s auto-resolve branch; loop body confirmed query-free. |
| `tests/test_reconciliation_matcher.py` | Linear-scaling benchmark covering positions, orders, fills | ✓ VERIFIED | 4 `scales_linearly` tests present (positions pre-existing from Phase 9, orders/fills/public-entry-point new), all pass. |
| `src/trading_platform/services/reconciliation_matcher.py` | Comparison-count instrumentation reachable from public entry point | ✓ VERIFIED | `match_snapshots_with_comparisons()` present; `match_snapshots()` delegates to it, contract unchanged. |
| `alembic/versions/0017_phase11_query_performance_indices.py` | Migration adding named indices EXPLAIN proves missing | ✓ VERIFIED (documented no-op) | Exists; `revision="0017_phase11_query_perf_indices"` (shortened from plan's 38-char spec to fit `VARCHAR(32)`, documented fix), `down_revision="0016_phase8_stale_run_status"` (matches 0016's exact revision string). Ships as an intentional no-op since EXPLAIN found no genuinely-missing index — consistent with the plan's own escape hatch. |
| `tests/test_query_index_usage.py` | EXPLAIN-based index-usage assertions over seeded+ANALYZEd tables | ✓ VERIFIED, but proves a gap | 5 tests present and passing; one of the 5 documents the broker-fill dedup Seq Scan as a genuine, unfixable-by-indexing finding rather than a false pass. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `_build_paper_session_plan` | in-memory intent resolution | single batched PaperOrder load per (strategy, session) | ✓ WIRED | Confirmed by code read: `_load_paper_order_index` called once before the loop; loop resolves via `_resolve_paper_intent_decision_from_index` against the returned dicts, no DB access inside. |
| `tests/test_paper_preflight_query_count.py` | `tests/support/query_counter.py` | import `count_queries` | ✓ WIRED | Test imports and uses `count_queries`; both tests pass live. |
| `tests/test_reconciliation_matcher.py` | `reconciliation_matcher` comparison counts | 10x entity-count increase yields <= ~10x comparisons | ✓ WIRED | All 4 linear-scaling tests pass; SUMMARY documents an independent simulated-O(n²) regression check confirming real detection power. |
| `tests/test_query_index_usage.py` | critical query EXPLAIN plans | seed rows + ANALYZE, assert Index Scan (not Seq Scan) | ⚠️ PARTIAL | Wired and functioning for 4/5 paths (Index Scan asserted and present). For the 5th path (broker-fill dedup), the link is wired to assert the OPPOSITE (Seq Scan present, documented as correct-and-unfixable) — this is honest test design, not broken wiring, but it means the "seq scans absent" invariant is not universally true across all critical paths. |
| `alembic/versions/0017...` | `db/models/*.py` Index() declarations | migration mirrors model-level Index additions | N/A (no-op) | No model-level `Index()` additions were made in any of `paper_order.py`, `paper_fill.py`, `position.py` — consistent with migration 0017 shipping as a documented no-op, since Task 1's EXPLAIN audit found no genuinely-missing index to add. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PERF-01 | 11-01 | Paper-preflight issues at most 2 queries total, regardless of position/candidate count | ✓ SATISFIED | Marked Complete in REQUIREMENTS.md; live tests pass; code confirms no per-candidate query in auto-resolve loop. |
| PERF-02 | 11-02 | Reconciliation runtime is O(n) over entity count, asserted by a linear-scaling benchmark | ✓ SATISFIED | Marked Complete in REQUIREMENTS.md; live tests pass across positions/orders/fills/public-entry-point. |
| PERF-03 | 11-03 | Every critical query has an explicit named index; EXPLAIN shows the index is used | ✗ BLOCKED (correctly, per REQUIREMENTS.md) | REQUIREMENTS.md leaves PERF-03 Pending — 4/5 critical paths verified as Index Scans; the order-lifecycle-sync broker-fill dedup query is a genuine, proven, out-of-scope Seq Scan gap (`deferred-items.md`). This matches the codebase exactly; no orphan, no overclaim. |

No orphaned requirement IDs found: all three plans (11-01, 11-02, 11-03) declare `requirements: [PERF-01]`, `[PERF-02]`, `[PERF-03]` respectively in PLAN frontmatter, and REQUIREMENTS.md's Phase 11 mapping table lists exactly these three IDs with no additional Phase-11-mapped IDs left unclaimed.

### Anti-Patterns Found

None. Grep for `TODO|FIXME|XXX|HACK|PLACEHOLDER` across all phase-modified files (`paper_execution.py`, `reconciliation_matcher.py`, migration 0017, `query_counter.py`, and the two new test files) returned no matches. No stub returns, no empty handlers, no console-log-only implementations found in the modified surfaces.

### Human Verification Required

None. All three success criteria are mechanically verifiable via test execution and EXPLAIN output, and all relevant tests were run live against a real Postgres instance during this verification (not merely inspected).

### Gaps Summary

Two of three ROADMAP success criteria (PERF-01, PERF-02) are fully and durably satisfied, with all claimed tests re-run live during verification and passing, and code inspection confirming the underlying implementation (no per-candidate query loop; O(n)-scaling matcher on all three entity types plus the public entry point).

The third (PERF-03) is honestly and correctly reported as partially satisfied, not silently overclaimed. Plan 11-03 did rigorous, empirically-grounded work: it disproved two of its own three candidate-gap hypotheses via manual EXPLAIN investigation before writing test code, found the third was actually already covered by an existing index once seeded past the real cost crossover, and — critically — did NOT paper over the one genuine remaining gap (the order-lifecycle-sync broker-fill dedup query). That query, `select(PaperFill.broker_fill_id)` in `_ingest_paper_fills` (called live from `sync_paper_state`), reads the entire `paper_fills` table unconditionally on every sync. It already has a named unique index, but EXPLAIN with `enable_seqscan=off` proves a forced Index Only Scan costs MORE than the Seq Scan Postgres already picks — so no index addition can satisfy the literal wording of ROADMAP Success Criterion 3 ("full sequential scans on large tables are absent") for this path. The real fix is a query-shape rewrite (scope to the current sync batch via `WHERE broker_fill_id IN (...)`), which is correctly out of scope for an index-only plan and is fully documented in `deferred-items.md` with the exact rewrite needed.

This is exactly the situation the task brief flagged for special scrutiny: is this an honest gap or an overclaim? Verification confirms it is an honest gap, correctly diagnosed, correctly left Pending in REQUIREMENTS.md, and correctly NOT marked as a passing xfail-free test suite — the test suite is green precisely because it asserts the gap's existence and cost-model justification, not because the gap was fixed. A green test suite does not signal "goal met" here; it signals "gap confirmed and precisely bounded." Because ROADMAP Success Criterion 3 is not literally met for one of three named critical-query categories (order-lifecycle sync), and PERF-03 remains Pending, the phase as a whole is `gaps_found` rather than `passed`, despite PERF-01/PERF-02 being clean, durable passes.

---

_Verified: 2026-07-14T07:44:48Z_
_Verifier: Claude (gsd-verifier)_
