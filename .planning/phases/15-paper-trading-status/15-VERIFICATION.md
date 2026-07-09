---
phase: 15-paper-trading-status
verified: 2026-07-09T00:00:00Z
status: passed
operator_signoff: "2026-07-09 — Operator approved Phase 15 as complete. The 4 populated-data-render items below are accepted as a deferred re-verify, gated on the pre-existing unconfigured-Alpaca-creds constraint (out of scope for v1.2 read-only console). Code + empty/error paths verified; populated-data branches to be re-verified when live broker data becomes available."
score: 4/4 requirements code-verified; 4/4 empty/error-path truths live-verified; 4 populated-data-render items deferred (no live broker data available), operator-accepted
human_verification:
  - test: "Configure Alpaca paper credentials (or seed a test account snapshot row) so /paper's Account snapshot panel receives a non-null latest_account_snapshot, then reload /paper."
    expected: "PaperAccountPanel renders a <dl> with Total equity, Cash, and Buying power (PAPR-04) plus gross exposure, open positions, snapshot source, and snapshot-at, using real (non-zero, non-fabricated) values."
    why_human: "External service integration (Alpaca) + real data never flowed through this branch during 15-03; only the null-snapshot code path was exercised live."
  - test: "With a non-null latest_reconciliation row present, reload /paper and inspect the Reconciliation panel."
    expected: "Status/as_of_session/finding_count/blocking_count/completed_at render, plus the blocks_execution badge shows red 'BLOCKS EXECUTION' or zinc 'does not block execution' depending on the real flag value, and the scope-labelled findings table renders real rows with the details column populated for findings that carry a details payload."
    why_human: "This branch (and the blocks_execution=true red-highlight path specifically) was never exercised against live data in 15-03; only the null-reconciliation empty state was confirmed."
  - test: "With a mix of open and closed positions present (or open and terminal orders), reload /paper and toggle the 'Reveal all' / 'Show open only' controls on PositionsPanel and OpenOrdersPanel."
    expected: "Default view shows only open/open-status rows; the 'Hiding N non-open …' disclosure note appears with the correct count; clicking Reveal all shows every row including closed/terminal ones with a visible Status column; toggling back hides them again."
    why_human: "Both panels' hidden-count disclosure and reveal-toggle logic exist in code and pass build/lint, but have never been exercised against a real mixed open/non-open dataset — 15-03 only observed the hiddenCount === 0 branch (zero rows total)."
  - test: "Seed >=100 raw positions or orders rows for trend_following_daily so data.count >= 100, and confirm the truncation note renders correctly whether or not the filtered (open) list is also empty."
    expected: "PositionsPanel shows 'Showing the 100 most-recent positions; older rows may be truncated.' and, if the open subset is empty, the compound message '...among the 100 most-recent; older rows may be truncated.' OpenOrdersPanel behaves equivalently for orders."
    why_human: "The `data.count >= 100` cap-honesty branch has code coverage but zero live exercise — the live checkpoint only ever saw count === 0 responses (Alpaca creds unconfigured)."
---

# Phase 15: Paper Trading Status Verification Report

