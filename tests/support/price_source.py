"""Scripted ``PriceSource`` test doubles (S2-R3, review round 3 follow-up).

``FreshPriceSource`` is the default every existing sending suite gets from the shared
seam (``tests/support/paper_execution_seams.py``): it returns a fresh latest trade at the
intent's own ``reference_price``, observed now, so a session keeps sending exactly as it
did before the pre-send price check existed. Only the S2-R3 tests override it with a
``ScriptedPriceSource``. Neither ever touches the network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from trading_platform.core import clock
from trading_platform.services.alpaca import PriceFailure, PriceLookupError, PriceObservation


@dataclass
class FreshPriceSource:
    """A fresh trade at the reference price (times ``ratio``), observed ``age_seconds`` ago."""

    ratio: Decimal = Decimal("1")
    age_seconds: int = 0
    fallback_price: Decimal = Decimal("100")
    calls: list[str] = field(default_factory=list)

    def latest_trade(
        self, symbol: str, *, reference_price: Decimal | None = None
    ) -> PriceObservation:
        self.calls.append(symbol)
        base = reference_price if reference_price is not None else self.fallback_price
        now = clock.now_utc()
        return PriceObservation(
            symbol=symbol,
            price=(base * self.ratio).quantize(Decimal("0.000001")),
            observed_at=now - timedelta(seconds=self.age_seconds),
            fetched_at=now,
            source="test_fresh",
            feed="iex",
        )

    def close(self) -> None:
        return None


Scripted = PriceObservation | PriceFailure | Exception | Callable[[], PriceObservation]


@dataclass
class ScriptedPriceSource:
    """Returns the scripted answers in order (the last one repeats).

    An item is a ``PriceObservation``, a ``PriceFailure`` (raised as ``PriceLookupError``),
    an exception (raised) or a zero-argument callable producing an observation.
    """

    script: Sequence[Scripted]
    calls: list[str] = field(default_factory=list)

    def latest_trade(
        self, symbol: str, *, reference_price: Decimal | None = None
    ) -> PriceObservation:
        del reference_price
        index = min(len(self.calls), len(self.script) - 1)
        self.calls.append(symbol)
        item = self.script[index]
        if isinstance(item, PriceFailure):
            raise PriceLookupError(item)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item

    def close(self) -> None:
        return None


def observation(
    symbol: str,
    price: Decimal | str,
    *,
    observed_at: datetime | None = None,
    fetched_at: datetime | None = None,
) -> PriceObservation:
    now = clock.now_utc()
    return PriceObservation(
        symbol=symbol,
        price=Decimal(str(price)),
        observed_at=observed_at or now,
        fetched_at=fetched_at or now,
        source="test_scripted",
        feed="iex",
    )
