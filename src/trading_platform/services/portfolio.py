"""Portfolio-state helpers for live risk evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from trading_platform.core.settings import PortfolioSettings, Settings, load_settings

MONEY_SCALE = Decimal("0.000001")


def _money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_SCALE)


@dataclass(frozen=True)
class PositionSnapshot:
    """Lightweight live-position view used for portfolio accounting."""

    symbol: str
    quantity: Decimal
    market_price: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class PortfolioState:
    """Deterministic portfolio snapshot used for sizing and risk evaluation."""

    cash: Decimal
    gross_exposure: Decimal
    total_equity: Decimal
    strategy_exposure: Decimal
    open_positions: tuple[PositionSnapshot, ...] = field(default_factory=tuple)
    open_symbols: frozenset[str] = field(default_factory=frozenset)

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
