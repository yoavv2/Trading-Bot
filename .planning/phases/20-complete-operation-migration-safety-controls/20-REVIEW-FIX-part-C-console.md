---
phase: 20-complete-operation-migration-safety-controls
fixed_at: 2026-09-29T00:00:00Z
review_path: .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-part-C-console.md
iteration: 1
findings_in_scope: 9
fixed: 9
skipped: 0
status: all_fixed
---

# Phase 20 (Part C: console): Code Review Fix Report

**Fixed at:** 2026-09-29
**Source review:** .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-part-C-console.md
**Iteration:** 1

**Summary:**
- Findings in scope: 9 (WR-C-01..08 from the review, plus WR-C-09 added by the orchestrator; Info findings IN-C-01..06 out of scope)
- Fixed: 9
- Skipped: 0

Fixes were applied directly on `main` (per the caller's instruction), not in a worktree, so the worktree/sentinel
setup and cleanup tail were not used. Every fix has vitest regression tests. All nine findings' regression tests were
confirmed failing against the pre-fix source before passing.

**Final gates (console/):** `npx vitest run` 328 passed (262 baseline + 66 new), `npx tsc --noEmit` clean,
`npx eslint src` clean. No Next.js API was used or changed, and no build or dev server was run.

## Fixed Issues

### WR-C-01: Control dialog can be dismissed mid-flight; the outcome is then lost or misapplied

**Files modified:** `console/src/components/controls/ControlConfirmDialog.tsx`, `console/src/components/controls/ControlConfirmDialog.test.tsx`
**Commit:** 47f02f3
**Status:** fixed: requires human verification (async state-machine change)
**Applied fix:** The dismiss button is disabled and Escape is ignored while `submitting` (the Escape effect now depends
on `submitting`). A per-opening generation ref (`openingRef`, bumped on every open/close transition) guards the
continuation: a response landing after a close/re-open no longer writes its error or "Already ..." notice into the
new opening and does not call `onClose`, but an `ok` result still fires `onDone` so displays converge on a change that
did commit.

### WR-C-02: No state re-verification after an ambiguous control failure

**Files modified:** `console/src/components/controls/ControlConfirmDialog.tsx`, `console/src/components/controls/KillSwitchControlTrigger.tsx`, `console/src/components/controls/StrategyControlTrigger.tsx`, `console/src/components/controls/ControlConfirmDialog.test.tsx`, `console/src/components/controls/controlTriggers.test.tsx`
**Commit:** 50e87cc
**Status:** fixed: requires human verification (error-classification logic)
**Applied fix:** New `onOutcomeUncertain` prop on `ControlConfirmDialog`, fired for failures where the new
`isOutcomeUncertain(result)` (in `api.ts`) is true: transport failure (`status === null`), any 5xx, or an unreadable 2xx
(WR-C-03). Both triggers wire it to `dispatchControlChanged(<domain>)`, so the banner/panel/badge refetch. Definitive
4xx rejections do not dispatch. It fires even when the response lands after the dialog was closed. `RetryJobDialog`
already called `onChanged()` on every error, so no change was needed there.

### WR-C-03: 2xx response with an unparsable/unexpected body crashes the caller after the mutation succeeded

**Files modified:** `console/src/lib/api.ts`, `console/src/lib/api.test.ts`
**Commit:** 95f8857
**Status:** fixed: requires human verification (result-contract change)
**Applied fix:** `postJson`/`putJson` now return `ok: false` (status preserved as the 2xx, `code: null`, message
`{endpoint} returned an unreadable response. Reload and verify the current state.`) when a successful response body is
unparseable or the wrong shape: the Job routes require a string `job_id`, the control routes require a boolean
`changed`. A missing `changed` can therefore no longer render "Already ... no change" for a change that happened.
`isOutcomeUncertain` (see WR-C-02) treats these 2xx failures as uncertain.

### WR-C-04: `putJson` reports 5xx / non-JSON failures as "Request rejected — check the input and try again."

**Files modified:** `console/src/lib/api.ts`, `console/src/lib/api.test.ts`
**Commit:** bac00d5
**Status:** fixed: requires human verification (spec deviation, see below)
**Applied fix:** `controlErrorMessage(detail, status?)` now resolves in this order: (1) a recognized `{code}` object
wins (so a typed 503 keeps its own copy); (2) a plain-string `detail` is verbatim (existing 404 pattern); (3) otherwise
`status >= 500` gets new outage copy; (4) otherwise the unchanged UI-SPEC "Request rejected — check the input and try
again." row. `putJson` passes the HTTP status. The 4xx row is untouched, as instructed.

### WR-C-05: Dialog accessibility gaps (`ControlConfirmDialog`, `RetryJobDialog`)

**Files modified:** `console/src/lib/useDialogFocus.ts` (new), `console/src/components/controls/ControlConfirmDialog.tsx`, `console/src/components/jobs/RetryJobDialog.tsx`, `console/src/components/controls/ControlConfirmDialog.test.tsx`, `console/src/components/jobs/RetryJobDialog.test.tsx`
**Commit:** 7f70c8e
**Applied fix:** New dependency-free `useDialogFocus(open, panelRef, initialFocusRef)` hook: focus moves into the panel on
open, Tab/Shift+Tab wrap inside the panel (and pull stray focus back in, e.g. after the focused button became
disabled), and focus returns to the opener on close/unmount. `ControlConfirmDialog` focuses the reason field (and moves
focus to Close when the "Already ..." notice replaces the form); `RetryJobDialog` focuses the safe Close button, not the
confirm action. Error text and the unchanged notice render as `role="alert"` (the notice is re-keyed so it mounts as a
fresh alert region). The dialog is `aria-describedby` its body copy, and the reason / typed-confirmation inputs are
`aria-describedby` their helper texts. The panels got `tabIndex={-1}`. The app root is deliberately NOT made
`inert`/`aria-hidden`, because the overlay renders inside that same tree and would inert itself.
**Not changed:** `CancelJobDialog` has the same gaps but is not in this finding's scope (Phase 19 file).

### WR-C-06: Confirm dialog and trigger vanish when the underlying state read fails while the dialog is open

**Files modified:** `console/src/lib/useLastKnownData.ts` (new), `console/src/components/KillSwitchBanner.tsx`, `console/src/components/status/KillSwitchPanel.tsx`, `console/src/app/controls/page.tsx`, `console/src/components/controls/StrategyControlSection.tsx`, `console/src/components/strategy/StrategyOverviewPanel.tsx`, `console/src/components/controls/KillSwitchControlTrigger.tsx`, `console/src/components/controls/StrategyControlTrigger.tsx`, plus tests in `KillSwitchBanner.test.tsx`, `KillSwitchPanel.test.tsx`, `StrategyControlSection.test.tsx`, `StrategyOverviewPanel.test.tsx`
**Commit:** 48d69eb
**Status:** fixed: requires human verification (tree-position/remount reasoning)
**Applied fix:** `useLastKnownData(result)` retains the last successful data across a failed refetch. Each site now renders
the trigger component at the same tree position in the ok and failed states so React does not remount it (and its
open dialog / typed input): `KillSwitchBanner` uses one shared root with a class/text switch; `KillSwitchPanel` passes a
`renderError` that keeps the action in the same flex row, and `renderAction` gains a second argument
`{ stateKnown }`; `StrategyControlSection` and `StrategyOverviewPanel` keep the trigger slot and swap only the badge /
error slots. Both triggers take a new `stateKnown` prop (default true) that withholds ONLY the trigger button (and its
mutations-off reason) while the state is unknown, so the UI-SPEC honesty rule "no trigger without a known current state"
still holds; the dialog stays open and, on recovery, the trigger button returns. Each site has a test that opens the
dialog, types a reason, fails the refetch, asserts the dialog and typed reason survive and the trigger button is
gone, then recovers.
**Known residual (not a gap):** in `StrategyOverviewPanel` the trigger still sits inside the strategy-detail success
branch. That detail query only refetches on mount or on the panel's manual Refresh, which sits behind the full-screen
overlay and outside the dialog's Tab trap, so that branch cannot flip while the dialog is open. Nothing was restructured.

### WR-C-07: `StrategySelectField` shows the placeholder while the form submits a hidden, unvalidated strategy id

**Files modified:** `console/src/components/jobs/new/jobFormKit.tsx`, `RiskEvaluationJobForm.tsx`, `PaperSessionJobForm.tsx`, `ReconciliationJobForm.tsx`, `BrokerOrderSyncJobForm.tsx` (all under `console/src/components/jobs/new/`), `jobFormKit.test.tsx`, `strategySelection.test.tsx` (new)
**Commit:** bdc6f3a
**Status:** fixed: requires human verification (form gating logic)
**Applied fix:** New `useStrategySelection(seed)` hook fetches the strategies list once, seeds the (trimmed) state, and
exposes `validStrategyId`, non-null only when the value is exactly one of the loaded options. `StrategySelectField` is
now presentational (takes `strategies`). The four forms gate `canSubmit` on `validStrategyId !== null` and submit
`validStrategyId`. A valid deep-link seed is retained while the list loads (not cleared), then becomes submittable; an
unregistered id or a failed list load blocks submit while the select shows the placeholder. A whitespace-padded seed
is trimmed and accepted. `BacktestJobForm` (Phase 19, not in this finding's file list) has its own copy of the same
select and was not changed.

### WR-C-08: `crypto.randomUUID()` is unguarded, and evaluated on every render in `useJobFormSubmission`

**Files modified:** `console/src/lib/idempotencyKey.ts` (new), `console/src/lib/idempotencyKey.test.ts` (new), `console/src/components/jobs/new/jobFormKit.tsx`, `console/src/components/jobs/new/BacktestJobForm.tsx`, `console/src/components/jobs/RetryJobDialog.tsx`, `console/src/components/jobs/CancelJobDialog.tsx`, and the matching test files
**Commit:** 37c49c3
**Applied fix:** `newIdempotencyKey()` uses `crypto.randomUUID()` when present, else an RFC 4122 v4 UUID from
`getRandomValues`, else `Math.random` (the server accepts any non-blank key up to 255 chars, so a UUID shape is kept
for tidiness). The forms (`useJobFormSubmission`, `BacktestJobForm`) no longer call it during render: the key is created
lazily on first submit and still rotates only when the payload changes (retry with the same payload reuses it). The
dialogs already generated once per opening in the open effect; they now use the helper. `CancelJobDialog` (not named in
the finding) had the identical crash and was fixed too. Tests remove `randomUUID` from the stubbed `crypto` and count
calls across re-renders.

### WR-C-09: Console copy for the error codes introduced by the backend review fixes (orchestrator-added)

**Files modified:** `console/src/lib/api.ts`, `console/src/lib/api.test.ts`
**Commit:** bba0aa9
**Applied fix:** Codes were verified by grep in `src/trading_platform` before writing copy. Findings from that check
differ from the brief in two ways:
- `as_of_session_out_of_calendar_range` and `date_range_out_of_calendar_range` are NOT top-level error codes. They are
  `reason` values (`PayloadFieldRejection`, `jobs/handlers/payload_fields.py`) inside the `invalid_job_payload` 422
  (Job submit) and `invalid_retry_payload` 422 (retry, which re-validates the stored payload and forwards
  `exc.reason`). Map keys for them would have been dead code, so a `PAYLOAD_REASON_COPY` lookup is applied to both
  codes' `reason`; unrecognized reasons still render verbatim as before. Copy:
  `This submission was rejected: the as-of session date is outside the exchange calendar's supported range.` /
  `... the date range is outside the exchange calendar's supported range.` (retry uses the
  `This Job can no longer be retried: ...` prefix).
- `invalid_control_reason` (422) and `invalid_control_request` (422) already had `MUTATION_ERROR_COPY` rows; no new rows,
  tests added asserting their copy.
New `MUTATION_ERROR_COPY` rows: `strategy_archived` (409, interpolates `strategy_id`), `control_state_unavailable` (503),
`internal_error` (500, app-level handler in `api/app.py`, applies to Job and control routes), and
`control_write_failed` (503, `controls.py:99/141`) which was not on the orchestrator's list but is the same class of
gap and was found while verifying spellings. The stale "eighteen codes" docstring was updated.

## Spec deviations (UI-SPEC amendment candidates)

`20-UI-SPEC.md` was not edited. These console copy rows/behaviours have no spec row and should be added:

1. **WR-C-04 outage row (new):** control-route failure with no recognized `{code}` and HTTP status >= 500 shows
   `Operation failed (HTTP {status}). The change may not have been applied — reload to verify the current state, then try again; if it persists, check the API logs.`
   The existing 4xx row `Request rejected — check the input and try again.` is unchanged; the amendment should
   scope that row to 4xx.
2. **WR-C-03 unreadable-response row (new):** `{endpoint} returned an unreadable response. Reload and verify the current state.`
3. **WR-C-09 rows (new):**
   - `strategy_archived`: `Strategy {strategy_id} is archived and cannot be enabled or disabled.`
   - `control_state_unavailable`: `Control state is unavailable — the API could not read the stored control state. Nothing was changed; check that database migrations are current, then try again.`
   - `control_write_failed`: `The control change could not be saved — nothing was committed. Try again; if it persists, check the API and database logs.`
   - `internal_error`: `The API hit an unexpected error. Reload to verify the current state, then try again; if it persists, check the API logs.`
   - `invalid_job_payload` / `invalid_retry_payload` reason lookup: `as_of_session_out_of_calendar_range` and `date_range_out_of_calendar_range` as above.
4. **WR-C-06 behaviour:** the trigger button is withheld while the state read is failing but the (already open)
   confirmation dialog is retained; the "no trigger when state unknown" honesty rule is otherwise preserved.

## Notes for the human verifier

- The remaining tier-2 risk is semantic, not syntactic: WR-C-01/02/06/07 change async or gating logic, hence the
  "requires human verification" status.
- Pre-existing `renderAction` consumers must accept the new second argument; the only consumer
  (`console/src/app/controls/page.tsx`) was updated, and an existing `KillSwitchPanel` test assertion was updated to
  expect `{ stateKnown: true }` as the second argument.
- `MutationResult` failures can now carry a 2xx `status` (unreadable success); consumers that branch on `status` for
  "server rejected" semantics should use `isOutcomeUncertain` rather than assuming 4xx/5xx.

---

_Fixed: 2026-09-29_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
