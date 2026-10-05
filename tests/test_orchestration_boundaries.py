"""Structural fences for the HTTP-only operator orchestration surface."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

import pytest

from trading_platform.worker.commands import DISPATCH
from trading_platform.worker.commands.run_jobs import run_jobs_command
from trading_platform.worker.parser import build_parser

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_PACKAGE_ROOTS = (
    _ROOT / "src/trading_platform/api",
    _ROOT / "src/trading_platform/worker",
    _ROOT / "src/trading_platform/jobs",
    _ROOT / "src/trading_platform/orchestration",
)
_SCHEMA_MUTATION_TARGETS = {
    "metadata.create_all",
    "metadata.drop_all",
    "alembic.command.upgrade",
    "alembic.command.downgrade",
}
# D-22 framework modules pinned free of domain imports -- see
# test_job_framework_modules_import_no_domain_layers below.
_JOB_FRAMEWORK_MODULES = (
    "runner",
    "queue",
    "lifecycle",
    "dependencies",
    "cancellation",
    "context",
    "contracts",
    "progress",
)
_RETAINED_CLI_COMMANDS = {
    "report-backtest",
    "report-strategy-analytics",
    "operator-status",
    "run-jobs",
    "kill-switch-trip",
}
_RETAINED_DISPATCH_COMMANDS = {
    "report-backtest",
    "report-strategy-analytics",
    "operator-status",
    "run-jobs",
    "kill-switch-trip",
}
_REMOVED_CLI_COMMANDS = {
    "dry-run",
    "backtest",
    "evaluate-risk",
    "submit-paper-orders",
    "run-paper-session",
    "sync-paper-state",
    "reconcile-paper-execution",
    "operator-control",
    "ingest-bars",
    "sync-metadata",
    "sync-sessions",
    "serve",
    "kill-switch-reset",
    "reset-kill-switch",
    "enable-strategy",
    "disable-strategy",
}


def _parser_commands(parser: argparse.ArgumentParser) -> set[str]:
    actions = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(actions) == 1
    return set(actions[0].choices)


def test_parser_exposes_only_retained_cli_surface() -> None:
    assert _parser_commands(build_parser()) == _RETAINED_CLI_COMMANDS


def test_dispatch_exposes_only_retained_non_serve_commands() -> None:
    assert set(DISPATCH) == _RETAINED_DISPATCH_COMMANDS


@pytest.mark.parametrize("command", sorted(_REMOVED_CLI_COMMANDS))
def test_removed_cli_commands_are_rejected_by_parser(command: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([command])

    assert exc_info.value.code == 2


def test_run_jobs_once_parses_and_dispatches_to_thin_worker_adapter() -> None:
    args = build_parser().parse_args(["run-jobs", "--once"])

    assert args.command == "run-jobs"
    assert args.once is True
    assert DISPATCH[args.command] is run_jobs_command


def test_worker_entrypoint_is_a_pure_dispatch_lookup() -> None:
    """D-30: `main()` resolves every command through `DISPATCH.get(args.command)`
    with zero `if args.command == <literal>` special cases."""
    entrypoint = _ROOT / "src/trading_platform/worker/__main__.py"
    tree = ast.parse(entrypoint.read_text(), filename=str(entrypoint))
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    def _compares_args_command_to_string_constant(node: ast.If) -> bool:
        test = node.test
        if not isinstance(test, ast.Compare):
            return False
        if ast.unparse(test.left) != "args.command":
            return False
        return any(
            isinstance(comparator, ast.Constant) and isinstance(comparator.value, str)
            for comparator in test.comparators
        )

    command_literal_ifs = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If) and _compares_args_command_to_string_constant(node)
    ]

    assert command_literal_ifs == []
    assert "DISPATCH.get(args.command)" in ast.unparse(main)


def test_worker_commands_call_no_reset_or_strategy_mutators() -> None:
    """D-15/D-30: `kill-switch-trip` is the sole surviving worker-CLI
    mutation, and it is trip-only -- no worker module may call
    `reset_kill_switch`, `enable_strategy`, or `disable_strategy`, and
    `trip_kill_switch` may only be called from the break-glass handler."""
    worker_root = _ROOT / "src/trading_platform/worker"
    forbidden_attrs = {"reset_kill_switch", "enable_strategy", "disable_strategy"}
    forbidden_offenders: list[str] = []
    trip_kill_switch_callers: list[str] = []

    for path in sorted(worker_root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in forbidden_attrs:
                forbidden_offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno} calls {node.func.attr}")
            if node.func.attr == "trip_kill_switch":
                trip_kill_switch_callers.append(str(path.relative_to(_ROOT)))

    assert not forbidden_offenders, forbidden_offenders
    assert trip_kill_switch_callers == ["src/trading_platform/worker/commands/operator.py"]


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imports.add(node.module or "")
    return imports


def _effective_routes() -> dict[str, set[str]]:
    from trading_platform.api.app import create_app

    routes: dict[str, set[str]] = {}
    for route in create_app().routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            path = str(getattr(candidate, "path", ""))
            methods = set(getattr(candidate, "methods", set()))
            routes.setdefault(path, set()).update(methods)
    return routes


def test_api_route_modules_declare_only_allowlisted_mutation_decorators() -> None:
    """D-12: the exact eight-route mutating-surface allowlist (P18/P19 "exactly two" pin
    + CTRL-01/02 and OPS-07 + the REC-01 broker statement of 20.1-10 + the REC-02 End
    operation control of 20.1-11 + the PAPER-02 owner control of 20.1-12)."""

    routes_dir = _ROOT / "src/trading_platform/api/routes"
    mutation_decorators: set[tuple[str, str, str]] = set()
    for path in routes_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                if decorator.func.attr not in {"post", "put", "patch", "delete"}:
                    continue
                route_path = decorator.args[0].value if decorator.args else ""
                assert isinstance(route_path, str)
                mutation_decorators.add((path.name, decorator.func.attr.upper(), route_path))

    assert mutation_decorators == {
        ("jobs.py", "POST", ""),
        ("jobs.py", "POST", "/{job_id}/cancel"),
        ("jobs.py", "POST", "/{job_id}/retry"),
        ("controls.py", "PUT", "/kill-switch"),
        ("controls.py", "PUT", "/strategies/{strategy_id}"),
        ("controls.py", "PUT", "/active-paper-strategy"),
        ("recovery.py", "POST", "/intents/{intent_id}/broker-statement"),
        ("execution_operations.py", "POST", "/{operation_id}/end"),
    }


def test_runtime_application_mutating_routes_are_exactly_the_allowlist() -> None:
    """D-12: the exact eight (method, path) pairs the runtime application serves."""

    routes = _effective_routes()
    mutations = {
        (method, path)
        for path, methods in routes.items()
        for method in methods.intersection({"POST", "PUT", "PATCH", "DELETE"})
    }

    assert mutations == {
        ("POST", "/api/v1/jobs"),
        ("POST", "/api/v1/jobs/{job_id}/cancel"),
        ("POST", "/api/v1/jobs/{job_id}/retry"),
        ("PUT", "/api/v1/controls/kill-switch"),
        ("PUT", "/api/v1/controls/strategies/{strategy_id}"),
        ("PUT", "/api/v1/controls/active-paper-strategy"),
        ("POST", "/api/v1/recovery/intents/{intent_id}/broker-statement"),
        ("POST", "/api/v1/execution-operations/{operation_id}/end"),
    }


def test_every_allowlisted_route_declares_the_mutation_guard() -> None:
    """D-12: every route in the eight-route allowlist carries
    require_mutations_enabled -- not just "every mutating route" generically
    (that's test_mutation_guard.py::test_every_mutating_route_requires_mutation_guard),
    but a route-by-route proof scoped to this exact set."""

    from trading_platform.api.app import create_app
    from trading_platform.api.dependencies import require_mutations_enabled

    allowlist = {
        ("POST", "/api/v1/jobs"),
        ("POST", "/api/v1/jobs/{job_id}/cancel"),
        ("POST", "/api/v1/jobs/{job_id}/retry"),
        ("PUT", "/api/v1/controls/kill-switch"),
        ("PUT", "/api/v1/controls/strategies/{strategy_id}"),
        ("PUT", "/api/v1/controls/active-paper-strategy"),
        ("POST", "/api/v1/recovery/intents/{intent_id}/broker-statement"),
        ("POST", "/api/v1/execution-operations/{operation_id}/end"),
    }

    app = create_app()
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            path = str(getattr(candidate, "path", ""))
            methods = set(getattr(candidate, "methods", set()) or set())
            dependant = getattr(candidate, "dependant", None)
            if dependant is None:
                continue
            for method in methods:
                key = (method, path)
                if key not in allowlist:
                    continue
                found.add(key)
                guarded = any(
                    dependency.call is require_mutations_enabled
                    for dependency in dependant.dependencies
                )
                assert guarded, f"Route {key} is missing require_mutations_enabled"

    assert found == allowlist


def test_control_route_adapter_imports_only_allowed_layers() -> None:
    imports = _module_imports(_ROOT / "src/trading_platform/api/routes/controls.py")

    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in (
            "sqlalchemy",
            "trading_platform.db",
            "trading_platform.worker",
            "trading_platform.jobs",
            "trading_platform.orchestration",
        )
    )
    assert imports.intersection({"trading_platform.services.operator_controls"}) == {
        "trading_platform.services.operator_controls"
    }
    assert not any(
        module.startswith("trading_platform.services.")
        and module != "trading_platform.services.operator_controls"
        for module in imports
    )


def test_job_route_adapter_imports_only_allowed_layers() -> None:
    imports = _module_imports(_ROOT / "src/trading_platform/api/routes/jobs.py")

    assert "trading_platform.orchestration.job_mutations" in imports
    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in ("sqlalchemy", "trading_platform.worker", "trading_platform.db")
    )
    assert imports.intersection({"trading_platform.services.job_reads"}) == {
        "trading_platform.services.job_reads"
    }
    assert not any(
        module.startswith("trading_platform.services.")
        and module != "trading_platform.services.job_reads"
        for module in imports
    )
    # OPS-03/OPS-07: jobs.py may reach into jobs.registry for
    # JobRegistry/UnknownJobTypeError (cancellation_mode/retry_blocked
    # composition) but no other jobs/ submodule.
    assert imports.intersection({"trading_platform.jobs.registry"}) == {
        "trading_platform.jobs.registry"
    }
    assert not any(
        module.startswith("trading_platform.jobs.") and module != "trading_platform.jobs.registry"
        for module in imports
    )


def test_orchestration_layer_has_no_transport_or_domain_service_dependencies() -> None:
    imports = _module_imports(_ROOT / "src/trading_platform/orchestration/job_mutations.py")

    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in (
            "fastapi",
            "starlette",
            "trading_platform.api",
            "trading_platform.worker",
            "trading_platform.services",
            "apscheduler",
            "trading_platform.ui",
        )
    )
    assert "trading_platform.jobs.registry" in imports
    assert "trading_platform.db.models" in imports


def test_run_jobs_is_a_thin_worker_loop_adapter() -> None:
    path = _ROOT / "src/trading_platform/worker/commands/run_jobs.py"
    source = path.read_text()
    imports = _module_imports(path)

    assert "trading_platform.jobs.runner" in imports
    assert "trading_platform.jobs.registry" in imports
    assert "run_worker_loop(" in source
    assert "JobStatus" not in source
    assert "select(" not in source
    assert "apply_job_transition" not in source
    assert not any(
        module.startswith("trading_platform.services.")
        and module != "trading_platform.services.config.validation"
        for module in imports
    )


def test_existing_service_boundary_stays_auto_scoped_and_strict() -> None:
    from tests.test_job_import_boundary import SERVICE_MODULES

    assert len(SERVICE_MODULES) >= 30


def test_default_registry_registers_exactly_the_phase20_job_types() -> None:
    """SC1: the single pin replacing all five Phase 17/18 emptiness tripwires,
    extended in Phase 20 (Plan 16) to the full eight-type registry and in Phase 20.1
    (Plan 09) to nine (``record-external-activity``, EXT-01)."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry

    registry = build_default_registry(load_settings())

    assert registry.list_job_types() == [
        "backtest",
        "broker-order-sync",
        "ingest-bars",
        "paper-session",
        "reconciliation",
        "record-external-activity",
        "risk-evaluation",
        "sync-market-sessions",
        "sync-symbol-metadata",
    ]
    for job_type in registry.list_job_types():
        assert registry.resolve_submission_spec(job_type).job_type == job_type


def test_default_registry_cancellation_modes_are_pinned() -> None:
    """D-01: the registered cancellation_mode map is exact."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry

    registry = build_default_registry(load_settings())

    expected = {
        "paper-session": "queued_only",
        "reconciliation": "queued_only",
        "record-external-activity": "queued_only",
        "broker-order-sync": "queued_only",
        "backtest": "step_boundary",
        "risk-evaluation": "step_boundary",
        "ingest-bars": "step_boundary",
        "sync-symbol-metadata": "step_boundary",
        "sync-market-sessions": "step_boundary",
    }
    actual = {
        job_type: registry.resolve_submission_spec(job_type).cancellation_mode.value
        for job_type in registry.list_job_types()
    }
    assert actual == expected


def test_default_registry_execution_modes_are_pinned() -> None:
    """D-22 (P19): the registered required_execution_mode map is exact."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry
    from trading_platform.services.config.validation import ExecutionMode

    registry = build_default_registry(load_settings())

    expected = {
        "paper-session": ExecutionMode.PAPER,
        "reconciliation": ExecutionMode.PAPER,
        "record-external-activity": ExecutionMode.PAPER,
        "broker-order-sync": ExecutionMode.PAPER,
        "backtest": ExecutionMode.BACKTEST,
        "risk-evaluation": ExecutionMode.BACKTEST,
        "ingest-bars": ExecutionMode.BACKTEST,
        "sync-symbol-metadata": ExecutionMode.BACKTEST,
        "sync-market-sessions": ExecutionMode.BACKTEST,
    }
    actual = {
        job_type: registry.resolve(job_type).required_execution_mode
        for job_type in registry.list_job_types()
    }
    assert actual == expected


def test_default_registry_retry_prerequisites_are_pinned() -> None:
    """The retry_prerequisite_for map is exact, and every declared prerequisite is itself a
    registered job type. Superseded by D-15 / 20.1-10: the reconcile-first retry prerequisite
    is gone for EVERY type (the paper-session retry is gated by the recovery predicate through
    ``recovery_gated``, see the next test)."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry, retry_prerequisite_for

    registry = build_default_registry(load_settings())

    expected = {
        "backtest": None,
        "broker-order-sync": None,
        "ingest-bars": None,
        "paper-session": None,
        "reconciliation": None,
        "record-external-activity": None,
        "risk-evaluation": None,
        "sync-market-sessions": None,
        "sync-symbol-metadata": None,
    }
    actual = {
        job_type: retry_prerequisite_for(registry.resolve_submission_spec(job_type))
        for job_type in registry.list_job_types()
    }
    assert actual == expected
    for prerequisite in actual.values():
        if prerequisite is not None:
            assert prerequisite in registry.list_job_types()


def test_default_registry_recovery_gated_map_is_pinned() -> None:
    """D-15: only paper-session is recovery gated; broker sync and every other type are not."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry, recovery_gated_for

    registry = build_default_registry(load_settings())

    actual = {
        job_type: recovery_gated_for(registry.resolve_submission_spec(job_type))
        for job_type in registry.list_job_types()
    }
    assert actual == {
        "backtest": False,
        "broker-order-sync": False,
        "ingest-bars": False,
        "paper-session": True,
        "reconciliation": False,
        "record-external-activity": False,
        "risk-evaluation": False,
        "sync-market-sessions": False,
        "sync-symbol-metadata": False,
    }


def test_default_registry_console_submission_map_is_pinned() -> None:
    """20.1-14 (D-31): the existing console cannot start or retry a paper session nor record
    external activity; every other registered type is interactive."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry, console_submission_for

    registry = build_default_registry(load_settings())

    actual = {
        job_type: console_submission_for(registry.resolve_submission_spec(job_type)).value
        for job_type in registry.list_job_types()
    }
    assert actual == {
        "backtest": "interactive",
        "broker-order-sync": "interactive",
        "ingest-bars": "interactive",
        "paper-session": "api_only",
        "reconciliation": "interactive",
        "record-external-activity": "api_only",
        "risk-evaluation": "interactive",
        "sync-market-sessions": "interactive",
        "sync-symbol-metadata": "interactive",
    }


def test_market_data_specs_have_no_mode_flags() -> None:
    """OPS-05: no market-data submission spec's payload model accepts a
    mode/behavior flag -- each spec's payload model field set is pinned."""
    from trading_platform.jobs.handlers import (
        ingest_bars_submission,
        sync_market_sessions_submission,
        sync_symbol_metadata_submission,
    )

    assert set(ingest_bars_submission._IngestBarsPayload.model_fields) == {
        "from_date",
        "to_date",
        "symbols",
    }
    assert set(sync_symbol_metadata_submission._SyncSymbolMetadataPayload.model_fields) == {
        "symbols"
    }
    assert set(sync_market_sessions_submission._SyncMarketSessionsPayload.model_fields) == {
        "from_date",
        "to_date",
    }


def test_job_framework_modules_import_no_domain_layers() -> None:
    jobs_root = _ROOT / "src/trading_platform/jobs"
    offenders: dict[str, set[str]] = {}
    for module_name in _JOB_FRAMEWORK_MODULES:
        path = jobs_root / f"{module_name}.py"
        if not path.exists():
            continue
        imports = _module_imports(path)
        forbidden = {
            module
            for module in imports
            if module == "trading_platform.services"
            or module.startswith("trading_platform.services.")
            or module == "trading_platform.strategies"
            or module.startswith("trading_platform.strategies.")
        }
        if forbidden:
            offenders[module_name] = forbidden

    assert not offenders, f"Job framework modules import domain layers: {offenders}"


def test_default_registry_handlers_declare_execution_mode() -> None:
    """D-22: every registered handler declares a real ExecutionMode."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry
    from trading_platform.services.config.validation import ExecutionMode

    registry = build_default_registry(load_settings())

    for job_type in registry.list_job_types():
        handler = registry.resolve(job_type)
        assert isinstance(handler.required_execution_mode, ExecutionMode)


def test_job_context_protocol_is_frozen() -> None:
    """D-03: JobContext's public member set must not silently grow."""
    path = _ROOT / "src/trading_platform/jobs/contracts.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    job_context = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "JobContext"
    )
    members = {
        statement.name
        for statement in job_context.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not statement.name.startswith("_")
    }

    assert members == {
        "job_id",
        "job_type",
        "payload",
        "report_progress",
        "log",
        "is_cancellation_requested",
        "raise_if_cancelled",
    }


