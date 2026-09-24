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
    "serve",
    "report-backtest",
    "report-strategy-analytics",
    "operator-status",
    "run-jobs",
}
_RETAINED_DISPATCH_COMMANDS = {
    "report-backtest",
    "report-strategy-analytics",
    "operator-status",
    "run-jobs",
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


def test_worker_entrypoint_has_only_serve_special_case_and_dispatch_lookup() -> None:
    entrypoint = _ROOT / "src/trading_platform/worker/__main__.py"
    tree = ast.parse(entrypoint.read_text(), filename=str(entrypoint))
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    special_cases = [node for node in main.body if isinstance(node, ast.If)]

    assert len(special_cases) == 2
    assert isinstance(special_cases[0].test, ast.Compare)
    assert ast.unparse(special_cases[0].test) == "args.command == 'serve'"
    assert "DISPATCH.get(args.command)" in ast.unparse(main)


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


def test_api_route_modules_have_only_the_two_job_mutation_decorators() -> None:
    routes_dir = _ROOT / "src/trading_platform/api/routes"
    mutation_decorators: set[tuple[str, str]] = set()
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
                mutation_decorators.add((decorator.func.attr.upper(), route_path))

    assert mutation_decorators == {("POST", ""), ("POST", "/{job_id}/cancel")}


def test_runtime_application_has_exactly_two_mutating_job_routes() -> None:
    routes = _effective_routes()
    mutations = {
        (method, path)
        for path, methods in routes.items()
        for method in methods.intersection({"POST", "PUT", "PATCH", "DELETE"})
    }

    assert mutations == {
        ("POST", "/api/v1/jobs"),
        ("POST", "/api/v1/jobs/{job_id}/cancel"),
    }


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


def test_default_registry_registers_exactly_the_phase19_job_types() -> None:
    """SC9: the single pin replacing all five Phase 17/18 emptiness tripwires."""
    from trading_platform.core.settings import load_settings
    from trading_platform.jobs.registry import build_default_registry

    registry = build_default_registry(load_settings())

    assert registry.list_job_types() == ["backtest"]
    assert registry.resolve_submission_spec("backtest").job_type == "backtest"


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


