from __future__ import annotations

from decimal import Decimal

from trading_platform.core.settings import PortfolioSettings
from trading_platform.services.portfolio import PortfolioService, PortfolioState


def test_empty_state_uses_typed_starting_cash() -> None:
    service = PortfolioService(
        PortfolioSettings(
            starting_cash=250_000,
            max_strategy_allocation_pct=1.0,
            max_total_portfolio_allocation_pct=1.0,
        )
    )

    state = service.empty_state()

    assert state.cash == Decimal("250000.000000")
    assert state.total_equity == Decimal("250000.000000")
    assert state.gross_exposure == Decimal("0.000000")
    assert state.position_count == 0


def test_compute_entry_size_rounds_down_to_whole_shares() -> None:
    service = PortfolioService(
        PortfolioSettings(
            starting_cash=100_000,
            max_strategy_allocation_pct=1.0,
            max_total_portfolio_allocation_pct=1.0,
        )
    )
    state = service.empty_state()

    sizing = service.compute_entry_size(
        state,
        candidate_price=Decimal("123"),
        risk_per_trade=Decimal("0.01"),
    )

    assert sizing.target_notional == Decimal("1000.000000")
    assert sizing.quantity == Decimal("8")
    assert sizing.approved_notional == Decimal("984.000000")


def test_compute_entry_size_honors_strategy_and_total_allocation_caps() -> None:
    service = PortfolioService(
        PortfolioSettings(
            starting_cash=100_000,
            max_strategy_allocation_pct=0.10,
            max_total_portfolio_allocation_pct=0.08,
        )
    )
    state = PortfolioState(
        cash=Decimal("5000.000000"),
        gross_exposure=Decimal("7750.000000"),
        total_equity=Decimal("100000.000000"),
        strategy_exposure=Decimal("9500.000000"),
    )

    sizing = service.compute_entry_size(
        state,
        candidate_price=Decimal("100"),
        risk_per_trade=Decimal("0.02"),
    )

    assert sizing.remaining_strategy_capacity == Decimal("500.000000")
    assert sizing.remaining_total_capacity == Decimal("250.000000")
    assert sizing.quantity == Decimal("2")
    assert sizing.approved_notional == Decimal("200.000000")
