"""Portfolio-state helpers for live risk evaluation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import PortfolioSettings, Settings, load_settings
from trading_platform.db.models import AccountSnapshot, Position, Strategy
from trading_platform.services.market_data_access import bars_for_session_date, latest_completed_session

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
        strategy_record = session.execute(
            select(Strategy).where(Strategy.strategy_id == strategy_id)
        ).scalar_one_or_none()
        if strategy_record is None:
            raise LookupError(f"Unknown strategy '{strategy_id}'.")

        latest_snapshot = session.execute(
            select(AccountSnapshot).order_by(AccountSnapshot.snapshot_at.desc()).limit(1)
        ).scalar_one_or_none()
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
        return PortfolioState(
            cash=cash,
            gross_exposure=gross_exposure,
            total_equity=_money(cash + gross_exposure),
            strategy_exposure=strategy_exposure,
            as_of_session=valuation_session,
            open_positions=tuple(strategy_positions),
            open_symbols=frozenset(strategy_symbols),
            total_open_positions=len(open_rows),
        )

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

        equity = state.total_equity if state.total_equity > 0 else _money(state.cash + state.gross_exposure)
        risk_budget = Decimal(str(risk_per_trade))
        target_notional = _money(equity * risk_budget)
        strategy_cap = _money(equity * Decimal(str(self.settings.max_strategy_allocation_pct)))
        total_cap = _money(equity * Decimal(str(self.settings.max_total_portfolio_allocation_pct)))
        remaining_strategy_capacity = _money(max(strategy_cap - state.strategy_exposure, Decimal("0")))
        remaining_total_capacity = _money(max(total_cap - state.gross_exposure, Decimal("0")))
        remaining_cash = _money(max(state.cash, Decimal("0")))

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