**Phase Goal:** Operator can check the live paper-trading state — what's open, what the broker says, what the account looks like — on one screen.
**Verified:** 2026-07-09
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `/paper` route reachable from a "Paper Trading" top-nav link on every screen | ✓ VERIFIED | `console/src/app/layout.tsx:46` — `<Link href="/paper" className="text-zinc-400 hover:text-zinc-100">Paper Trading</Link>` in the shared nav rendered by the root layout (applies to every screen). `next build` registers `○ /paper` as a static route. |
| 2 | Account snapshot (PAPR-04): equity/cash/buying-power visible when present; explicit "no snapshot" empty state when null — not zeros, not blank | ✓ code-verified / ? populated-render not live-exercised | `PaperAccountPanel.tsx` — null branch renders `"No account snapshot recorded yet."`; non-null branch renders a `<dl>` with `total_equity`/`cash`/`buying_power` plus 4 more fields. 15-03 operator confirmed the null-state live. The non-null `<dl>` branch has never received real data (Alpaca creds unconfigured) — see human_verification item 1. |
| 3 | Reconciliation (PAPR-03): status/counts/blocks_execution badge + scope-labelled findings visible when present; explicit "no reconciliation" empty state when null | ✓ code-verified / ? populated-render not live-exercised | `PaperReconciliationPanel.tsx` — null branch renders `"No reconciliation has been recorded yet."`; non-null branch renders the summary `<dl>`, a red/zinc `blocks_execution` badge, and a findings table under the heading "Recent execution findings (strategy-wide, most-recent)" (correct honesty framing — not "this reconciliation's findings"). 15-03 confirmed the null-state live. Non-null branch (badge colors, findings rows, details column) never exercised — see human_verification item 2. |
| 4 | Current positions (PAPR-01): open positions visible with symbol/qty/avg-entry/cost-basis by default; non-open rows disclosed + revealable, never silently dropped; explicit empty state | ✓ code-verified / ? populated-render not live-exercised | `PositionsPanel.tsx` — self-fetches `/api/v1/operations/positions?strategy_id=trend_following_daily&limit=100`, filters `status === "open"`, computes `hiddenCount`, renders disclosure + reveal toggle, renders `"No open positions."` when empty, renders a cap note when `count >= 100`. 15-03 confirmed the zero-rows empty state live. Disclosure/reveal-toggle and cap-note branches never exercised against real mixed data — see human_verification items 3 and 4. |
| 5 | Open orders (PAPR-02): open-lifecycle orders visible with symbol/side/qty/status/client_order_id by default; terminal orders disclosed + revealable; 100-row cap disclosed | ✓ code-verified / ? populated-render not live-exercised | `OpenOrdersPanel.tsx` — self-fetches `/api/v1/operations/orders?strategy_id=trend_following_daily&limit=100`, filters via `OPEN_STATUSES = {pending_submission, submitted, partially_filled}`, computes `hiddenCount`, renders disclosure + reveal toggle, renders `"No open orders."` when empty, renders cap note when `count >= 100`. 15-03 confirmed the zero-rows empty state live. Disclosure/reveal-toggle and cap-note branches never exercised — see human_verification items 3 and 4. |
| 6 | Every panel degrades to an ErrorState naming its exact endpoint on API-down; never blank/fake-success | ✓ VERIFIED | 15-03-SUMMARY step 6: operator stopped the backend, pressed Refresh on each section, and confirmed the Account & Reconciliation section named `/api/v1/analytics/strategies/trend_following_daily`, Positions named `/api/v1/operations/positions`, and Open Orders named `/api/v1/operations/orders`; kill-switch banner showed amber "unknown"; restart + refresh confirmed recovery. |

