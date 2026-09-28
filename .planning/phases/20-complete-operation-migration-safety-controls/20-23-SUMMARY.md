---
phase: 20-complete-operation-migration-safety-controls
plan: 23
subsystem: ui
tags: [console, nextjs, react, controls, kill-switch, strategy, shortcuts, vitest]

requires:
  - phase: 20-complete-operation-migration-safety-controls
    provides: "20-16 (Job forms, jobTypeForms), 20-18 (control UI kit), 20-22 (/controls page)"
provides:
  - "KillSwitchBanner inline Trip/Reset trigger (armed slim bar, tripped red banner, unknown amber with no trigger) and killswitch:changed refetch"
  - "StrategyOverviewPanel badge from DB control state with inline Enable/Disable trigger; 'Control state unavailable' fallback"
  - "JobShortcutLink (generalized Run backtest shortcut) plus Evaluate risk on /strategy"
  - "PaperJobShortcuts (Run paper session, Run reconciliation, Sync broker orders) on /paper"
affects: [20-24-orchestration-closure]

tech-stack:
  added: []
  patterns:
    - "Banner armed/tripped share one JSX tree (same element types/positions, only class/text differ) so the trigger is never remounted on a state flip"
    - "Stable-mount test: same trigger DOM node stays connected with its open dialog snapshot across a *:changed event"

key-files:
  created:
    - console/src/components/KillSwitchBanner.test.tsx
    - console/src/components/strategy/StrategyOverviewPanel.test.tsx
    - console/src/components/shortcuts/JobShortcutLink.tsx
    - console/src/components/shortcuts/JobShortcutLink.test.tsx
    - console/src/components/paper/PaperJobShortcuts.tsx
  modified:
    - console/src/components/KillSwitchBanner.tsx
    - console/src/components/strategy/StrategyOverviewPanel.tsx
    - console/src/app/paper/page.tsx
    - console/src/components/jobs/new/NewJobView.test.tsx

key-decisions:
  - "Honored the 20-18 caller constraint: the banner renders armed and tripped through a single tree with isTripped passed as a prop; StrategyOverviewPanel mounts the trigger at one position keyed only on control-state success. Both are proven by DOM-node-identity tests with an open dialog."
  - "StrategyOverviewPanel reads control status with a constant STRATEGY_ID (hooks cannot be conditional on the detail fetch); the static config enabled flag is no longer read for display."
  - "CTRL-01/CTRL-02 closed: all four call sites (/controls x2, banner, /strategy) are live."

patterns-established:
  - "JobShortcutLink: accent link when mutations enabled, else disabled accent button + capability reason"

requirements-completed: [CTRL-01, CTRL-02, OPS-02, OPS-03, OPS-04, OPS-06]

duration: ~15min
completed: 2026-09-28
---

# Phase 20 Plan 23: Inline Controls and Job Shortcuts Summary

**Inline kill-switch (global banner) and strategy Enable/Disable (/strategy, from DB control state) triggers sharing one confirm dialog, plus Evaluate risk and three /paper shortcuts deep-linking into prefilled /jobs/new forms.**

## Performance

- Tasks: 2/2, console suite 262 passed (was 249), `tsc --noEmit` and `eslint src` clean.
- `next build` deliberately not run (poisoned Turbopack cache risk; vitest + tsc + eslint used per orchestrator instruction).

## Accomplishments

- KillSwitchBanner: armed -> slim `Kill switch: ARMED` bar + `Trip Kill Switch`; tripped -> existing red text + `Reset Kill Switch`; unknown -> amber banner, no trigger (T-20-23-02). Refetches on `killswitch:changed`.
- StrategyOverviewPanel: `StrategyStatusBadge` + `StrategyControlTrigger` fed by `useStrategyControlState`; failure -> `Control state unavailable`, no badge/trigger (T-20-23-01). `strategy.enabled` no longer referenced.
- `JobShortcutLink` replaces the inline Run backtest markup; `Evaluate risk` added; `PaperJobShortcuts` mounted at top of /paper.
- No new `fetch(` call sites (SC6).

## Task Commits

1. Task 1: KillSwitchBanner inline Trip/Reset - `87f5106`
2. Task 2: /strategy inline control + shortcuts - `a6e1267`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] NewJobView.test.tsx Run backtest disabled-reason assertion**
- **Found during:** Task 2 full suite run
- **Issue:** The pre-existing test used `getByText("Mutations disabled on this deployment")` on StrategyOverviewPanel; the reason now legitimately renders beside each gated control (Run backtest, Evaluate risk, Enable/Disable trigger), so `getByText` threw on multiple matches.
- **Fix:** Switched to `getAllByText(...).length > 0`; the button-disabled assertion is unchanged.
- **Files modified:** console/src/components/jobs/new/NewJobView.test.tsx
- **Commit:** a6e1267

**2. [Note] Reason text repeats on /strategy when mutations are off** — a consequence of UI-SPEC's "each control disabled with inline reason" at every site; not collapsed.

Also: each `JobShortcutLink`/trigger calls `useMutationCapability`, so /strategy issues several identical `GET /api/v1/job-types` requests (previously one). Left as-is (no shared cache exists; behavior correct).

## Known Stubs

None.

## Threat Flags

None.

## Issues Encountered

None beyond the deviation above.

## Self-Check: PASSED
