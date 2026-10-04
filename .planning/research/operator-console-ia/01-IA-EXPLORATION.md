# Operator Console — Product Understanding & Information Architecture Exploration

_Date: 2026-09-29 · Type: research (no implementation) · Successor: [02-HYBRID-IA-PROPOSAL.md](02-HYBRID-IA-PROPOSAL.md)_

Status: exploration only. No production code, API, or backend behavior changed.
Evidence: backend source (`src/trading_platform`), console source (`console/src`), and the live local console/API on 2026-09-29.

---

## 1. How the platform works (operator's-eye model)

### 1.1 What the product is
A single-operator **paper-trading** system for one strategy today (`TrendFollowingDailyV1`, long-only SMA50/SMA200 trend following over 10 US equities, daily bars) against one Alpaca paper account. It also runs **backtests**. There is **no scheduler**: every step of the trading day is started by the operator by hand.

### 1.2 The trading day is a pipeline of five manual steps
Each step is a Job type. The order matters and the console does not say so.

| # | Operator intent | Job type | What it really does | Produces |
|---|---|---|---|---|
| 1 | "Make sure I have today's data" | `sync-market-sessions`, `ingest-bars` (`sync-symbol-metadata` is optional; nothing depends on it) | Exchange calendar + daily OHLCV from Polygon | session rows, bars, ingestion run (`succeeded/partial/failed`) |
| 2 | "What does the strategy want to do, and is it allowed?" | `risk-evaluation` | Generates signals for the session **and** runs every signal through the risk engine. Needs no broker. | `risk_evaluation` run + one risk decision per symbol + an account snapshot |
| 3 | "Trade" | `paper-session` | Uses the latest *succeeded* risk run for that session. Recovers in-flight orders, **reconciles against the broker**, then gates (strategy disabled → reconciliation blocks → nothing-to-do → kill switch), then submits approved orders to Alpaca. | a `reconciliation` run and, if it got that far, a `paper_execution` run |
| 4 | "Bring my books up to date with the broker" | `broker-order-sync` | Pulls order states, fills, positions, account from Alpaca | orders/fills/positions updated, `broker_sync` snapshot (no run) |
| 5 | "Are my books and the broker in agreement?" | `reconciliation` | Report-only comparison of local vs broker orders/fills/positions/account | `reconciliation` run with findings; `blocks_execution` flag |

Backtests (`backtest`) are a separate research loop: date range → metrics, trades, equity curve.

### 1.3 Who can stop trading, and what "stopped" means
Trading = submission of new paper orders. Three things can stop it:

| Gate | State | Checked | What it does **not** do |
|---|---|---|---|
| Global kill switch | `armed` (trading may proceed) / `tripped` (blocked) | inside every paper session, before and during submission | does not cancel open broker orders; does not cancel queued Jobs; does not stop syncs/reconciliation |
| Strategy status | `active`/`disabled` (UI: enabled/disabled). YAML `enabled` is display-only | first gate in paper session | does not stop risk evaluation, backtests, syncs |
| Reconciliation | latest reconciliation `blocks_execution` (any finding: `MISSING_LOCAL`, `MISSING_BROKER`, `QUANTITY_MISMATCH`, `PRICE_MISMATCH`, `STATE_MISMATCH`, account divergence, `submission_/sync_failure_threshold_exceeded`) | re-computed inside every paper session | there is no "resolve" action — it clears only when books and broker agree |

Things that **do not** block a session but silently cause it to trade nothing: stale/missing bars (become `stale_market_data` risk rejections → `noop_no_candidates`), insufficient history, market closed (no market-hours check exists).

### 1.4 Outcomes the operator must be able to read
A `paper-session` Job ends **SUCCEEDED** even when trading was blocked. The truth is `result_summary.action`:
`submitted_missing_orders` · `noop_no_candidates` · `noop_existing_orders` · `blocked_strategy_disabled` · `blocked_reconciliation` · `blocked_global_kill_switch` (also used for mid-run trips, possibly after partial submission).
Because the nothing-to-do check runs before the kill-switch check, a tripped switch with nothing to trade reports `noop_*`, not blocked.

