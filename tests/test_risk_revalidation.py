"""``revalidate_pinned_intent``: portfolio-dependent limits for a pinned intent at the fresh price.

Pure (no database): the function receives the CURRENT portfolio state and risk limits and the
S2-R3 price observation. It never generates a signal, never changes the pinned quantity and
never reads market data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.core.settings import PortfolioSettings
from trading_platform.services import risk as risk_module
from trading_platform.services.alpaca import PriceObservation
from trading_platform.services.execution.operations import RISK_LIMIT_PORTFOLIO_CODES
from trading_platform.services.portfolio import PortfolioService, PortfolioState, PositionSnapshot
from trading_platform.services.risk import (
    PinnedIntentSpec,
    RiskDecisionCode,
    RiskLimits,
    revalidate_pinned_intent,
)

NOW = datetime(2024, 1, 8, 15, 0, tzinfo=UTC)


def _price(symbol: str, price: str) -> PriceObservation:
    return PriceObservation(
        symbol=symbol, price=Decimal(price), observed_at=NOW, fetched_at=NOW, source="test"
    )


def _position(symbol: str, quantity: str, price: str = "100") -> PositionSnapshot:
    return PositionSnapshot(
        position_id=f"pos-{symbol}",
        strategy_id="trend_following_daily",
        symbol=symbol,
        quantity=Decimal(quantity),
        average_entry_price=Decimal(price),
        market_price=Decimal(price),
        market_value=Decimal(quantity) * Decimal(price),
    )


def _state(
    *,
    cash: str = "100000",
    positions: tuple[PositionSnapshot, ...] = (),
    other_exposure: str = "0",
) -> PortfolioState:
    strategy_exposure = sum((p.market_value for p in positions), start=Decimal("0"))
    gross = strategy_exposure + Decimal(other_exposure)
    cash_value = Decimal(cash)
    return PortfolioState(
        cash=cash_value,
        gross_exposure=gross,
        total_equity=cash_value + gross,
        strategy_exposure=strategy_exposure,
        open_positions=positions,
        open_symbols=frozenset(p.symbol for p in positions),
        total_open_positions=len(positions) + (1 if Decimal(other_exposure) > 0 else 0),
    )


def _limits(**overrides: object) -> RiskLimits:
    portfolio = PortfolioSettings(**overrides)  # type: ignore[arg-type]
    return RiskLimits(max_positions=3, portfolio=portfolio)


def _buy(symbol: str = "AAPL", quantity: str = "10", reference: str = "100") -> PinnedIntentSpec:
    return PinnedIntentSpec(symbol, "buy", Decimal(quantity), Decimal(reference))


def _sell(symbol: str = "AAPL", quantity: str = "10", reference: str = "100") -> PinnedIntentSpec:
    return PinnedIntentSpec(symbol, "sell", Decimal(quantity), Decimal(reference))


def test_every_portfolio_code_the_function_can_emit_is_a_reevaluation_code() -> None:
    emitted = {
        RiskDecisionCode.DUPLICATE_OPEN_POSITION,
        RiskDecisionCode.NO_OPEN_POSITION,
        RiskDecisionCode.MAX_POSITIONS,
        RiskDecisionCode.STRATEGY_ALLOCATION_CAP,
        RiskDecisionCode.TOTAL_ALLOCATION_CAP,
        RiskDecisionCode.INSUFFICIENT_CASH,
    }
    assert {code.value for code in emitted} <= RISK_LIMIT_PORTFOLIO_CODES
    assert RiskDecisionCode.ORDER_ROUNDS_TO_ZERO.value not in {code.value for code in emitted}


def test_entry_passes_with_room() -> None:
    result = revalidate_pinned_intent(_buy(), _state(), _limits(), _price("AAPL", "102"))

    assert result.ok
    assert result.code is RiskDecisionCode.APPROVED
    assert result.valuation_price == Decimal("102")
    assert result.notional == Decimal("1020.000000")


def test_buy_is_valued_at_the_higher_of_fresh_and_reference_price() -> None:
    result = revalidate_pinned_intent(_buy(), _state(), _limits(), _price("AAPL", "95"))

    assert result.valuation_price == Decimal("100")
    assert result.notional == Decimal("1000.000000")


def test_duplicate_open_position_fails() -> None:
    state = _state(positions=(_position("AAPL", "5"),))

    result = revalidate_pinned_intent(_buy(), state, _limits(), _price("AAPL", "100"))

    assert result.failed
    assert result.code is RiskDecisionCode.DUPLICATE_OPEN_POSITION


def test_max_positions_fails_and_passes_below_the_limit() -> None:
    full = _state(positions=(_position("A", "1"), _position("B", "1"), _position("C", "1")))
    below = _state(positions=(_position("A", "1"), _position("B", "1")))

    failed = revalidate_pinned_intent(_buy(), full, _limits(), _price("AAPL", "100"))
    passed = revalidate_pinned_intent(_buy(), below, _limits(), _price("AAPL", "100"))

    assert failed.code is RiskDecisionCode.MAX_POSITIONS
    assert passed.ok


def test_strategy_allocation_cap_fails_and_passes() -> None:
    limits = _limits(max_strategy_allocation_pct=0.01)
    # equity 100000 -> strategy cap 1000; a 10 x 100 entry (1000) fits, 10 x 101 does not.
    passed = revalidate_pinned_intent(_buy(), _state(), limits, _price("AAPL", "100"))
    failed = revalidate_pinned_intent(_buy(), _state(), limits, _price("AAPL", "101"))

    assert passed.ok
    assert failed.code is RiskDecisionCode.STRATEGY_ALLOCATION_CAP


def test_total_allocation_cap_fails_and_passes() -> None:
    limits = _limits(max_total_portfolio_allocation_pct=0.5)
    # equity 100000 + 40000 other exposure = 140000 -> total cap 70000, other exposure 40000.
    state = _state(cash="100000", other_exposure="40000")
    passed = revalidate_pinned_intent(_buy(quantity="10"), state, limits, _price("AAPL", "100"))
    failed = revalidate_pinned_intent(_buy(quantity="400"), state, limits, _price("AAPL", "100"))

    assert passed.ok
    assert failed.code is RiskDecisionCode.TOTAL_ALLOCATION_CAP


def test_insufficient_cash_fails_and_passes() -> None:
    passed = revalidate_pinned_intent(_buy(), _state(cash="1000"), _limits(), _price("AAPL", "100"))
    failed = revalidate_pinned_intent(_buy(), _state(cash="999"), _limits(), _price("AAPL", "100"))

    assert passed.ok
    assert failed.code is RiskDecisionCode.INSUFFICIENT_CASH


def test_a_cash_shortfall_appears_only_at_the_fresh_price() -> None:
    # Enough cash at the reference price (1000), short at the fresh +2% price (1020).
    state = _state(cash="1010")

    at_reference = revalidate_pinned_intent(_buy(), state, _limits(), _price("AAPL", "100"))
    at_fresh = revalidate_pinned_intent(_buy(), state, _limits(), _price("AAPL", "102"))

    assert at_reference.ok
    assert at_fresh.code is RiskDecisionCode.INSUFFICIENT_CASH


def test_exit_passes_with_a_covering_position_and_fails_without() -> None:
    state = _state(positions=(_position("AAPL", "10"),))

    passed = revalidate_pinned_intent(_sell(), state, _limits(), _price("AAPL", "99"))
    flat = revalidate_pinned_intent(_sell(), _state(), _limits(), _price("AAPL", "99"))
    short = revalidate_pinned_intent(_sell(quantity="11"), state, _limits(), _price("AAPL", "99"))

    assert passed.ok
    assert passed.notional == Decimal("990.000000")
    assert flat.code is RiskDecisionCode.NO_OPEN_POSITION
    assert short.code is RiskDecisionCode.NO_OPEN_POSITION


def test_risk_configuration_change_takes_effect_immediately() -> None:
    state = _state()
    before = revalidate_pinned_intent(_buy(), state, _limits(), _price("AAPL", "100"))
    after = revalidate_pinned_intent(
        _buy(), state, _limits(max_strategy_allocation_pct=0.001), _price("AAPL", "100")
    )

    assert before.ok
    assert after.code is RiskDecisionCode.STRATEGY_ALLOCATION_CAP


def test_no_signal_generation_and_no_resizing(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("revalidate_pinned_intent must not generate signals or size orders")

    monkeypatch.setattr(risk_module.PortfolioRiskService, "validate", forbidden)
    monkeypatch.setattr(risk_module.PortfolioRiskService, "_evaluate_signal", forbidden)
    monkeypatch.setattr(PortfolioService, "compute_entry_size", forbidden)
    pinned = _buy(quantity="7")

    result = revalidate_pinned_intent(pinned, _state(), _limits(), _price("AAPL", "101"))

    assert result.ok
    assert pinned.quantity == Decimal("7")
    assert result.notional == Decimal("707.000000")


def test_earlier_fill_alone_never_requests_reevaluation() -> None:
    """Intent 1 filled exactly as planned (cash reduced, the position now held, exposure up):
    intent 2, sized against the evaluation state that already counted intent 1's exposure,
    still passes against the refreshed portfolio."""

    limits = _limits(max_strategy_allocation_pct=0.02)  # strategy cap 2000 of 100000 equity
    # Evaluation state before any order: 10 x 100 (A) then 10 x 100 (B) fit the 2000 cap.
    before = _state()
    assert revalidate_pinned_intent(_buy("A"), before, limits, _price("A", "100")).ok
    # After A fills 10 @ 100: cash -1000, position A held (exposure +1000), equity unchanged.
    after_fill = _state(cash="99000", positions=(_position("A", "10"),))

    result = revalidate_pinned_intent(_buy("B"), after_fill, limits, _price("B", "100"))

    assert result.ok


def test_forced_cash_shortfall_after_a_fill_fails_insufficient_cash() -> None:
    after_fill = _state(cash="500", positions=(_position("A", "10"),))

    result = revalidate_pinned_intent(_buy("B"), after_fill, _limits(), _price("B", "100"))

    assert result.code is RiskDecisionCode.INSUFFICIENT_CASH
