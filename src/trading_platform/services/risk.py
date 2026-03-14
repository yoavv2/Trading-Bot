"""Risk-validation pipeline for strategy signals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings, get_strategy_config, load_settings
from trading_platform.services.market_data_access import (
    latest_completed_session,
    missing_bars_for_session,
    persisted_session_dates,
)
from trading_platform.services.portfolio import (
    EntrySizingResult,
    PortfolioService,
    PortfolioState,
    PositionSnapshot,
)
from trading_platform.strategies.signals import Signal, SignalBatch, SignalDirection


class RiskDecisionOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class RiskDecisionCode(StrEnum):
    APPROVED = "approved"
    NON_ACTIONABLE_SIGNAL = "non_actionable_signal"
    STALE_MARKET_DATA = "stale_market_data"
    DUPLICATE_OPEN_POSITION = "duplicate_open_position"
    NO_OPEN_POSITION = "no_open_position"
    MAX_POSITIONS = "max_positions"
    STRATEGY_ALLOCATION_CAP = "strategy_allocation_cap"
    TOTAL_ALLOCATION_CAP = "total_allocation_cap"
    INSUFFICIENT_CASH = "insufficient_cash"
    ORDER_ROUNDS_TO_ZERO = "order_rounds_to_zero"


@dataclass(frozen=True)
class RiskEvaluationRequest:
    db_session: Session
    signal_batch: SignalBatch
    portfolio_state: PortfolioState


@dataclass(frozen=True)
class RiskDecision:
    strategy_id: str
    symbol: str
    session_date: Any
    signal_direction: str
    signal_reason: str
    outcome: RiskDecisionOutcome
    code: RiskDecisionCode
    reason: str
    reference_price: Decimal
    proposed_quantity: Decimal | None = None
    proposed_notional: Decimal | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "session_date": self.session_date.isoformat(),
            "signal_direction": self.signal_direction,
            "signal_reason": self.signal_reason,
            "outcome": self.outcome.value,
            "code": self.code.value,
            "reason": self.reason,
            "reference_price": float(self.reference_price),
            "proposed_quantity": float(self.proposed_quantity) if self.proposed_quantity is not None else None,
            "proposed_notional": float(self.proposed_notional) if self.proposed_notional is not None else None,
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class RiskEvaluationResult:
    strategy_id: str
    as_of_session: Any
    decisions: tuple[RiskDecision, ...]

    @property
    def approved(self) -> list[RiskDecision]:
        return [decision for decision in self.decisions if decision.outcome == RiskDecisionOutcome.APPROVED]

    @property
    def rejected(self) -> list[RiskDecision]:
        return [decision for decision in self.decisions if decision.outcome == RiskDecisionOutcome.REJECTED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "as_of_session": self.as_of_session.isoformat(),
            "decision_count": len(self.decisions),
            "approved_count": len(self.approved),
            "rejected_count": len(self.rejected),
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


class RiskService(ABC):
    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Describe the risk capability exposed to the platform."""

    @abstractmethod
    def validate(self, payload: RiskEvaluationRequest) -> RiskEvaluationResult:
        """Validate strategy signals against the live portfolio state."""


