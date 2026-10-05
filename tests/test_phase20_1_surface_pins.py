"""Phase 20.1 gate (plan 20.1-13): the final pins of the new surface and the meta tests of the
end-to-end scenarios.

Exact-set pins (T-20.1-13-04, surface creep): the mutating route set, the Job registry, the
Alembic chain and the closed enums introduced by Phase 20.1. Meta tests (T-20.1-13-01/-03): every
scenario E1..E15 of 05-INTERIM-API-OPERATIONS section 3 has a named test with a declared POST
budget, whose docstring requirement markers cover exactly the thirteen requirement ids of the gate,
and the scenario module never uses an in-process broker fake (the broker is faked at the HTTP
transport only).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database

from trading_platform.api.app import create_app
from trading_platform.api.dependencies import require_mutations_enabled
from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.jobs.registry import build_default_registry

_ROOT = Path(__file__).resolve().parents[1]
_E2E = _ROOT / "tests" / "test_phase20_1_api_e2e.py"
_PLAN = (
    _ROOT
    / ".planning/phases/20.1-operator-state-correctness/20.1-13-PLAN.md"
)

THE_EIGHT_MUTATING_ROUTES = {
    ("POST", "/api/v1/jobs"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/jobs/{job_id}/retry"),
    ("PUT", "/api/v1/controls/kill-switch"),
    ("PUT", "/api/v1/controls/strategies/{strategy_id}"),
    ("PUT", "/api/v1/controls/active-paper-strategy"),
    ("POST", "/api/v1/recovery/intents/{intent_id}/broker-statement"),
    ("POST", "/api/v1/execution-operations/{operation_id}/end"),
}

THE_THIRTEEN_REQUIREMENTS = {
    "PAPER-01",
    "PAPER-02",
    "ACCT-01",
    "EXT-01",
    "COR-01",
    "COR-03",
    "COR-04",
    "COR-05",
    "COR-06",
    "PROV-01",
    "REC-01",
    "REC-02",
    "COMPAT-01",
}

#: The named scenario tests of 05 section 3 (04 P20.1-13); E8 also has variants whose names start
#: with ``test_e8_ambiguous_submission_order_never_found``.
SCENARIO_TEST_NAMES = (
    "test_e1_first_start_from_no_owner",
    "test_e2_29_sep_carry_over",
    "test_e3_session_with_one_working_order",
    "test_e4_immediately_filled_order",
    "test_e5_earlier_fill_changes_the_portfolio",
    "test_e6_data_correction",
    "test_e7_ambiguous_submission_order_found",
    "test_e8_ambiguous_submission_order_never_found",
    "test_e9_external_activity",
    "test_e10_historical_execution",
    "test_e11_window_expiry",
    "test_e12_symbol_metadata",
    "test_e13_handover",
    "test_e14_mutations_disabled",
    "test_e15_legacy_console_truthfulness_api_half",
)


# ---------------------------------------------------------------------------
# Route, registry and migration pins
# ---------------------------------------------------------------------------


def _effective_routes() -> Iterator[tuple[str, str, object]]:
    app = create_app()
    for route in app.routes:
        candidates = (
            route.effective_candidates() if hasattr(route, "effective_candidates") else [route]
        )
        for candidate in candidates:
            path = str(getattr(candidate, "path", ""))
            for method in set(getattr(candidate, "methods", set()) or set()):
                yield method, path, candidate


def test_final_mutating_routes_are_exactly_the_eight() -> None:
    """The runtime route table has exactly the eight mutating routes, every one guarded by
    ``require_mutations_enabled``, and no path of the application contains 'withdraw'."""

    mutating = {
        (method, path): candidate
        for method, path, candidate in _effective_routes()
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert set(mutating) == THE_EIGHT_MUTATING_ROUTES
    for key, candidate in mutating.items():
        dependant = getattr(candidate, "dependant", None)
        assert dependant is not None, key
        assert any(dep.call is require_mutations_enabled for dep in dependant.dependencies), (
            f"{key} is missing require_mutations_enabled"
        )
    assert not [path for _, path, _ in _effective_routes() if "withdraw" in path.lower()]


def test_registry_has_exactly_nine_job_types() -> None:
    registry = build_default_registry(load_settings())
    assert sorted(registry.list_job_types()) == sorted(
        [
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
    )


def test_alembic_chain_is_linear_from_0021() -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, heads

    chain: list[str] = []
    revision = script.get_revision(heads[0])
    while revision is not None:
        chain.append(revision.revision)
        assert isinstance(revision.down_revision, (str, type(None))), (
            f"{revision.revision} has a merge or branch point"
        )
        if revision.revision == "0021_phase20_operations_safety":
            break
        revision = script.get_revision(revision.down_revision) if revision.down_revision else None
    chain.reverse()

    assert chain[0] == "0021_phase20_operations_safety"
    assert chain[1].startswith("0022_")  # the 20.1-01 revision (matched by prefix)
    assert chain[2:] == [
        "0023_phase20_1_order_submission_attempts",
        "0024_phase20_1_account_reconciliation_runs",
        "0025_phase20_1_external_broker_activity",
        "0026_phase20_1_recovery_records",
        "0027_phase20_1_execution_operations",
        "0028_phase20_1_attempt_log_append_only",
    ]
    assert heads[0] == "0028_phase20_1_attempt_log_append_only"
    # each revision descends from the previous one (linear).
    for previous, current in zip(chain, chain[1:], strict=False):
        assert script.get_revision(current).down_revision == previous


# ---------------------------------------------------------------------------
# R-8: one default for a never-registered strategy on every surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def pins_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with migrated_database(monkeypatch, "pins201"):
        monkeypatch.setenv("TRADING_PLATFORM_ORCHESTRATION__MUTATIONS_ENABLED", "true")
        clear_settings_cache()
        yield
        clear_settings_cache()


def test_r8_no_row_status_agrees_everywhere(pins_db: None) -> None:
    """R-8 (deferred here from wave-1 plans 20.1-01 / 20.1-03): for a never-registered strategy the
    control read, analytics ``strategy.status`` and ``ensure_strategy_state`` all say ``disabled``,
    and the reads write nothing."""

    from sqlalchemy import select

    from trading_platform.db.models import Strategy
    from trading_platform.db.session import session_scope
    from trading_platform.services.operator_controls import OperatorControlService

    strategy_id = "trend_following_daily"
    settings = load_settings()
    with TestClient(create_app()) as client:
        control = client.get(f"/api/v1/controls/strategies/{strategy_id}").json()
        analytics = client.get(f"/api/v1/analytics/strategies/{strategy_id}").json()
        with session_scope(settings) as session:
            assert session.execute(select(Strategy)).first() is None  # the reads wrote nothing
        ensured = OperatorControlService(settings=settings).ensure_strategy_state(strategy_id)

    assert control["status"] == "disabled"
    assert analytics["strategy"]["status"] == "disabled"
    assert ensured.status == "disabled" or str(ensured.status).endswith("disabled")
    # the mutating get-or-create persisted the same default the reads reported
    with session_scope(settings) as session:
        persisted = session.execute(select(Strategy.status)).scalar_one()
    assert str(getattr(persisted, "value", persisted)) == "disabled"


# ---------------------------------------------------------------------------
# Closed enums introduced by 20.1
# ---------------------------------------------------------------------------


def test_closed_enums_pinned() -> None:
    from trading_platform.db.models import AttemptOutcomeClass
    from trading_platform.db.models.execution_operation import IntentDisposition, OperationState
    from trading_platform.jobs.registry import ConsoleSubmission
    from trading_platform.services.batch_outcomes import (
        BatchOutcome,
        OperationFailureReason,
        SymbolFailureReason,
    )
    from trading_platform.services.execution.attempts import (
        SubmissionClass,
        SubmissionIntentState,
    )
    from trading_platform.services.execution.intent_identity import (
        BasisFailure,
        CandidateDisposition,
    )
    from trading_platform.services.execution.operations import (
        PausedReason,
        ReevaluationReason,
        TerminatedReason,
    )
    from trading_platform.services.execution.permission import TradingBlocker
    from trading_platform.services.external_activity import ExternalActivityRejection
    from trading_platform.services.paper_account_checks import CheckId, CheckReason
    from trading_platform.services.recovery import GateCode, RecoveryClassification

    def values(enum: type) -> set[str]:
        return {member.value for member in enum}  # type: ignore[attr-defined]

    assert values(GateCode) == {
        "outcome_unresolved",
        "reconciliation_required",
        "reconciliation_not_clean",
    }
    assert values(OperationState) == {
        "running",
        "paused",
        "requires_reevaluation",
        "terminated",
        "completed",
    }
    assert values(PausedReason) == {
        "awaiting_reconciliation",
        "broker_unavailable",
        "execution_window_not_open",
        "kill_switch_tripped",
        "not_active_paper_strategy",
        "outcome_unresolved",
        "price_moved_beyond_tolerance",
        "price_unavailable",
        "reconciliation_blocking",
        "strategy_disabled",
        "unrecognized_broker_activity",
        "working_order_commitments_unaccounted",
    }
    assert values(ReevaluationReason) == {"evaluation_data_changed", "strategy_settings_changed"}
    assert values(TerminatedReason) == {
        "cancelled_by_operator",
        "evaluation_superseded",
        "execution_window_elapsed",
    }
    assert values(SubmissionIntentState) == {
        "planned",
        "registered_unsent",
        "not_sent",
        "submitted",
        "ambiguous",
        "rejected",
        "expired_unsent",
        "cancelled_unsent",
    }
    assert values(IntentDisposition) == {"open", "cancelled_unsent", "expired_unsent"}
    assert values(CheckId) == {"A1", "A2", "A3", "A4", "A5", "A6", "A7"}
    assert values(CheckReason) == {
        "broker_job_active",
        "no_account_reconciliation",
        "no_broker_observed_snapshot",
        "non_terminal_orders",
        "open_operation",
        "open_positions",
        "outgoing_owner_enabled",
        "reconciliation_not_clean",
        "reconciliation_stale",
        "unexplained_exposure",
        "unrecognized_items",
        "unresolved_outcome",
    }
    assert values(BatchOutcome) == {"complete", "partial", "failed"}
    assert values(SymbolFailureReason) == {
        "fetch_error",
        "invalid_response",
        "missing_required_fields",
        "not_found",
    }
    assert values(OperationFailureReason) == {
        "database_write",
        "invalid_configuration",
        "provider_auth",
        "provider_unavailable",
    }
    assert values(ConsoleSubmission) == {"api_only", "interactive"}
    assert values(TradingBlocker) == {
        "kill_switch_tripped",
        "no_active_paper_strategy",
        "outcome_unresolved",
        "reconciliation_blocking",
        "strategy_disabled",
        "unrecognized_broker_activity",
        "working_order_commitments_unaccounted",
    }
    assert values(ExternalActivityRejection) == {
        "broker_record_unavailable",
        "external_exposure_nonzero",
        "external_order_not_terminal",
        "order_owned_by_strategy",
    }
    assert values(RecoveryClassification) == {
        "found_verified",
        "not_found",
        "not_sent",
        "nothing_submitted",
        "rejected_at_submission",
        "unresolved",
    }
    assert values(AttemptOutcomeClass) == {
        "pre_connection",
        "deadline_expired",
        "ambiguous",
        "duplicate_reported",
        "rejected",
        "accepted",
    }
    assert values(SubmissionClass) == {
        "accepted",
        "ambiguous",
        "exists_reported",
        "not_sent",
        "rejected",
    }
    assert values(CandidateDisposition) == {
        "action_already_submitted",
        "duplicate_open_position",
        "exit_quantity_mismatch",
        "no_open_position",
        "replay_of_earlier_decision",
    }
    assert values(BasisFailure) == {
        "basis_not_broker_observed",
        "basis_positions_mismatch",
        "basis_stale",
        "executions_not_synced",
        "fills_not_ingested",
        "predates_executions",
        "reconciliation_missing",
    }


# ---------------------------------------------------------------------------
# Meta tests over the scenario module
# ---------------------------------------------------------------------------


def _e2e_functions() -> list[ast.FunctionDef]:
    tree = ast.parse(_E2E.read_text(), filename=str(_E2E))
    return [node for node in tree.body if isinstance(node, ast.FunctionDef)]


def _scenario_functions() -> list[ast.FunctionDef]:
    return [fn for fn in _e2e_functions() if re.match(r"test_e\d+_", fn.name)]


def test_every_e_scenario_has_a_named_test() -> None:
    names = {fn.name for fn in _scenario_functions()}
    for expected in SCENARIO_TEST_NAMES:
        assert any(name == expected or name.startswith(expected) for name in names), expected
    numbers = {int(re.match(r"test_e(\d+)_", name).group(1)) for name in names}  # type: ignore[union-attr]
    assert numbers == set(range(1, 16))
    # E8 has the base test and its variants.
    assert (
        len([n for n in names if n.startswith("test_e8_ambiguous_submission_order_never_found")])
        >= 4
    )
    # every scenario test declares its expected broker POST count (default is 0, but explicit).
    for fn in _scenario_functions():
        calls = [
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "expect_posts" in calls, f"{fn.name} does not declare its POST budget"


def test_e_scenario_docstrings_cover_exactly_the_thirteen_requirements() -> None:
    covered: set[str] = set()
    for fn in _scenario_functions():
        docstring = ast.get_docstring(fn) or ""
        first = docstring.splitlines()[0] if docstring else ""
        match = re.fullmatch(r"E(\d+) \| Requirements: \[([A-Z0-9, -]+)\]", first)
        assert match, f"{fn.name}: first docstring line is {first!r}"
        number = re.match(r"test_e(\d+)_", fn.name).group(1)  # type: ignore[union-attr]
        assert match.group(1) == number, fn.name
        covered |= {item.strip() for item in match.group(2).split(",")}
    assert covered == THE_THIRTEEN_REQUIREMENTS


def test_scenario_module_never_uses_an_in_process_broker_fake() -> None:
    """T-20.1-13-01: the broker is faked at the HTTP transport only (the real attempt log runs)."""

    tree = ast.parse(_E2E.read_text(), filename=str(_E2E))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            used.update(alias.name for alias in node.names)
    for forbidden in (
        "FakeExecutionService",
        "FakeBrokerClient",
        "ScriptedExecutionService",
        "allow_paper_execution",
        "allow_direct_paper_execution",
    ):
        assert forbidden not in used, forbidden
    source = _E2E.read_text()
    assert "ScriptedAlpaca" in source
    assert "MockTransport" in (_ROOT / "tests/support/scripted_broker.py").read_text()


def test_no_console_file_is_referenced_by_the_gate() -> None:
    """This plan is a test and documentation gate: no console/ path in its file list."""

    text = _PLAN.read_text()
    block = text.split("files_modified:")[1].split("autonomous:")[0]
    listed = [line.strip().removeprefix("- ") for line in block.splitlines() if line.strip()]
    assert listed and not [path for path in listed if path.startswith("console/")]
    assert "tests/test_console_api_contract.py" not in listed
