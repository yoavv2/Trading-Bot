"""Structural fences for the HTTP-only operator orchestration surface."""

from __future__ import annotations

import argparse
import ast
import subprocess
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
_PHASE19_OPERATION_TYPES = {
    "backtest",
    "risk",
    "paper",
    "reconciliation",
    "market-data",
    "broker-order-lifecycle",
}
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


def test_default_registry_remains_empty_until_phase_19() -> None:
    from trading_platform.jobs.registry import build_default_registry

    assert build_default_registry().list_job_types() == []


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


def _class_job_types(tree: ast.Module) -> dict[str, str]:
    types: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "job_type"
                    for target in statement.targets
                )
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                types[node.name] = statement.value.value
    return types


def _registered_operation_types(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    class_types = _class_job_types(tree)
    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "register":
            continue
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                registered.add(argument.value)
            if isinstance(argument, ast.Name) and argument.id in class_types:
                registered.add(class_types[argument.id])
            if isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name):
                if argument.func.id in class_types:
                    registered.add(class_types[argument.func.id])
    return registered.intersection(_PHASE19_OPERATION_TYPES)


def test_phase18_diff_excludes_console_and_phase19_handler_registrations() -> None:
    changed_paths = subprocess.run(
        ["git", "diff", "--name-only", "f33e62c...HEAD"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    console_paths = [path for path in changed_paths if path.startswith("console/")]
    registrations = {
        str(path.relative_to(_ROOT)): sorted(_registered_operation_types(path))
        for path in _runtime_python_files()
        if _registered_operation_types(path)
    }

    assert not console_paths, f"Phase 18 must not change console paths: {console_paths}"
    assert not registrations, f"Phase 19 Job registrations found: {registrations}"
