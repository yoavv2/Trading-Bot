---
phase: 20-complete-operation-migration-safety-controls
plan: 26
subsystem: console, dialogs
tags: [react, shell-body-split, UAT-gap-3, WR-C-01, WR-C-05, gap-closure]
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "ControlConfirmDialog, RetryJobDialog, WR-C-01/05/06/08 fixes (20-17..20-24)"
provides:
  - "ControlConfirmDialog, CancelJobDialog, RetryJobDialog each split into an exported persistent shell and a non-exported per-opening body"
  - "Clean first committed frame on every dialog opening; no reset-on-open effect"
  - "Reason field focused on every ControlConfirmDialog opening, including unchanged -> Close -> re-open"
  - "Per-body-mount Idempotency-Key via useState lazy initializer (Cancel, Retry)"
  - "20-UI-SPEC Amendment 2026-09-29 and WR-C-05 amendment"
affects: [20-HUMAN-UAT gap 3 re-verification]
tech-stack:
  added: []
  patterns:
    - "Persistent shell owns cross-opening refs (openingRef); body mounts fresh per opening and holds all state"
    - "FrameProbe: sibling component reading the DOM in useLayoutEffect to assert the first committed frame"
key-files:
  created: []
  modified:
    - console/src/components/controls/ControlConfirmDialog.tsx
    - console/src/components/controls/ControlConfirmDialog.test.tsx
    - console/src/components/jobs/CancelJobDialog.tsx
    - console/src/components/jobs/CancelJobDialog.test.tsx
    - console/src/components/jobs/RetryJobDialog.tsx
    - console/src/components/jobs/RetryJobDialog.test.tsx
    - .planning/phases/20-complete-operation-migration-safety-controls/20-UI-SPEC.md
    - .planning/phases/20-complete-operation-migration-safety-controls/20-REVIEW-FIX-part-C-console.md
key-decisions:
  - "Dialog shells are never keyed: keying would reset openingRef and regress the WR-C-01 stale-continuation guard; enforced by a tag-scoped static test"
  - "CancelJobDialog gets no focus management or role=alert (Phase 19 file, outside WR-C-05); recorded in the UI-SPEC amendment"
requirements-completed: [CTRL-01, CTRL-02, OPS-07]
duration: ~20min
completed: 2026-09-29
---

# Phase 20 Plan 26: Per-opening dialog body (UAT gap 3) Summary

Each confirmation dialog is now a persistent shell plus a body that mounts fresh on every opening, so a re-opened dialog is clean on its first committed frame and the passive-effect reset is gone.

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | Amend 20-UI-SPEC and the WR-C-05 fix record | 91ca3b0 |
| 2 | ControlConfirmDialog shell/body split with first-frame and focus tests | a26e1f8 |
| 3 | CancelJobDialog and RetryJobDialog shell/body split, per-mount idempotency key | 7dd4730 |

## What changed

- **ControlConfirmDialog**: the exported shell owns `wasOpenRef` and `openingRef` (bumped on each open/close transition, the WR-C-01 guard) and has no state setters. It renders `ControlConfirmDialogBody` only while open, passing `openingRef` as a prop. The body holds the five state values, refs, `useDialogFocus(true, ...)`, the unchanged-focus effect (keyed on `[unchanged]`), the Escape effect, `handleConfirm` and the unchanged JSX. `setReason("")` and `setUnchanged(false)` no longer appear in code.
- **CancelJobDialog / RetryJobDialog**: shell is `if (!open) return null; return <Body/>`. Idempotency-Key is `useState(() => newIdempotencyKey())` in the body, replacing `idempotencyKeyRef` and the `wasOpenRef` reset effect. Retry body calls `useDialogFocus(true, panelRef, closeRef)`.
- Docs: 20-UI-SPEC amendment (5 rules) placed before "Checker Sign-Off", two inline sentences, and the WR-C-05 amendment line.

## New tests (all confirmed RED against the pre-fix implementation)

ControlConfirmDialog.test.tsx, describe "UAT gap 3: every opening is clean on its first committed frame":
- (a) typed reason, then Keep Current State
- (b) changed:false unchanged notice, then Close
- (c) changed:true close
- (d) error response, then Keep Current State
- (e) RESET typed field filled, then Keep Current State
- "focus after unchanged -> Close -> re-open (plain)" and "(StrictMode)": lands on the Reason textarea

RED result before the refactor: 7 failed (5 first-frame plus 2 focus).

Static describe "WR-C-01: mount sites never key a dialog shell" (brace-depth-aware tag scanner; exactly 1 tag per mount site; no `key=`; plus a scanner self-test). These are guard tests over unchanged call sites and pass both before and after by design, so they have no RED state.

CancelJobDialog.test.tsx, describe "UAT gap 3: clean first frame":
- re-opens with an empty reason and an enabled Cancel Job after a typed reason plus Keep Job (RED)
- re-opens without the previous error text after a failed cancel (RED)
- uses one Idempotency-Key per opening, differing between openings (passes before and after; guards the lazy-initializer change)

RetryJobDialog.test.tsx, describe "UAT gap 3: clean first frame":
- re-opens with no alert and no error text after a 409 (RED)
- re-opens without the replay notice and with Retry Job enabled after a replayed 200 (RED)
- focuses Close on the first opening and again after close + re-open (passes before and after; guards the focus contract)

RED result before the refactor: 4 failed in the two job test files.

## Verification

- `npx vitest run` (full console suite): 38 files, 346 tests pass.
- `npx tsc --noEmit` and eslint on all touched source and test files: clean.
- Existing WR-C-01 ("does not apply a stale response to a re-opened dialog, but still reports onDone"), WR-C-02, WR-C-05, WR-C-06, WR-C-08, controlTriggers, JobHeaderPanel and consoleBoundaries suites pass unmodified.
- Comment-filtered counts: `setReason("")` 0, `setUnchanged(false)` 0, `wasOpenRef` 0 in both job dialogs, `useState(() => newIdempotencyKey())` 1 in each job dialog.
- User-file guard prints USER_FILES_UNCHANGED (Makefile, README.md, console/README.md, docker-compose.yml, two tests files untouched).

## Deviations from Plan

None - plan executed as written. Minor note: the plan's Cancel failed-cancel test mentions mocking `cancelJob`; the file's existing convention stubs `fetch`, so the test uses a 409 `job_not_cancellable` response through the real client instead.

## Known Stubs

None.

## Threat Flags

None. Threats T-20-26-01..04 are mitigated: fresh body per opening (first-frame tests assert confirm disabled, reason "", zero alerts), `openingRef` kept in the never-keyed shell, per-mount idempotency key, and zero stale `role=alert` on re-open.

## Self-Check: PASSED

Files and commits 91ca3b0, a26e1f8, 7dd4730 verified present.
