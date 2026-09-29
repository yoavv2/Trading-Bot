---
status: diagnosed
trigger: "alpaca-fills-page-size-422: reconciliation, broker-order-sync and paper-session Jobs FAIL against live Alpaca paper with AlpacaClientError 422 {\"code\":40010001,\"message\":\"tried to set the page size to 500, but the maximum is 100\"}"
created: 2026-09-29T00:00:00Z
updated: 2026-09-29T00:00:00Z
---

## Current Focus

hypothesis: CONFIRMED. AlpacaClient.list_fills(page_size=500) -> GET /v2/account/activities/FILL exceeds the documented and live-enforced page_size max of 100. Both list_fills and list_orders are single-page, with no pagination.
test: done (docs + live read-only GETs)
expecting: n/a
next_action: none (diagnose-only). Hand the fix spec in Resolution.fix to a Phase 20 gap plan.

## Symptoms

expected: reconciliation, broker-order-sync and paper-session Jobs can complete against the live Alpaca PAPER broker.
actual: Automated UAT on 2026-09-29. Jobs dbf6a335 (reconciliation), 302020fc (broker-order-sync) and 45b09f0d (paper-session), as_of_session 2026-03-13, strategy trend_following_daily, FAILED with failure_reason handler_error. Earlier paper-session Job 52468936 failed the same way, flagged outcome_uncertain=true.
errors: AlpacaClientError: Alpaca request failed with status 422: {"code":40010001,"message":"tried to set the page size to 500, but the maximum is 100"}
reproduction: Test 3 in .planning/phases/20-complete-operation-migration-safety-controls/20-HUMAN-UAT.md, gap 2
started: Predates Phase 20. src/trading_platform/services/alpaca.py last touched in d5579f8 (phase 10). E2E tests use a fake broker.

## Eliminated

## Evidence

- timestamp: 2026-09-29T00:00:00Z
  checked: src/trading_platform/services/alpaca.py lines 266-285
  found: list_orders(status="all", limit=500) sends one GET /v2/orders with params {status, direction: desc, limit}. list_fills(page_size=500) sends one GET /v2/account/activities/FILL with params {direction: desc, page_size}. Neither loops; neither passes page_token/after/until. Both return only page one.
  implication: The 500 is the literal value in the 422 message. Even at a legal size, both methods return only the first page.

- timestamp: 2026-09-29T00:00:00Z
  checked: grep for list_fills/list_orders callers in src/
  found: Two production callers. services/execution/sync_orders.py:69-70 (sync_paper_state) and services/reconciliation/report.py:127-128. Both call with no arguments, so they use the defaults of 500. api/routes/operations.py list_orders/list_fills are unrelated DB route functions of the same name.
  implication: Every Job that syncs or reconciles broker state hits the 422 on the fills call.

- timestamp: 2026-09-29T00:00:00Z
  checked: tests/test_alpaca_execution.py
  found: The existing pattern is httpx.MockTransport(handler) plus httpx.Client(transport=..., base_url=...) passed as AlpacaClient(settings, http_client=...). There are no tests of list_fills or list_orders against the transport. Fakes in test_paper_execution.py, test_execution_reconciliation.py, test_phase20_service_job_links.py and test_strategy_job_types_e2e.py replace the whole client.
  implication: No existing test pins page_size=500 or limit=500. The HTTP parameters were never asserted.

- timestamp: 2026-09-29T00:00:00Z
  checked: Alpaca docs (curl of ReadMe .md/OpenAPI). https://docs.alpaca.markets/reference/getaccountactivitiesbyactivitytype-1 (GET /v2/account/activities/{activity_type}), https://docs.alpaca.markets/reference/getaccountactivities, https://docs.alpaca.markets/docs/account-activities
  found: page_size schema {"default": 100, "maximum": 100}. "If `date` is not specified, the default and maximum value is 100. If `date` is specified ... no maximum page size." page_token = "ID of the last activity from the last page"; with direction=desc "results will end before the activity with the specified ID". after/until/date filter on activity created time. direction defaults to desc.
  implication: page_size=500 violates the documented schema. Pagination uses the page_token cursor, which is the last item's id.

