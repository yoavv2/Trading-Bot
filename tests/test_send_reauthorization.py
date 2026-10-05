"""Transaction T1 re-authorizes EVERY HTTP attempt against the live gate, window and price (SAF-02).

20.1-20. Before this plan the kill switch, the owner, the strategy's enabled status and the
execution window were checked once per intent (the permission check) while up to four HTTP
attempts (about two minutes with connect timeouts) could follow. ``authorize_send`` (T1, the
first attempt and every in-loop retry) now re-reads them through the SAME non-locking statement
the trading gate uses plus the window verdict; the price age is re-measured against the
application clock. Any refusal pauses the operation through the existing path with ZERO further
POST. Real PostgreSQL (throwaway database), the real client over ``httpx.MockTransport``, no
network.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select, text
from tests.support.calendar_facts import et, seed_calendar
from tests.support.paper_eligibility import allow_paper_execution
from tests.support.paper_execution_seams import allow_direct_paper_execution
from tests.support.paper_ownership import set_active_paper_strategy
from tests.support.price_source import ScriptedPriceSource, observation
from tests.test_execution_operations import (  # noqa: F401  (fixture + helper reuse)
    IN_WINDOW,
    PAST_CUTOFF,
    S,
    _t1_setup,
    ops_db,
)
from tests.test_paper_execution import migrated_paper_db  # noqa: F401  (database fixture)
from tests.test_paper_session_operations import (
    DEFAULT_BATCH,
    STRATEGY,
    _start,
    attempt_outcomes,
    finish_jobs,
    intent_rows,
    intent_states,
    operation_row,
    seed_batch,
)

from trading_platform.core import clock
from trading_platform.core.settings import AlpacaBrokerSettings, load_settings
from trading_platform.db.models import OrderLifecycleState, PaperOrder
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services import alpaca as alpaca_module
from trading_platform.services.alpaca import AlpacaClient, AlpacaExecutionService
from trading_platform.services.execution import operations as ops
from trading_platform.services.execution import permission as permission_module
from trading_platform.services.execution import submit_orders as submit_orders_module
from trading_platform.services.execution.attempts import SubmissionIntentState
from trading_platform.services.execution.operations import (
    PausedReason,
    SendRefusal,
    SendRefusedError,
    TerminatedReason,
)
from trading_platform.services.operator_controls import OperatorControlService

ONE_INTENT = DEFAULT_BATCH[:1]
_REAL_WINDOW_FACTS = permission_module.evaluation_window_facts


@pytest.fixture(autouse=True)
def _seams(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared run-time seam (historical session, stubbed window and manifest). The window
    test restores the REAL window facts; every other test keeps the stub open."""

    allow_paper_execution(monkeypatch)
    allow_direct_paper_execution(monkeypatch)


@pytest.fixture()
def real_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the seam's window stub: T1 judges the REAL persisted calendar."""

    monkeypatch.setattr(permission_module, "evaluation_window_facts", _REAL_WINDOW_FACTS)


# ---------------------------------------------------------------------------
# The real client behind a transport whose attempts all fail before a connection
# ---------------------------------------------------------------------------


class _Transport:
    def __init__(self) -> None:
        self.posts: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(request)
        raise httpx.ConnectTimeout("connect timed out", request=request)


def _service(transport: _Transport, *, max_retries: int = 3) -> AlpacaExecutionService:
    settings = AlpacaBrokerSettings(
        api_key="k", api_secret="s", max_retries=max_retries, retry_backoff_factor=0.0
    )
    client = AlpacaClient(
        settings,
        http_client=httpx.Client(
            transport=httpx.MockTransport(transport), base_url="https://paper-api.alpaca.markets"
        ),
    )
    return AlpacaExecutionService(settings, client=client)