**Score:** 6/6 truths hold at the code level and build/lint gate; 2/6 (nav link, error-degradation) are fully live-verified end to end; 4/6 have their empty-state branch live-verified but their populated-data branch is unexercised due to no available broker data (Alpaca paper credentials unconfigured, a documented pre-existing constraint, not a code gap).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `console/src/components/paper/PaperAccountPanel.tsx` | PAPR-04 presentational panel, min 25 lines | ✓ VERIFIED | 53 lines. Null empty state + populated `<dl>` with all required fields. No `fetch()`, no StatusPanel import. |
| `console/src/components/paper/PaperReconciliationPanel.tsx` | PAPR-03 presentational panel, min 30 lines | ✓ VERIFIED | 124 lines. Null empty state, blocks_execution badge, scope-labelled findings table with per-row highlight. |
| `console/src/components/paper/PaperAnalyticsSection.tsx` | Single shared useApiQuery + FetchMeta/ErrorState chrome, min 40 lines | ✓ VERIFIED | 67 lines. One `useApiQuery<AnalyticsResponse>("/api/v1/analytics/strategies/trend_following_daily")` call; feeds `.paper` slice to both panels; single ErrorState branch. |
| `console/src/app/paper/page.tsx` | Route composing all panels, min 8 lines | ✓ VERIFIED | 23 lines. Renders `PaperAnalyticsSection` + `PositionsPanel` + `OpenOrdersPanel` in one `<main>`. Confirmed as a registered static route by `next build`. |
| `console/src/app/layout.tsx` | Contains `/paper` nav link | ✓ VERIFIED | Line 46: `href="/paper"`, matching sibling link className exactly. |
| `console/src/components/paper/PositionsPanel.tsx` | PAPR-01 self-fetching panel, min 45 lines | ✓ VERIFIED | 148 lines. Fetches, filters open, discloses+reveals, empty state, cap note. |
| `console/src/components/paper/OpenOrdersPanel.tsx` | PAPR-02 self-fetching panel, min 50 lines | ✓ VERIFIED | 175 lines. Fetches, `OPEN_STATUSES` filter, discloses+reveals, empty state, cap note, inline error surfacing. |
| `console/src/components/paper/types.ts` | Shared types (extra artifact, not in must_haves but referenced by all panels) | ✓ VERIFIED | 46 lines, matches the plan's verified interface block exactly. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `PaperAnalyticsSection.tsx` | `/api/v1/analytics/strategies/trend_following_daily` | `useApiQuery` | ✓ WIRED | Line 19, single-line call matches plan's grep pattern; backend route confirmed present at `src/trading_platform/api/routes/analytics.py`. |
| `console/src/app/layout.tsx` | `/paper` route | `next/link` | ✓ WIRED | Line 46, `href="/paper"`. |
| `PositionsPanel.tsx` | `/api/v1/operations/positions` | `useApiQuery` | ✓ WIRED | Line 42; backend route confirmed at `src/trading_platform/api/routes/operations.py:42-43`. |
| `OpenOrdersPanel.tsx` | `/api/v1/operations/orders` | `useApiQuery` | ✓ WIRED | Line 59; backend route confirmed at `src/trading_platform/api/routes/operations.py:22`. |
| `paper/page.tsx` | `PositionsPanel` + `OpenOrdersPanel` | import + render | ✓ WIRED | Lines 2-3 import, lines 18-19 render, alongside `PaperAnalyticsSection`. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| PAPR-01 | 15-02 | Operator can view current positions | ✓ SATISFIED (empty-path live-verified; populated-path code-verified only) | `PositionsPanel.tsx`; REQUIREMENTS.md marks `[x]` Complete. |
| PAPR-02 | 15-02 | Operator can view open orders | ✓ SATISFIED (empty-path live-verified; populated-path code-verified only) | `OpenOrdersPanel.tsx`; REQUIREMENTS.md marks `[x]` Complete. |
| PAPR-03 | 15-01 | Operator can view latest reconciliation result and findings | ✓ SATISFIED (empty-path live-verified; populated-path code-verified only) | `PaperReconciliationPanel.tsx`; REQUIREMENTS.md marks `[x]` Complete. |
| PAPR-04 | 15-01 | Operator can view latest account snapshot (equity, cash, buying power) | ✓ SATISFIED (empty-path live-verified; populated-path code-verified only) | `PaperAccountPanel.tsx`; REQUIREMENTS.md marks `[x]` Complete. |

No orphaned requirements: REQUIREMENTS.md's "Paper Trading Status" section lists exactly PAPR-01..04, and both 15-01 (`requirements: [PAPR-03, PAPR-04]`) and 15-02 (`requirements: [PAPR-01, PAPR-02]`) claim all four between them. 15-03 explicitly claims no requirements field (verification-only plan) and does not need to.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `console/src/components/paper/PaperAccountPanel.tsx` | 25, 30, 35 | `toLocaleString()` on `total_equity`/`cash`/`buying_power` | ℹ️ Info | Rounds to browser-locale default precision (typically 3 decimals) whereas `PositionsPanel`/`OpenOrdersPanel` render `quantity`/`average_entry_price`/`cost_basis` as raw numbers verbatim. This was explicitly sanctioned by the 15-01 plan text ("Format numbers verbatim (e.g. `String(value)` or `toLocaleString()`)"), so it is not a deviation from spec, but it is an inconsistency worth noting given the phase's "never hide precision" honesty theme — sub-cent equity/cash precision could be silently rounded in display. Not a blocker. |

No TODO/FIXME/placeholder/console.log-only implementations found in any of the 7 phase-15 files.

