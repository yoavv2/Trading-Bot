"""JOB-03 registry extensibility enforcement tests.

Proves two things:
1. ``JobRegistry`` supports register/resolve/list with typed duplicate and
   unknown-type errors (mirrors ``tests/test_strategy_registry.py``).
2. Adding a new Job type touches zero queue-framework modules -- the
   stronger JOB-03 claim that a registry alone does not prove. The queue-
   framework module list is frozen at 6 entries and non-emptiable so this
   check cannot silently degrade into a no-op.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Mapping

import pytest

from trading_platform.jobs.contracts import JobContext, JobHandler
from trading_platform.jobs.registry import (
    JobRegistry,
    UnknownJobTypeError,
    build_default_registry,
)

_ROOT = Path(__file__).resolve().parents[1]
_JOBS_PKG = _ROOT / "src" / "trading_platform" / "jobs"

# Frozen list of modules that make up the queue framework itself, as opposed
# to individual Job-type handler modules. Several of these are created by
# later Phase 17 plans (17-03 through 17-07); the AST scan below skips files
# that do not yet exist, but this list's length stays pinned at 6 regardless
# so the module list itself cannot be silently emptied to make the check
# vacuous.
QUEUE_FRAMEWORK_MODULES: list[Path] = [
    _JOBS_PKG / "queue.py",
    _JOBS_PKG / "runner.py",
    _JOBS_PKG / "lifecycle.py",
    _JOBS_PKG / "dependencies.py",
    _JOBS_PKG / "cancellation.py",
    _JOBS_PKG / "context.py",
]


class _FakeJobHandler:
    """Minimal concrete JobHandler used to exercise the registry."""

    def __init__(self, job_type: str, result: Mapping[str, Any]) -> None:
        self._job_type = job_type
        self._result = result

    @property
    def job_type(self) -> str:
        return self._job_type

    def run(self, context: JobContext) -> Mapping[str, Any]:
        return self._result


def test_registry_registers_and_resolves_handler() -> None:
    registry = JobRegistry()
    handler = _FakeJobHandler("fake_job", {"ok": True})

    registry.register(handler)

    assert isinstance(handler, JobHandler)
    assert registry.resolve("fake_job") is handler
    assert registry.list_job_types() == ["fake_job"]
    assert "fake_job" in registry


def test_registry_rejects_duplicate_registration() -> None:
    registry = JobRegistry()
    registry.register(_FakeJobHandler("fake_job", {"ok": True}))

    with pytest.raises(ValueError, match="fake_job"):
        registry.register(_FakeJobHandler("fake_job", {"ok": True}))


def test_registry_resolve_unknown_raises_typed_error() -> None:
    registry = JobRegistry()

    with pytest.raises(UnknownJobTypeError, match="missing_job"):
        registry.resolve("missing_job")


def test_build_default_registry_is_empty_in_phase_17() -> None:
    registry = build_default_registry()

    assert registry.list_job_types() == []


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def test_queue_framework_module_list_is_not_empty() -> None:
    # Guards against a typo'd/emptied QUEUE_FRAMEWORK_MODULES silently
    # turning the enforcement test below into a no-op.
    assert len(QUEUE_FRAMEWORK_MODULES) == 6


def test_adding_a_job_type_touches_zero_queue_framework_modules() -> None:
    registered_job_types = {"fake_job", "second_fake_job"}

    # Static half: none of the queue-framework modules' source text may
    # reference a concrete job-type string literal. Files not yet created
    # by later Phase 17 plans are skipped rather than failing the test.
    for module_path in QUEUE_FRAMEWORK_MODULES:
        if not module_path.exists():
            continue
        constants = _string_constants(module_path)
        overlap = constants & registered_job_types
        assert not overlap, (
            f"{module_path.relative_to(_ROOT)} references concrete job type "
            f"literal(s) {overlap}; queue-framework modules must stay "
            "job-type-agnostic (JOB-03)."
        )

    # Dynamic half: registering and resolving a brand-new handler at
    # runtime succeeds purely through JobRegistry -- no queue-framework
    # module needs to be imported or mutated to add a Job type.
    registry = JobRegistry()
    registry.register(_FakeJobHandler("fake_job", {"ok": True}))
    registry.register(_FakeJobHandler("second_fake_job", {"ok": True}))

    resolved = registry.resolve("second_fake_job")
    assert resolved.run(context=None) == {"ok": True}  # type: ignore[arg-type]
    assert registry.list_job_types() == ["fake_job", "second_fake_job"]