A Job that **FAILED** after touching the broker has `outcome_uncertain = true` ("orders may or may not have been sent"). Retrying such a `paper-session`/`broker-order-sync` is refused with `reconciliation_required` until a newer reconciliation Job succeeds.

### 1.5 Concept map — what the operator needs vs. what is implementation

| Tier | Concepts |
|---|---|
| **Domain (operator must understand)** | Trading permission (allowed/blocked + why) · Strategy (rules, universe, enabled) · Trading session (a market day's decision → orders) · Signal · Risk decision (approved/rejected + reason) · Order · Fill · Position · Account (cash, equity, exposure) · Books-vs-broker agreement (reconciliation) · Market data readiness · Backtest result |
| **Safety-critical** | Kill switch state + consequences · Strategy enabled · Reconciliation blocking · Uncertain outcomes (orders may have been sent) · Order status `unknown` · Repeated submission/sync failures · Broker-accepted-but-not-recorded (`reconciliation_scheduled`) |
| **Decision support** | Why no trades (risk reason distribution) · Data freshness / coverage · Exposure vs caps · Backtest metrics · "What should I run next" |
| **History / audit** | Sessions by date · Control changes (who/when/why) · Operations run · Orders/fills history · Backtests |
| **Debug / technical** | Job lifecycle events, logs, progress, payload, lease/idempotency · Run IDs, artifact counts · parameters_snapshot / result_summary JSON · health/readiness/DB info |
| **Internal (should not be first-class in the UI)** | `StrategyRun` as a generic container (`operator_control` runs!) · `dry_bootstrap` · Job as a separate top-level noun · execution-event `details` JSON · snake/kebab-case enum codes |

**On Jobs:** from the code, a Job is the *transport* for an operator action (lifecycle, cancellation, retry, logs). The operator's noun is the **operation** ("ingest bars for Mar 13", "run today's session"), and its meaningful output lives in domain records (a session outcome, a reconciliation result). Jobs deserve first-class **visibility** (progress, failure, uncertain outcome, retry lineage), but not necessarily a first-class **navigation** slot. The four directions take four different stances on this on purpose.

### 1.6 Gaps in today's read API that any redesign will hit
Marked on every artboard as **[needs new read]** vs **[derived]** (computable from existing endpoints in the console).

| Needed by the UX | Today |
|---|---|
| Worker alive / queue moving | **Not exposed.** `heartbeat_at`, `lease_*` not serialized; `/ready` checks DB only. Queued Jobs wait silently if the worker is down. |
| Market-data freshness/coverage | Not exposed; only visible indirectly as `stale_market_data` risk rejections. |
| Reconciliation detail/findings | No endpoint; only `runs?run_type=reconciliation`, analytics `paper.latest_reconciliation`, and execution events. |
| Unified history / failure indicator | Not exposed (Phase 21 plans it). `OperatorStatusService` already aggregates most "home screen" facts but is CLI-only (`operator-status`). |
| Trading permission | **Derivable** from kill-switch GET + `controls/strategies/{id}` GET + analytics `paper.latest_reconciliation.blocks_execution`. |

---

## 2. Audit of the current console (problems, with live examples)

Current nav: System Status · Strategy · Runs · Jobs · Paper Trading · Controls, plus a kill-switch banner.

### The headline example — "everything is green, and nothing can trade"
On 2026-09-29 the console shows: Health **ok**, Readiness **ready**, Kill switch **ARMED**, Strategy **ENABLED**, Reconciliation **succeeded / does not block execution**, latest paper-session Job **SUCCEEDED**.
The actual situation:
- Latest session with data is **2026-03-13**, ~6.5 months behind today. Every session/reconciliation/risk run uses it.
- The last risk evaluation rejected all 10 symbols: bars missing for AMD, AMZN, META, NVDA, QQQ, TSLA (`stale_market_data`) and 7 symbols `insufficient_history`.
- So the "SUCCEEDED" paper session did nothing (`noop_no_candidates`).
- 3 `ingest-bars` Jobs failed today (`handler_error`), and 3 broker-touching Jobs (paper-session 09:23 and 13:22, broker-order-sync 13:22) failed with `outcome_uncertain` — 7 failures in total; nothing outside the Jobs table says so.
- A paper session failed at 13:22 with **outcome uncertain** (Alpaca 422) — orders might have been sent. The console shows this only as a field on the job detail page.
No screen says "trading is effectively not happening, because market data is incomplete; fix by ingesting bars for 6 symbols".

### Problems by category
**Status & interpretation**
1. **"ARMED"** is the kill switch's *allowed* state, but reads like "loaded/dangerous". No page says "Trading: allowed" or "blocked".
2. **SUCCEEDED ≠ traded.** Job status is shown; `result_summary.action` (`noop_no_candidates`, `blocked_*`) is a raw key/value in the job detail.
3. **Ingest SUCCEEDED ≠ ingested.** The 15:48 `ingest-bars` Job is SUCCEEDED while its result says `ingestion_succeeded: false`, `symbols_failed: ["QQQ"]` (partial ingestion is not a Job failure).
4. **Reconciliation "succeeded" + "does not block execution"** with as-of **2026-03-13** — freshness is never interpreted.
5. **Outcome uncertain** — the most safety-critical failure — is a "Yes" field between "Failure message" and "Dependencies".
6. Data without interpretation: raw ISO timestamps with microseconds and timezone (`2026-09-29T15:50:13.878685+03:00`), raw codes (`handler_error`, `operator_control`, `api_control`, `broker_sync`), raw JSON in "Details".

**Hierarchy & emphasis**
7. **Home is infrastructure.** System Status shows DB driver, DB host, schema manager, app version — answers "is the server up?", not "is trading OK?". Account, positions, last session outcome are not on the home page.
8. **"Latest Run" on home is a kill-switch reset** (`operator_control`, trigger `api_control`) — presented as the latest strategy run.
9. **Runs is ~80% `operator_control` rows** (every control click, even a no-op, creates a run). Real sessions, risk evaluations and backtests are buried.
10. **"Recent execution findings" on Paper Trading are control audit events** with a raw JSON column; actual reconciliation findings would look identical.

**Navigation & relationships**
11. **Jobs and Runs are two parallel histories** of the same operations (a Job produces run IDs; runs link back to a Job), with no joined view and different vocabularies (`paper-session` Job vs `reconciliation`+`paper_execution` runs).
12. **Kill switch appears in 3 places** (banner, System Status, Controls); strategy enable/disable in 2 (Strategy, Controls). Controls is a page for two buttons.
13. **Actions are scattered and unexplained**: Run backtest / Evaluate risk on Strategy; Run paper session / Run reconciliation / Sync broker orders on Paper; generic New Job lists 8 types alphabetically with one-line descriptions. Nothing tells the operator the order (data → risk → session → sync), prerequisites (a session needs a succeeded risk run), or consequences (paper session may submit orders to Alpaca; kill switch doesn't cancel open orders).
14. Answering "why didn't it trade today?" takes **Jobs → job detail → linked run ID → (different) risk run ID not linked → Runs → run detail → risk decisions table** — 5+ hops, requiring knowledge that a session consumes a risk run.

**Terminology**
15. ENABLED (UI) vs `active` (DB) vs YAML `enabled: true` (display-only, never gates).
16. Four words for similar things: run, job, session, operation. "Paper Trading" page vs "paper-session" job vs `paper_execution` run.
17. Kebab-case job types and snake_case run types shown verbatim.

**Missing, safety-relevant**
18. No global "something needs attention" indicator; failures are found by scanning a table (Phase 21 plans one).
19. No worker liveness: a queued Job with the worker down looks identical to a Job about to start.
20. No data-freshness signal anywhere.

**Mobile**
21. Six inline top-nav links; 6–7-column tables (Runs, Jobs, findings with JSON); desktop 2-column panel grid. The one mobile-critical task — "check status / trip the kill switch" — requires a desktop-shaped page.

---

## 3. Operator mental models & workflows

| # | Question / job | Needs to see | Decisions | Actions | Deeper, when wrong |
|---|---|---|---|---|---|
| W1 | **"Is everything OK right now?"** (glance, incl. phone) | Trading permission + reason; latest session outcome in words; open positions/orders count; anything needing attention | Do nothing / investigate / stop trading | Trip kill switch | Attention item → cause |
| W2 | **"Run today's trading"** | Which session; data readiness; whether a risk evaluation exists for it and what it approved; gates; what will happen at the broker | Proceed? Which session? | Ingest/sync data → Evaluate → Run session → Sync broker | Stage failure → operation log |
| W3 | **"What happened in the last session / why no trades?"** | Session outcome sentence; per-symbol signal + risk decision; orders submitted/filled | Is "no trades" correct or a data problem? | Re-ingest data; re-evaluate | Risk decision reasons; data coverage; job logs |
| W4 | **"Stop everything now"** | Current state; exactly what trip does and does not do; open orders at broker | Trip kill switch vs disable strategy | Trip / reset (reason required); disable/enable | Control audit trail |
| W5 | **"Something failed — fix it"** | What failed, in words; whether orders might have been sent (uncertain); what's safe to do next | Retry? Reconcile first? Ignore? | Reconcile → Retry; Cancel queued | Job events, logs, payload, lineage |
| W6 | **"Do my books agree with the broker?"** | Positions, orders, cash/equity; last reconciliation + age + findings | Is trading blocked by drift? | Sync broker; Reconcile | Finding list per symbol; order lifecycle events |
| W7 | **"Is the strategy any good?"** (research) | Strategy rules/config; backtest results; compare runs | Keep/adjust/disable | Run backtest | Signals, trades, equity curve, assumptions |
| W8 | **"Keep the data healthy"** | Latest session with bars; coverage gaps by symbol; calendar range; last ingest outcome | Which range/symbols to fetch | Ingest bars; sync sessions; sync metadata | Ingestion run per-symbol failures |
| W9 | **"What changed, when, and why?"** (audit) | Unified chronological record: controls (reason), operations, sessions, failures | — | — | Record detail |
| W10 | **"Debug the machine"** | Job lifecycle/events/logs/progress; run artifacts; health/readiness/config | Is it infra or domain? | Cancel, retry | Raw JSON, IDs |

Frequency/urgency shape: **W1 is constant and often mobile; W4/W5 are rare but urgent; W2 is daily; W3/W6 follow W2; W7–W10 are occasional and desktop.**

---

## 4. Information-architecture directions

A shared scenario is rendered in every direction so architectures, not data, are compared:
**Scenario S1 (live, 2026-09-29 ~17:30):** kill switch armed · strategy enabled · latest data session 2026-03-13 · risk run: 0/10 approved (3 exit signals rejected `stale_market_data` because bars are missing for 6 symbols; 7 symbols flat `insufficient_history`) · session 15:47 `noop_no_candidates` · 3 failed `ingest-bars` today · 3 broker-touching failures with outcome uncertain (paper-session 09:23 and 13:22 — Alpaca 422 — and broker-order-sync 13:22); a reconciliation succeeded at 15:46 afterwards, so retry is now permitted · account $100,000 cash, 0 positions, 0 open orders.

Jobs stance per direction (deliberately different):
- A — **Operations are an activity layer** (a global tray + Operations page).
- B — **Jobs dissolve into pipeline stages** ("attempts" of a stage).
- C — **Jobs are debug-tier only**; the unit is an *event* in a timeline or an *issue*.
- D — **Operations belong to the object they act on** (strategy vs platform).

### Direction A — "Posture Board" (state-first control room)
1. **Mental model:** The system has a small number of *postures*, each always visible and colour-coded by meaning: **Trading permission · Market data · Books vs broker · Operations engine**. You read posture first, then drill into a domain.
2. **Top-level IA:** Overview · Portfolio · Strategy · Research · Operations · (Settings/System under Operations).
3. **Navigation:** Persistent left rail with domain pages; a persistent top **Posture strip** (4 lanes, each clickable to its domain) on every page; kill switch button lives in the strip.
4. **Primary workflows:** W1 = read strip. W2 = Overview "Next step" card + actions on the domain page that owns them (ingest on Market data, session on Portfolio/Trading). W5 = Operations page, filtered to failures.
5. **System state:** Operations lane: "Worker: not reported [needs new read]", queue depth, failures today. Infra details (DB, versions) on Operations › System.
6. **Trading state:** Trading lane: "Allowed — kill switch armed, strategy enabled, books agree" [derived]; Overview shows last session outcome sentence + account/positions.
7. **Problems & safety:** Lanes turn amber/red with a one-line reason ("Market data: 6 of 10 symbols missing bars for 13 Mar"). Uncertain outcomes are a red Books lane state.
8. **Actions:** Contextual — each domain page has its 1–3 actions with a consequence line. Global activity tray shows running/finished operations.
9. **History:** Each domain has a "Recent" section; Operations page is the cross-domain log of operator actions and their outcomes; control audit under Trading permission.
10. **Tech/debug:** Operation detail (job events/logs/payload) reachable from tray and Operations; Run detail reachable from session/backtest records; "Technical" disclosure sections.
11. **Mobile:** Posture strip becomes a vertical 4-row status list (the home screen); bottom tabs: Status · Portfolio · Activity · More. Kill switch in header.
12. **Advantages:** Answers W1 in one glance on every page; closest to how operators of real systems (trading desks, SRE) read state; incremental from today's code.
13. **Trade-offs:** Posture lanes need derivations and at least two new reads (worker, data freshness) to be honest; lanes can over-simplify multi-cause problems.
14. **Solves:** #1, #4, #5, #7, #8, #12, #18, #20, mobile glance.
15. **Makes harder:** Workflows that cross domains (W2) still involve moving between pages; "what happened when" is spread across domains.

### Direction B — "Trading Day" (session pipeline / runbook)
1. **Mental model:** The unit of the product is **the trading session** (a market date). Each session moves through a fixed pipeline: **Data → Decide (signals & risk) → Trade → Sync → Verify**. The console is a runbook for today and a journal of past days.
2. **Top-level IA:** Today · Journal (sessions) · Portfolio · Strategy Lab (config + backtests) · System.
3. **Navigation:** Top tabs; Today is home. Journal rows open a **Session page** (same pipeline, frozen).
4. **Primary workflows:** W2 is the spine — each stage shows status, explanation, and exactly one primary action, enabled only when prerequisites are met ("Evaluate needs bars for all 10 symbols"). W3 = open the session → Decide stage → per-symbol decisions.
5. **System state:** A thin "Machine" footer (worker [needs new read], last failure) and System tab; infra only matters when a stage can't run.
6. **Trading state:** Gates (kill switch, strategy, reconciliation) are drawn *on* the Trade stage as three locks; account/positions on Portfolio and in the Sync stage summary.
7. **Problems & safety:** A stage turns red/amber with the reason and the fix; uncertain outcome shows on the Trade stage as "orders may have been sent — Verify before retrying" and the Verify stage becomes the primary action.
8. **Actions:** Ordered, stage-bound, with consequence text. Kill switch sits on the Trade stage and in the header.
9. **History:** Journal = one row per session date with an outcome sentence ("13 Mar — nothing traded: data incomplete"), plus stage attempts inside each session.
10. **Tech/debug:** Each stage attempt = a Job; expand an attempt to see events/logs; runs appear as "records" inside stages.
11. **Mobile:** Pipeline becomes a vertical stepper; Today is essentially the whole mobile app; Journal is a list.
12. **Advantages:** Teaches the system's real order of operations; makes prerequisites/gates visible; "why no trades?" is one tap; matches future scheduling (a scheduler would just advance stages).
13. **Trade-offs:** Backtests, data maintenance over ranges, and multi-session operations don't fit a single day; needs a session-keyed read that groups jobs/runs by `as_of_session` [needs new read, or heavy client joining]. The session is 13 Mar while the calendar says 29 Sep — the model must show "latest session with data" honestly.
14. **Solves:** #2, #3, #11, #13, #14, #16, W2/W3 friction.
15. **Makes harder:** Non-session work (ingest arbitrary ranges, backtests) is secondary; cross-session situations (drift from last week) are less visible; the pipeline can imply automation that doesn't exist.

### Direction C — "Attention & Timeline" (exception-driven)
1. **Mental model:** The operator is **on call**. The console's job is to say: "here is the verdict, here is what needs you, here is what happened." Everything else is lookup.
2. **Top-level IA:** Attention (home) · Timeline · Look up (Portfolio, Strategy, Sessions, Backtests, Operations) · Run… (command menu).
3. **Navigation:** Minimal; home is a **verdict sentence** + **attention queue**; a **Run…** command palette launches any operation; "Look up" is an object browser/search.
4. **Primary workflows:** W1/W5 = read verdict and top issue; each issue card contains *what happened · why it matters · what to do (buttons) · what the action will do*. W9 = Timeline.
5. **System state:** Only as issues ("Operations engine: no heartbeat data — can't confirm worker is running" [needs new read]) or in Look up › System.
6. **Trading state:** Verdict line always on top: "Trading allowed, but nothing will trade: market data incomplete for 13 Mar." Account/positions summary chip in the header.
7. **Problems & safety:** Issues ranked by severity: Blocking/safety → Needs action → FYI. Uncertain-outcome issue explains the reconcile-first rule and offers "Reconcile now" then "Retry".
8. **Actions:** Attached to issues (recommended) + Run… palette (everything) + kill switch in header. Every action shows a consequence summary before confirm.
9. **History:** One unified Timeline interleaving sessions, operations, control changes, findings, failures — filterable; each item expands inline (Phase 21's `/history` would feed it [needs new read]).
10. **Tech/debug:** Jobs are never a nav item; a timeline item or issue has "Technical record" → job lifecycle/logs/payload.
11. **Mobile:** The most natural: inbox of issue cards; tap = full-screen card with actions; Timeline tab; kill switch in header.
12. **Advantages:** Lowest cognitive load when things are fine; best for "what do I do next"; strongest mobile story; aligns with Phase 21 (history + failure indicator).
13. **Trade-offs:** Needs a good rules layer to turn facts into issues (the product's intelligence lives in these rules); when there are no issues, the home is sparse and positional awareness (portfolio) is secondary; relies on new aggregate reads.
14. **Solves:** #1–#6, #9–#11, #13, #18, W5, mobile.
15. **Makes harder:** Browsing/exploration (research, config) is pushed into "Look up"; operators who want a fixed dashboard of numbers get less; issue rules must be maintained or the inbox lies.

### Direction D — "Strategy Workspace" (object-centric, multi-strategy-ready)
1. **Mental model:** The platform hosts **strategies**; each strategy is a workspace with its live trading, its decisions, its research and its controls. Shared **Platform** services (market data, broker account, operations engine) sit underneath. A global **safety bar** is above everything.
2. **Top-level IA:** Safety bar · Strategies (▾ switcher; one today) → [Live · Decisions · Research · History · Settings & controls] · Account · Market data · Platform (operations, system).
3. **Navigation:** Left rail with strategy list + platform sections; tabs inside a strategy.
4. **Primary workflows:** W2/W3 inside the strategy's Live and Decisions tabs; W7 in Research; W8 in Market data; W4 = safety bar (global) + strategy controls (local).
5. **System state:** Platform section: operations engine, data coverage, broker connectivity; a platform status dot in the rail.
6. **Trading state:** Strategy Live tab: enabled, last session outcome, orders/positions attributed to the strategy; Account page = broker-level truth.
7. **Problems & safety:** Global issues in the safety bar; strategy-scoped issues on the strategy's rail item (badge) and Live tab.
8. **Actions:** Owned by the object: strategy actions (evaluate, run session, backtest, enable/disable) in the strategy; platform actions (ingest, sync calendar, sync broker, reconcile) in Platform/Account.
9. **History:** Per-strategy History tab (sessions, backtests, control changes) + Platform › Operations log.
10. **Tech/debug:** Platform › Operations (jobs) and "Technical" tabs on records.
11. **Mobile:** Strategy switcher at top, tabs become a segmented control; Platform under More. Weakest mobile story of the four.
12. **Advantages:** Matches the backend's per-strategy scoping and the roadmap (Strategy Lab, Portfolio, promotion pipeline); clear ownership of actions; scales to N strategies.
13. **Trade-offs:** With one strategy today, the workspace layer is overhead; account/reconciliation are per-strategy in the API but one broker account in reality — ownership is ambiguous; cross-strategy "is everything OK" must be solved separately (safety bar).
14. **Solves:** #11, #12, #13, #15, future growth.
15. **Makes harder:** Glance-level W1 (needs the safety bar to carry it), mobile, and platform-level problems that affect all strategies.

---

## 5. Decisions needed before visual work
1. **Primary organizing unit:** state (A), time/session (B), exceptions (C), or object/strategy (D)? Hybrids are possible (e.g. C's verdict + attention on top of B's Today).
2. **Is Jobs a navigation destination** or only reachable from what it produced / an activity layer / debug tier?
3. **Home screen question:** "Is it safe and working?" vs "What do I do next?" vs "What's my portfolio?"
4. **How much interpretation the console may do:** are derived verdicts ("nothing will trade because data is incomplete") acceptable in the console, or must interpretation come from the backend (new aggregate reads, e.g. exposing `OperatorStatusService`)?
5. **Which read-side additions are acceptable** (worker liveness, data freshness, reconciliation detail, unified history)? Some directions depend on them to be honest.
6. **Mobile scope:** glance + kill switch only, or full triage (reconcile, retry) from the phone?
7. **Multi-strategy horizon:** design for 1 strategy now (A/B/C) or pay the workspace cost now (D)?
8. **Research placement:** is backtesting part of the operator console or a separate "Lab" area with its own IA (Stage 2)?
9. **Vocabulary:** adopt operator terms (Trading allowed/blocked, Session, Operation, Books vs broker) and keep enum codes only in technical views?
10. **Phase 21 scope interaction:** Phase 21 plans `/history` + global failure indicator + nav changes; the chosen direction should reshape or supersede it before it's implemented.

---

## 6. Paper file status
File: "Operator Console — IA Exploration" — https://app.paper.design/file/01M3PW8KGBK78HTT6TEFVTWDP9

| Page | Artboards | Status |
|---|---|---|
| 00 · Model, audit, scenario | 00.1 Scenario S1 (console says vs true) · 00.2 Product model (pipeline, gates, concept tiers) · 00.3 Operator questions W1–W10 · 00.4 Audit | done |
| A · Posture Board | A.0 card · A.1 Overview · A.2 Market data domain page · A.3 Mobile status + Stop trading sheet | done |
| B · Trading Day | B.0 card · B.1 Today pipeline · B.2 Session 13 Mar drill-down · B.3 Mobile stepper + Journal | done |
| C · Attention & Timeline | C.0 card · C.1 Attention (verdict, issues, timeline rail) | desktop home done; last screenshot review skipped; drill-down + mobile not built |
| D · Strategy Workspace | — | not built in Paper (fully specified in §4 above) |

Paper's weekly MCP quota ran out mid-build (resets in ~3 days, or with Paper Pro).

### Verification + errata (2026-09-29, read-only API check)
- The 15:48 `ingest-bars` Job (710d46bf) is SUCCEEDED, but its result says `ingestion_succeeded: false`, `symbols_failed: ["QQQ"]`. It covered only QQQ/SPY for 9–13 Mar. **Another "success hides failure" case — add to audit.** The six missing symbols (AMD, AMZN, META, NVDA, QQQ, TSLA) are still missing, so the scenario claim holds, as of the last evaluation.
- The two failed ingests at 15:47 were `IngestionAllSymbolsFailedError` (PolygonClientError for QQQ and SPY).
- Errata, C.1 "Ingest has failed 3 times today" body should read: "Failures at 13:21, 15:47, 15:47 (Polygon client errors). The 15:48 ingest is marked succeeded, but QQQ failed inside it and it only covered QQQ/SPY — the six missing symbols are still missing." (Paper quota blocked the edit.)
- **Correction (found while writing the hybrid proposal):** the 15:46:50 standalone reconciliation (Job c32bc2d5) **blocked execution** — 0 findings but `account_divergence.buying_power` local 100000 vs broker 400000 (the 13:22 risk-evaluation snapshot writes `buying_power=cash`). The broker sync at 15:46:58 then wrote a broker snapshot, and the in-session check at 15:47:16 was clean. Artboards A.1, A.2, B.1, B.2 and C.1 that say "reconciled 15:46, clean / books agree" are wrong: the clean check was 15:47, inside the session. The backend still unlocked retry at 15:46 because its reconcile-first rule only requires a newer *succeeded* reconciliation Job. See [02-HYBRID-IA-PROPOSAL.md](02-HYBRID-IA-PROPOSAL.md) §4.2 and §15.

## 7. Questions resolved by operator decisions (2026-09-30)

Full decision log: [02-HYBRID-IA-PROPOSAL.md §0.1](02-HYBRID-IA-PROPOSAL.md). Planning impact: [03-PLANNING-CHANGES.md](03-PLANNING-CHANGES.md).

| Open question in this document (§5) | Resolution | Basis |
|---|---|---|
| 1. Primary organizing unit | Hybrid: posture (A) for "is everything OK", session pipeline (B) for the daily workflow, attention (C) for problems | **Operator decision** |
| 2. Is Jobs a navigation destination? | No. Jobs are execution infrastructure, reachable in System › Technical and as "technical record" links | **Operator decision** |
| 3. Home screen question | "Is it safe and working, and what do I do next?" (verdict + next action + posture + issues) | Proposed in 02 §3, follows from the hybrid |
| 4. Who interprets | Backend computes safety-relevant meaning as closed enums; frontend owns wording and layout | Proposed in 02 §9; implied by the Phase 21 re-plan |
| 5. Acceptable read-side additions | All proposed reads, plus a persisted worker heartbeat | Heartbeat: **operator decision**; other reads: proposed (03 §3) |
| 6. Mobile scope | Monitoring + emergency/risk-reducing controls only, until an auth and network-access model exists | **Operator decision** |
| 7. Multi-strategy horizon | **Still open.** Uncommitted work in the tree (2026-09-29 18:01) registers three more strategies; see 03 §9 | Open |
| 8. Research placement | Research area inside the console (strategy definition + backtests) | Proposed in 02 §2 |
| 9. Vocabulary | Operator terms; raw codes only in System › Technical | Proposed; technical placement is an **operator decision** |
| 10. Phase 21 interaction | Option (b): Phase 21 re-planned as read models/APIs; AUD-02/NOTIF-02 move to a separate Operator Console milestone | **Operator decision** |
| "Current session" | The Market Calendar is the source of truth; if it doesn't cover the relevant day, the state is explicit `unknown`, never an old session. Later refined into three facts: trading day, evaluation session, execution window (03 F-4) | **Operator decision** |
| "Controls have no session" (artboard B.2) | Controls are global Control Changes / Activity events, never Runs | **Operator decision** |

The scenario defects found here (snapshot/buying-power block, weak retry unlock, partial ingest reported SUCCEEDED, calendar ending 13 Mar) are now correctness work COR-01..COR-04.
