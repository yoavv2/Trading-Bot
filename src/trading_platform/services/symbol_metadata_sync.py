"""Symbol-metadata sync service (ORCH-02).

Moved from ``scripts/sync_symbol_metadata.py`` so the ``sync-symbol-metadata``
Job handler (a Phase 20 addition) can call a plain ``services.*`` function
instead of a CLI script (``JobHandler`` may only import ``services.*``,
``jobs/contracts.py``). A Job is never a preview-only run (OPS-05 forbids
behavior flags), so ``MetadataSyncResult`` carries no preview-mode field --
there is no code path here that fetches without also persisting.

COR-03/D-28: the sync is two-phase. Phase 1 fetches and classifies every
ticker without any database write; phase 2 upserts all valid overviews in ONE
transaction. A per-symbol failure (``SymbolFailureReason``) leaves the other
symbols synced and the Job SUCCEEDED (outcome ``partial``); an operation-level
failure (``OperationFailureReason``: auth, configuration, database write,
provider-wide outage) fails the run and marks no symbol synced.

``fetch_ticker_overview``/``PolygonAuthError``/``httpx`` are imported at
module level (the retired script imported them function-locally); the
worker's dynamic script-import workaround (``worker/commands/ingest.py::run_sync_metadata``,
which reached into the script via a runtime module-loading trick) no longer
has anything to reach into.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.symbol import Symbol
from trading_platform.db.session import session_scope
from trading_platform.services.batch_outcomes import (
    BatchOutcome,
    OperationFailureReason,
    SymbolFailureReason,
)
from trading_platform.services.polygon import PolygonAuthError

logger = logging.getLogger(__name__)


# Overview fields a symbol must carry to be usable for trading readiness
# (D-29: market, symbol_type, primary_exchange). A symbol whose overview lacks
# any of them is never persisted by the sync.
REQUIRED_OVERVIEW_FIELDS: tuple[str, ...] = ("market", "type", "primary_exchange")


class InvalidOverviewResponseError(ValueError):
    """The provider answered, but the payload is not a ticker-overview object."""


def fetch_ticker_overview(ticker: str, settings: Settings) -> dict[str, Any] | None:
    """Fetch the Polygon ticker overview for a single symbol.

    Returns the result dict, or ``None`` on a 404/no-data response. Raises
    ``PolygonAuthError`` on an auth failure, ``RuntimeError`` on a transport
    error, ``httpx.HTTPStatusError`` on another HTTP error and
    ``InvalidOverviewResponseError`` for a malformed payload.
    """

    base_url = settings.market_data.polygon.base_url
    api_key = settings.market_data.polygon.api_key
    timeout = settings.market_data.polygon.timeout_seconds

    if not api_key:
        raise PolygonAuthError(
            "Polygon API key is not configured. "
            "Set TRADING_PLATFORM_MARKET_DATA__POLYGON__API_KEY in .env or the shell."
        )

    url = f"{base_url}/v3/reference/tickers/{ticker}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.TransportError as exc:
        raise RuntimeError(f"Network error fetching {ticker}: {exc}") from exc

    if response.status_code == 401:
        raise PolygonAuthError(f"Polygon returned 401 Unauthorized for {ticker}.")
    if response.status_code == 404:
        return None
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise InvalidOverviewResponseError(f"Unparseable overview payload for {ticker}.") from exc
    if not isinstance(payload, dict):
        raise InvalidOverviewResponseError(f"Overview payload for {ticker} is not an object.")
    results = payload.get("results")
    if results is not None and not isinstance(results, dict):
        raise InvalidOverviewResponseError(f"Overview results for {ticker} is not an object.")
    return results


def _parse_list_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def upsert_symbol_metadata(session: Session, ticker: str, overview: dict[str, Any]) -> Symbol:
    """Upsert symbol metadata from a Polygon ticker-overview result dict."""

    existing = session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalar_one_or_none()

    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "ticker": ticker,
        "name": overview.get("name"),
        "market": overview.get("market"),
        "locale": overview.get("locale"),
        "primary_exchange": overview.get("primary_exchange"),
        "symbol_type": overview.get("type"),
        "active": overview.get("active", True),
        "description": overview.get("description"),
        "list_date": _parse_list_date(overview.get("list_date")),
        "currency_name": overview.get("currency_name"),
        "cik": overview.get("cik"),
        "composite_figi": overview.get("composite_figi"),
        "share_class_figi": overview.get("share_class_figi"),
        "metadata_provider": "polygon",
        "updated_at": now,
    }

    if existing is None:
        values["id"] = uuid.uuid4()
        values["created_at"] = now
        symbol = Symbol(**values)
        session.add(symbol)
        session.flush()
        return symbol

    for key, value in values.items():
        if key not in ("id", "created_at"):
            setattr(existing, key, value)
    session.flush()
    return existing


class SymbolMetadataSyncFailedError(RuntimeError):
    """Raised by ``MetadataSyncResult.raise_if_failed()`` when the sync outcome
    is ``failed`` (an operation-level failure, or no symbol could be synced).

    The message names closed reasons only, never provider text or URLs.
    """

    def __init__(
        self,
        failed: tuple[str, ...],
        operation_failure: OperationFailureReason | None = None,
    ) -> None:
        self.failed = failed
        self.operation_failure = operation_failure
        if operation_failure is not None:
            message = f"Symbol metadata sync failed: operation failure {operation_failure.value}"
            if failed:
                message += f"; failed: {', '.join(failed)}"
        else:
            message = f"Symbol metadata sync failed for: {', '.join(failed)}"
        super().__init__(message)


@dataclass
class MetadataSyncResult:
    """Outcome of one ``sync_symbol_metadata`` call. Carries no preview-mode
    field -- a Job is never a preview-only run (OPS-05).

    ``failed`` lists every ticker with a per-symbol failure (``failures`` holds
    the closed reason of each); ``skipped`` is deprecated and always empty (a
    404 is the ``not_found`` failure reason now). ``operation_failure`` is set
    when the run as a whole failed; ``synced`` is then always empty.
    """

    synced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    operation_failure: OperationFailureReason | None = None

    @property
    def outcome(self) -> BatchOutcome:
        if self.operation_failure is not None or not self.synced:
            return BatchOutcome.FAILED
        if self.failed:
            return BatchOutcome.PARTIAL
        return BatchOutcome.COMPLETE

    @property
    def succeeded(self) -> bool:
        return self.outcome is BatchOutcome.COMPLETE

    def to_dict(self) -> dict[str, Any]:
        return {
            "synced": self.synced,
            "skipped": self.skipped,
            "failed": self.failed,
            "synced_count": len(self.synced),
            "skipped_count": len(self.skipped),
            "failed_count": len(self.failed),
            "succeeded": self.succeeded,
            "outcome": self.outcome.value,
            "failures": [dict(item) for item in self.failures],
            "operation_failure": (
                self.operation_failure.value if self.operation_failure is not None else None
            ),
        }

    def raise_if_failed(self) -> None:
        """Raise iff the outcome is ``failed`` (partial results do not raise)."""

        if self.outcome is BatchOutcome.FAILED:
            raise SymbolMetadataSyncFailedError(tuple(self.failed), self.operation_failure)


@dataclass(frozen=True)
class _FetchOutcome:
    """Phase-1 classification of one ticker: exactly one of ``overview`` /
    ``reason`` is set."""

    ticker: str
    overview: dict[str, Any] | None = None
    reason: SymbolFailureReason | None = None
    transient: bool = False


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, (RuntimeError, httpx.TransportError))


def _missing_required_fields(overview: dict[str, Any]) -> bool:
    return any(
        not isinstance(overview.get(name), str) or not overview[name].strip()
        for name in REQUIRED_OVERVIEW_FIELDS
    )


def _classify_ticker(ticker: str, settings: Settings) -> _FetchOutcome:
    """Fetch and classify one ticker. ``PolygonAuthError`` propagates (it is an
    operation-level failure); every other error is a per-symbol failure."""

    try:
        overview = fetch_ticker_overview(ticker, settings)
    except PolygonAuthError:
        raise
    except InvalidOverviewResponseError as exc:
        logger.error(
            "metadata_sync_failed",
            extra={"context": {"ticker": ticker, "error": type(exc).__name__}},
        )
        return _FetchOutcome(ticker, reason=SymbolFailureReason.INVALID_RESPONSE)
    except Exception as exc:
        logger.error(
            "metadata_sync_failed",
            extra={"context": {"ticker": ticker, "error": type(exc).__name__}},
        )
        return _FetchOutcome(
            ticker, reason=SymbolFailureReason.FETCH_ERROR, transient=_is_transient(exc)
        )

    if overview is None:
        logger.warning("metadata_not_found", extra={"context": {"ticker": ticker}})
        return _FetchOutcome(ticker, reason=SymbolFailureReason.NOT_FOUND)
    if not isinstance(overview, dict):
        return _FetchOutcome(ticker, reason=SymbolFailureReason.INVALID_RESPONSE)
    if _missing_required_fields(overview):
        logger.warning("metadata_missing_required_fields", extra={"context": {"ticker": ticker}})
        return _FetchOutcome(ticker, reason=SymbolFailureReason.MISSING_REQUIRED_FIELDS)
    return _FetchOutcome(ticker, overview=overview)


def sync_symbol_metadata(symbols: list[str] | tuple[str, ...], *, settings: Settings) -> MetadataSyncResult:
    """Fetch and upsert Polygon ticker-overview metadata for ``symbols``.

    Phase 1 (no database write): with no API key configured the run stops
    before any fetch (``invalid_configuration``); otherwise each ticker is
    fetched and classified. An auth failure stops immediately
    (``provider_auth``); if every ticker failed transiently the run is
    ``provider_unavailable``. Phase 2: every valid overview is upserted in one
    transaction; any error there is ``database_write`` and nothing persists.
    An operation-level failure therefore never leaves a symbol marked synced.
    """

    result = MetadataSyncResult()

    logger.info(
        "metadata_sync_started",
        extra={"context": {"symbols": list(symbols)}},
    )

    if not settings.market_data.polygon.api_key:
        result.operation_failure = OperationFailureReason.INVALID_CONFIGURATION
        logger.error("metadata_sync_operation_failed", extra={"context": result.to_dict()})
        return result

    valid: list[_FetchOutcome] = []
    transient_failures = 0
    for ticker in symbols:
        try:
            outcome = _classify_ticker(ticker, settings)
        except PolygonAuthError as exc:
            logger.error(
                "metadata_sync_auth_failed",
                extra={"context": {"ticker": ticker, "error": type(exc).__name__}},
            )
            result.operation_failure = OperationFailureReason.PROVIDER_AUTH
            logger.error("metadata_sync_operation_failed", extra={"context": result.to_dict()})
            return result

        if outcome.reason is not None:
            result.failed.append(ticker)
            result.failures.append({"symbol": ticker, "reason": outcome.reason.value})
            if outcome.transient:
                transient_failures += 1
        else:
            valid.append(outcome)

    if symbols and transient_failures == len(symbols):
        result.operation_failure = OperationFailureReason.PROVIDER_UNAVAILABLE
        logger.error("metadata_sync_operation_failed", extra={"context": result.to_dict()})
        return result

    if valid:
        try:
            with session_scope(settings) as db_session:
                for item in valid:
                    assert item.overview is not None
                    upsert_symbol_metadata(db_session, item.ticker, item.overview)
        except Exception as exc:
            logger.error(
                "metadata_sync_write_failed",
                extra={"context": {"error": type(exc).__name__}},
            )
            result.operation_failure = OperationFailureReason.DATABASE_WRITE
            logger.error("metadata_sync_operation_failed", extra={"context": result.to_dict()})
            return result
        result.synced.extend(item.ticker for item in valid)
        for item in valid:
            logger.info("metadata_synced", extra={"context": {"ticker": item.ticker}})

    logger.info(
        "metadata_sync_completed",
        extra={"context": result.to_dict()},
    )

    return result


__all__ = [
    "REQUIRED_OVERVIEW_FIELDS",
    "InvalidOverviewResponseError",
    "MetadataSyncResult",
    "SymbolMetadataSyncFailedError",
    "fetch_ticker_overview",
    "sync_symbol_metadata",
    "upsert_symbol_metadata",
]