- timestamp: 2026-09-29T00:00:00Z
  checked: Alpaca docs https://docs.alpaca.markets/reference/getallorders-1 (GET /v2/orders), updatedAt 2026-05-27
  found: limit: "Defaults to 50 and max is 500." after/until: submission-time filters, exclusive. direction: by submission time, default desc. before_order_id / after_order_id: "Return orders submitted before/after the order with this ID (exclusive). Mutually exclusive ... Do not combine with after/until."
  implication: limit=500 is legal but is exactly the ceiling. One page silently caps at 500 orders. The documented cursor is before_order_id when direction=desc.

- timestamp: 2026-09-29T00:00:00Z
  checked: LIVE read-only GETs to https://paper-api.alpaca.markets (endpoint contains "paper"; GET only; keys not printed)
  found: |
    FILL page_size=500 -> 422 {"code":40010001,"message":"tried to set the page size to 500, but the maximum is 100"} (exact UAT text)
    FILL page_size=101 -> 422 (same code); FILL page_size=100 -> 200, 0 items (account has no fills)
    FILL date=2026-03-13 page_size=500 -> 422. CONTRADICTS the docs' "no maximum when date is set"
    FILL page_token=<bogus> -> 200 [] (token not validated)
    ORDERS status=all limit=500 -> 200, 0 items; limit=501 -> 200; limit=10000 -> 200; limit=0 -> 200
    ORDERS before_order_id=<unknown uuid> -> 404 {"code":40410000,...} (cursor is parsed and resolved)
    ACCOUNT -> 200 status ACTIVE
  implication: The 422 is exclusively from list_fills. The orders endpoint does NOT reject limit>500, so any truncation is silent, and the client must enforce <=500 itself. The date-based page_size exemption cannot be relied on. The paper account currently holds 0 orders and 0 fills, so after a page_size fix the three Jobs would complete with empty broker sets (multi-page behaviour cannot be exercised live).

- timestamp: 2026-09-29T00:00:00Z
  checked: Callers and completeness needs. reconciliation/report.py load_broker_state (used by reconcile_paper_execution and by submit_orders.py:786 run_paper_session), matcher.py _match_orders/_match_fills, sync_orders.py sync_paper_state (broker_order_sync handler)
  found: |
    Local orders and fills are loaded for the WHOLE strategy history (only filter: StrategyRun.strategy_id); there is no session/date bound.
    _match_orders: an active local order (pending_submission with attempts>0, submitted, partially_filled) absent from broker_orders -> MISSING_BROKER finding, severity error, blocks_execution=True.
    _match_fills: a broker fill absent locally -> missing-local-fill finding.
    recover_inflight_paper_orders: local active/submission_failed orders are recovered ONLY if found in broker_state.orders.
    sync_paper_state: _sync_paper_orders and _ingest_paper_fills only process what the broker returned; fills past page 1 are never ingested.
    Both broker lists are direction=desc, so truncation drops the OLDEST items.
  implication: Truncation is silent data loss with asymmetric effects. (a) orders past 500: an old still-active local order looks missing at the broker -> a false blocking MISSING_BROKER finding (fail-closed but wrong) and no inflight recovery. (b) fills past 100/500: missing-local-fill findings are silently suppressed (fail-OPEN) and sync never ingests those fills. Invariant needed: each call returns the COMPLETE broker set for the query (all orders with status=all; all FILL activities), or raises a typed error.

- timestamp: 2026-09-29T00:00:00Z
  checked: jobs/handlers/paper_session.py and runner.py docs
  found: The paper-session handler logs event_code external_broker_session_started before calling run_paper_session. The runner forces outcome_uncertain=True for any later handler_error (D-03/D-19). load_broker_state runs before any submission in run_paper_session.
  implication: 52468936 outcome_uncertain=true is designed conservative behaviour, not a second bug. The 422 fires before submission, and the live account has 0 orders, which corroborates that nothing was submitted.

- timestamp: 2026-09-29T00:00:00Z
  checked: 20-VERIFICATION.md SC1 row and REQUIREMENTS.md OPS-03/04/06
  found: SC1 is VERIFIED, citing tests/test_strategy_job_types_e2e.py and tests/test_paper_session_job_e2e.py as "API -> worker -> service E2E". Those tests inject fake broker clients that replace AlpacaClient entirely. OPS-03/04/06 are marked [x] Complete.
  implication: The E2E evidence never exercised AlpacaClient's HTTP parameters. SC1/OPS-03/04/06 need an annotation or amendment.

