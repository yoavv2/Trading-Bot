# Deferred Items — Phase 10 (Startup Hardening)

## From 10-02 (Log Sanitization Core)

**`emit_structured_log`'s chokepoint covers the `context` dict only, not the `message` positional argument.**

`sanitize()` is applied to the assembled `context` dict before `logger.log(...)`, satisfying this plan's declared scope (Task 2's action text: "the `context` dict is `sanitize(context, unmask_ids=...)` before `logger.log`"). However, the `message` string passed to `emit_structured_log`/`logger.log` is NOT scrubbed — `JsonLogFormatter.format()` emits `record.getMessage()` raw. A call like `emit_structured_log(logger, INFO, "connecting with password=hunter2")` would leak the secret in the `message` field of the emitted JSON line, even though any secret placed in `context` is redacted.

This is intentional and in-scope-as-written: this plan's `<verification>` section explicitly scopes LOG-06 (the emitted-line enforcement test, which would need to cover full-payload including `message`) to plan 10-06, and the plan's `key_facts` names "the formatter as a defense-in-depth backstop" as the mechanism that would close this gap — also 10-06's territory (`10-06-PLAN.md — Logger migration + formatter backstop + import-boundary & emitted-line enforcement tests`).

**Action for 10-06:** When adding the formatter backstop, sanitize `record.getMessage()` (or the raw `message` string before formatting) in addition to `context`, so LOG-06's "no emitted log line contains `password=`..." enforcement test can assert over the whole JSON line, not just the `context` sub-object. Do not assume today's "single sanitization chokepoint" claim (10-02-SUMMARY.md, `get_logger` docstring) covers the message string — it covers `context` only.

**Minor, non-blocking:** `configure_logging()`'s copy of `settings.debug_unmask_ids` into the module-level `_DEBUG_UNMASK_IDS` flag has no dedicated test (the existing unmask test sets the module global directly). Trivial one-line assignment, low risk; worth a quick assertion if 10-06 touches `configure_logging` anyway.
