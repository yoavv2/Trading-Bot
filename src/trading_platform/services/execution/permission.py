"""Per-intent permission check (REC-02, D-17/D-25/D-26, S2-R3).

``check_intent_permission`` is run before EVERY broker action of an execution operation (the
start loop of 20.1-15 and, with ``continuation=True``, the Continue mode of 20.1-16). It is
fresh every time: nothing decided at evaluation or at an earlier intent is assumed. Its
result is exactly one of ``ok`` | ``pause(reason, detail)`` | ``reevaluate(reason)`` |
``terminate(reason)`` in this FIXED precedence (terminal and permanent conditions before
temporary ones; a test pins the table):

1. execution window: past the cutoff -> terminate(execution_window_elapsed); the trading day
   moved on -> terminate(evaluation_superseded); before the open or an unknown calendar ->
   pause(execution_window_not_open) (detail ``calendar_data_unavailable`` when unknown);
2. provenance: the evaluation manifest of the PINNED risk run -> reevaluate(
   evaluation_data_changed | strategy_settings_changed);
3. pauses: owner -> not_active_paper_strategy, enabled -> strategy_disabled, kill switch ->
   kill_switch_tripped, an unresolved outcome -> outcome_unresolved, a working order of the
   strategy (any operation, or fills not yet synced) -> working_order_commitments_unaccounted,
   ONLY for ``continuation``: no standalone reconciliation after the latest broker effect ->
   awaiting_reconciliation (checks 2-4 of 03 sec.3.7 A2 are Continue preconditions, not
   per-action checks; a fresh start relies on the in-session reconciliation), a blocking
   reconciliation -> unrecognized_broker_activity (its unrecognized count > 0) else
   reconciliation_blocking;
4. price (S2-R3): no fresh observation -> pause(price_unavailable, detail); a deviation beyond
   ``pre_send_max_price_deviation`` -> pause(price_moved_beyond_tolerance);
5. risk: ``revalidate_pinned_intent`` at the fresh price against the refreshed portfolio ->
   reevaluate(risk_limit_failed:<code>).

The check never changes an identity (client_order_id, symbol, side, quantity), never re-plans
and writes nothing. Time comes from ``core.clock.now_utc()`` unless ``now`` is given.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from trading_platform.core import clock
from trading_platform.core.settings import Settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    OrderLifecycleState,
    PaperOrder,
    Strategy,
    StrategyRun,
    Symbol,
)
from trading_platform.services import operator_controls
from trading_platform.services.alpaca import PriceFailure, PriceLookupError, PriceObservation
from trading_platform.services.calendar_facts import (
    execution_window_from_window,
    load_calendar_window,
)
from trading_platform.services.evaluation_manifest import ManifestVerificationStatus
from trading_platform.services.execution.operations import (
    PausedReason,
    ReevaluationReason,
    TerminatedReason,
    WindowVerdict,
    is_working_order,
    risk_limit_failed,
    window_verdict,
)
from trading_platform.services.portfolio import PortfolioService, execution_basis_problem
from trading_platform.services.reconciliation.latest import (
    StandaloneReconciliation,
    latest_broker_effect_at,
    latest_standalone_reconciliation,
)
from trading_platform.services.recovery import GateCode, strategy_recovery_status
from trading_platform.services.risk import (
    PinnedIntentSpec,
    RiskRevalidation,
    current_risk_limits,
    revalidate_pinned_intent,
    verify_risk_run_manifest,
)


class PermissionVerdict(StrEnum):
    OK = "ok"
    PAUSE = "pause"
    REEVALUATE = "reevaluate"
    TERMINATE = "terminate"


#: The fixed precedence of the check steps (highest first); a table test pins it.
PERMISSION_STEPS: tuple[str, ...] = (
    "execution_window",
    "provenance",
    "owner",
    "enabled",
    "kill_switch",
    "outcome_unresolved",
    "working_orders",
    "awaiting_reconciliation",
    "reconciliation_blocking",
    "price",
    "risk",
)


@dataclass(frozen=True)
class PermissionOutcome:
    """Result of one permission check; ``reason`` is the operation reason value to persist."""

    verdict: PermissionVerdict
    step: str | None = None
    reason: str | None = None
    detail: str | None = None
    observation: PriceObservation | None = None
    revalidation: RiskRevalidation | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict is PermissionVerdict.OK

    def audit(self) -> dict[str, Any]:
        """Closed-shape audit payload stored with the attempt's permission-check event."""

        payload: dict[str, Any] = {
            "verdict": self.verdict.value,
            "step": self.step,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.observation is not None:
            payload["price_observation"] = self.observation.to_dict()
        if self.revalidation is not None:
            payload["risk_revalidation"] = {
                "code": self.revalidation.code.value,
                "valuation_price": str(self.revalidation.valuation_price),
                "notional": str(self.revalidation.notional),
            }
        payload.update(self.details)
        return payload


def _pause(
    step: str, reason: PausedReason, detail: str | None = None, **details: Any
) -> PermissionOutcome:
    return PermissionOutcome(
        PermissionVerdict.PAUSE, step=step, reason=reason.value, detail=detail, details=details
    )


def _reevaluate(
    step: str, reason: str, detail: str | None = None, **details: Any
) -> PermissionOutcome:
    return PermissionOutcome(
        PermissionVerdict.REEVALUATE, step=step, reason=reason, detail=detail, details=details
    )


def _terminate(step: str, reason: TerminatedReason) -> PermissionOutcome:
    return PermissionOutcome(PermissionVerdict.TERMINATE, step=step, reason=reason.value)


@dataclass(frozen=True)
class PinnedIntent:
    """The identity of the pinned intent being permitted; never changed by the check."""

    symbol: str
    side: str
    quantity: Decimal
    reference_price: Decimal | None
    client_order_id: str | None = None
    intent_id: uuid.UUID | None = None


class PriceSource(Protocol):
    """Injectable pre-send price source (S2-R3).

    Production: ``services.alpaca.AlpacaPriceSource`` (read-only latest-trade GET). Tests
    script observations and never call the network. ``reference_price`` is a hint for
    scripted doubles; the Alpaca source ignores it.
    """

    def latest_trade(
        self, symbol: str, *, reference_price: Decimal | None = None
    ) -> PriceObservation: ...


# ---------------------------------------------------------------------------
# Working orders (TL-2 widened from the operation to the strategy)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkingOrder:
    paper_order_id: uuid.UUID
    client_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    status: str
    last_synced_at: datetime | None


def strategy_working_orders(session: Session, strategy_id: str) -> list[WorkingOrder]:
    """The strategy's orders the broker knows that are not locally terminal, or that reached a
    terminal state without ever being synced (their fills may not be ingested yet): the TL-2
    predicate (``operations.is_working_order``) over the WHOLE strategy, independent of any
    operation, so a terminated operation's working order still blocks. One statement."""

    terminal = (
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.EXPIRED,
    )
    at_broker = or_(
        PaperOrder.broker_order_id.is_not(None),
        PaperOrder.status.in_(
            (OrderLifecycleState.SUBMITTED, OrderLifecycleState.PARTIALLY_FILLED)
        ),
    )
    rows = session.execute(
        select(PaperOrder, Symbol.ticker)
        .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .join(Symbol, Symbol.id == PaperOrder.symbol_id)
        .where(
            Strategy.strategy_id == strategy_id,
            at_broker,
            or_(PaperOrder.status.not_in(terminal), PaperOrder.last_synced_at.is_(None)),
        )
        .order_by(PaperOrder.created_at, PaperOrder.id)
    ).all()
    return [
        WorkingOrder(
            paper_order_id=order.id,
            client_order_id=order.client_order_id,
            symbol=ticker,
            side=order.side,
            quantity=order.quantity,
            status=order.status.value,
            last_synced_at=order.last_synced_at,
        )
        for order, ticker in rows
        if is_working_order(order)
    ]


# ---------------------------------------------------------------------------
# Seams (patched by tests/support/paper_execution_seams.py in suites whose subject is not
# the calendar or provenance; production has no bypass)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowFacts:
    verdict: WindowVerdict
    #: Today's regular-session open of the execution session (None when unknown).
    session_opens_at: datetime | None


def evaluation_window_facts(
    session: Session, *, as_of_session: Any, now: datetime, settings: Settings
) -> WindowFacts:
    """Where the evaluation session stands against the clock, from the persisted calendar."""

    window = load_calendar_window(session, now=now, settings=settings)
    verdict = window_verdict(window, settings, as_of_session)
    opens_at = None
    if verdict is WindowVerdict.OPEN:
        opens_at = execution_window_from_window(window, settings, as_of_session).opens_at
    return WindowFacts(verdict=verdict, session_opens_at=opens_at)


# ---------------------------------------------------------------------------
# The S2-R3 price step
# ---------------------------------------------------------------------------


def price_step(
    intent: PinnedIntent,
    *,
    price_source: PriceSource,
    session_opens_at: datetime | None,
    now: datetime,
    settings: Settings,
) -> tuple[PriceObservation | None, PermissionOutcome | None]:
    """Obtain and judge the fresh observation: ``(observation, None)`` on success, else
    ``(maybe observation, pause outcome)``. Freshness (all required): positive finite price,
    observed at or after today's regular-session open (never a previous day's trade), not dated
    later than the fetch time plus ``pre_send_price_future_skew_seconds`` (price_invalid) and not
    older than ``pre_send_price_max_age_seconds`` MEASURED FROM THE FETCH TIME
    (``max(now, fetched_at)``, because ``now`` was captured before the GET); tolerance: |price - reference| / reference
    <= ``pre_send_max_price_deviation``."""

    step = "price"
    try:
        observation = price_source.latest_trade(
            intent.symbol,
            reference_price=intent.reference_price,
        )
    except PriceLookupError as exc:
        return None, _pause(step, PausedReason.PRICE_UNAVAILABLE, exc.failure.value)
    except Exception:
        return None, _pause(
            step, PausedReason.PRICE_UNAVAILABLE, PriceFailure.PRICE_LOOKUP_FAILED.value
        )

    if not observation.price.is_finite() or observation.price <= 0:
        return observation, _pause(
            step, PausedReason.PRICE_UNAVAILABLE, PriceFailure.PRICE_INVALID.value
        )
    if session_opens_at is not None and observation.observed_at < session_opens_at:
        return observation, _pause(
            step, PausedReason.PRICE_UNAVAILABLE, PriceFailure.NO_TRADE_TODAY.value
        )
    judged_at = max(now, observation.fetched_at)
    skew = timedelta(seconds=settings.execution.pre_send_price_future_skew_seconds)
    if observation.observed_at > judged_at + skew:
        return observation, _pause(
            step, PausedReason.PRICE_UNAVAILABLE, PriceFailure.PRICE_INVALID.value
        )
    age = (judged_at - observation.observed_at).total_seconds()
    if age > settings.execution.pre_send_price_max_age_seconds:
        return observation, _pause(
            step, PausedReason.PRICE_UNAVAILABLE, PriceFailure.PRICE_STALE.value
        )
    reference = intent.reference_price
    if reference is not None and reference > 0:
        deviation = abs(observation.price - reference) / reference
        if deviation > Decimal(str(settings.execution.pre_send_max_price_deviation)):
            return observation, _pause(
                step,
                PausedReason.PRICE_MOVED_BEYOND_TOLERANCE,
                f"deviation_{deviation:.4f}",
                deviation=str(deviation),
            )
    return observation, None


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def _unrecognized_count(session: Session, reconciliation: StandaloneReconciliation) -> int:
    """Unrecognized orders + fills of an ACCOUNT-scope reconciliation (0 when not recorded)."""

    if reconciliation.scope != "account":
        return 0
    run = session.get(AccountReconciliationRun, reconciliation.run_id)
    summary = (run.classification_summary if run is not None else None) or {}
    total = 0
    for kind in ("orders", "fills"):
        part = summary.get(kind)
        if isinstance(part, dict):
            value = part.get("unrecognized")
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
    return total


def _strategy_enabled(gate: operator_controls.TradingGateState) -> bool:
    """Shared predicate: the requested strategy has a control row and is ``active``."""

    return gate.strategy is not None and gate.strategy.is_execution_enabled


def _reconciliation_block(
    session: Session, reconciliation: StandaloneReconciliation | None
) -> PausedReason | None:
    """Shared predicate: why a latest standalone reconciliation blocks trading, or ``None``
    (no reconciliation or a clean one). Unrecognized broker activity wins over plain blocking."""

    if reconciliation is None or reconciliation.is_clean:
        return None
    if _unrecognized_count(session, reconciliation) > 0:
        return PausedReason.UNRECOGNIZED_BROKER_ACTIVITY
    return PausedReason.RECONCILIATION_BLOCKING


class TradingBlocker(StrEnum):
    """Closed reasons trading is blocked right now (20.1-14; Phase 21 reuses the list).

    The value order is the deterministic output order of ``current_trading_blockers``. Every
    member is the per-intent pause reason of the same name, except ``no_active_paper_strategy``
    and ``strategy_disabled`` (the ``owner`` and ``enabled`` steps).
    """

    NO_ACTIVE_PAPER_STRATEGY = "no_active_paper_strategy"
    STRATEGY_DISABLED = "strategy_disabled"
    KILL_SWITCH_TRIPPED = "kill_switch_tripped"
    OUTCOME_UNRESOLVED = "outcome_unresolved"
    RECONCILIATION_BLOCKING = "reconciliation_blocking"
    UNRECOGNIZED_BROKER_ACTIVITY = "unrecognized_broker_activity"
    WORKING_ORDER_COMMITMENTS_UNACCOUNTED = "working_order_commitments_unaccounted"


def current_trading_blockers(
    session: Session, *, now: datetime | None = None
) -> list[TradingBlocker]:
    """Why a paper session cannot trade right now, from the SAME predicates as
    ``check_intent_permission`` (owner, enabled, kill switch, recovery, working orders,
    latest standalone reconciliation). Read-only; a bounded number of statements independent
    of history size. Empty list = nothing blocks.

    The three recovery gate codes (``outcome_unresolved``, ``reconciliation_required``,
    ``reconciliation_not_clean``) are all ``outcome_unresolved`` here: each refuses a session
    at submit time. The per-intent check pauses on ``outcome_unresolved`` only and leaves the
    other two to its reconciliation step (a running session reconciles in-session).
    """

    at = now or clock.now_utc()
    gate = operator_controls.load_trading_gate_state(session)
    owner_id = gate.owner.strategy_id
    if owner_id is None:
        # Nothing owns the account: nothing may trade. The kill switch is global, so it is
        # still reported; strategy-scoped facts have no subject.
        blockers = [TradingBlocker.NO_ACTIVE_PAPER_STRATEGY]
        if gate.kill_switch.is_tripped:
            blockers.append(TradingBlocker.KILL_SWITCH_TRIPPED)
        return blockers

    gate = operator_controls.load_trading_gate_state(session, strategy_id=owner_id)
    blockers = []
    if not _strategy_enabled(gate):
        blockers.append(TradingBlocker.STRATEGY_DISABLED)
    if gate.kill_switch.is_tripped:
        blockers.append(TradingBlocker.KILL_SWITCH_TRIPPED)
    if strategy_recovery_status(session, owner_id, now=at).gate_code is not None:
        blockers.append(TradingBlocker.OUTCOME_UNRESOLVED)
    effect_at = latest_broker_effect_at(session, owner_id)
    reconciliation = latest_standalone_reconciliation(session, owner_id, completed_after=effect_at)
    reconciliation_reason = _reconciliation_block(session, reconciliation)
    if reconciliation_reason is not None:
        blockers.append(TradingBlocker(reconciliation_reason.value))
    if strategy_working_orders(session, owner_id):
        blockers.append(TradingBlocker.WORKING_ORDER_COMMITMENTS_UNACCOUNTED)
    order = list(TradingBlocker)
    return sorted(blockers, key=order.index)


def check_intent_permission(
    session: Session,
    *,
    strategy_id: str,
    as_of_session: Any,
    risk_run_id: uuid.UUID,
    intent: PinnedIntent,
    price_source: PriceSource,
    settings: Settings,
    continuation: bool = False,
    now: datetime | None = None,
) -> PermissionOutcome:
    """Run the fixed-precedence permission check for ONE pinned intent. Read-only."""

    at = now or clock.now_utc()

    # (1) execution window
    facts = evaluation_window_facts(session, as_of_session=as_of_session, now=at, settings=settings)
    if facts.verdict is WindowVerdict.ELAPSED:
        return _terminate("execution_window", TerminatedReason.EXECUTION_WINDOW_ELAPSED)
    if facts.verdict is WindowVerdict.SUPERSEDED:
        return _terminate("execution_window", TerminatedReason.EVALUATION_SUPERSEDED)
    if facts.verdict is WindowVerdict.UNKNOWN:
        return _pause(
            "execution_window",
            PausedReason.EXECUTION_WINDOW_NOT_OPEN,
            "calendar_data_unavailable",
        )
    if facts.verdict is WindowVerdict.NOT_YET_OPEN:
        return _pause("execution_window", PausedReason.EXECUTION_WINDOW_NOT_OPEN, "not_yet_open")

    # (2) provenance of the PINNED run
    verification = verify_risk_run_manifest(
        risk_run_id=risk_run_id, strategy_id=strategy_id, settings=settings
    )
    if verification.status is ManifestVerificationStatus.STRATEGY_SETTINGS_CHANGED:
        return _reevaluate("provenance", ReevaluationReason.STRATEGY_SETTINGS_CHANGED.value)
    if verification.status is ManifestVerificationStatus.EVALUATION_DATA_CHANGED:
        return _reevaluate("provenance", ReevaluationReason.EVALUATION_DATA_CHANGED.value)
    if verification.status is ManifestVerificationStatus.MANIFEST_MISSING:
        return _reevaluate(
            "provenance", ReevaluationReason.EVALUATION_DATA_CHANGED.value, "manifest_missing"
        )

    # (3) pauses
    gate = operator_controls.load_trading_gate_state(session, strategy_id=strategy_id)
    block = gate.ownership_block_for(strategy_id)
    if block is not None:
        return _pause("owner", PausedReason.NOT_ACTIVE_PAPER_STRATEGY, block.value)
    if not _strategy_enabled(gate):
        return _pause("enabled", PausedReason.STRATEGY_DISABLED)
    if gate.kill_switch.is_tripped:
        return _pause(
            "kill_switch",
            PausedReason.KILL_SWITCH_TRIPPED,
            kill_switch=gate.kill_switch.to_dict(),
        )
    recovery = strategy_recovery_status(session, strategy_id, now=at)
    if recovery.gate_code is GateCode.OUTCOME_UNRESOLVED:
        return _pause("outcome_unresolved", PausedReason.OUTCOME_UNRESOLVED)
    working = strategy_working_orders(session, strategy_id)
    if working:
        return _pause(
            "working_orders",
            PausedReason.WORKING_ORDER_COMMITMENTS_UNACCOUNTED,
            working_orders=[order.client_order_id for order in working],
        )
    effect_at = latest_broker_effect_at(session, strategy_id)
    reconciliation = latest_standalone_reconciliation(
        session, strategy_id, completed_after=effect_at
    )
    if continuation and reconciliation is None:
        return _pause("awaiting_reconciliation", PausedReason.AWAITING_RECONCILIATION)
    reconciliation_reason = _reconciliation_block(session, reconciliation)
    if reconciliation_reason is not None:
        return _pause("reconciliation_blocking", reconciliation_reason)

    # (4) fresh price
    observation, price_outcome = price_step(
        intent,
        price_source=price_source,
        session_opens_at=facts.session_opens_at,
        now=at,
        settings=settings,
    )
    if price_outcome is not None:
        if observation is not None:
            return PermissionOutcome(
                price_outcome.verdict,
                step=price_outcome.step,
                reason=price_outcome.reason,
                detail=price_outcome.detail,
                observation=observation,
                details=price_outcome.details,
            )
        return price_outcome
    assert observation is not None

    # (5) portfolio risk at the fresh price against the refreshed portfolio
    # COR-01/SAF-09: sizing uses cash from a broker-observed snapshot no older than
    # execution.account_snapshot_max_age_seconds; the configured-cash fallback never sizes a send.
    state, basis = PortfolioService(settings).load_state_with_basis(
        session, strategy_id=strategy_id, as_of_session=as_of_session, now=at
    )
    basis_problem = execution_basis_problem(
        basis, max_age_seconds=settings.execution.account_snapshot_max_age_seconds
    )
    if basis_problem is not None:
        return _pause("risk", PausedReason.AWAITING_RECONCILIATION, basis_problem)
    revalidation = revalidate_pinned_intent(
        PinnedIntentSpec(
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            reference_price=intent.reference_price
            if intent.reference_price is not None
            else observation.price,
        ),
        state,
        current_risk_limits(settings, strategy_id),
        observation,
    )
    if revalidation.failed:
        return PermissionOutcome(
            PermissionVerdict.REEVALUATE,
            step="risk",
            reason=risk_limit_failed(revalidation.code),
            detail=revalidation.reason[:64],
            observation=observation,
            revalidation=revalidation,
        )
    return PermissionOutcome(
        PermissionVerdict.OK, observation=observation, revalidation=revalidation
    )


__all__ = [
    "PERMISSION_STEPS",
    "PermissionOutcome",
    "PermissionVerdict",
    "PinnedIntent",
    "TradingBlocker",
    "PriceSource",
    "WindowFacts",
    "WorkingOrder",
    "check_intent_permission",
    "current_trading_blockers",
    "evaluation_window_facts",
    "price_step",
    "strategy_working_orders",
]