- timestamp: 2026-09-29T00:00:00Z
  checked: STATE.md [15-03], [20-08]; read-only SELECT on local DB localhost:5432/trading_platform (psycopg read_only=True)
  found: STATE.md records that Alpaca paper credentials were unconfigured in dev/CI through Phase 15 and Phase 20 plan execution. Local paper_orders = 0 rows (all strategies); paper_fills = 0.
  implication: This UAT is the first real-credential run, which is why a Phase 10 defect surfaced now. Both sides are empty, so post-fix reconciliation will produce no MISSING_BROKER findings. A live re-run proves only that the 422 is gone; multi-page completeness is provable only by the MockTransport unit tests.

## Resolution

root_cause: |
  src/trading_platform/services/alpaca.py:279-285 AlpacaClient.list_fills defaults page_size=500 and sends one GET /v2/account/activities/FILL?direction=desc&page_size=500.
  Alpaca documents page_size {default 100, maximum 100}, and live paper enforces it even when date is set: 422 code 40010001, "tried to set the page size to 500, but the maximum is 100".
  load_broker_state (reconciliation Job, and paper-session via run_paper_session) and sync_paper_state (broker-order-sync Job) both call list_fills() with defaults, so all three Job types fail with handler_error.
  Latent second defect, which the same completeness requirement exposes: list_fills and list_orders (limit=500, the documented max, and the server silently accepts larger values) are both SINGLE-PAGE, with no page_token / before_order_id loop. At a legal size they silently drop the oldest items (direction=desc) past 100 fills / 500 orders.
  The callers compare against the UNBOUNDED local history. The effect is false blocking MISSING_BROKER findings for old active orders, suppressed missing-local-fill findings (fail-open), and fills that are never ingested.
  Predates Phase 20 (d5579f8, phase 10). Every E2E test replaces AlpacaClient with a fake, so the HTTP params were never exercised.
fix: |
  NOT APPLIED (goal: find_root_cause_only). Proposed spec for the Phase 20 gap plan.
  Constants (services/alpaca.py, typing.Final):
    ALPACA_ACTIVITIES_MAX_PAGE_SIZE = 100   # docs maximum; live-enforced even with date=
    ALPACA_ORDERS_MAX_LIMIT = 500           # docs maximum; server does NOT enforce (limit=10000 -> 200)
    ALPACA_FILLS_MAX_PAGES = 100            # hard cap 10_000 fills per call
    ALPACA_ORDERS_MAX_PAGES = 20            # hard cap 10_000 orders per call
  Typed errors (all subclass AlpacaClientError, so the existing handler_error path is unchanged):
    AlpacaPaginationError(AlpacaClientError), base
    AlpacaPaginationCapExceededError(AlpacaPaginationError): endpoint, pages_fetched, items_fetched, max_pages
    AlpacaPaginationStalledError(AlpacaPaginationError): endpoint, cursor, duplicate_id  (repeated id, cursor not advancing, item with no string id, or non-list payload)
  Public signatures: list_fills() and list_orders() take NO page_size/limit kwargs (no caller passes them; the four test fakes are zero-arg). list_orders may keep status: Literal["open","closed","all"] = "all".
  Invariants:
    I1 every FILL request has page_size == 100 (never above ALPACA_ACTIVITIES_MAX_PAGE_SIZE); no FILL request sends date/after/until.
    I2 every /v2/orders request has limit == 500 (never above ALPACA_ORDERS_MAX_LIMIT), status=<arg>, direction=desc; no request combines before_order_id with after/until.
    I3 request 1 carries no cursor. Request N+1 carries page_token (fills) or before_order_id (orders) == id of the LAST item of page N.
    I4 loop terminates after the first page with len(page) < page_size (includes the empty page). The number of requests is at most MAX_PAGES.
    I5 the return value is the in-order concatenation of all pages, with every id unique. A repeated id, or a next cursor equal to the current cursor, raises AlpacaPaginationStalledError immediately.
    I6 if MAX_PAGES full pages are fetched, raise AlpacaPaginationCapExceededError. Never return a partial list.
  No date bounding: the broker query window must equal the local window (reconciliation/sync load the local history unbounded by date). Bounding only the broker side would create false blocking MISSING_BROKER findings and hide missing-local fills. Behaviour change: none in semantics (still "all fills"/"all orders"). Only the latency grows with history; the cap turns growth past 10_000 into a typed Job failure.
  Also amend the 20-HUMAN-UAT.md gap-2 root_cause, which says "leave list_orders". list_orders is single-page and silently caps at 500.
verification: n/a (diagnose-only)
files_changed: []