def _between_attempts(monkeypatch: pytest.MonkeyPatch, mutate: Callable[[], None]) -> None:
    """Run ``mutate`` once, in the back-off sleep between attempt 1 and its retry."""

    done: list[bool] = []

    def sleep(_seconds: float) -> None:
        if not done:
            done.append(True)
            mutate()

    monkeypatch.setattr(alpaca_module.time, "sleep", sleep)


def _one_attempt_then_paused(
    transport: _Transport, reason: PausedReason, *, detail: str | None = None
) -> None:
    """Exactly one POST left the process, its attempt row is pre_connection and nothing else was
    begun; the operation is paused with ``reason`` and the intent is still provably not sent."""

    assert len(transport.posts) == 1
    operation = operation_row()
    assert (operation.state, operation.reason) == ("paused", reason.value)
    if detail is not None:
        assert operation.reason_detail == detail
    (_intent, client_order_id), *_ = intent_rows()
    assert attempt_outcomes(client_order_id) == [(1, "pre_connection")]
    with session_scope(load_settings()) as session:
        status = session.execute(select(PaperOrder.status)).scalar_one()
    assert status == OrderLifecycleState.PENDING_SUBMISSION
    assert intent_states(operation.id)[0][1] is SubmissionIntentState.NOT_SENT


# ---------------------------------------------------------------------------
# Each control, changed between a pre_connection failure and its retry
# ---------------------------------------------------------------------------


