---
phase: 20-complete-operation-migration-safety-controls
plan: 27
subsystem: broker-integration
tags: [alpaca, pagination, httpx, reconciliation, gap-closure]
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-25 edits to 20-VERIFICATION.md (ordering only; no code dependency)"
provides:
  - "AlpacaClient.list_fills / list_orders return the COMPLETE broker set via cursor pagination at documented limits, or raise a typed AlpacaPaginationError"
  - "SC1 amendment and OPS-03/04/06 traceability stating live UAT test 3 re-run is pending"
affects: [reconciliation, broker-order-sync, paper-session, 20-HUMAN-UAT test 3]
tech-stack:
  added: []
  patterns: ["last-id cursor pagination with cap and stall guards; cursor key absent (never empty) on request 1; caps read as module globals at call time"]
key-files:
  created: [tests/test_alpaca_pagination.py]
  modified: [src/trading_platform/services/alpaca.py, .planning/phases/20-complete-operation-migration-safety-controls/20-VERIFICATION.md, .planning/REQUIREMENTS.md]
key-decisions:
  - "No date bounding on either endpoint: local history is unbounded, so bounding only the broker side would create false MISSING_BROKER findings"
  - "Callers (report.py, sync_orders.py) untouched: list_fills() takes no args, list_orders() takes only status"
requirements-completed: [OPS-03, OPS-04, OPS-06]
duration: ~25min
completed: 2026-09-29
---

# Phase 20 Plan 27: Alpaca Pagination Fix Summary

Closes UAT gap 2: `list_fills` sent `page_size=500` (Alpaca max 100, so live 422) and both list calls were single-page. They now paginate by cursor at the documented limits and either return the complete set or raise a typed error.

## Delivered

- Constants (`typing.Final`) in `services/alpaca.py`: `ALPACA_ACTIVITIES_MAX_PAGE_SIZE=100`, `ALPACA_ORDERS_MAX_LIMIT=500`, `ALPACA_FILLS_MAX_PAGES=100`, `ALPACA_ORDERS_MAX_PAGES=20`.
- Errors: `AlpacaPaginationError(AlpacaClientError)`, `AlpacaPaginationCapExceededError` (`endpoint`, `pages_fetched`, `items_fetched`, `max_pages`), `AlpacaPaginationStalledError` (`endpoint`, `cursor`, `detail`). Job handler_error path unchanged.
- `AlpacaClient._paginate`: fills use `page_token`, orders use `before_order_id` (last id of the previous page); loop ends on the first short or empty page; stall on repeated id, non-advancing cursor, missing/non-string id, or non-list payload; cap raises rather than returning a partial list.
- `list_fills(self)` and `list_orders(self, *, status="all")`.

## Tests (tests/test_alpaca_pagination.py, 23 cases, httpx.MockTransport only)

Constants; signatures (fills, orders); error hierarchy; fills: single short page, empty, 100/100/37 cursor chain (237 items in server order), exact-multiple terminates on empty page, cap (monkeypatched to 3), duplicate id across and within pages, missing/int/empty id, non-list payload, never sends date/after/until; orders: single short page and status=open, 500/500/12 chain (1012 items), never combines before_order_id with after/until, exact multiple, cap (monkeypatched to 2, items_fetched 1000), duplicate id; `load_broker_state` with 503 orders and 105 fills over two pages each. `Settings` obtained via `load_settings()` (works in the unit test environment).

## Verification

- Targeted suites (pagination, alpaca_execution, execution_reconciliation, paper_execution, strategy_job_types_e2e): 77 passed; ruff clean.
- Full backend suite: 1063 passed (includes tests/test_orchestration_boundaries.py and tests/test_dev_workflow.py).
- CALLERS_UNTOUCHED, USER_FILES_UNCHANGED, DOCS_OK all printed. `grep -c "leave list_orders"` in 20-HUMAN-UAT.md is 0 (nothing to record).

## Deviations from Plan

None in behavior. Process note: the shared `_paginate` and `list_orders` rewrite were written together; to keep task commits honest, the Task 1 commit contains the pre-existing `list_orders` and Task 2 adds the paginated one. Task 2's orders tests passed on first run because the implementation was already written (no separate RED for that half; the RED for `_paginate` was the Task 1 import failure).

## Commits

- 2352df5: Task 1, constants, typed errors, paginated list_fills, fills tests
- f40116e: Task 2, paginated list_orders, orders and load_broker_state tests
- 9ddadb0: Task 3, SC1 amendment and OPS-03/04/06 traceability

## Reminder (live re-verification pending)

20-HUMAN-UAT test 3 must be re-run against live Alpaca paper. The paper account has 0 orders and 0 fills, so the live run only proves the 422 is gone; completeness is proven by the MockTransport tests.

## Known Stubs

None.

## Self-Check: PASSED
