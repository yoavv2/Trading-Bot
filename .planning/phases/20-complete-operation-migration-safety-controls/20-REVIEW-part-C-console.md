---
phase: 20-complete-operation-migration-safety-controls
part: C-console
reviewed: 2026-09-28T00:00:00Z
depth: standard
files_reviewed: 30
files_reviewed_list:
  - console/src/app/controls/page.tsx
  - console/src/app/layout.tsx
  - console/src/app/paper/page.tsx
  - console/src/components/KillSwitchBanner.tsx
  - console/src/components/controls/ControlConfirmDialog.tsx
  - console/src/components/controls/KillSwitchControlTrigger.tsx
  - console/src/components/controls/StrategyControlSection.tsx
  - console/src/components/controls/StrategyControlTrigger.tsx
  - console/src/components/controls/controlEvents.ts
  - console/src/components/controls/useStrategyControlState.ts
  - console/src/components/jobs/RetryJobDialog.tsx
  - console/src/components/jobs/detail/JobDetailView.tsx
  - console/src/components/jobs/detail/JobHeaderPanel.tsx
  - console/src/components/jobs/detail/JobResourcesPanel.tsx
  - console/src/components/jobs/new/BrokerOrderSyncJobForm.tsx
  - console/src/components/jobs/new/IngestBarsJobForm.tsx
  - console/src/components/jobs/new/PaperSessionJobForm.tsx
  - console/src/components/jobs/new/ReconciliationJobForm.tsx
  - console/src/components/jobs/new/RiskEvaluationJobForm.tsx
  - console/src/components/jobs/new/SyncMarketSessionsJobForm.tsx
  - console/src/components/jobs/new/SyncSymbolMetadataJobForm.tsx
  - console/src/components/jobs/new/jobFormKit.tsx
  - console/src/components/jobs/types.ts
  - console/src/components/paper/PaperJobShortcuts.tsx
  - console/src/components/shortcuts/JobShortcutLink.tsx
  - console/src/components/status/KillSwitchPanel.tsx
  - console/src/components/strategy/StrategyOverviewPanel.tsx
  - console/src/components/strategy/StrategyStatusBadge.tsx
  - console/src/lib/api.ts
  - console/src/lib/jobTypeForms.ts
findings:
  critical: 0
  warning: 8
  info: 6
  total: 14
status: issues_found
---

# Phase 20 Part C (console): Code Review Report

**Reviewed:** 2026-09-28
**Depth:** standard
**Files Reviewed:** 30
**Status:** issues_found

## Summary

Reviewed the Phase 20 console changes (diff base 961cdab) against the server routes
(`api/routes/controls.py`, `api/routes/jobs.py`, `orchestration/job_mutations.py`), 20-UI-SPEC.md,
and 20-CONTEXT.md. No Next.js API usage was flagged as wrong (`use(params)`, `useSearchParams` under
`Suspense`, `useRouter().push` all match the existing precedent).

Verified sound (no finding):
- Wire shapes: PUT bodies `{state|status, reason}`, response fields `state|status/changed/run_id`, the
  structured `{detail:{code,...}}` error contract, `reconciliation_required` code, and the retry route's
  detail fields all match the server. Retry sends `{}` plus `Idempotency-Key`, as the server expects.
- The 18-entry MUTATION_ERROR_COPY table matches the UI-SPEC copy rows verbatim.
- Read-model keys: `RetryBlock.to_dict()` always emits `strategy_id` (null when absent), and
  `get_job_detail` + the jobs route always emit `retry_of_job_id`, `retried_as_job_id`, `retry_blocked`, and
  `cancellation_mode` (null for an unregistered type), so `computeRetryGate` and the queued-only cancel gate
  cannot see `undefined`. `submission_defaults` values are strings (`symbols` is a comma-joined string via
  `format_symbols_default`), so `parseSymbolsInput` is safe. Navigating `/jobs/A` -> `/jobs/B` changes the
  dynamic segment value, which App Router treats as a new segment (page remounts), so `retryOpen` does not
  leak across Jobs.
- Retry idempotency-key reuse is safe: `JobOrchestrationService.retry` only persists a `JobMutation` row
  on success, so reusing one key across rejected attempts within a dialog opening cannot cause a false
  `idempotency_key_conflict`, and a reused key after a lost response correctly replays.
- The stale-snapshot design (`openedAsTripped` / `openedAsEnabled`) is correct for the `changed:false`
  case, and the control PUTs are idempotent by target state, so a stale trigger label cannot cause a
  wrong-direction mutation.
- Event-listener cleanup in `ControlConfirmDialog`, `RetryJobDialog`, and `useControlChanged` is correct.
  `refetch` (`runFetch`) is identity-stable per endpoint, so `useControlChanged` does not churn.
