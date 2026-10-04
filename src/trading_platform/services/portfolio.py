"""Portfolio-state helpers for live risk evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import PortfolioSettings, Settings, load_settings
from trading_platform.db.models import AccountSnapshot, Position, Strategy
from trading_platform.services.account_baseline import latest_broker_observed_account_snapshot
from trading_platform.services.market_data_access import (
    bars_for_session_date,
    latest_completed_session,
)

MONEY_SCALE = Decimal("0.000001")


def _money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_SCALE)


@dataclass(frozen=True)
class PositionSnapshot:
    """Lightweight live-position view used for portfolio accounting."""

    position_id: str
    strategy_id: str
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    market_price: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class PortfolioState:
    """Deterministic portfolio snapshot used for sizing and risk evaluation."""

    cash: Decimal
    gross_exposure: Decimal
    total_equity: Decimal
    strategy_exposure: Decimal
    as_of_session: date | None = None
    open_positions: tuple[PositionSnapshot, ...] = field(default_factory=tuple)
    open_symbols: frozenset[str] = field(default_factory=frozenset)
    total_open_positions: int = 0

    @property
    def position_count(self) -> int:
        return len(self.open_positions)


BasisSource = Literal["broker_sync", "configured_starting_cash"]


@dataclass(frozen=True)
class PortfolioBasis:
    """Where an evaluation's portfolio inputs came from (D-27).

    ``source`` is closed: ``broker_sync`` when cash came from the latest
    broker-observed account snapshot, ``configured_starting_cash`` when no such
    snapshot exists (the fallback is recorded, never silent).
    """

    source: BasisSource
    cash: Decimal
    gross_exposure: Decimal
    total_equity: Decimal
    positions: tuple[dict[str, Any], ...]
    total_open_positions: int
    as_of_session: date | None
    snapshot_id: str | None
    snapshot_at: datetime | None
    age_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "cash": str(self.cash),
            "gross_exposure": str(self.gross_exposure),
            "total_equity": str(self.total_equity),
            "positions": [dict(position) for position in self.positions],
            "total_open_positions": self.total_open_positions,
            "as_of_session": self.as_of_session.isoformat() if self.as_of_session else None,
            "snapshot_id": self.snapshot_id,
            "snapshot_at": self.snapshot_at.isoformat() if self.snapshot_at else None,
            "age_seconds": self.age_seconds,
        }


@dataclass(frozen=True)
class EntrySizingResult:
    """Whole-share sizing output bounded by cash and allocation limits."""

    quantity: Decimal
    candidate_price: Decimal
    target_notional: Decimal
    approved_notional: Decimal
    remaining_cash: Decimal
    remaining_strategy_capacity: Decimal
    remaining_total_capacity: Decimal


@dataclass(frozen=True)
class EntryCapacity:
    """Remaining room for a new entry under the allocation caps and cash (shared limit arithmetic).

    Both ``compute_entry_size`` (evaluation sizing) and the pre-send revalidation of a pinned
    intent read this one computation, so the numbers cannot drift apart.
    """

    equity: Decimal
    remaining_strategy_capacity: Decimal
    remaining_total_capacity: Decimal
    remaining_cash: Decimal


class PortfolioService:
    """Compute deterministic position sizing from typed portfolio settings."""

    def __init__(self, settings: Settings | PortfolioSettings | None = None) -> None:
        if settings is None:
            self._settings = load_settings().portfolio
        elif isinstance(settings, Settings):
            self._settings = settings.portfolio
        else:
            self._settings = settings

    @property
    def settings(self) -> PortfolioSettings:
        return self._settings

    def empty_state(self, *, cash: Decimal | float | int | None = None) -> PortfolioState:
        starting_cash = cash if cash is not None else self.settings.starting_cash_decimal
        resolved_cash = _money(starting_cash)
        return PortfolioState(
            cash=resolved_cash,
            gross_exposure=_money(0),
            total_equity=resolved_cash,
            strategy_exposure=_money(0),
        )

    def load_state(
        self,
        session: Session,
        *,
        strategy_id: str,
        as_of_session: date | None = None,
    ) -> PortfolioState:
        state, _basis = self.load_state_with_basis(
            session, strategy_id=strategy_id, as_of_session=as_of_session
        )
        return state

    def load_state_with_basis(
        self,
        session: Session,
        *,
        strategy_id: str,
        as_of_session: date | None = None,
        now: datetime | None = None,
    ) -> tuple[PortfolioState, PortfolioBasis]:
        """Compute the portfolio state and record where its cash came from (D-27).

        Cash comes only from the latest broker-observed account snapshot
        (``snapshot_source == 'broker_sync'``), falling back to the configured
        starting cash. ``buying_power`` is never read.
        """

        strategy_record = session.execute(
            select(Strategy).where(Strategy.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if strategy_record is None:
            raise LookupError(f"Unknown strategy '{strategy_id}'.")

        latest_snapshot = latest_broker_observed_account_snapshot(session)
        cash = _money(
            latest_snapshot.cash if latest_snapshot is not None else self.settings.starting_cash_decimal
        )

        valuation_session = as_of_session or latest_completed_session(
            session,
            exchange=load_settings().market_data.calendar.exchange,
        )

        open_rows = session.execute(
            select(Position, Strategy.strategy_id)
            .join(Strategy, Strategy.id == Position.strategy_id)
            .where(Position.status == "open")
        ).all()
        symbols = [row.Position.symbol_ref.ticker for row in open_rows]
        price_map = (
            bars_for_session_date(session, valuation_session, symbols=symbols)
            if valuation_session is not None and symbols
            else {}
        )

        basis_positions: list[dict[str, Any]] = []
        strategy_positions: list[PositionSnapshot] = []
        strategy_symbols: set[str] = set()
        gross_exposure = Decimal("0")
        strategy_exposure = Decimal("0")

        for position, owner_strategy_id in open_rows:
            ticker = position.symbol_ref.ticker
            market_price = (
                price_map[ticker].close if ticker in price_map else _money(position.average_entry_price)
            )
            market_value = _money(position.quantity * market_price)
            gross_exposure += market_value
            basis_positions.append(
                {
                    "symbol": ticker,
                    "quantity": str(_money(position.quantity)),
                    "market_value": str(market_value),
                    "strategy_id": owner_strategy_id,
                }
            )

            if owner_strategy_id != strategy_id:
                continue

            strategy_exposure += market_value
            strategy_symbols.add(ticker)
            strategy_positions.append(
                PositionSnapshot(
                    position_id=str(position.id),
                    strategy_id=owner_strategy_id,
                    symbol=ticker,
                    quantity=_money(position.quantity),
                    average_entry_price=_money(position.average_entry_price),
                    market_price=_money(market_price),
                    market_value=market_value,
                )
            )

        gross_exposure = _money(gross_exposure)
        strategy_exposure = _money(strategy_exposure)
        state = PortfolioState(
            cash=cash,
            gross_exposure=gross_exposure,
            total_equity=_money(cash + gross_exposure),
            strategy_exposure=strategy_exposure,
            as_of_session=valuation_session,
            open_positions=tuple(strategy_positions),
            open_symbols=frozenset(strategy_symbols),
            total_open_positions=len(open_rows),
        )
        reference_now = now or datetime.now(UTC)
        basis_source: BasisSource = (
            "broker_sync" if latest_snapshot is not None else "configured_starting_cash"
        )
        basis = PortfolioBasis(
            source=basis_source,
            cash=state.cash,
            gross_exposure=state.gross_exposure,
            total_equity=state.total_equity,
            positions=tuple(basis_positions),
            total_open_positions=state.total_open_positions,
            as_of_session=valuation_session,
            snapshot_id=str(latest_snapshot.id) if latest_snapshot is not None else None,
            snapshot_at=latest_snapshot.snapshot_at if latest_snapshot is not None else None,
            age_seconds=(
                max((reference_now - latest_snapshot.snapshot_at).total_seconds(), 0.0)
                if latest_snapshot is not None
                else None
            ),
        )
        return state, basis

    def record_snapshot(
        self,
        session: Session,
        *,
        strategy_id: str | None,
        state: PortfolioState,
        source_run_id=None,
        snapshot_source: str = "derived",
        snapshot_at: datetime | None = None,
    ) -> AccountSnapshot:
        """Persist a snapshot of ``state``. No production caller since 20.1-03 (D-27)."""

        strategy_record = None
        if strategy_id is not None:
            strategy_record = session.execute(
                select(Strategy).where(Strategy.strategy_id == strategy_id)
            ).scalar_one_or_none()

        snapshot = AccountSnapshot(
            strategy_id=strategy_record.id if strategy_record is not None else None,
            source_run_id=source_run_id,
            snapshot_source=snapshot_source,
            snapshot_at=snapshot_at or datetime.now(UTC),
            cash=state.cash,
            gross_exposure=state.gross_exposure,
            total_equity=state.total_equity,
            buying_power=state.cash,
            open_positions=state.total_open_positions or state.position_count,
        )
        session.add(snapshot)
        session.flush()
        return snapshot

    def entry_capacity(self, state: PortfolioState) -> EntryCapacity:
        """Remaining strategy / total allocation capacity and cash of ``state``."""

        equity = (
            state.total_equity
            if state.total_equity > 0
            else _money(state.cash + state.gross_exposure)
        )
        strategy_cap = _money(equity * Decimal(str(self.settings.max_strategy_allocation_pct)))
        total_cap = _money(equity * Decimal(str(self.settings.max_total_portfolio_allocation_pct)))
        return EntryCapacity(
            equity=equity,
            remaining_strategy_capacity=_money(
                max(strategy_cap - state.strategy_exposure, Decimal("0"))
            ),
            remaining_total_capacity=_money(max(total_cap - state.gross_exposure, Decimal("0"))),
            remaining_cash=_money(max(state.cash, Decimal("0"))),
        )

    def compute_entry_size(
        self,
        state: PortfolioState,
        *,
        candidate_price: Decimal | float | int,
        risk_per_trade: Decimal | float | int,
    ) -> EntrySizingResult:
        price = _money(candidate_price)
        if price <= 0:
            return EntrySizingResult(
                quantity=Decimal("0"),
                candidate_price=price,
                target_notional=_money(0),
                approved_notional=_money(0),
                remaining_cash=_money(max(state.cash, Decimal("0"))),
                remaining_strategy_capacity=_money(0),
                remaining_total_capacity=_money(0),
            )

        capacity = self.entry_capacity(state)
        risk_budget = Decimal(str(risk_per_trade))
        target_notional = _money(capacity.equity * risk_budget)
        remaining_strategy_capacity = capacity.remaining_strategy_capacity
        remaining_total_capacity = capacity.remaining_total_capacity
        remaining_cash = capacity.remaining_cash

        approved_notional = min(
            target_notional,
            remaining_strategy_capacity,
            remaining_total_capacity,
            remaining_cash,
        )
        if approved_notional <= 0:
            quantity = Decimal("0")
            approved_notional = _money(0)
        else:
            quantity = (approved_notional / price).quantize(Decimal("1"), rounding=ROUND_DOWN)
            approved_notional = _money(quantity * price)

        return EntrySizingResult(
            quantity=quantity,
            candidate_price=price,
            target_notional=target_notional,
            approved_notional=approved_notional,
            remaining_cash=remaining_cash,
            remaining_strategy_capacity=remaining_strategy_capacity,
            remaining_total_capacity=remaining_total_capacity,
        )
