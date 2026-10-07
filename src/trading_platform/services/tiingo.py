"""Tiingo end-of-day REST client (research provider).

Facts this adapter relies on (official docs, verified 2026-10-07):
* metadata ``GET /tiingo/daily/<ticker>`` -> ``ticker, name, exchangeCode, startDate, endDate``;
* prices ``GET /tiingo/daily/<ticker>/prices?startDate=&endDate=`` -> rows with
  ``date, open, high, low, close, volume, adjOpen, adjHigh, adjLow, adjClose, adjVolume,
  divCash, splitFactor``; the adjusted series is CRSP split + dividend adjusted;
* authentication by the ``Authorization: Token <key>`` header. The documented ``?token=``
  query parameter is deliberately refused here so the key can never land in a URL, a log
  line or a persisted request record;
* the free plan publishes 50 requests per hour, 1,000 per day and 500 unique symbols per
  month. Every HTTP attempt (a retry included) is admitted by the caller-supplied
  ``RequestBudget`` first; the shared ``DatabaseRequestBudget`` coordinates every process
  of this application and fails visibly (``BudgetExhaustedError``) instead of sleeping.
  A provider 429 is ``TiingoRateLimitError`` and is never retried here.

Session dates are the first ten characters of the provider's ``date`` string, parsed as an
ISO date with no timezone conversion (the string is UTC midnight carrying the session date).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from trading_platform.core.settings import TiingoProviderSettings
from trading_platform.services.research.budget import (
    OUTCOME_AUTH_FAILED,
    OUTCOME_HTTP_ERROR,
    OUTCOME_OK,
    OUTCOME_RATE_LIMITED,
    OUTCOME_TRANSPORT_ERROR,
    PURPOSE_METADATA,
    PURPOSE_PRICES,
    BudgetExhaustedError,
    RequestBudget,
)

logger = logging.getLogger(__name__)

PROVIDER = "tiingo"
FORBIDDEN_QUERY_PARAMS = frozenset({"token"})


class TiingoClientError(Exception):
    """Non-recoverable client error."""


class TiingoAuthError(TiingoClientError):
    """401/403 from the provider: missing or invalid key, or plan entitlement."""


class TiingoRateLimitError(TiingoClientError):
    """HTTP 429 from the provider (``provider_rate_limited``)."""

    code = "provider_rate_limited"


#: The shared budget's refusal, re-exported under the adapter's historical name.
TiingoRequestBudgetExceededError = BudgetExhaustedError


@dataclass(frozen=True)
class TiingoAssetMetadata:
    ticker: str
    name: str | None
    exchange_code: str | None
    start_date: date | None
    end_date: date | None
    description: str | None = None


@dataclass(frozen=True)
class TiingoDailyRow:
    """One provider row: raw OHLCV, adjusted OHLCV and the corporate-action factors."""

    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adj_open: Decimal
    adj_high: Decimal
    adj_low: Decimal
    adj_close: Decimal
    adj_volume: int
    div_cash: Decimal
    split_factor: Decimal


def parse_session_date(raw: str) -> date:
    """First ten characters, ISO date, no timezone conversion."""

    if not isinstance(raw, str) or len(raw) < 10:
        raise TiingoClientError(f"Unparseable provider date: {raw!r}")
    return date.fromisoformat(raw[:10])


def _decimal(value: Any) -> Decimal:
    if value is None:
        raise TiingoClientError("Missing numeric field in provider row.")
    return Decimal(str(value))


def _optional_date(value: Any) -> date | None:
    if not value:
        return None
    return parse_session_date(str(value))


def row_from_payload(payload: dict[str, Any]) -> TiingoDailyRow:
    return TiingoDailyRow(
        session_date=parse_session_date(payload["date"]),
        open=_decimal(payload.get("open")),
        high=_decimal(payload.get("high")),
        low=_decimal(payload.get("low")),
        close=_decimal(payload.get("close")),
        volume=int(payload.get("volume") or 0),
        adj_open=_decimal(payload.get("adjOpen")),
        adj_high=_decimal(payload.get("adjHigh")),
        adj_low=_decimal(payload.get("adjLow")),
        adj_close=_decimal(payload.get("adjClose")),
        adj_volume=int(payload.get("adjVolume") or 0),
        div_cash=_decimal(payload.get("divCash", 0)),
        split_factor=_decimal(payload.get("splitFactor", 1)),
    )


class TiingoClient:
    """Header-authenticated client; every attempt is admitted by ``budget`` first.

    Use as a context manager. ``requests_made`` counts HTTP attempts, retries included,
    which is what the ledger charges.
    """

    def __init__(
        self,
        settings: TiingoProviderSettings,
        *,
        budget: RequestBudget,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.api_key:
            raise TiingoAuthError("Tiingo API key is not configured (TIINGO_API_KEY).")
        self._settings = settings
        self._budget = budget
        self._sleep = sleep
        self._attempts = 0
        self._client = httpx.Client(
            base_url=settings.base_url,
            headers={
                "Authorization": f"Token {settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TiingoClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def requests_made(self) -> int:
        return self._attempts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        symbol: str | None = None,
        purpose: str = PURPOSE_PRICES,
    ) -> Any:
        if params and FORBIDDEN_QUERY_PARAMS.intersection(params):
            raise TiingoClientError("The API key is sent as a header; a token query parameter is forbidden.")
        attempts = 0
        last_exc: Exception | None = None
        while attempts <= self._settings.max_retries:
            admission = self._budget.admit(symbol=symbol, purpose=purpose)
            self._attempts += 1
            attempts += 1
            try:
                response = self._client.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                self._budget.complete(admission, outcome=OUTCOME_TRANSPORT_ERROR, status_code=None)
                last_exc = exc
                if attempts <= self._settings.max_retries:
                    self._sleep(self._settings.retry_backoff_factor * (2 ** (attempts - 1)))
                continue
            if response.status_code in (401, 403):
                self._budget.complete(admission, outcome=OUTCOME_AUTH_FAILED, status_code=response.status_code)
                raise TiingoAuthError(
                    f"Tiingo returned {response.status_code}; check the key and plan entitlement."
                )
            if response.status_code == 429:
                self._budget.complete(admission, outcome=OUTCOME_RATE_LIMITED, status_code=429)
                raise TiingoRateLimitError("Tiingo returned 429 Too Many Requests.")
            if response.status_code >= 400:
                self._budget.complete(admission, outcome=OUTCOME_HTTP_ERROR, status_code=response.status_code)
                response.raise_for_status()
            self._budget.complete(admission, outcome=OUTCOME_OK, status_code=response.status_code)
            return response.json()
        raise TiingoClientError(
            f"Tiingo request failed after {self._settings.max_retries} retries: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_metadata(self, ticker: str) -> TiingoAssetMetadata:
        payload = self._get(f"/tiingo/daily/{ticker}", symbol=ticker, purpose=PURPOSE_METADATA)
        return TiingoAssetMetadata(
            ticker=str(payload.get("ticker") or ticker).upper(),
            name=payload.get("name"),
            exchange_code=payload.get("exchangeCode"),
            start_date=_optional_date(payload.get("startDate")),
            end_date=_optional_date(payload.get("endDate")),
            description=payload.get("description"),
        )

    def fetch_daily_prices(self, ticker: str, from_date: date, to_date: date) -> list[TiingoDailyRow]:
        logger.info(
            "tiingo_fetch_prices",
            extra={
                "context": {
                    "ticker": ticker,
                    "from_date": from_date.isoformat(),
                    "to_date": to_date.isoformat(),
                }
            },
        )
        payload = self._get(
            f"/tiingo/daily/{ticker}/prices",
            params={
                "startDate": from_date.isoformat(),
                "endDate": to_date.isoformat(),
                "format": "json",
            },
            symbol=ticker,
            purpose=PURPOSE_PRICES,
        )
        if not isinstance(payload, list):
            raise TiingoClientError("Unexpected prices payload shape.")
        rows = [row_from_payload(item) for item in payload]
        rows.sort(key=lambda row: row.session_date)
        return rows