- No XSS / unsafe rendering: all server strings render as React text; every constructed href uses
  `encodeURIComponent` or server-issued UUIDs.
- Locked decisions honoured: capability gating of the safety triggers on `useMutationCapability`
  (D-14/P19 D-21), destructive confirm styling for all four actions, plain-string 404 rendering.

Findings are all robustness/UX-honesty defects in a safety-critical surface. None is a data-loss or
security vulnerability, so no BLOCKER-tier findings are raised.

## Narrative Findings (AI reviewer)

No structural (fallow) findings were supplied for this part.

## Warnings

### WR-C-01: Control dialog can be dismissed mid-flight; the outcome is then lost or misapplied

**File:** `console/src/components/controls/ControlConfirmDialog.tsx:71-84, 98-113, 174-181`
**Issue:** While `submitting` is true, the dismiss button ("Keep Current State") stays enabled and the
Escape handler still calls `onClose()`. For a kill-switch trip this is doubly misleading:
1. The label promises the state is kept, but the in-flight PUT can still commit the change.
2. When the response lands after the dialog was closed, an error is written to hidden state (operator
   never sees that the trip failed), and `setUnchanged(true)` / `onClose()` are applied.
3. If the operator reopens the dialog before the old response lands, the reset-on-open effect has already
   cleared state, so the stale response then sets `unchanged=true` (or calls `onClose()`) on the new
   opening.
**Severity note (why WARNING, not BLOCKER):** the window is one request round trip; on success `onDone`
still fires (the dialog component stays mounted), so the banner/panel converge to the true state; on
failure the displayed state is simply unchanged, so no persistent false indicator results. The residual
harm is the misleading dismiss label and a lost error, not a wrong displayed state.
**Fix:** Disable the dismiss button and ignore Escape while `submitting`, and/or guard the continuation
with a per-opening generation ref:
```tsx
const openingRef = useRef(0);
// in the reset-on-open effect: openingRef.current += 1;
async function handleConfirm() {
  const opening = openingRef.current;
  setSubmitting(true);
  setErrorMessage(null);
  const result = await onConfirm(trimmedReason);
  if (opening !== openingRef.current) { if (result.ok) onDone?.(); return; }
  ...
}
// Escape: if (event.key === "Escape" && !submitting) onClose();
// button: disabled={submitting}
```

### WR-C-02: No state re-verification after an ambiguous control failure

**File:** `console/src/components/controls/ControlConfirmDialog.tsx:103-113` (with `KillSwitchControlTrigger.tsx:72`, `StrategyControlTrigger.tsx:67`)
**Issue:** `onDone` (which dispatches `killswitch:changed` / `strategy:changed`) fires only for `ok: true`.
On a transport failure (`status === null`) or a 5xx, the server may well have committed the change
(the response, not the write, was lost). The dialog shows the error, but the banner, `KillSwitchPanel`
and strategy badge keep displaying the pre-mutation state until the operator manually refreshes or
changes route. `RetryJobDialog` already handles this class by calling `onChanged()` on every error;
the safety controls, where a stale ARMED indicator is the worse failure, do not.
**Severity note (why WARNING, not BLOCKER):** the operator is shown an explicit error naming the
unreachable endpoint (so the outcome is flagged as uncertain, not silently wrong), the PUT is idempotent
by target state so a retry is safe, and the stale display self-corrects on the next route change.
**Fix:** Add an `onError?: (result) => void` (or reuse `onDone` semantics) that dispatches the domain
event when `result.status === null || result.status >= 500`, so displays re-verify the true state.

### WR-C-03: 2xx response with an unparsable/unexpected body crashes the caller after the mutation succeeded

**File:** `console/src/lib/api.ts:218-230, 385-397`; consumers `ControlConfirmDialog.tsx:105`, `RetryJobDialog.tsx:93`, `jobFormKit.tsx:66`
**Issue:** `postJson`/`putJson` map a JSON parse failure on a successful response to
`data = null as T` and still return `ok: true`. Every consumer then dereferences `result.data.*`
(`result.data.changed`, `result.data.job_id`) and throws a `TypeError` inside a `void`-ed async handler.
Effects: an unhandled promise rejection, no error message, no navigation/close, and for the control
dialog `onDone` has already fired but the dialog stays open with the confirm button re-enabled. The same
applies to a well-formed but wrongly shaped body: a missing `changed` is treated as falsy, so the dialog
would show "Already TRIPPED — no change (recorded)" for a change that did happen.
**Fix:** Treat an unparsable 2xx body as a failure result (or validate the minimal shape) inside
`postJson`/`putJson`:
```ts
let data: T;
try { data = (await response.json()) as T; }
catch { return { ok: false, status, code: null, detail: null,
                 message: `${endpoint} returned an unreadable response. Reload and verify the current state.` }; }
```
and/or guard `typeof result.data?.changed === "boolean"` in the dialog.

