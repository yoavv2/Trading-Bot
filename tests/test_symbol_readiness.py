"""D-29/R-29: per-symbol metadata readiness (``services.symbol_readiness``)."""

from __future__ import annotations

import pytest
from tests.support.migrated_db import migrated_database
from tests.support.query_counter import count_queries
from tests.support.symbol_metadata import ready_symbol_fields

from trading_platform.core.settings import load_settings
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.symbol_readiness import (
    REQUIRED_MARKET,
    REQUIRED_REQUIREMENTS,
    REQUIRED_SYMBOL_TYPES,
    NotReadyReason,
    readiness_from_symbol,
    symbols_readiness,
)


def _symbol(**overrides: object) -> Symbol:
    fields = {**ready_symbol_fields(), **overrides}
    return Symbol(ticker="AAPL", **fields)


def test_closed_reason_and_requirements() -> None:
    assert {member.value for member in NotReadyReason} == {"missing_metadata"}
    assert REQUIRED_REQUIREMENTS == (
        "metadata_provider",
        "active",
        "market",
        "symbol_type",
        "primary_exchange",
    )
    assert REQUIRED_MARKET == "stocks"
    assert REQUIRED_SYMBOL_TYPES == frozenset({"CS", "ETF"})


def test_all_requirements_present_is_ready() -> None:
    readiness = readiness_from_symbol("AAPL", _symbol())

    assert readiness.ready is True
    assert readiness.reason is None
    assert readiness.missing_requirements == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metadata_provider", None),
        ("active", False),
        ("market", None),
        ("symbol_type", None),
        ("primary_exchange", None),
    ],
)
def test_each_requirement_individually_missing_is_not_ready(field: str, value: object) -> None:
    readiness = readiness_from_symbol("AAPL", _symbol(**{field: value}))

    assert readiness.ready is False
    assert readiness.reason is NotReadyReason.MISSING_METADATA
    assert readiness.missing_requirements == (field,)


def test_etf_accepted_other_types_and_markets_rejected() -> None:
    assert readiness_from_symbol("SPY", _symbol(symbol_type="ETF")).ready is True
    for bad_type in ("ETN", "FX", "CRYPTO", "cs", ""):
        result = readiness_from_symbol("X", _symbol(symbol_type=bad_type))
        assert result.ready is False
        assert result.missing_requirements == ("symbol_type",)
    crypto = readiness_from_symbol("X", _symbol(market="crypto"))
    assert crypto.ready is False
    assert crypto.missing_requirements == ("market",)


def test_blank_primary_exchange_is_not_ready() -> None:
    for blank in ("", "   "):
        result = readiness_from_symbol("X", _symbol(primary_exchange=blank))
        assert result.ready is False
        assert result.missing_requirements == ("primary_exchange",)


def test_unknown_ticker_is_not_ready_with_every_requirement_missing() -> None:
    readiness = readiness_from_symbol("NOPE", None)

    assert readiness.ready is False
    assert readiness.reason is NotReadyReason.MISSING_METADATA
    assert readiness.missing_requirements == REQUIRED_REQUIREMENTS


@pytest.fixture()
def readiness_db(monkeypatch: pytest.MonkeyPatch):
    with migrated_database(monkeypatch, "symbol_readiness") as name:
        yield name


def test_symbols_readiness_reads_persisted_rows(readiness_db: str) -> None:
    settings = load_settings()
    with session_scope(settings) as session:
        session.add(Symbol(ticker="AAPL", **ready_symbol_fields()))
        session.add(Symbol(ticker="LEGACY", active=True))

    with session_scope(settings) as session:
        result = symbols_readiness(session, ["AAPL", "LEGACY", "MISSING"])

    assert result["AAPL"].ready is True
    assert result["LEGACY"].ready is False
    assert result["LEGACY"].missing_requirements == (
        "metadata_provider",
        "market",
        "symbol_type",
        "primary_exchange",
    )
    assert result["MISSING"].ready is False
    assert result["MISSING"].missing_requirements == REQUIRED_REQUIREMENTS


def test_symbols_readiness_is_one_statement_for_any_number_of_symbols(readiness_db: str) -> None:
    settings = load_settings()
    tickers = [f"T{index:02d}" for index in range(50)]
    with session_scope(settings) as session:
        for index, ticker in enumerate(tickers):
            fields = ready_symbol_fields() if index % 2 == 0 else {"active": True}
            session.add(Symbol(ticker=ticker, **fields))

    for subset in (tickers[:1], tickers):
        with session_scope(settings) as session:
            with count_queries(session) as counter:
                result = symbols_readiness(session, subset)
        assert counter.count == 1
        assert set(result) == set(subset)

    with session_scope(settings) as session:
        with count_queries(session) as counter:
            assert symbols_readiness(session, []) == {}
        assert counter.count == 0
