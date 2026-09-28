---
phase: 20-complete-operation-migration-safety-controls
plan: 22
subsystem: ui
tags: [console, nextjs, react, controls, kill-switch, strategy, vitest]

requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-13 (control API client), 20-18 (ControlConfirmDialog, KillSwitchControlTrigger, StrategyControlTrigger, useStrategyControlState, controlEvents, StrategyStatusBadge)"
provides:
  - "/controls page: kill-switch state + Trip/Reset trigger and strategy state + Enable/Disable trigger"
  - "KillSwitchPanel optional renderAction slot fed from its single fetch, plus killswitch:changed refetch"
  - "StrategyControlSection (DB control-state badge + trigger; ErrorState and no trigger when state unknown)"
  - "Controls nav link in the root layout"
affects: [20-23-inline-controls-and-shortcuts, 20-24-orchestration-closure]

tech-stack:
  added: []
  patterns:
    - "Function-prop slot (renderAction) on a client panel; the hosting page is itself a client component because function props cannot cross the server/client boundary"
    - "Trigger mounted at one stable JSX position taking the state boolean as a prop (20-18 caller constraint), proven by DOM-node identity + open-dialog tests"

key-files:
  created:
    - console/src/components/controls/StrategyControlSection.tsx
    - console/src/components/controls/StrategyControlSection.test.tsx
    - console/src/components/status/KillSwitchPanel.test.tsx
    - console/src/app/controls/page.tsx
    - console/src/app/controls/page.test.tsx
  modified:
    - console/src/components/status/KillSwitchPanel.tsx
    - console/src/app/layout.tsx

key-decisions:
  - "/controls page.tsx is a 'use client' component (no separate wrapper) because renderAction is a function prop."
  - "KillSwitchPanel keeps its original markup when renderAction is absent (System Status renders exactly as before); the flex wrapper only exists on the renderAction path, and that choice is a constant per mount, not state-dependent."
  - "Strategy section reads GET /api/v1/controls/strategies/{id} via useStrategyControlState (DB status), per the UI-SPEC 2026-09-28 amendment, not the config-derived strategy detail."
  - "CTRL-01/CTRL-02 are NOT closed here: Plan 20-23 also owns them and delivers the inline banner and /strategy call sites."

patterns-established:
  - "Stable-mount test: capture the trigger button element, open its dialog, flip server state, dispatch the domain event, assert the same element isConnected with the new label and the dialog still shows its snapshot state."

requirements-completed: []

duration: ~15min
completed: 2026-09-28
---

# Phase 20 Plan 22: /controls Page Summary

**Dedicated /controls page with kill-switch Trip/Reset and strategy Enable/Disable triggers driven by single-fetch state displays, event-based sync, and a nav link.**

## Accomplishments
- `KillSwitchPanel` gained `renderAction?: (data: KillSwitchData) => ReactNode` rendered beside the state display from the same `useApiQuery` result (still exactly one fetch of `/api/v1/system/kill-switch`), exports `KillSwitchData`, and refetches once on `killswitch:changed`. The System Status usage passes nothing and renders as before.
- `StrategyControlSection` renders `StrategyStatusBadge` and `StrategyControlTrigger` from `useStrategyControlState("trend_following_daily")`; a failed read renders the shared `ErrorState` and no trigger.
- `/controls` page (heading "Controls", `space-y-6` stack) and a "Controls" nav link after Paper Trading.
- 9 new tests (KillSwitchPanel 4, StrategyControlSection 4, page 1): full console suite 249 passed; `tsc --noEmit` and `eslint` clean.
- 20-18 binding constraint honored: both triggers are mounted at a single JSX position taking the boolean as a prop. Tests in `KillSwitchPanel.test.tsx` and `StrategyControlSection.test.tsx` open a dialog, flip server state, dispatch the domain event, and assert the same trigger DOM node stays connected (label flips) while the dialog keeps its snapshot "Current state" text.

## Task Commits
1. Task 1: KillSwitchPanel slot + StrategyControlSection - `3577052`
2. Task 2: /controls page + nav link - `9eb7e5d`

## Deviations from Plan

### Verification adjustment
- Task 2's verify command includes `npm run build`. Per the orchestrator's instruction (poisoned Turbopack cache risk) `next build` was NOT run; `tsc --noEmit`, `eslint` and the full vitest suite (249 passed) were used instead. The `/controls` route in the production build is therefore not exercised by this plan.

### Additions
- Added `console/src/app/controls/page.test.tsx` (not in the plan's file list) to cover the page's heading and both triggers.

None otherwise - plan executed as written.

## Issues Encountered
None.

## Known Stubs
None.

## Threat Flags
None. T-20-22-01 (DB control-state source, no trigger on unknown state), T-20-22-02 (single fetch + killswitch:changed refetch) and T-20-22-03 (triggers use `useMutationCapability`) are mitigated as planned.

## Next Phase Readiness
Plan 20-23 mounts the same triggers inline (KillSwitchBanner, StrategyOverviewPanel) and must keep them at stable positions per the 20-18 constraint; it also closes CTRL-01/CTRL-02.

## Self-Check: PASSED
