"""Polygon.io REST client for daily OHLCV bar ingestion."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import httpx

from trading_platform.core.settings import PolygonProviderSettings
from trading_platform.services.data import DailyBar, DailyBarRequest

logger = logging.getLogger(__name__)

_PROVIDER = "polygon"


class PolygonClientError(Exception):
    """Raised when the Polygon REST client encounters a non-recoverable error."""


class PolygonAuthError(PolygonClientError):
    """Raised when the Polygon API key is missing or invalid."""


def _normalize_timestamp(ts_ms: int | None) -> datetime | None:
    """Convert a Polygon millisecond epoch timestamp to a timezone-aware datetime."""
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def _build_session_date(ts_ms: int, adjusted: bool) -> date:
    """Derive the session date from a Polygon bar timestamp (ms epoch, Eastern snap).

    Polygon returns the open timestamp of the bar in Eastern Time semantics.
    For daily bars the date component is the session date.
    """
    # Polygon daily-bar timestamps represent Eastern midnight; we use the UTC
    # date of the timestamp since the difference never crosses a session boundary
    # for daily aggregates.
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.date()


def _result_to_bar(result: dict[str, Any], symbol: str, adjusted: bool) -> DailyBar:
    """Convert a single Polygon aggregate result dict to a normalized DailyBar."""
    ts_ms: int = result["t"]
    return DailyBar(
        symbol=symbol,
        session_date=_build_session_date(ts_ms, adjusted),
        open=Decimal(str(result["o"])),
        high=Decimal(str(result["h"])),
        low=Decimal(str(result["l"])),
        close=Decimal(str(result["c"])),
        volume=int(result["v"]),
        vwap=Decimal(str(result["vw"])) if result.get("vw") is not None else None,
        trade_count=result.get("n"),
        adjusted=adjusted,
        provider=_PROVIDER,
        provider_timestamp=_normalize_timestamp(ts_ms),
    )


class PolygonClient:
    """Thin httpx-based client for the Polygon stocks aggregates endpoint."""

    def __init__(self, settings: PolygonProviderSettings) -> None:
        self._settings = settings
        if not settings.api_key:
            raise PolygonAuthError(
                "Polygon API key is not configured. "
                "Set TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY in .env or the shell."
            )
        self._client = httpx.Client(
            base_url=settings.base_url,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=settings.timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PolygonClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_with_retry(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue a GET request with simple exponential back-off on transient errors."""
        attempts = 0
        last_exc: Exception | None = None
        while attempts <= self._settings.max_retries:
            try:
                response = self._client.get(url, params=params)
                if response.status_code == 401:
                    raise PolygonAuthError(
                        f"Polygon returned 401 Unauthorized. Check your API key. URL={url}"
                    )
                if response.status_code == 403:
                    raise PolygonAuthError(
                        f"Polygon returned 403 Forbidden. Check your plan/permissions. URL={url}"
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                attempts += 1
                if attempts <= self._settings.max_retries:
                    sleep_seconds = self._settings.retry_backoff_factor * (2 ** (attempts - 1))
                    logger.warning(
                        "polygon_request_retry",
                        extra={
                            "context": {
                                "url": url,
                                "attempt": attempts,
                                "sleep_seconds": sleep_seconds,
                                "error": str(exc),
                            }
                        },
                    )
                    time.sleep(sleep_seconds)
        raise PolygonClientError(
            f"Polygon request failed after {self._settings.max_retries} retries: {last_exc}"
        )

    def _paginate_aggregates(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        adjusted: bool,
    ) -> Iterator[dict[str, Any]]:
        """Yield raw result dicts across all Polygon pagination pages."""
        multiplier = 1
        timespan = "day"
        adjusted_str = "true" if adjusted else "false"
        path = f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_date.isoformat()}/{to_date.isoformat()}"
        params: dict[str, Any] = {
            "adjusted": adjusted_str,
            "sort": "asc",
            "limit": 50000,
        }

        page_count = 0
        next_url: str | None = path

        while next_url is not None:
            if page_count > 0:
                # next_url from Polygon is a full URL; strip the base to keep httpx happy
                if next_url.startswith("http"):
                    payload = self._get_with_retry(next_url, params=None)
                else:
                    payload = self._get_with_retry(next_url, params=None)
            else:
                payload = self._get_with_retry(path, params=params)

            page_count += 1
            results = payload.get("results") or []
            yield from results

            next_url = payload.get("next_url")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_daily_bars(self, request: DailyBarRequest) -> list[DailyBar]:
        """Fetch and normalize all daily bars for a single symbol and date range."""
        logger.info(
            "polygon_fetch_bars",
            extra={
                "context": {
                    "symbol": request.symbol,
                    "from_date": request.from_date.isoformat(),
                    "to_date": request.to_date.isoformat(),
                    "adjusted": request.adjusted,
                }
            },
        )
        bars: list[DailyBar] = []
        for result in self._paginate_aggregates(
            request.symbol,
            request.from_date,
            request.to_date,
            request.adjusted,
        ):
            bars.append(_result_to_bar(result, request.symbol, request.adjusted))
        return bars