def test_kill_switch_between_pre_connection_failure_and_retry_sends_nothing_more(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    seed_batch(ONE_INTENT)
    transport = _Transport()
    _between_attempts(
        monkeypatch,
        lambda: OperatorControlService(settings=load_settings()).trip_kill_switch(
            reason="between attempts", actor="pytest", trigger_source="pytest"
        ),
    )

    _start(_service(transport))

    _one_attempt_then_paused(transport, PausedReason.KILL_SWITCH_TRIPPED)


def test_strategy_disabled_between_attempts_pauses(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    seed_batch(ONE_INTENT)
    transport = _Transport()
    _between_attempts(
        monkeypatch,
        lambda: OperatorControlService(settings=load_settings()).disable_strategy(
            STRATEGY, reason="between attempts", actor="pytest", trigger_source="pytest"
        ),
    )

    _start(_service(transport))

    _one_attempt_then_paused(transport, PausedReason.STRATEGY_DISABLED)


def test_owner_change_between_attempts_pauses(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    seed_batch(ONE_INTENT)
    transport = _Transport()
    # a direct singleton write (no owner at all): the ownership decision is re-made in T1
    _between_attempts(
        monkeypatch,
        lambda: set_active_paper_strategy(load_settings(), None, reason="between attempts"),
    )

    _start(_service(transport))

    _one_attempt_then_paused(
        transport, PausedReason.NOT_ACTIVE_PAPER_STRATEGY, detail="no_active_paper_strategy"
    )


def test_window_closing_between_attempts_pauses_then_lazily_terminates(
    migrated_paper_db: str,
    real_window: None,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """REAL calendar and clock (no window stub): the permission check sees the window open, the
    retry's T1 sees the application clock past the cutoff. T1 only pauses
    (execution_window_not_open); the existing lazy expiry (D-21) then terminates the operation
    execution_window_elapsed on its next touch."""

    seed_calendar(date(2025, 11, 24), date(2026, 1, 9))
    now = [IN_WINDOW]
    monkeypatch.setattr(clock, "now_utc", lambda: now[0])
    seed_batch(ONE_INTENT, session_date=S)
    transport = _Transport()
    _between_attempts(monkeypatch, lambda: now.__setitem__(0, PAST_CUTOFF))

    _start(_service(transport), session_date=S)

    _one_attempt_then_paused(transport, PausedReason.EXECUTION_WINDOW_NOT_OPEN, detail="elapsed")
    finish_jobs()
    operation_id = operation_row().id
    with session_scope(load_settings()) as session:
        touched = ops.touch_operation(session, operation_id)
    assert touched.changed is True
    operation = operation_row()
    assert (operation.state, operation.reason) == (
        "terminated",
        TerminatedReason.EXECUTION_WINDOW_ELAPSED.value,
    )
    assert len(transport.posts) == 1


def test_first_attempt_t1_refuses_a_tripped_kill_switch(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The kill switch trips AFTER the permission check and BEFORE the first T1: refused, ZERO POST
    and no attempt row at all."""

    seed_batch(ONE_INTENT)
    transport = _Transport()
    real_authorize = submit_orders_module.authorize_send

    def trip_then_authorize(*args: Any, **kwargs: Any) -> Any:
        OperatorControlService(settings=load_settings()).trip_kill_switch(
            reason="before first T1", actor="pytest", trigger_source="pytest"
        )
        return real_authorize(*args, **kwargs)

    monkeypatch.setattr(submit_orders_module, "authorize_send", trip_then_authorize)

    _start(_service(transport))

    assert transport.posts == []
    operation = operation_row()
    assert (operation.state, operation.reason) == ("paused", "kill_switch_tripped")
    (_intent, client_order_id), *_ = intent_rows()
    assert attempt_outcomes(client_order_id) == []


# ---------------------------------------------------------------------------
# T1 never locks a gate row (lock order of handover / _prepare_start is singleton-first)
# ---------------------------------------------------------------------------

_LOCKING_GATE_TABLES = ("active_paper_strategy", "system_controls", "strategies")


def test_t1_gate_read_takes_no_lock(ops_db: str, real_window: None) -> None:  # noqa: F811
    operation_id, intent_id, job_id = _t1_setup()
    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    engine = get_engine()
    holder = engine.connect()
    holder_tx = holder.begin()
    try:
        # another transaction holds the singleton, the kill switch row and the strategy rows
        # FOR UPDATE, exactly as handover (set_active_paper_strategy) does
        holder.execute(text("SELECT id FROM active_paper_strategy WHERE id = 1 FOR UPDATE"))
        holder.execute(text("SELECT name FROM system_controls FOR UPDATE"))
        holder.execute(text("SELECT id FROM strategies FOR UPDATE"))

        event.listen(engine, "before_cursor_execute", capture)
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                outcome["auth"] = ops.authorize_send(
                    operation_id, intent_id, 3, job_id, lease_owner="worker-1"
                )
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "authorize_send waited on a gate-row lock"
        assert "error" not in outcome, outcome.get("error")
        assert outcome["auth"].attempt_number == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        holder_tx.rollback()
        holder.close()

    locking = [
        s for s in statements if any(clause in s.upper() for clause in ("FOR UPDATE", "FOR SHARE"))
    ]
    assert locking, "T1 must still lock the operation row and the order row"
    for statement in locking:
        lowered = statement.lower()
        assert not any(
            f"from {table}" in lowered or f"join {table}" in lowered
            for table in _LOCKING_GATE_TABLES
        ), statement
    assert any("from execution_operations" in s.lower() for s in locking)
    assert any("from paper_orders" in s.lower() for s in locking)


# ---------------------------------------------------------------------------
# The refusal -> pause mapping is total over the closed refusal vocabulary
# ---------------------------------------------------------------------------

#: Refusals that raise OperationConflictError (a lost authority or a broken intent link): the
#: executor ends, nothing is paused by it.
_CONFLICT_REFUSALS = frozenset(
    {
        SendRefusal.OPERATION_NOT_FOUND,
        SendRefusal.OPERATION_NOT_RUNNING,
        SendRefusal.STALE_EPOCH,
        SendRefusal.WRONG_EXECUTOR,
        SendRefusal.LEASE_LOST,
        SendRefusal.INTENT_NOT_FOUND,
        SendRefusal.INTENT_NOT_OPEN,
        SendRefusal.INTENT_NOT_REGISTERED,
    }
)
#: Refusals that pause outcome_unresolved (the intent is not provably unsent).
_OUTCOME_UNRESOLVED_REFUSALS = frozenset(
    {SendRefusal.OUTCOME_UNRESOLVED, SendRefusal.INTENT_NOT_SENDABLE}
)


def test_send_refusal_pause_mapping_is_total() -> None:
    pause = submit_orders_module._SEND_REFUSAL_PAUSE
    assert set(pause) | _CONFLICT_REFUSALS | _OUTCOME_UNRESOLVED_REFUSALS == set(SendRefusal)
    assert set(pause).isdisjoint(_CONFLICT_REFUSALS | _OUTCOME_UNRESOLVED_REFUSALS)
    assert _CONFLICT_REFUSALS.isdisjoint(_OUTCOME_UNRESOLVED_REFUSALS)
    # existing closed pause reasons only (no new state or reason)
    assert {r for r in pause.values()} <= set(PausedReason)
    assert pause == {
        SendRefusal.KILL_SWITCH_TRIPPED: PausedReason.KILL_SWITCH_TRIPPED,
        SendRefusal.STRATEGY_DISABLED: PausedReason.STRATEGY_DISABLED,
        SendRefusal.NOT_ACTIVE_PAPER_STRATEGY: PausedReason.NOT_ACTIVE_PAPER_STRATEGY,
        SendRefusal.EXECUTION_WINDOW_CLOSED: PausedReason.EXECUTION_WINDOW_NOT_OPEN,
        SendRefusal.PRICE_STALE: PausedReason.PRICE_UNAVAILABLE,
    }


# ---------------------------------------------------------------------------
# Direct T1 refusals (each control, first attempt)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("trip", SendRefusal.KILL_SWITCH_TRIPPED),
        ("disable", SendRefusal.STRATEGY_DISABLED),
        ("owner_cleared", SendRefusal.NOT_ACTIVE_PAPER_STRATEGY),
        ("window_closed", SendRefusal.EXECUTION_WINDOW_CLOSED),
    ],
)
def test_direct_t1_refuses_each_control_and_begins_no_attempt(
    ops_db: str,  # noqa: F811
    real_window: None,
    monkeypatch: pytest.MonkeyPatch,
    mutate: str,
    expected: SendRefusal,
) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    settings = load_settings()
    if mutate == "trip":
        OperatorControlService(settings=settings).trip_kill_switch(
            reason="t", actor="pytest", trigger_source="pytest"
        )
    elif mutate == "disable":
        OperatorControlService(settings=settings).disable_strategy(
            "trend_following_daily", reason="t", actor="pytest", trigger_source="pytest"
        )
    elif mutate == "owner_cleared":
        set_active_paper_strategy(settings, None)
    else:
        monkeypatch.setattr(clock, "now_utc", lambda: PAST_CUTOFF)

    with pytest.raises(SendRefusedError) as excinfo:
        ops.authorize_send(operation_id, intent_id, 3, job_id, lease_owner="worker-1")

    assert excinfo.value.refusal is expected
    with session_scope(settings) as session:
        from trading_platform.db.models import OrderSubmissionAttempt

        assert session.execute(select(OrderSubmissionAttempt.id)).first() is None


def test_t1_window_not_yet_open_is_a_window_refusal(
    ops_db: str,
    real_window: None,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    operation_id, intent_id, job_id = _t1_setup()
    monkeypatch.setattr(clock, "now_utc", lambda: et(2025, 12, 3, 9, 0))
    with pytest.raises(SendRefusedError) as excinfo:
        ops.authorize_send(operation_id, intent_id, 3, job_id, lease_owner="worker-1")
    assert excinfo.value.refusal is SendRefusal.EXECUTION_WINDOW_CLOSED
    assert excinfo.value.detail == "not_yet_open"


def test_healthy_t1_still_authorizes(ops_db: str, real_window: None) -> None:  # noqa: F811
    operation_id, intent_id, job_id = _t1_setup()
    auth = ops.authorize_send(operation_id, intent_id, 3, job_id, lease_owner="worker-1")
    assert auth.attempt_number == 1


# ---------------------------------------------------------------------------
# Price age re-measured in T1 (the observation the permission check used)
# ---------------------------------------------------------------------------


def test_t1_refuses_a_stale_price_before_the_post(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The permission check accepts a 100 s old trade (limit 120 s); 30 s later, at the first T1,
    it is 130 s old: refused, ZERO POST, no attempt row, paused price_unavailable / price_stale."""

    seed_batch(ONE_INTENT)
    transport = _Transport()
    base = datetime.now(UTC)
    now = [base]
    monkeypatch.setattr(clock, "now_utc", lambda: now[0])
    monkeypatch.setattr(
        submit_orders_module,
        "_default_price_source",
        lambda settings: ScriptedPriceSource(
            [
                observation(
                    "AAPL",
                    "120",
                    observed_at=base - timedelta(seconds=100),
                    fetched_at=base,
                )
            ]
        ),
    )
    real_authorize = submit_orders_module.authorize_send

    def later_authorize(*args: Any, **kwargs: Any) -> Any:
        now[0] = base + timedelta(seconds=30)
        return real_authorize(*args, **kwargs)

    monkeypatch.setattr(submit_orders_module, "authorize_send", later_authorize)

    _start(_service(transport))

    assert transport.posts == []
    operation = operation_row()
    assert (operation.state, operation.reason) == ("paused", "price_unavailable")
    assert operation.reason_detail == "price_stale"
    (_intent, client_order_id), *_ = intent_rows()
    assert attempt_outcomes(client_order_id) == []


def test_retry_t1_remeasures_price_age(
    migrated_paper_db: str,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """The retry's T1 judges the SAME observation again: once the application clock passes
    observed_at + max age the retry is refused and the POST count stays at the attempts made."""

    seed_batch(ONE_INTENT)
    transport = _Transport()
    base = datetime.now(UTC)
    now = [base]
    monkeypatch.setattr(clock, "now_utc", lambda: now[0])
    monkeypatch.setattr(
        submit_orders_module,
        "_default_price_source",
        lambda settings: ScriptedPriceSource(
            [observation("AAPL", "120", observed_at=base - timedelta(seconds=10), fetched_at=base)]
        ),
    )
    _between_attempts(monkeypatch, lambda: now.__setitem__(0, base + timedelta(seconds=300)))

    _start(_service(transport))

    _one_attempt_then_paused(transport, PausedReason.PRICE_UNAVAILABLE, detail="price_stale")


def test_direct_t1_price_observation_bounds(
    ops_db: str,
    real_window: None,  # noqa: F811
) -> None:
    """Direct T1: a fresh observation authorizes; stale and future-dated ones are refused; a call
    without ``price_observed_at`` behaves exactly as before."""

    operation_id, intent_id, job_id = _t1_setup()
    settings = load_settings()
    skew = settings.execution.pre_send_price_future_skew_seconds
    max_age = settings.execution.pre_send_price_max_age_seconds
    for observed_at in (
        IN_WINDOW - timedelta(seconds=max_age + 1),
        IN_WINDOW + timedelta(seconds=skew + 1),
    ):
        with pytest.raises(SendRefusedError) as excinfo:
            ops.authorize_send(
                operation_id,
                intent_id,
                3,
                job_id,
                lease_owner="worker-1",
                price_observed_at=observed_at,
            )
        assert excinfo.value.refusal is SendRefusal.PRICE_STALE
    auth = ops.authorize_send(
        operation_id,
        intent_id,
        3,
        job_id,
        lease_owner="worker-1",
        price_observed_at=IN_WINDOW - timedelta(seconds=max_age),
    )
    assert auth.attempt_number == 1
