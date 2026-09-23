# Phase 19: Job Operations Vertical Slice - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-23
**Phase:** 19-job-operations-vertical-slice
**Areas discussed:** Job → domain result contract (user-added), Backtest payload & dates, Running-cancel honesty, Submit entry & disabled UX, Worker config / broker keys

---

## Job → domain result contract (user-added)

User framing: "Define exactly how a successful backtest Job references its resulting StrategyRun, and how the console discovers/navigates to that run without inferring it from logs or querying by timestamps. The relationship should be explicit, persisted, and available through the Job API."

| Option | Description | Selected |
|--------|-------------|----------|
| strategy_runs.job_id FK | Nullable UNIQUE FK written in run-creation txn | |
| Generic job_resources table | (job_id, kind enum, resource_id) via new JobContext method | |
| Both: FK + generic exposure | FK persistence; Job API exposes generic closed-kind resources list | ✓ |

| Option | Description | Selected |
|--------|-------------|----------|
| resources[] on detail | `resources: [{kind, id, status, links.self}]` on GET /jobs/{id} | ✓ |
| Separate /jobs/{id}/resources | New link beside progress/logs/events | |
| Both | Embedded + link on compact reference | |

| Option | Description | Selected |
|--------|-------------|----------|
| As soon as run exists | Visible from RUNNING through any terminal state | ✓ |
| Only at terminal state | Hidden until Job finishes | |

| Option | Description | Selected |
|--------|-------------|----------|
| resources[] authoritative | result_summary also has run_id; equality test; console links only via resources[]; amend SC1/JOBUI-02 | ✓ |
| Drop run_id from result_summary | Single source of truth | |
| Keep both, both link | Literal roadmap wording | |

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, expose job_id on run API | Run header shows "Created by Job <id>" | ✓ |
| No, one direction only | Defer to Phase 21 | |

---

## Backtest payload & dates

| Option | Description | Selected |
|--------|-------------|----------|
| Required in API; console pre-fills | Deterministic payload/replay | ✓ |
| Optional; resolve at execution | Worker resolves missing dates | |

Submit-time 422 rejections (multi-select): Unknown strategy_id ✓, from_date > to_date ✓, to_date in future ✓, Unknown payload keys ✓

| Option | Description | Selected |
|--------|-------------|----------|
| Catalog carries defaults | Generic `submission_defaults` per catalog entry | ✓ |
| Dedicated read endpoint | e.g. /backtests/default-window | |

| Option | Description | Selected |
|--------|-------------|----------|
| "job" | Single value for Job-originated runs | ✓ |
| "operator_console" | Human surface label | |

---

## Running-cancel honesty

| Option | Description | Selected |
|--------|-------------|----------|
| Leave run truthful | Run keeps real domain status | ✓ |
| Mark run cancelled too | Rewrite run to mirror Job | |

| Option | Description | Selected |
|--------|-------------|----------|
| Composed from generic fields | One agnostic function, table-tested | ✓ |
| Status badge only | Operator combines signals | |

| Option | Description | Selected |
|--------|-------------|----------|
| Optional reason | Matches Phase 18 API | ✓ |
| Required in console | Mirrors Phase 20 control pattern | |

| Option | Description | Selected |
|--------|-------------|----------|
| Step text + indeterminate | percent=null until SUCCEEDED | ✓ |
| Coarse percentages | Fixed per-step values | |

---

## Submit entry & disabled UX

| Option | Description | Selected |
|--------|-------------|----------|
| /jobs/new?type=backtest only | Catalog-driven picker + form map | |
| Also shortcut on Strategy page | Same form + /strategy deep-link | ✓ |
| Strategy page only | Inline form | |

| Option | Description | Selected |
|--------|-------------|----------|
| Disabled by default | Explicit enable in compose; render.yaml disabled | ✓ |
| Enabled by default | Only render.yaml disables | |

| Option | Description | Selected |
|--------|-------------|----------|
| Visible but disabled + reason | Controls shown, disabled with note | ✓ |
| Hidden entirely | No mutation controls | |

| Option | Description | Selected |
|--------|-------------|----------|
| Field on job-types catalog | `{mutations_enabled, items}` | ✓ |
| Field on system/status endpoint | Global flag, extra fetch | |
| Infer from 403 on attempt | No read signal | |

---

## Worker config / broker keys

| Option | Description | Selected |
|--------|-------------|----------|
| Base boot + per-type check | BACKTEST-level boot; per-type mode check; `config_invalid` failure reason | ✓ |
| Keep PAPER; require keys | Compose needs Alpaca keys even for backtest | |
| --mode flag filters registry | Mode-scoped workers | |

| Option | Description | Selected |
|--------|-------------|----------|
| No, keep catalog minimal | Config problems surface as FAILED Jobs | ✓ |
| Yes, expose required_mode | Generic pre-submit warning | |

---

## Claude's Discretion

- cancellation_mode enum naming (closed, only values needed now)
- Mutating-route guard order (pin with test; 403 first recommended)
- Polling cadence / pause-when-hidden (extend useApiQuery, no library)
- Log tail follow/auto-scroll UX
- Idempotency-Key lifecycle in console (per form instance; reuse on transport retry; regenerate on payload change; replay → "Already submitted")
- "Jobs" nav link added now
- Mutating client function names in api.ts; ORCH-07 env var name; typed 403 code; migration naming

## Deferred Ideas

- Catalog `required_mode` exposure (revisit in Phase 20 if config failures common)
- Generic `job_resources` table / `JobContext.record_output` (revisit if outputs stop being FK-able)
