---
status: diagnosed
trigger: "control-dialog-stale-state-on-reopen: A re-opened ControlConfirmDialog briefly renders the previous opening's state; after unchanged notice -> Close -> re-open, focus lands on Keep Current State instead of the reason textarea."
created: 2026-09-29T00:00:00Z
updated: 2026-09-29T13:50:00Z
---

## Current Focus

hypothesis: CONFIRMED (a) and (b); (c) job dialogs share the class.
test: scratch repro (layout-effect sibling probe = first committed frame) + real repo suites against a patched copy via alias
expecting: n/a (diagnosed)
next_action: hand diagnosis back to orchestrator (goal find_root_cause_only); fix belongs to a gap-closure plan

## Symptoms

expected: A re-opened control confirmation dialog starts clean on its FIRST rendered frame: prompt body "Current state: X. This will change it to: Y.", empty reason, confirm disabled, focus on the reason textarea (WR-C-05). All four call sites.
actual: Playwright probe 2026-09-29: Case A (type reason, Keep Current State, re-open) +0ms old reason + enabled confirm, +1000ms clean. Case B (unchanged notice, Close, re-open) +0ms "Already ENABLED — no change (recorded)", only Close, focus on Close; +1000ms prompt clean but focus on "Keep Current State" persistently. Case C (changed:true close, re-open) +0ms previous reason + enabled confirm; +1000ms clean.
errors: None
reproduction: 20-HUMAN-UAT.md tests 2 and 5a (gap 3); probe2.js in scratchpad (do not re-run: real mutations)
started: Since WR-C-01/05/06 fixes (reset-in-effect pattern predates; focus hook added in WR-C-05)

## Eliminated

- hypothesis: Frozen-snapshot trigger pattern (openedAsTripped/openedAsEnabled) feeds stale props into the re-opened dialog
  evidence: Snapshot is re-taken on every trigger click; Case B first-frame body read "Already ENABLED" = stale internal `unchanged` + fresh targetState prop. Repro with a plain useState harness (no trigger) reproduces all cases.
  timestamp: 2026-09-29T13:45:00Z

- hypothesis: openingRef bump (WR-C-01) causes the stale frame
  evidence: openingRef is only read in handleConfirm's continuation; it affects no render output. Patched version keeping the same effect-based bump in a persistent shell passes all cases.
  timestamp: 2026-09-29T13:48:00Z

- hypothesis (fix option): moving the reset into useLayoutEffect fixes it
  evidence: Scratch variant ControlConfirmDialog.layout.tsx still fails A, B, B2, C, D. The stale DOM is still committed (sibling layout effect in the same commit sees it) and focus still lands on Keep Current State.
  timestamp: 2026-09-29T13:47:00Z

## Evidence

- timestamp: 2026-09-29T00:00:00Z
  checked: ControlConfirmDialog.tsx
  found: Component stays mounted when closed (returns null only after hooks). reason/typedValue/submitting/errorMessage/unchanged reset via setState inside useEffect([open]) on the false->true transition. Render reads stale state first. openingRef bump also happens in that passive effect.
  implication: The first commit after open=true renders previous opening's state; reset requires a second render.

- timestamp: 2026-09-29T00:00:00Z
  checked: useDialogFocus.ts + ControlConfirmDialog effect order
  found: useDialogFocus effect deps [open] only; picks initialFocusRef.current ?? first focusable. Declared after the reset effect but in the same passive flush, so it sees the stale DOM. With stale unchanged=true, textarea is not rendered => reasonRef.current null => first focusable = dismiss button ("Close"). Separate effect [open, unchanged] also focuses dismissRef while stale unchanged=true. After reset re-render, useDialogFocus does not re-run (open unchanged) and the unchanged effect's condition is false.
  implication: Focus persistently stays on the dismiss button, now labelled "Keep Current State". Matches Case B.

- timestamp: 2026-09-29T00:00:00Z
  checked: KillSwitchControlTrigger / StrategyControlTrigger
  found: Both always mount ControlConfirmDialog (open = snapshot !== null), so the dialog instance and its state persist across openings. Frozen snapshot only affects label/current/target props, not dialog internal state.
  implication: Trigger pattern is not the cause but is why state persists (dialog never unmounts between openings, by design for WR-C-06).

