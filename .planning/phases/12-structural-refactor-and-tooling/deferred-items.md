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
