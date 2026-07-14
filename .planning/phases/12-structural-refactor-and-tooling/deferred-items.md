# Phase 12 Deferred Items

## 12-02: `services/config/secrets.py` not yet in LOG-01 enforcement list

`tests/test_log_enforcement.py`'s `IN_SCOPE_MODULES` list (LOG-01 static
AST-scan enforcement of no direct `logging.getLogger(...)` calls) contains a
length-guard assertion (`len(IN_SCOPE_MODULES) == 12`) that is frozen under
the Phase-12 zero-behavior-change contract. STRUCT-06 (12-02) split
`core/config_validation.py` into `services/config/validation.py` (added to
this list, 1:1 path swap, count unchanged) and `services/config/secrets.py`
(a newly extracted, zero-I/O pure helper — confirmed to contain no `logging`
import or `getLogger` call). Adding `secrets.py` to the enforcement list
would bump the length-guard assertion to 13, which is out of scope for a
no-behavior-change refactor plan.

**Follow-up:** a future plan (not gated by zero-behavior-change) should add
`services/config/secrets.py` to `IN_SCOPE_MODULES` and bump the length-guard
assertion to 13, so a future edit that reintroduces `logging.getLogger(...)`
inside `secrets.py` is caught by LOG-01 enforcement. No live risk today —
`secrets.py` has zero logging calls as of 12-02.

## 12-06: `run_sync_metadata`'s scripts-path resolution was already broken pre-refactor

Discovered while moving `run_sync_metadata` verbatim into
`worker/commands/ingest.py`: the pre-split `worker/__main__.py` computed the
`scripts/` directory via `Path(__file__).resolve().parents[4] / "scripts"`.
From `worker/__main__.py`'s location, `parents[4]` resolves to the directory
**above** the project root (no `scripts/` there) — a genuine, pre-existing
off-by-one bug. A real (non-`--dry-run`) `sync-metadata` invocation would
have raised `ModuleNotFoundError: sync_symbol_metadata` in production; no
test exercises this path end-to-end (only `--dry-run`-adjacent source-scan
assertions touch `run_sync_metadata`), so the bug shipped silently.

Per the zero-behavior-change contract, this was NOT fixed here — moving the
function one directory deeper (into `commands/`) would have silently
"fixed" it by accident if the literal index were left unchanged, which is
itself a behavior change caused by this task's move. Instead, the index was
adjusted to `parents[5]` in the new location, reproducing the exact original
(broken) resolved path bit-for-bit. See
`src/trading_platform/worker/commands/ingest.py` for the inline comment.

**Follow-up:** a future plan should fix the off-by-one (use `parents[4]` from
`commands/ingest.py`, which correctly resolves to the project root) and add
a regression test that a real `sync-metadata` invocation can import
`sync_symbol_metadata` without needing to pre-seed `sys.path` in the test
itself.
