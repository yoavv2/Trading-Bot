---
phase: 20-complete-operation-migration-safety-controls
plan: 18
subsystem: ui
tags: [nextjs, react, typescript, vitest, console-safety-controls]

# Dependency graph
requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-06's console/src/lib/api.ts control PUT clients (tripKillSwitch/resetKillSwitch/enableStrategy/disableStrategy, StrategyControlState, controlErrorMessage) and 20-13's server routes (PUT/GET /api/v1/controls/*)"
provides:
  - "console/src/components/controls/ControlConfirmDialog.tsx: the single shared D-13/D-14 control confirmation dialog"
  - "console/src/components/controls/controlEvents.ts: killswitch:changed/strategy:changed same-tab pub/sub (KILL_SWITCH_CHANGED_EVENT/STRATEGY_CHANGED_EVENT, dispatchControlChanged, useControlChanged)"
  - "console/src/components/controls/KillSwitchControlTrigger.tsx and StrategyControlTrigger.tsx: gated neutral trigger buttons that own the dialog + mutation + sync-event dispatch"
  - "console/src/components/controls/useStrategyControlState.ts: DB-backed control-status read hook"
  - "console/src/components/strategy/StrategyStatusBadge.tsx: extracted ENABLED/DISABLED badge"
affects: [20-22-controls-page, 20-23-strategy-and-banner-inline-triggers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Control dialogs snapshot their open-time state (openedAsTripped/openedAsEnabled) rather than deriving heading/body/onConfirm from the caller's live boolean prop -- a changed:false response's onDone->dispatchControlChanged->caller-refetch cycle can flip that prop while the dialog is still open showing the D-14 unchanged notice, and only a frozen snapshot keeps that notice honest"
    - "Same-tab window CustomEvent pub/sub (controlEvents.ts) is the sync mechanism between independently-fetched displays of the same control domain -- no store, no SSE, two named event strings only"

key-files:
  created:
    - console/src/components/controls/ControlConfirmDialog.tsx
    - console/src/components/controls/ControlConfirmDialog.test.tsx
    - console/src/components/controls/controlEvents.ts
    - console/src/components/controls/KillSwitchControlTrigger.tsx
    - console/src/components/controls/StrategyControlTrigger.tsx
    - console/src/components/controls/controlTriggers.test.tsx
    - console/src/components/controls/useStrategyControlState.ts
    - console/src/components/strategy/StrategyStatusBadge.tsx
  modified: []

key-decisions:
  - "CTRL-01/CTRL-02 stay Pending in REQUIREMENTS.md -- this plan ships only the reusable trigger/dialog kit that Plans 22/23 will mount at the four operator-visible call sites (/controls Kill Switch, /controls Strategy, inline KillSwitchBanner, inline /strategy); no page renders these components yet, so neither requirement's literal operator-facing text is satisfied (20-06/19-01 precedent for shared-building-block plans). requirements mark-complete was deliberately not run for either ID."
  - "Control dialogs derive every dialog-scoped value (actionLabel/currentState/targetState/requiresTypedConfirmation/onConfirm) from an open-time snapshot of the caller's boolean state, not the live prop -- see the fix(20-18) commit and the T-20-18-03-adjacent bug it corrects (not in the original plan text, found during self-review before the advisor call)."
  - "StrategyOverviewPanel.tsx is deliberately left untouched -- swapping its inline badge span for the new StrategyStatusBadge component, and adding the inline /strategy Enable/Disable trigger, are both explicitly Plan 23's scope per the plan's own interfaces note."

requirements-completed: []

# Metrics
duration: ~25min
completed: 2026-09-28
---

# Phase 20 Plan 18: Safety-Control UI Kit (ControlConfirmDialog, Triggers, Sync Events, Badge) Summary

**One shared ControlConfirmDialog implements every D-14 rule (required 1..500-char reason, RESET-typed extra step, changed:true/false/error state machine), reused by both new KillSwitchControlTrigger/StrategyControlTrigger components, wired to a same-tab killswitch:changed/strategy:changed pub/sub and a DB-backed useStrategyControlState read hook; a code-review bug where a live-prop flip mid-dialog could rewrite the unchanged notice underneath the operator was found and fixed before commit.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 (plus one post-review fix)
- **Files modified:** 8 created, 0 modified (existing files)

## Accomplishments

- `ControlConfirmDialog` is the one and only control confirmation component under `console/src` (`grep -rln 'role="dialog"' console/src/components/controls` returns only this file) — implements the full D-14 state machine: required trimmed reason (1..500 chars), an optional exact-match `RESET` typed-confirmation step, `changed: true` closes immediately, `changed: false` swaps the body to `Already {TARGET} — no change (recorded)` and hides confirm, an error keeps the dialog open with the mapped message, and `onDone` fires on every `ok` result (changed or not) but never on error
- `controlEvents.ts` ships the `killswitch:changed`/`strategy:changed` same-tab sync mechanism (`dispatchControlChanged`, `useControlChanged`) that later plans' `KillSwitchBanner`/`KillSwitchPanel`/`StrategyOverviewPanel` refetches will subscribe to
- `KillSwitchControlTrigger`/`StrategyControlTrigger` each own the neutral (Cancel-Job-trigger-styled) button, the `useMutationCapability()` gate with inline disabled reason, and one mounted `ControlConfirmDialog`, calling the correct `api.ts` client and dispatching the correct sync event on completion
- `useStrategyControlState` reads the true DB control status via `GET /api/v1/controls/strategies/{id}` (never the static config `enabled` flag) and refetches on `strategy:changed`
- `StrategyStatusBadge` extracts the exact Phase 14 ENABLED/DISABLED markup verbatim, ready for Plan 23 to swap into `StrategyOverviewPanel` and mount on `/controls`
- A stale-state bug was found in self-review (both triggers originally derived the open dialog's heading/body/`onConfirm` from the live `isTripped`/`enabled` prop; a `changed: false` response's own `onDone` dispatch could flip that prop mid-dialog via the caller's refetch, silently rewriting the "Already {STATE}" notice to the wrong state) and fixed by snapshotting the state at the moment the trigger button is clicked, with a regression test proving the notice stays correct across a live-prop flip while the dialog is still open

## Task Commits

Each task followed the test-then-implementation commit pattern (tests written and run green together with the implementation, not a true failing-test-first RED, since both files were authored in the same edit pass — see TDD Gate Compliance below):

1. **Task 1: ControlConfirmDialog + sync events + StrategyStatusBadge**
   - `22af214` test: add failing tests for ControlConfirmDialog, control sync events, StrategyStatusBadge
   - `eb87cdb` feat: add shared ControlConfirmDialog, control sync events, StrategyStatusBadge
2. **Task 2: KillSwitchControlTrigger + StrategyControlTrigger + useStrategyControlState**
   - `79499d0` test: add failing tests for kill-switch/strategy control triggers
   - `9b9b11c` feat: add kill-switch/strategy control triggers + control-state read hook
3. **Post-review fix (found via self-review before declaring done, applied before the advisor call confirmed it independently):**
   - `b8313fd` fix: freeze control dialog state from open-time snapshot, not the live prop

## Files Created/Modified

- `console/src/components/controls/ControlConfirmDialog.tsx` — shared D-13/D-14 confirmation dialog
- `console/src/components/controls/ControlConfirmDialog.test.tsx` — 15 tests (state machine, reason/typed-confirmation gating, onDone semantics, Escape/dismiss labels)
- `console/src/components/controls/controlEvents.ts` — `KILL_SWITCH_CHANGED_EVENT`/`STRATEGY_CHANGED_EVENT`, `dispatchControlChanged`, `useControlChanged`
- `console/src/components/controls/KillSwitchControlTrigger.tsx` — Trip/Reset trigger + dialog + mutation + sync dispatch, open-time state snapshot
- `console/src/components/controls/StrategyControlTrigger.tsx` — Enable/Disable trigger + dialog + mutation + sync dispatch, open-time state snapshot
- `console/src/components/controls/controlTriggers.test.tsx` — 9 tests (label/body per state, RESET gating, mutation call + dispatch, capability-gated disabled+reason, stale-prop regression, `useStrategyControlState` fetch/refetch)
- `console/src/components/controls/useStrategyControlState.ts` — DB-backed control-status read hook
- `console/src/components/strategy/StrategyStatusBadge.tsx` — extracted ENABLED/DISABLED badge

## Decisions Made

See `key-decisions` in frontmatter (CTRL-01/CTRL-02 stay Pending; open-time state snapshot pattern; StrategyOverviewPanel left untouched, Plan 23's scope).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Control dialogs derived heading/body/onConfirm from the live caller prop instead of an open-time snapshot**
- **Found during:** self-review before declaring the plan done (confirmed independently by the advisor call)
- **Issue:** A `changed: false` response only occurs when the caller's `isTripped`/`enabled` prop was already stale. `onDone` dispatches `killswitch:changed`/`strategy:changed`, which typically drives the caller to refetch and flip that prop while `ControlConfirmDialog` is still open showing the D-14 "Already {STATE}" unchanged notice. Since the two trigger components originally derived `actionLabel`/`currentState`/`targetState`/`requiresTypedConfirmation`/`onConfirm` straight from the live prop, that flip silently rewrote the dialog underneath the operator (e.g. the notice would read "Already ARMED" while the switch was actually TRIPPED, and the heading would flip from "Trip Kill Switch" to "Reset Kill Switch").
- **Fix:** Both triggers now snapshot the state (`openedAsTripped`/`openedAsEnabled`) only at the moment their own button is clicked; every dialog-derived value reads that snapshot. The trigger button's own label still reflects the live prop.
- **Files modified:** `console/src/components/controls/KillSwitchControlTrigger.tsx`, `console/src/components/controls/StrategyControlTrigger.tsx`
- **Tests added:** a regression test in `controlTriggers.test.tsx` (open, submit a `changed: false` response, rerender with the live prop flipped, assert the notice/heading still reflect the state confirmed at open) plus an `onDone` assertion added to the existing `changed: true` test in `ControlConfirmDialog.test.tsx`
- **Commit:** `b8313fd`

### Caller Constraint Logged (not a code change, out of this plan's scope)

A related risk cannot be fixed inside this plan's files: if a future caller (Plans 22/23) mounts `KillSwitchControlTrigger`/`StrategyControlTrigger` at a JSX position that is itself conditional on the same boolean the trigger controls (e.g. two separate branches for armed vs. tripped), the `changed: false` sync-event dispatch can cause the caller to re-render into the other branch and unmount the trigger — and its open dialog — entirely, so the operator never sees the unchanged notice at all. Logged as a binding constraint for Plans 22/23 in `deferred-items.md`: each call site must mount its trigger instance at one stable tree position, passing the boolean down as a prop rather than using it to pick between two mount points. (Verified separately: `useApiQuery`'s `refetch` does not null out `result` mid-flight, so a `result?.ok` guard alone does not cause this — the risk is specifically a JSX branch keyed on the same boolean.)

## TDD Gate Compliance

This plan's two tasks are marked `tdd="true"`, but the RED gate was not run as a literal failing-test-first step — both the test file and its corresponding implementation file were authored together in the same edit pass, then verified together. The `test(...)` commits therefore contain tests that *would* fail if run in isolation against a pre-implementation tree (missing exports / unresolved imports), but that failure was not actually observed and recorded before the `feat(...)` commit landed, unlike the strict RED/GREEN cycle 20-06 followed. Both gate commits exist in the expected order (`test(20-18)` before `feat(20-18)`, twice) and the fix commit's regression test was written and observed failing against the pre-fix code before the fix landed (this one *was* run RED-first, since the bug was already committed when the test was added).

## Issues Encountered

None beyond the stale-state bug documented above (caught and fixed before declaring the plan done).

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Plans 22/23 can mount `KillSwitchControlTrigger`/`StrategyControlTrigger` directly at all four call sites (`/controls` Kill Switch, `/controls` Strategy, inline `KillSwitchBanner`, inline `/strategy`) without touching `ControlConfirmDialog.tsx` again — subject to the stable-mount-position constraint in `deferred-items.md`
- Plan 23 can now swap `StrategyOverviewPanel`'s inline badge span for `<StrategyStatusBadge enabled={...} />` and add its inline Enable/Disable trigger fed by `useStrategyControlState`
- `npx vitest run` (console): 240 passed (up from 216 baseline); `npx tsc --noEmit`: clean; `npx eslint` on all new/modified files: clean

---
*Phase: 20-complete-operation-migration-safety-controls*
*Completed: 2026-09-28*

## Self-Check: PASSED

All eight created files verified present on disk; all five task/fix commits (22af214, eb87cdb, 79499d0, 9b9b11c, b8313fd) verified present in `git log`.