def _runtime_python_files() -> tuple[Path, ...]:
    return tuple(
        path for package_root in _RUNTIME_PACKAGE_ROOTS for path in package_root.rglob("*.py")
    )


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent is not None else None
    return None


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve_import_alias(target: str, aliases: dict[str, str]) -> str:
    root, *rest = target.split(".")
    if root not in aliases:
        return target
    return ".".join((aliases[root], *rest))


def _schema_mutation_offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases = _import_aliases(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _dotted_name(node.func)
        if target is not None:
            resolved = _resolve_import_alias(target, aliases)
            normalized_target = ".".join(resolved.split(".")[-2:])
            if (
                normalized_target in _SCHEMA_MUTATION_TARGETS
                or resolved in _SCHEMA_MUTATION_TARGETS
            ):
                offenders.append(f"{path.relative_to(_ROOT)}:{node.lineno} calls {resolved}")

        if target is None or target.split(".")[-1] not in {
            "run",
            "call",
            "check_call",
            "check_output",
            "Popen",
        }:
            continue
        arguments = [*node.args]
        arguments.extend(keyword.value for keyword in node.keywords if keyword.arg == "args")
        for argument in arguments:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                literal = argument.value
            elif isinstance(argument, (ast.List, ast.Tuple)):
                literal = " ".join(
                    item.value
                    for item in argument.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
            else:
                continue
            normalized_literal = " ".join(literal.lower().split())
            if "alembic upgrade" in normalized_literal or "alembic downgrade" in normalized_literal:
                offenders.append(
                    f"{path.relative_to(_ROOT)}:{node.lineno} runs {normalized_literal!r}"
                )
    return offenders


def test_runtime_packages_forbid_schema_mutation_and_alembic_commands() -> None:
    offenders = [
        offender
        for path in _runtime_python_files()
        for offender in _schema_mutation_offenders(path)
    ]

    assert not offenders, "Runtime schema mutation is Alembic-only:\n" + "\n".join(offenders)




# ---------------------------------------------------------------------------
# Closed-world boundary for scripts/, the Makefile and worker commands
# (D-26..D-31, ORCH-01/02/08). Every scan below uses explicit roots -- never a
# repo-wide rglob -- so the stale `.claude/worktrees/` copy of src/ is never
# scanned (orchestrator decision 5).
# ---------------------------------------------------------------------------
_SCRIPTS_ROOT = _ROOT / "scripts"
_WORKER_ROOT = _ROOT / "src/trading_platform/worker"
_MAKEFILE = _ROOT / "Makefile"

_SCRIPT_EXEMPTIONS: dict[str, str] = {
    "migrate.py": "deployment tooling (schema migrations)",
    "seed_phase1.py": "deployment tooling (strategy catalog seed)",
    "generate_signals.py": (
        "read-only: evaluates the strategy against persisted bars, writes nothing "
        "(verified transitively)"
    ),
    "export_backtest_report.py": (
        "report: pure build_backtest_report read, writes local files only "
        "(D-31; tests/test_read_path_purity.py)"
    ),
    "operator_status.py": (
        "read/report: pure strategy control-state read (D-31; tests/test_read_path_purity.py)"
    ),
    "report_strategy_analytics.py": "read/report (D-31; tests/test_read_path_purity.py)",
}
_DEPLOYMENT_TOOLING = {"migrate.py", "seed_phase1.py"}
_KEPT_MAKE_TARGETS = {
    "up",
    "down",
    "logs",
    "migrate",
    "seed",
    "export-backtest-report",
    "generate-signals",
    "test",
    "console",
    "console-install",
    # Canonical host development (tests/test_dev_workflow.py).
    "api",
    "worker",
    "dev",
}
_SCRIPT_TOP_LEVEL_DEFS: dict[str, set[str]] = {
    "migrate.py": {"build_alembic_config", "build_parser", "main"},
    "seed_phase1.py": {"_config_reference", "seed_phase_one", "build_parser", "main"},
    "generate_signals.py": {"build_parser", "resolve_as_of", "main"},
    "export_backtest_report.py": {"build_parser", "main"},
    "operator_status.py": {"build_parser", "main"},
    "report_strategy_analytics.py": {"build_parser", "main"},
}
_MUTATING_ENTRY_POINTS = {
    "run_backtest",
    "run_risk_evaluation",
    "run_paper_session",
    "run_paper_order_submission",
    "reconcile_paper_execution",
    "apply_reconciliation_corrections",
    "recover_inflight_paper_orders",
    "sync_paper_state",
    # ACCT-01 (20.1-08): the owner-less account-level entry points.
    "sync_account_state",
    "reconcile_account",
    # EXT-01 (20.1-09): audited recording of external broker activity (Job handler only).
    "record_external_orders",
    # REC-01 (20.1-10): evidence is appended only by broker-sync passes and the statement control.
    "record_broker_statement",
    "assess_unestablished_intents",
    "record_scan_failure_for_unestablished",
    # REC-02 (20.1-11): execution operation mutators, incl. the S1-R3 fencing primitives
    # (they write the epoch / executor / attempt-row columns). Reachable only from services.
    "create_operation",
    "begin_continuation",
    "touch_operation",
    "end_operation",
    "terminate_operation",
    "transition",
    "acquire_execution",
    "authorize_send",
    "complete_attempt_late",
    "ingest_daily_bars",
    "sync_symbol_metadata",
    "upsert_symbol_metadata",
    "sync_market_sessions",
    "upsert_market_sessions",
    "enable_strategy",
    "disable_strategy",
    # PAPER-02 (20.1-12): seed / hand over / release the single paper owner (route only).
    "set_active_paper_strategy",
    "trip_kill_switch",
    "reset_kill_switch",
    "ensure_strategy_record",
    "ensure_strategy_control_state",
    "persist_backtest_metrics",
    "_upsert_backtest_metric",
    "submit_job",
    "run_dry_bootstrap",
}
_ALLOWED_MUTATING_CALLS = {
    ("src/trading_platform/worker/commands/operator.py", "trip_kill_switch"),
}


def _scripts_files() -> list[Path]:
    return sorted(_SCRIPTS_ROOT.glob("*.py"))


def _scanned_mutation_paths() -> list[Path]:
    """scripts/*.py minus deployment tooling, plus every worker module."""
    scripts = [p for p in _scripts_files() if p.name not in _DEPLOYMENT_TOOLING]
    return scripts + sorted(_WORKER_ROOT.rglob("*.py"))


def _terminal_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _mutating_hits(path: Path) -> list[tuple[str, str, int]]:
    """(relative path, pinned name, lineno) for calls/imports of pinned entry points."""
    rel = path.relative_to(_ROOT).as_posix()
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _terminal_name(node.func)
            if name in _MUTATING_ENTRY_POINTS:
                hits.append((rel, name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _MUTATING_ENTRY_POINTS:
                    hits.append((rel, alias.name, node.lineno))
    return hits


def _makefile_targets() -> set[str]:
    import re

    targets: set[str] = set()
    for line in _MAKEFILE.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
        if match and match.group(1) != ".PHONY":
            targets.add(match.group(1))
    return targets


def test_scripts_directory_is_exactly_the_exempt_set() -> None:
    assert {p.name for p in _scripts_files()} == set(_SCRIPT_EXEMPTIONS)


def test_every_exemption_has_a_nonblank_reason() -> None:
    assert _DEPLOYMENT_TOOLING <= set(_SCRIPT_EXEMPTIONS)
    for name, reason in _SCRIPT_EXEMPTIONS.items():
        assert reason.strip(), name
    assert set(_SCRIPT_TOP_LEVEL_DEFS) == set(_SCRIPT_EXEMPTIONS)


def test_makefile_targets_are_exactly_the_kept_set() -> None:
    assert _makefile_targets() == _KEPT_MAKE_TARGETS


def test_makefile_phony_matches_kept_targets() -> None:
    phony_lines = [
        line for line in _MAKEFILE.read_text().splitlines() if line.startswith(".PHONY:")
    ]
    assert len(phony_lines) == 1
    phony = phony_lines[0].split(":", 1)[1].split()
    assert len(phony) == len(set(phony))
    assert set(phony) == _KEPT_MAKE_TARGETS


def test_makefile_recipes_reference_only_exempt_scripts_and_dispatch_commands() -> None:
    import re

    text = _MAKEFILE.read_text()
    referenced_scripts = set(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", text))
    referenced_commands = set(re.findall(r"trading_platform\.worker\s+([a-z-]+)", text))

    assert referenced_scripts <= set(_SCRIPT_EXEMPTIONS)
    assert referenced_commands <= set(DISPATCH)


def test_scripts_and_worker_commands_call_no_mutating_entry_points() -> None:
    offenders = [
        f"{rel}:{lineno} uses {name}"
        for path in _scanned_mutation_paths()
        for rel, name, lineno in _mutating_hits(path)
        if (rel, name) not in _ALLOWED_MUTATING_CALLS
    ]

    assert offenders == []


def test_mutating_scan_is_not_vacuous() -> None:
    """The scan sees the one allowed break-glass call, and would flag a new one."""
    allowed_seen = {
        (rel, name)
        for path in _scanned_mutation_paths()
        for rel, name, _lineno in _mutating_hits(path)
    }
    assert allowed_seen == _ALLOWED_MUTATING_CALLS

    probe = ast.parse("from x import run_backtest\nrun_backtest()\nsvc.submit_job(1)\n")
    names = [
        _terminal_name(node.func) for node in ast.walk(probe) if isinstance(node, ast.Call)
    ]
    assert names == ["run_backtest", "submit_job"]
    assert {"run_backtest", "submit_job"} <= _MUTATING_ENTRY_POINTS


def test_exempt_scripts_are_thin_wrappers() -> None:
    for name, expected_defs in _SCRIPT_TOP_LEVEL_DEFS.items():
        path = _SCRIPTS_ROOT / name
        tree = ast.parse(path.read_text(), filename=str(path))
        defs = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]

        assert defs == expected_defs, name
        assert classes == [], name


def test_boundary_scans_exclude_claude_worktrees() -> None:
    scanned = _scanned_mutation_paths() + _scripts_files() + [_MAKEFILE]
    assert scanned
    for path in scanned:
        assert ".claude" not in path.parts, path
        assert path.is_relative_to(_SCRIPTS_ROOT) or path.is_relative_to(_WORKER_ROOT) or path == _MAKEFILE


def test_recovery_route_adapter_imports_only_allowed_layers() -> None:
    """REC-01: the recovery router reaches the domain only through OperatorControlService."""

    imports = _module_imports(_ROOT / "src/trading_platform/api/routes/recovery.py")

    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in (
            "sqlalchemy",
            "trading_platform.db",
            "trading_platform.worker",
            "trading_platform.jobs",
            "trading_platform.orchestration",
        )
    )
    service_imports = {m for m in imports if m.startswith("trading_platform.services.")}
    assert service_imports == {"trading_platform.services.operator_controls"}


def test_execution_operation_route_adapter_imports_only_allowed_layers() -> None:
    """REC-02: the execution-operations router reaches the domain only through
    OperatorControlService (End) and OperationReadService (R2 reads)."""

    path = _ROOT / "src/trading_platform/api/routes/execution_operations.py"
    imports = _module_imports(path)

    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imports
        for forbidden in (
            "sqlalchemy",
            "trading_platform.db",
            "trading_platform.worker",
            "trading_platform.jobs",
            "trading_platform.orchestration",
        )
    )
    service_imports = {m for m in imports if m.startswith("trading_platform.services.")}
    assert service_imports == {
        "trading_platform.services.operator_controls",
        "trading_platform.services.operation_reads",
    }
    assert path.read_text().count("trading_platform.services") == 2
