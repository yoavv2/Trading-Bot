"""LOG-01 / LOG-06 enforcement tests.

LOG-01: execution, reconciliation, and config/startup/control path modules
must obtain loggers ONLY through `trading_platform.core.logging.get_logger`
-- direct `logging.getLogger(...)` is forbidden there. Enforced below by a
static AST scan of the enumerated in-scope module files.

LOG-06: under default config, no emitted log line may leak a credential
(password/api_key/Authorization) or a full broker order ID -- only the
last-6 masked form is permitted. Enforced below by driving a real emission
through the production formatter/handler wiring (`configure_logging`) and
inspecting the captured, serialized JSON lines.
"""

from __future__ import annotations

import ast
import io
import json
import logging
from pathlib import Path

import pytest

from trading_platform.core import logging as core_logging
from trading_platform.core.logging import emit_structured_log, get_logger
from trading_platform.core.settings import LoggingSettings

# ---------------------------------------------------------------------------
# LOG-01: import-boundary test
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "trading_platform"

# Exact in-scope module list (execution, reconciliation, and config/startup/
# control paths, per 10-06-PLAN.md `<key_facts>`). Any future edit that
# reintroduces a raw `logging.getLogger(...)` call in one of these files
# must fail this test -- that is the whole point of LOG-01 enforcement.
IN_SCOPE_MODULES: list[Path] = [
    _SRC / "services" / "paper_execution.py",
    _SRC / "services" / "alpaca.py",
    _SRC / "services" / "order_state_machine.py",
    _SRC / "services" / "concurrency_guard.py",
    _SRC / "services" / "operator_status.py",
    _SRC / "services" / "operator_controls.py",
    _SRC / "services" / "reconciliation.py",
    _SRC / "worker" / "__main__.py",
    _SRC / "services" / "bootstrap.py",
    _SRC / "api" / "app.py",
    _SRC / "core" / "startup.py",
    _SRC / "core" / "config_validation.py",
]