- timestamp: 2026-09-29T13:44:00Z
  checked: scratch repro scratchpad/dialogdebug/repro.test.tsx (sibling FrameProbe useLayoutEffect = first committed frame, runs before passive effects)
  found: Original impl. Case A first frame {reason:'drill', confirmDisabled:false}; Case B first frame {reason:null (no textarea), already:true}; Case C first frame {reason:'drill', confirmDisabled:false}; Case D first frame contains stale role=alert "Request rejected". Settled state after act is clean in A/C/D.
  implication: Hypothesis (a) confirmed: the stale state is committed to the DOM (not merely painted), including stale role=alert nodes an AT can announce.

- timestamp: 2026-09-29T13:45:00Z
  checked: Case B2 (unchanged -> Close -> re-open, await flush, activeElement)
  found: settled activeElement = button "Keep Current State" (expected textarea). Same with StrictMode wrapper.
  implication: Hypothesis (b) confirmed. Two contributors: useDialogFocus fallback (reasonRef null -> first focusable = dismiss) AND the [open, unchanged] effect (open changed, stale unchanged=true -> dismissRef.focus()). Neither re-runs after the reset render.

- timestamp: 2026-09-29T13:46:00Z
  checked: CancelJobDialog and RetryJobDialog (both mounted persistently by JobHeaderPanel while status allows)
  found: Same reset-in-useEffect([open]) pattern. Repro: Cancel first frame after re-open shows stale reason "old"; Retry first frame shows stale role=alert "Retry rejected". Retry focus is fine (Close always rendered). Cancel has no focus management (pre-existing, out of WR-C-05 scope).
  implication: Same bug class in both job dialogs (ControlConfirmDialog docstring says it copied CancelJobDialog's reset-on-open mechanics).

- timestamp: 2026-09-29T13:48:00Z
  checked: patched copy scratchpad/dialogdebug/ControlConfirmDialog.patched.tsx (persistent shell owns openingRef + wasOpenRef bump effect; body with all per-opening state rendered only when open)
  found: repro A, B, B2, C, D pass, with and without <StrictMode> (the scratch 'WR-C-01 harness' cases only check dismiss-disabled-in-flight and a same-opening error->retry; they do NOT exercise the stale-continuation guard). WR-C-01 guard evidence is the repo test 'does not apply a stale response to a re-opened dialog, but still reports onDone', passing against the patched copy. Real repo suites (controls/*, KillSwitchBanner, KillSwitchPanel, StrategyOverviewPanel: 6 files, 60 tests) pass unmodified against the patch via regex alias; alias proven active by a throw-sabotage run (6/6 files failed on the sentinel).
  implication: Fix direction preserves WR-C-01 (in-flight guard), WR-C-05 (focus in/trap/restore) and WR-C-06 (dialog + typed reason survive read failure).

- timestamp: 2026-09-29T13:49:00Z
  checked: why existing tests missed it
  found: RTL render/rerender/fireEvent wrap in act(), which flushes passive effects before assertions, so queries only see settled state. The only re-open test (WR-C-01) asserts after act. No test re-opens after an unchanged notice (only path to the persistent focus bug).
  implication: Regression tests need a same-commit layout-effect probe for first-frame invariants, plus an unchanged->Close->re-open focus test.

## Resolution

root_cause: ControlConfirmDialog stays mounted across openings (the triggers always render it and it returns null only after its hooks) and resets its per-opening state (reason, typedValue, submitting, errorMessage, unchanged) with setState inside a passive useEffect keyed on `open`. The first commit of each re-opening therefore renders the previous opening's state (old reason with confirm enabled, stale error alert, or the "Already X" alert with no textarea). useDialogFocus runs in that same passive-effect flush against the stale DOM: after an unchanged notice the textarea is absent, so it falls back to the first focusable element (the dismiss button), and the [open, unchanged] effect also focuses dismissRef. The reset re-render then relabels that button "Keep Current State" and neither effect re-runs, so focus stays there. CancelJobDialog and RetryJobDialog use the same reset-in-effect pattern (stale first frame; no persistent focus bug).
fix: (not applied; diagnose-only) Split into a persistent shell that owns openingRef/wasOpenRef (WR-C-01 guard unchanged) and a body component rendered only while open, holding all per-opening state and useDialogFocus; delete the reset effect. Same shell/body pattern for CancelJobDialog/RetryJobDialog with the idempotency key created once per body mount.
verification:
files_changed: []