### WR-C-04: `putJson` reports 5xx / non-JSON failures as "Request rejected — check the input and try again."

**File:** `console/src/lib/api.ts:407-424` (fallback constant at 97-98, used at 350)
**Issue:** When the control PUT fails with a proxy/server error (Next rewrite 500, 502/504, an HTML or
plain-text body), `rawDetail` is `null`, so `controlErrorMessage(null)` returns the input-validation copy.
For a Trip Kill Switch attempted during an API outage this tells the operator their input is wrong when
the real condition is "the request may not have been processed". This is spec-literal (UI-SPEC: "any body
whose `detail` is not a `{code}` object"), but the spec row was written for pydantic array-shaped 422s,
not for outages, so the interaction is a spec gap rather than an endorsed behaviour.
**Fix:** Only use the "Request rejected" copy for 4xx; for `status >= 500` (or an unparseable body on a
non-4xx) return the generic "Operation failed (HTTP {status}). Try again; if it persists, check the API
logs." copy, and consider amending the UI-SPEC row accordingly.

### WR-C-05: Dialog accessibility gaps (`ControlConfirmDialog`, `RetryJobDialog`)

**File:** `console/src/components/controls/ControlConfirmDialog.tsx:119-194`; `console/src/components/jobs/RetryJobDialog.tsx:100-157`
**Issue:** Both dialogs declare `aria-modal="true"` but implement none of the behaviour it promises:
- No focus is moved into the dialog on open (keyboard/screen-reader users stay on the trigger behind the
  backdrop), no Tab trap, and focus is not restored to the trigger on close.
- The rest of the page is not inert, so Tab reaches controls behind the overlay.
- Error text (`errorMessage`, `Already ... no change`) has no `role="alert"`/`aria-live`, so a failed
  Trip/Reset is not announced.
- The helper texts ("Required, up to 500 characters", "Resetting the kill switch allows trading to
  resume.") are not tied to their inputs via `aria-describedby`; the body copy is not referenced by
  `aria-describedby` on the dialog.
The overlay mechanics are UI-SPEC-mandated (plain React-state overlay, no native `<dialog>`), but focus
management is additive and does not conflict with that.
**Fix:** On open, focus the reason textarea (Retry: the confirm/Close button) via a ref in the open
effect; trap Tab within the panel; restore focus to the opener on close; add `role="alert"` to the error
paragraph; add `aria-describedby` on the fields and dialog; set `inert`/`aria-hidden` on the app root while
open.

### WR-C-06: Confirm dialog and trigger vanish when the underlying state read fails while the dialog is open

**File:** `console/src/components/KillSwitchBanner.tsx:53-68`; `console/src/components/controls/StrategyControlSection.tsx:36-48`; `console/src/components/strategy/StrategyOverviewPanel.tsx:113-114, 127-141`; `console/src/components/status/KillSwitchPanel.tsx` (via `StatusPanel`)
**Issue:** The docstrings say the trigger stays mounted across a state flip so its dialog survives, but that
holds only for `ok -> ok`. The dialog is a child of the trigger, and the trigger is rendered only in the
`result.ok` branch. `onDone` dispatches the domain event, which starts a refetch immediately after a
`changed:false` response; if that refetch fails (or the FetchMeta refresh fails, since `runFetch` replaces
`result` with the failure), the branch flips to the amber/ErrorState output, the trigger unmounts, and the
open "Already TRIPPED — no change (recorded)" notice (which UI-SPEC says the operator must explicitly
dismiss) disappears silently, along with any typed `RESET` input.
**Fix:** Keep the last successful value for rendering the trigger, or hoist the dialog's open state above
the branch (e.g. into a small context/provider mounted next to the banner/section) so a transient read
failure cannot unmount an open dialog.

### WR-C-07: `StrategySelectField` shows the placeholder while the form submits a hidden, unvalidated strategy id

**File:** `console/src/components/jobs/new/jobFormKit.tsx:138-172`; initial state in `ReconciliationJobForm.tsx:41-43`, `PaperSessionJobForm.tsx:45-47`, `BrokerOrderSyncJobForm.tsx:42-44`, `RiskEvaluationJobForm.tsx:39-41`
**Issue:** `strategy_id` is seeded from the `?strategy_id=` deep-link query param, which is arbitrary
user-controlled text. A controlled `<select>` whose `value` matches no `<option>` (unregistered id, id
with stray whitespace, or the strategies list still loading/failed) renders "Select a strategy…", yet
`canSubmit` is true (`strategyId.trim().length > 0`) and the untrimmed hidden value is sent. The operator
submits something other than what the form displays. The server rejects an unknown id, but the error is
confusing and a stray-whitespace id is submitted untrimmed.
**Fix:** In the forms, normalize/validate the seed against the loaded strategies list (clear it if not
present), require the value to be one of the options for `canSubmit`, and send `strategyId.trim()`.
`StrategySelectField` can expose the loaded ids via a callback for this.

### WR-C-08: `crypto.randomUUID()` is unguarded, and evaluated on every render in `useJobFormSubmission`

**File:** `console/src/components/jobs/new/jobFormKit.tsx:47`; `console/src/components/jobs/RetryJobDialog.tsx:54`
**Issue:** `useRef<string>(crypto.randomUUID())` evaluates its argument on every render (only the first
value is kept). More importantly `crypto.randomUUID` is undefined outside secure contexts (for example
the console opened over plain HTTP on a LAN IP): every new Job form then throws during render, and
`RetryJobDialog` throws inside its open effect. There is no error boundary, so the whole page tree
unmounts.
**Fix:** Lazy-init and provide a fallback:
```ts
const keyRef = useRef<string | null>(null);
if (keyRef.current === null) keyRef.current = newIdempotencyKey();
// newIdempotencyKey(): crypto.randomUUID?.() ?? uuid-v4 from crypto.getRandomValues
```
(Same helper for `RetryJobDialog` and `BacktestJobForm`; the server validates key format, so keep it UUID-shaped.)

## Info

### IN-C-01: Root layout metadata is stale

**File:** `console/src/app/layout.tsx:19`
**Issue:** `description: "Read-only operator console for the trading platform."` is no longer true; the
console now trips/resets the kill switch, enables/disables strategies, and submits/retries/cancels Jobs.
**Fix:** `"Operator console for the trading platform."`

### IN-C-02: Hard-coded strategy id duplicated across three files

**File:** `console/src/components/controls/StrategyControlSection.tsx:9`; `console/src/components/paper/PaperJobShortcuts.tsx:5`; `console/src/components/strategy/StrategyOverviewPanel.tsx:11,78,133`
**Issue:** `"trend_following_daily"` is declared three times (plus a fourth literal inside the
`useApiQuery` URL at `StrategyOverviewPanel.tsx:78`). `StrategyOverviewPanel` also mixes the constant
(trigger) with the id from the fetched result (shortcuts), so the two could disagree.
**Fix:** Export one `DEFAULT_STRATEGY_ID` from a shared module and use it everywhere.

### IN-C-03: Duplicated markup and duplicated wire type

**File:** `console/src/components/status/KillSwitchPanel.tsx:50-69`; `console/src/components/KillSwitchBanner.tsx:10-18`
**Issue:** The `<p className="text-2xl ...">{state}</p>` element is copied in both branches of the
`renderAction ? ... : ...` ternary. `KillSwitchState` in the banner duplicates the newly exported
`KillSwitchData`.
**Fix:** Render the `<p>` once and wrap it conditionally; import `KillSwitchData` in the banner.

### IN-C-04: Banner positively asserts "ARMED" but only refreshes on route change or same-tab events

**File:** `console/src/components/KillSwitchBanner.tsx:44-51, 91-93`
**Issue:** Phase 20 changes the armed state from silent to an explicit "Kill switch: ARMED" bar, while
D-15 introduces a break-glass CLI trip that the console cannot observe. A tripped switch made from the
CLI (or another tab) leaves the open console asserting ARMED until navigation. The as-of `FetchMeta`
mitigates this, and UI-SPEC explicitly rules out a polling-interval change, so this is noted rather than
raised as a defect.
**Fix (within the locked constraint):** refetch on `visibilitychange` (tab refocus) and/or `window`
`focus`, which is not a polling change.

### IN-C-05: Form footer does not clear a stale error during resubmission; no client date-range check

**File:** `console/src/components/jobs/new/jobFormKit.tsx:50-70`; `IngestBarsJobForm.tsx:59-63`; `SyncMarketSessionsJobForm.tsx:47-48`
**Issue:** `outcome` is not reset when a new `submit()` starts, so a previous red error stays visible while
the retry is in flight. `from_date > to_date` is only caught server-side (`invalid_job_payload`).
**Fix:** `setOutcome({ kind: "idle" })` at the start of `submit`; disable submit (with helper text) when
`fromDate > toDate`.

### IN-C-06: Reset-on-open runs in an effect, so the first open frame shows the previous opening's state

**File:** `console/src/components/controls/ControlConfirmDialog.tsx:60-69`
**Issue:** State (`reason`, `typedValue`, `unchanged`, `errorMessage`) is cleared in a `useEffect` after
`open` flips true, so the first render of a reopened dialog can show the previous opening's "Already ...
no change" notice or typed `RESET` for a frame before the reset. The reason length limit is also enforced
silently (confirm just disables) with no `maxLength`/counter.
**Fix:** Mount an inner component only while `open` (state then resets by construction) and add
`maxLength` or an over-limit hint.

---

_Reviewed: 2026-09-28_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
