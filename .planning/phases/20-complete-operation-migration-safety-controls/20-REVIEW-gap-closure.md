---
phase: 20-complete-operation-migration-safety-controls
scope: gap-closure 20-25..20-28 (full-phase review is 20-REVIEW.md)
reviewed: 2026-09-29T12:02:09Z
depth: standard
diff_base: 38806d2
files_reviewed: 8
files_reviewed_list:
  - console/src/components/controls/ControlConfirmDialog.tsx
  - console/src/components/jobs/CancelJobDialog.tsx
  - console/src/components/jobs/RetryJobDialog.tsx
  - src/trading_platform/jobs/handlers/ingest_bars.py
  - src/trading_platform/services/alpaca.py
  - src/trading_platform/services/data.py
  - src/trading_platform/services/ingestion.py
  - src/trading_platform/services/operator_controls.py
findings:
  critical: 0
  warning: 2
  info: 4
  total: 6
status: issues_found
---

# Phase 20 (gap-closure 20-25..20-28): Code Review Report

**Reviewed:** 2026-09-29
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Reviewed the gap-closure diff (`git diff 38806d2 HEAD`) of the eight listed files against the intent recorded in the 20-25..20-28 summaries. No BLOCKER was proven. The change set is largely sound:

- **20-25 (ingest-bars all-fail).** `succeeded_count` is incremented only after the per-symbol `session_scope` exits, so a commit failure is counted as a failure and never as both. `run_status` and `run_error_message` are always bound on the return path, because the only path that skips them re-raises. Empty `symbols` cannot reach the service through the Job path (submission validates `EMPTY_SYMBOLS`). The run `error_message` column is `Text` and the symbol count is capped upstream. The persisted message uses exception class names only.
- **20-28 (DB-clock audit timestamps).** `started_at` is a server-default `now()`, which is the transaction start. `clock_timestamp()` read after the row lock is therefore >= `started_at` by construction. No stray `datetime.now(UTC)` remains in the mutators.
- **20-26 (dialog split).** The ControlConfirmDialog shell/body split is correct: `openingRef` lives in the never-keyed shell and the body holds all state.

Two residual robustness defects remain. One is the WR-C-01 stale-continuation hazard, which is pre-existing in the two Job dialogs and only narrowed by the split. The other is the hard, unconfigurable pagination cap. There are also four minor items.

## Warnings

### WR-01: Cancel/Retry dialog bodies apply a stale continuation to a later opening

**File:** `console/src/components/jobs/CancelJobDialog.tsx:69-85`, `console/src/components/jobs/RetryJobDialog.tsx:83-97`
**Issue:** After the shell/body split, the Job dialogs still lack the stale-response guard that `ControlConfirmDialog` has via `openingRef` (WR-C-01). Neither dialog disables Keep Job/Close or gates Escape while `submitting`. Escape is ungated at Cancel:53 and Retry:70, and the dismiss buttons are never disabled.

Sequence: the operator clicks Cancel Job, presses Keep Job or Escape while the POST is in flight, and re-opens the dialog. The old body has unmounted and the new body is mounted with its own state.

- **CancelJobDialog:** when the first request resolves OK, the closure calls `onClose()` at line 81. That also closes the NEW opening. The cancel it reported did commit, so the impact is mostly a dialog closing that the operator just re-opened. On an error result, `setErrorMessage` targets the unmounted body, so the message is silently lost.
- **RetryJobDialog:** the stale continuation calls `onNavigate(...)` at line 92 or `onChanged()` at line 96 from a dialog the operator already dismissed. The real residual is navigating the operator away after they dismissed the dialog.

A re-opened body also submits with a fresh Idempotency-Key while the first request is in flight. The server still rejects the duplicate (409 `job_not_cancellable` / retry linkage), so this is a UX defect, not a double-write.

This is a pre-existing gap in the job dialogs, not a regression, and the 20-26 summary scopes the `openingRef` guard to ControlConfirmDialog only. The split actually narrowed it. Before, the body stayed mounted across openings, so a stale `setErrorMessage` landed in the live opening. Now it lands in an unmounted body. The remaining stale calls are the callbacks (`onClose`, `onNavigate`, `onChanged`).

**Fix:** Either mirror the control dialog (a shell-owned `openingRef` bumped on open transitions, with the body comparing it after `await` and skipping `onClose()`/`setState`/`onNavigate` when stale, while still reporting `onCancelled`/`onChanged`), or the cheaper option: disable the dismiss buttons and gate Escape while `submitting`, as `ControlConfirmDialog` does.