### Human Verification Required

### 1. Account snapshot populated-data render (PAPR-04)

**Test:** Configure Alpaca paper credentials (or otherwise get a non-null `latest_account_snapshot` into the analytics response) and reload `/paper`.
**Expected:** `PaperAccountPanel` renders real Total equity, Cash, Buying power (plus gross exposure/open positions/snapshot source/snapshot-at) — not zeros, not the empty-state message.
**Why human:** External broker integration; this branch exists in code and passed build/lint but has never received live data — 15-03 observed only the null-snapshot path.

### 2. Reconciliation populated-data render + blocks_execution badge (PAPR-03)

**Test:** With a non-null `latest_reconciliation` present (ideally one instance with `blocks_execution: true` and one with `false`), reload `/paper`.
**Expected:** Status/as_of_session/finding_count/blocking_count/completed_at render correctly; the red "BLOCKS EXECUTION" badge and zinc "does not block execution" badge both display correctly depending on the real flag; findings table rows render with correct severity/message/details formatting, and rows with `blocks_execution: true` are visually highlighted.
**Why human:** Never exercised live; only the null-reconciliation empty state was confirmed in 15-03.

### 3. Positions/orders disclosure + reveal-toggle with real mixed data (PAPR-01/PAPR-02)

**Test:** With a mix of open and closed positions (and open and terminal orders) present, reload `/paper` and exercise the "Reveal all" / "Show open only" toggle on both panels.
**Expected:** Default view shows only open rows; "Hiding N non-open …" note shows the correct count; Reveal all shows every row with a visible Status column; toggle flips back correctly.
**Why human:** Code path exists and is plausible from inspection, but 15-03 only ever observed `hiddenCount === 0` (zero total rows) — the disclosure/reveal interaction itself is unexercised.

### 4. 100-row truncation disclosure with a real capped response (PAPR-01/PAPR-02)

**Test:** Seed ≥100 raw rows for `trend_following_daily` positions and/or orders so `data.count >= 100`, then reload `/paper`.
**Expected:** The truncation note renders (and, if the filtered/open subset is also empty, the compound "...older rows may be truncated" message renders instead of a bare "No open positions/orders.").
**Why human:** This is a data-availability gap, not a code gap — the branch was never reached live since Alpaca paper credentials remain unconfigured and no other data source ever produced ≥100 rows.

### Gaps Summary

No code gaps. All four PAPR requirements are implemented, wired to their correct backend endpoints (confirmed to exist server-side), build and lint cleanly, contain no direct-`fetch()` bypasses, contain no placeholder/TODO/stub patterns, and match every artifact/key-link must-have declared in the 15-01/15-02 plan frontmatter. The `/paper` route is reachable from a nav link present on every screen (root layout), and Phase 15's own operator checkpoint (15-03) live-confirmed the honest-empty-state and endpoint-named-error-state behavior — the two branches that were actually reachable given the current environment (Alpaca paper credentials unconfigured, per STATE.md, a pre-existing and explicitly documented constraint carried from before this phase).

The remaining gap is a **data-availability gap, not a code gap**: the ROADMAP's success criteria for Phase 15 are phrased as "Operator can view current positions and open orders … the latest reconciliation result … the latest account snapshot" — i.e., viewing real values, not just honest absence-of-value. That stronger claim (populated `<dl>`/table rendering, the blocks_execution badge in both states, the reveal-toggle actually revealing something, and the 100-row cap note under a real capped response) has code coverage and passed automated build/lint, but has never been exercised against real data because no broker feed or seeded dataset has produced non-empty positions/orders/snapshot/reconciliation rows yet. This is why overall status is `human_needed` rather than `passed`: the four items above should be re-run through the same 15-03-style live checkpoint once Alpaca paper credentials are configured (or a seeded dataset is available), at which point this phase can be marked fully `passed` with no further code changes anticipated.

This does not block Phase 16 (Analytics & Charting), which depends on Phase 14's run-detail/selection UX, not on Phase 15's live broker data.

---

*Verified: 2026-07-09*
*Verifier: Claude (gsd-verifier)*