class PortfolioRiskService(RiskService):
    """Deterministic v1 risk service for signal gating."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or load_settings()
        self._portfolio_service = PortfolioService(self._settings)

    def describe(self) -> dict[str, Any]:
        return {
            "service": "risk",
            "status": "available",
            "detail": "Deterministic portfolio and risk validation is enabled.",
        }

    def validate(self, payload: RiskEvaluationRequest) -> RiskEvaluationResult:
        batch = payload.signal_batch
        config = get_strategy_config(self._settings, batch.strategy_id)
        stale_reason = self._stale_data_reason(
            payload.db_session,
            strategy_id=batch.strategy_id,
            as_of_session=batch.as_of_session,
        )

        working_state = payload.portfolio_state
        decisions_by_symbol: dict[str, RiskDecision] = {}

        for signal in batch.exit_signals:
            decision = self._evaluate_signal(
                signal,
                state=working_state,
                max_positions=config.risk.max_positions,
                risk_per_trade=config.risk.risk_per_trade,
                stale_reason=stale_reason,
            )
            decisions_by_symbol[signal.symbol] = decision
            if decision.outcome == RiskDecisionOutcome.APPROVED:
                working_state = self._without_position(working_state, signal.symbol)

        for signal in [item for item in batch.signals if item.direction != SignalDirection.EXIT]:
            decision = self._evaluate_signal(
                signal,
                state=working_state,
                max_positions=config.risk.max_positions,
                risk_per_trade=config.risk.risk_per_trade,
                stale_reason=stale_reason,
            )
            decisions_by_symbol[signal.symbol] = decision
            if decision.outcome == RiskDecisionOutcome.APPROVED and signal.direction == SignalDirection.LONG:
                working_state = self._with_approved_entry(working_state, signal, decision)

        return RiskEvaluationResult(
            strategy_id=batch.strategy_id,
            as_of_session=batch.as_of_session,
            decisions=tuple(decisions_by_symbol[signal.symbol] for signal in batch.signals),
        )

    def _evaluate_signal(
        self,
        signal: Signal,
        *,
        state: PortfolioState,
        max_positions: int,
        risk_per_trade: float,
        stale_reason: str | None,
    ) -> RiskDecision:
        reference_price = signal.indicators.close

        if signal.direction == SignalDirection.FLAT:
            return self._reject(
                signal,
                code=RiskDecisionCode.NON_ACTIONABLE_SIGNAL,
                reason=f"Signal is flat ({signal.reason.value}); there is no execution candidate to approve.",
            )

        if stale_reason is not None:
            return self._reject(
                signal,
                code=RiskDecisionCode.STALE_MARKET_DATA,
                reason=stale_reason,
            )

        if signal.direction == SignalDirection.EXIT:
            position = next((item for item in state.open_positions if item.symbol == signal.symbol), None)
            if position is None:
                return self._reject(
                    signal,
                    code=RiskDecisionCode.NO_OPEN_POSITION,
                    reason="No open position exists for this symbol, so there is nothing to exit.",
                )
            return self._approve(
                signal,
                quantity=position.quantity,
                notional=position.quantity * reference_price,
                reason="Exit signal approved against the current open position.",
            )

        if signal.symbol in state.open_symbols:
            return self._reject(
                signal,
                code=RiskDecisionCode.DUPLICATE_OPEN_POSITION,
                reason="A live open position already exists for this symbol.",
            )

        if state.position_count >= max_positions:
            return self._reject(
                signal,
                code=RiskDecisionCode.MAX_POSITIONS,
                reason=f"Open positions already meet the strategy max position limit ({max_positions}).",
            )

        sizing = self._portfolio_service.compute_entry_size(
            state,
            candidate_price=reference_price,
            risk_per_trade=risk_per_trade,
        )
        if sizing.quantity <= 0:
            return self._reject_for_sizing(signal, sizing)

        return self._approve(
            signal,
            quantity=sizing.quantity,
            notional=sizing.approved_notional,
            reason=(
                "Entry signal approved within position, strategy allocation, "
                "portfolio allocation, and cash limits."
            ),
            metadata={
                "target_notional": float(sizing.target_notional),
                "remaining_cash": float(sizing.remaining_cash),
                "remaining_strategy_capacity": float(sizing.remaining_strategy_capacity),
                "remaining_total_capacity": float(sizing.remaining_total_capacity),
            },
        )

    def _stale_data_reason(
        self,
        db_session: Session,
        *,
        strategy_id: str,
        as_of_session,
    ) -> str | None:
        exchange = self._settings.market_data.calendar.exchange
        latest_session = latest_completed_session(db_session, exchange=exchange)
        if latest_session is None:
            return "No persisted completed market session is available for risk evaluation."
        if as_of_session > latest_session:
            return (
                f"Requested session {as_of_session.isoformat()} is newer than the latest persisted "
                f"completed session {latest_session.isoformat()}."
            )

        between = persisted_session_dates(
            db_session,
            start=as_of_session,
            end=latest_session,
            exchange=exchange,
        )
        lag_sessions = max(len(between) - 1, 0) if between else 0
        if lag_sessions > self._settings.portfolio.stale_data_max_session_lag:
            return (
                f"Requested session {as_of_session.isoformat()} is stale by {lag_sessions} session(s); "
                f"allowed lag is {self._settings.portfolio.stale_data_max_session_lag}."
            )

        config = get_strategy_config(self._settings, strategy_id)
        missing = missing_bars_for_session(
            db_session,
            as_of_session,
            symbols=list(config.universe),
        )
        if missing:
            joined = ", ".join(missing)
            return (
                f"Persisted bars are missing for session {as_of_session.isoformat()} "
                f"for symbols: {joined}."
            )
        return None

    def _reject_for_sizing(self, signal: Signal, sizing: EntrySizingResult) -> RiskDecision:
        if sizing.remaining_strategy_capacity <= 0:
            code = RiskDecisionCode.STRATEGY_ALLOCATION_CAP
            reason = "Strategy allocation capacity is exhausted."
        elif sizing.remaining_total_capacity <= 0:
            code = RiskDecisionCode.TOTAL_ALLOCATION_CAP
            reason = "Total portfolio allocation capacity is exhausted."
        elif sizing.remaining_cash < sizing.candidate_price:
            code = RiskDecisionCode.INSUFFICIENT_CASH
            reason = "Available cash is below the cost of one share at the current reference price."
        else:
            code = RiskDecisionCode.ORDER_ROUNDS_TO_ZERO
            reason = "Sizing rounded down to zero whole shares at the current risk budget."
        return self._reject(signal, code=code, reason=reason)

    def _approve(
        self,
        signal: Signal,
        *,
        quantity: Decimal,
        notional,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> RiskDecision:
        return RiskDecision(
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            session_date=signal.session_date,
            signal_direction=signal.direction.value,
            signal_reason=signal.reason.value,
            outcome=RiskDecisionOutcome.APPROVED,
            code=RiskDecisionCode.APPROVED,
            reason=reason,
            reference_price=signal.indicators.close,
            proposed_quantity=quantity,
            proposed_notional=Decimal(str(notional)).quantize(Decimal("0.000001")),
            metadata=metadata or {},
        )

    def _reject(
        self,
        signal: Signal,
        *,
        code: RiskDecisionCode,
        reason: str,
    ) -> RiskDecision:
        return RiskDecision(
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            session_date=signal.session_date,
            signal_direction=signal.direction.value,
            signal_reason=signal.reason.value,
            outcome=RiskDecisionOutcome.REJECTED,
            code=code,
            reason=reason,
            reference_price=signal.indicators.close,
            metadata={},
        )

    def _without_position(self, state: PortfolioState, symbol: str) -> PortfolioState:
        remaining_positions = tuple(position for position in state.open_positions if position.symbol != symbol)
        released_value = sum(
            (position.market_value for position in state.open_positions if position.symbol == symbol),
            start=Decimal("0"),
        )
        return PortfolioState(
            cash=state.cash,
            gross_exposure=max(state.gross_exposure - released_value, Decimal("0")).quantize(Decimal("0.000001")),
            total_equity=state.total_equity,
            strategy_exposure=max(state.strategy_exposure - released_value, Decimal("0")).quantize(Decimal("0.000001")),
            as_of_session=state.as_of_session,
            open_positions=remaining_positions,
            open_symbols=frozenset(position.symbol for position in remaining_positions),
            total_open_positions=max(state.total_open_positions - 1, 0),
        )

    def _with_approved_entry(
        self,
        state: PortfolioState,
        signal: Signal,
        decision: RiskDecision,
    ) -> PortfolioState:
        quantity = decision.proposed_quantity or Decimal("0")
        notional = decision.proposed_notional or Decimal("0")
        pending_position = PositionSnapshot(
            position_id=f"pending:{signal.symbol}",
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            quantity=quantity,
            average_entry_price=signal.indicators.close,
            market_price=signal.indicators.close,
            market_value=notional,
        )
        open_positions = tuple([*state.open_positions, pending_position])
        return PortfolioState(
            cash=state.cash,
            gross_exposure=(state.gross_exposure + notional).quantize(Decimal("0.000001")),
            total_equity=state.total_equity,
            strategy_exposure=(state.strategy_exposure + notional).quantize(Decimal("0.000001")),
            as_of_session=state.as_of_session,
            open_positions=open_positions,
            open_symbols=frozenset([*state.open_symbols, signal.symbol]),
            total_open_positions=state.total_open_positions + 1,
        )


class PlaceholderRiskService(RiskService):
    def describe(self) -> dict[str, Any]:
        return {
            "service": "risk",
            "status": "deferred",
            "detail": "Deferred to Phase 4 portfolio and risk controls.",
        }

    def validate(self, payload: RiskEvaluationRequest) -> RiskEvaluationResult:
        raise NotImplementedError("Risk validation is deferred to Phase 4.")