```tsx
if (event.key === "Escape" && !submitting) onClose();
...
<button type="button" onClick={onClose} disabled={submitting}>Keep Job</button>
```

### WR-02: Fixed, unconfigurable pagination caps turn a large-but-valid broker history into a permanent reconcile/sync outage

**File:** `src/trading_platform/services/alpaca.py:49-51`, `:399-405`
**Issue:** `list_orders` and `list_fills` are deliberately unbounded by date (correct, to avoid false MISSING_BROKER findings). The caps, though, are module constants: 100 pages x 100 = 10,000 fills and 20 x 500 = 10,000 orders. Once a paper account passes 10,000 fills or orders, every `load_broker_state` (reconciliation) and `sync_orders` call raises `AlpacaPaginationCapExceededError`. That fails the Job with handler_error, and retrying can never succeed.

Nothing in `Settings` or the CLI lets an operator raise the cap. The only remedy is a code edit and redeploy. A safety-control path (reconciliation blocks execution) therefore depends on a constant that a long-lived account will eventually hit, and each call also re-downloads the whole history.

**Fix:** Move the caps to `AlpacaBrokerSettings` (defaulting to the current values) so the typed error is actionable. Alternatively, let reconciliation pass an `after` cursor or `until` bound once local history has a known floor. At minimum, document the 10k ceiling as a known operational limit in the runbook and surface it in the error message with the setting name.

## Info

### IN-01: Unreachable "cursor did not advance" branch in `_paginate`

**File:** `src/trading_platform/services/alpaca.py:394-398`
**Issue:** `cursor` is always the last id of the previous page, so it is already in `seen_ids`. If the current page's last id equals `cursor`, the loop at lines 378-389 raises the duplicate-id `AlpacaPaginationStalledError` first. The `next_cursor == cursor` check can never fire, and it is presented as a separate guard in the 20-27 summary and tests.
**Fix:** Delete the branch, or keep it and note it as belt-and-braces. Do not count it as independent coverage.

### IN-02: Cap check is a false positive when the total is an exact multiple of `max_pages * page_size`

**File:** `src/trading_platform/services/alpaca.py:399-405`
**Issue:** After the `max_pages`-th full page, the code raises `AlpacaPaginationCapExceededError` without fetching one more page to confirm more data exists. A complete set of exactly 10,000 items raises instead of returning. The direction is safe (fail closed), but the error text ("still has full pages") overstates what was observed.
**Fix:** On reaching the cap, fetch one more page. Raise only if it is non-empty, or word the error as "reached max_pages; completeness unverified".

### IN-03: Pagination termination and cursor semantics are unverified against live Alpaca

**File:** `src/trading_platform/services/alpaca.py:391-392`, `:326-327`, `:342`
**Issue:** The loop ends on the first page shorter than `page_size`. The 20-27 summary states the live paper account has 0 orders and 0 fills, and the tests use MockTransport only. Nothing exercises `page_token`/`before_order_id` with `direction=desc` against the real API. If Alpaca ever returns a short page mid-listing (server-side clamp), the result is silently truncated. That is the failure mode this plan set out to remove. Terminating only on an empty page would cost one extra request and match the "complete-set-or-typed-error" contract more strictly. The duplicate-id guard makes a wrong cursor direction fail loudly rather than silently, which is acceptable.
**Fix:** Keep the pending live UAT re-run of test 3 as a hard gate, ideally against an account with more than one page. Consider ending on an empty page only.

### IN-04: `IngestionResult.succeeded` can disagree with `run_status`; the error type is not copy/pickle-safe

**File:** `src/trading_platform/services/data.py:93-96`, `:57-66`
**Issue:**
- `succeeded` is still `failed_count == 0`, independent of `run_status`. A direct service call with an empty symbol list (the Job path cannot produce one) yields `run_status == "failed"` while `succeeded is True`. This is a second source of truth, against the invariant-2 intent that the service derives status once.
- `IngestionAllSymbolsFailedError.__init__` takes keyword-only arguments but passes a single message to `super().__init__`. `copy.copy`, `pickle` or any `cls(*exc.args)` reconstruction raises `TypeError`. The runner catches handler exceptions in-process (`jobs/runner.py:252`), and no multiprocessing or pickle use was found under `jobs/`, so nothing breaks today; it is a latent hazard only.

**Fix:** Define `succeeded` as `self.run_status == "succeeded"`, keeping the 8-key summary semantics, since PARTIAL is already not succeeded. Make the constructor positional-or-keyword (drop the bare `*`), or add `__reduce__` returning `(_rebuild, (run_id, symbols_failed, detail))` through a small module-level factory.

---

_Reviewed: 2026-09-29_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