def _find_direct_getlogger_calls(path: Path) -> list[int]:
    """Return the line numbers of any `logging.getLogger(...)` call in `path`.

    Uses an AST walk (not a substring `grep`) so a `logging.getLogger`
    mention inside a comment, docstring, or string literal does not produce
    a false positive; only an actual call expression counts. Plain
    `import logging` (for `logging.WARNING`/`logging.INFO` level constants
    or `logging.Logger` type hints) is explicitly allowed.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "getLogger"
            and isinstance(func.value, ast.Name)
            and func.value.id == "logging"
        ):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize(
    "module_path",
    IN_SCOPE_MODULES,
    ids=[p.relative_to(_SRC).as_posix() for p in IN_SCOPE_MODULES],
)
def test_import_boundary_no_direct_get_logger(module_path: Path) -> None:
    assert module_path.exists(), f"in-scope module not found: {module_path}"
    hits = _find_direct_getlogger_calls(module_path)
    assert not hits, (
        f"{module_path.relative_to(_ROOT)} calls logging.getLogger(...) "
        f"directly at line(s) {hits}; use "
        "trading_platform.core.logging.get_logger(...) instead (LOG-01)."
    )


def test_import_boundary_module_list_is_not_empty() -> None:
    # Guards against a typo'd/emptied IN_SCOPE_MODULES silently making the
    # parametrized test above a no-op.
    assert len(IN_SCOPE_MODULES) == 12


# ---------------------------------------------------------------------------
# LOG-06: emitted-line enforcement (no leaked credentials / full order ID
# under default config)
# ---------------------------------------------------------------------------

FULL_BROKER_ORDER_ID = "PAPER-ORDER-1234567890ABCDEF"
RAW_PASSWORD = "hunter2-super-secret"
RAW_API_KEY = "sk-live-abcdefghijklmno"
RAW_AUTH_HEADER = "Bearer eyJhbGciOiJIUzI1NiJ9.secret-payload"


@pytest.fixture
def _isolated_root_logger():
    """Snapshot/restore root-logger handlers, level, and the debug-unmask
    global so this test's `configure_logging()` calls don't leak state into
    other tests in the suite."""
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_unmask = core_logging._DEBUG_UNMASK_IDS
    try:
        yield
    finally:
        root_logger.handlers.clear()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(original_level)
        core_logging._DEBUG_UNMASK_IDS = original_unmask


def _capture_json_lines(*, debug_unmask_ids: bool) -> list[str]:
    """Drive a representative execution emission through the exact
    production formatter/handler wiring (`configure_logging`) and return
    the captured, serialized JSON lines.

    Exercises two emission paths:
    - `emit_structured_log`, the standard chokepoint (10-02's context
      sanitization contract).
    - a direct `logger.warning(..., extra={"context": {...}})` call that
      bypasses `emit_structured_log` entirely -- this is what actually
      proves the formatter-level backstop (10-06 Task 2), not just
      chokepoint discipline by well-behaved callers.
    """
    settings = LoggingSettings(debug_unmask_ids=debug_unmask_ids)
    core_logging.configure_logging(settings)
    root_logger = logging.getLogger()

    stream = io.StringIO()
    handler = root_logger.handlers[0]
    handler.stream = stream

    logger = get_logger("trading_platform.test_log_enforcement")

    emit_structured_log(
        logger,
        logging.INFO,
        "paper order submitted successfully",
        strategy_id="trend_following_daily",
        broker_order_id=FULL_BROKER_ORDER_ID,
        password=RAW_PASSWORD,
        api_key=RAW_API_KEY,
        headers={"Authorization": RAW_AUTH_HEADER},
    )

    logger.warning(
        "direct emission bypassing emit_structured_log",
        extra={
            "context": {
                "broker_order_id": FULL_BROKER_ORDER_ID,
                "password": RAW_PASSWORD,
                "api_key": RAW_API_KEY,
                "headers": {"Authorization": RAW_AUTH_HEADER},
            }
        },
    )

    return [line for line in stream.getvalue().splitlines() if line.strip()]


def test_default_config_emitted_lines_never_leak_secrets_or_full_order_id(
    _isolated_root_logger,
) -> None:
    lines = _capture_json_lines(debug_unmask_ids=False)
    assert len(lines) == 2

    for line in lines:
        # Every emitted line must remain valid JSON per JsonLogFormatter's
        # own contract -- sanitization must not corrupt the payload shape.
        json.loads(line)

        assert "password=" not in line
        assert "api_key=" not in line
        assert RAW_PASSWORD not in line
        assert RAW_API_KEY not in line
        assert RAW_AUTH_HEADER not in line
        assert FULL_BROKER_ORDER_ID not in line

    # The masked last-6 form must still be present -- proves the ID was
    # masked, not silently dropped.
    masked_suffix = FULL_BROKER_ORDER_ID[-6:]
    assert any(masked_suffix in line for line in lines)


def test_debug_unmask_flag_reveals_full_broker_order_id(_isolated_root_logger) -> None:
    lines = _capture_json_lines(debug_unmask_ids=True)
    assert any(FULL_BROKER_ORDER_ID in line for line in lines)

    # Credentials remain fully redacted regardless of the unmask flag --
    # `debug_unmask_ids` only ever affects broker-order-id masking.
    for line in lines:
        assert RAW_PASSWORD not in line
        assert RAW_API_KEY not in line
        assert RAW_AUTH_HEADER not in line


def test_default_config_scrubs_secret_embedded_in_message_string(_isolated_root_logger) -> None:
    """Dedicated regression test for the gap deferred-items.md flagged from
    10-02: `emit_structured_log`'s own chokepoint sanitizes the `context`
    dict but NEVER the `message` positional argument -- only the Task 2
    formatter backstop closes that gap. The other tests in this module seed
    secrets exclusively via `context`/`extra` fields, so they would still
    pass even if the message string were left completely unsanitized; this
    test is the one that actually exercises and proves the message-string
    path.

    Uses VALUE-based assertions only (not `"password=" not in line`):
    `_scrub_string`'s embedded-secret pattern preserves the `key=` prefix
    and redacts only the value (`password=hunter2` -> `password=[REDACTED]`),
    so the substring `"password="` legitimately survives in this line -- the
    other tests instead seed credentials via dict keys, which serialize as
    JSON `"password": "[REDACTED]"` (no `=`) and can use the substring form.
    """
    settings = LoggingSettings(debug_unmask_ids=False)
    core_logging.configure_logging(settings)
    root_logger = logging.getLogger()

    stream = io.StringIO()
    handler = root_logger.handlers[0]
    handler.stream = stream

    logger = get_logger("trading_platform.test_log_enforcement")
    emit_structured_log(
        logger,
        logging.INFO,
        f"connecting with password={RAW_PASSWORD} api_key={RAW_API_KEY}",
        strategy_id="trend_following_daily",
    )

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])

    assert RAW_PASSWORD not in lines[0]
    assert RAW_API_KEY not in lines[0]
    assert "[REDACTED]" in payload["message"]
