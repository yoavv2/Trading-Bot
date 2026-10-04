"""Thin Alpaca paper-trading client and execution adapter."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Literal, TypeVar

import httpx

from trading_platform.core.logging import get_logger
from trading_platform.core.settings import AlpacaBrokerSettings
from trading_platform.db.models import AttemptOutcomeClass
from trading_platform.services.execution import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
    OrderTimeInForce,
    OrderType,
)
from trading_platform.services.execution.attempts import (
    BROKER_MESSAGE_MAX_CHARS,
    NOT_SENT_OUTCOMES,
    AttemptRecord,
    SubmissionClass,
    classify_attempt_exception,
    classify_http_response,
    classify_submission,
    current_attempt_log,
)

logger = get_logger(__name__)

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class BrokerStatusClass(StrEnum):
    """Closed classification of a raw Alpaca order status (D-13)."""

    WORKING = "working"
    TERMINAL = "terminal"
    TERMINAL_WITH_SUCCESSOR = "terminal_with_successor"
    UNKNOWN = "unknown"


UNMAPPED_BROKER_STATUS: Final = "unmapped_broker_status"

# Single source of truth for the status mapping: the 16 documented Alpaca order
# statuses (Placing Orders, accessed 2026-09-30) plus the legacy ``held``.
# ``done_for_day`` is working (it can resume); ``replaced`` is terminal with a
# successor order (the successor is unrecognized until verified); anything not
# listed here is ``unknown`` with reason ``unmapped_broker_status``.
_BROKER_STATUS_CLASSES: Final[dict[str, BrokerStatusClass]] = {
    "new": BrokerStatusClass.WORKING,
    "partially_filled": BrokerStatusClass.WORKING,
    "filled": BrokerStatusClass.TERMINAL,
    "done_for_day": BrokerStatusClass.WORKING,
    "canceled": BrokerStatusClass.TERMINAL,
    "expired": BrokerStatusClass.TERMINAL,
    "replaced": BrokerStatusClass.TERMINAL_WITH_SUCCESSOR,
    "pending_cancel": BrokerStatusClass.WORKING,
    "pending_replace": BrokerStatusClass.WORKING,
    "accepted": BrokerStatusClass.WORKING,
    "pending_new": BrokerStatusClass.WORKING,
    "accepted_for_bidding": BrokerStatusClass.WORKING,
    "stopped": BrokerStatusClass.WORKING,
    "rejected": BrokerStatusClass.TERMINAL,
    "suspended": BrokerStatusClass.WORKING,
    "calculated": BrokerStatusClass.WORKING,
    "held": BrokerStatusClass.WORKING,
}

# Working statuses that normalize to PENDING (partially_filled has its own status).
_PENDING_BROKER_STATUSES = {
    status
    for status, status_class in _BROKER_STATUS_CLASSES.items()
    if status_class == BrokerStatusClass.WORKING and status != "partially_filled"
}


def classify_broker_status(raw: str | None) -> BrokerStatusClass:
    """Closed class of a raw broker status; any other or missing value is unknown."""

    if not raw:
        return BrokerStatusClass.UNKNOWN
    return _BROKER_STATUS_CLASSES.get(raw, BrokerStatusClass.UNKNOWN)


def broker_status_reason(raw: str | None) -> str | None:
    """``unmapped_broker_status`` exactly when the status class is unknown."""

    if classify_broker_status(raw) == BrokerStatusClass.UNKNOWN:
        return UNMAPPED_BROKER_STATUS
    return None


EnumT = TypeVar("EnumT")

# Alpaca docs + live check 2026-09-29: GET /v2/account/activities page_size max is 100
# (server-enforced with 422, even when date= is supplied).
ALPACA_ACTIVITIES_MAX_PAGE_SIZE: Final = 100
# Alpaca docs: GET /v2/orders limit max is 500 (NOT server-enforced, so the client caps it).
ALPACA_ORDERS_MAX_LIMIT: Final = 500
# Hard page caps bound memory/time (<= 10,000 items per call); exceeding one raises.
ALPACA_FILLS_MAX_PAGES: Final = 100
ALPACA_ORDERS_MAX_PAGES: Final = 20


class AlpacaClientError(Exception):
    """Raised when the Alpaca REST client encounters a non-recoverable error."""


class AlpacaAuthError(AlpacaClientError):
    """Raised when Alpaca credentials are missing or rejected."""


class AttemptLogNotBoundError(AlpacaClientError):
    """Raised when an order POST is requested without a bound durable attempt log.

    D-12 requires every HTTP attempt to be logged durably before and after it, so
    the submit path fails closed (zero POSTs) when no log is bound.
    """


class AlpacaOrderSubmissionError(AlpacaClientError):
    """Base for the closed outcomes of an order POST that did not produce an order.

    Carries the submission class and an attempt summary (attempt numbers and
    closed outcome-class names only; no headers, credentials or free text).
    """

    def __init__(
        self,
        message: str,
        *,
        submission_class: SubmissionClass,
        attempts: Sequence[tuple[int, str]] = (),
        http_status: int | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.submission_class = submission_class
        self.attempts: tuple[tuple[int, str], ...] = tuple(attempts)
        self.http_status = http_status
        self.reason = reason

    @property
    def attempt_numbers(self) -> tuple[int, ...]:
        return tuple(number for number, _ in self.attempts)


class AmbiguousOrderSubmissionError(AlpacaOrderSubmissionError):
    """The order may or may not exist at the broker; it must never be re-sent."""


class OrderNotSentError(AlpacaOrderSubmissionError):
    """Every attempt provably never left the process (safe to retry in a later session)."""


class OrderRejectedError(AlpacaOrderSubmissionError):
    """The broker refused the order (4xx other than the duplicate-id reply)."""


class AlpacaPaginationError(AlpacaClientError):
    """Raised when a paginated Alpaca listing cannot be returned as a complete set."""


class AlpacaPaginationCapExceededError(AlpacaPaginationError):
    """Raised when a listing still has full pages after the configured page cap."""

    def __init__(
        self,
        *,
        endpoint: str,
        pages_fetched: int,
        items_fetched: int,
        max_pages: int,
    ) -> None:
        self.endpoint = endpoint
        self.pages_fetched = pages_fetched
        self.items_fetched = items_fetched
        self.max_pages = max_pages
        super().__init__(
            f"Alpaca pagination cap exceeded for {endpoint}: {pages_fetched} full pages "
            f"({items_fetched} items) reached max_pages={max_pages}; "
            "refusing to return a partial list"
        )


class AlpacaPaginationStalledError(AlpacaPaginationError):
    """Raised when a listing cannot advance safely (repeat id, bad payload, stuck cursor)."""

    def __init__(self, *, endpoint: str, cursor: str | None, detail: str) -> None:
        self.endpoint = endpoint
        self.cursor = cursor
        self.detail = detail
        super().__init__(
            f"Alpaca pagination stalled for {endpoint} at cursor {cursor!r}: {detail}"
        )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _coerce_enum(enum_cls: type[EnumT], value: str, default: EnumT) -> EnumT:
    try:
        return enum_cls(value)  # type: ignore[arg-type]
    except ValueError:
        return default


def _normalize_status(broker_status: str | None) -> ExecutionOrderStatus:
    if not broker_status:
        return ExecutionOrderStatus.UNKNOWN
    if broker_status in _PENDING_BROKER_STATUSES:
        return ExecutionOrderStatus.PENDING
    if broker_status == "partially_filled":
        return ExecutionOrderStatus.PARTIALLY_FILLED
    if broker_status == "filled":
        return ExecutionOrderStatus.FILLED
    if broker_status == "canceled":
        return ExecutionOrderStatus.CANCELED
    if broker_status == "rejected":
        return ExecutionOrderStatus.REJECTED
    if broker_status == "expired":
        return ExecutionOrderStatus.EXPIRED
    if broker_status == "replaced":
        return ExecutionOrderStatus.REPLACED
    return ExecutionOrderStatus.UNKNOWN


def _normalize_quantity(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def _normalize_money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def _normalized_result(payload: dict[str, Any]) -> OrderSubmissionResult:
    broker_status = str(payload.get("status") or "unknown")
    return OrderSubmissionResult(
        client_order_id=str(payload.get("client_order_id") or ""),
        broker_order_id=str(payload.get("id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=_coerce_enum(OrderSide, str(payload.get("side") or "buy"), OrderSide.BUY),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        order_type=_coerce_enum(OrderType, str(payload.get("type") or "market"), OrderType.MARKET),
        time_in_force=_coerce_enum(
            OrderTimeInForce,
            str(payload.get("time_in_force") or "day"),
            OrderTimeInForce.DAY,
        ),
        status=_normalize_status(broker_status),
        broker_status=broker_status,
        submitted_at=_parse_datetime(payload.get("submitted_at")),
        raw_payload=payload,
        status_reason=broker_status_reason(broker_status),
    )


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    status: ExecutionOrderStatus
    broker_status: str
    submitted_at: datetime | None
    filled_at: datetime | None
    canceled_at: datetime | None
    updated_at: datetime | None
    raw_payload: dict[str, Any]
    status_reason: str | None = None


@dataclass(frozen=True)
class BrokerFillSnapshot:
    broker_fill_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal
    market_value: Decimal
    current_price: Decimal
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    cash: Decimal
    buying_power: Decimal
    equity: Decimal
    long_market_value: Decimal
    short_market_value: Decimal
    raw_payload: dict[str, Any]


def _normalized_order_snapshot(payload: dict[str, Any]) -> BrokerOrderSnapshot:
    broker_status = str(payload.get("status") or "unknown")
    return BrokerOrderSnapshot(
        broker_order_id=str(payload.get("id") or ""),
        client_order_id=str(payload.get("client_order_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=_coerce_enum(OrderSide, str(payload.get("side") or "buy"), OrderSide.BUY),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        status=_normalize_status(broker_status),
        broker_status=broker_status,
        submitted_at=_parse_datetime(payload.get("submitted_at")),
        filled_at=_parse_datetime(payload.get("filled_at")),
        canceled_at=_parse_datetime(payload.get("canceled_at")),
        updated_at=_parse_datetime(payload.get("updated_at")),
        raw_payload=payload,
        status_reason=broker_status_reason(broker_status),
    )


def _normalized_fill_snapshot(payload: dict[str, Any]) -> BrokerFillSnapshot:
    return BrokerFillSnapshot(
        broker_fill_id=str(payload.get("id") or ""),
        broker_order_id=str(payload.get("order_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=_coerce_enum(OrderSide, str(payload.get("side") or "buy"), OrderSide.BUY),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        price=_normalize_money(payload.get("price") or "0"),
        filled_at=_parse_datetime(payload.get("transaction_time")) or datetime.now(UTC),
        raw_payload=payload,
    )


def _normalized_position_snapshot(payload: dict[str, Any]) -> BrokerPositionSnapshot:
    return BrokerPositionSnapshot(
        symbol=str(payload.get("symbol") or ""),
        quantity=_normalize_quantity(payload.get("qty") or "0"),
        average_entry_price=_normalize_money(payload.get("avg_entry_price") or "0"),
        cost_basis=_normalize_money(payload.get("cost_basis") or "0"),
        market_value=_normalize_money(payload.get("market_value") or "0"),
        current_price=_normalize_money(payload.get("current_price") or "0"),
        raw_payload=payload,
    )


def _normalized_account_snapshot(payload: dict[str, Any]) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        cash=_normalize_money(payload.get("cash") or "0"),
        buying_power=_normalize_money(payload.get("buying_power") or "0"),
        equity=_normalize_money(payload.get("equity") or "0"),
        long_market_value=_normalize_money(payload.get("long_market_value") or "0"),
        short_market_value=_normalize_money(payload.get("short_market_value") or "0"),
        raw_payload=payload,
    )


_NOT_FOUND: Final = object()
"""Sentinel returned by ``_request_with_retry(..., allow_not_found=True)`` on HTTP 404 only."""


def _attempt_summary(history: Sequence[AttemptRecord]) -> list[tuple[int, str]]:
    return [
        (
            attempt.attempt_number,
            attempt.outcome_class.value if attempt.outcome_class is not None else "incomplete",
        )
        for attempt in history
    ]


def _all_established_not_sent(history: Sequence[AttemptRecord]) -> bool:
    """True iff every attempt so far is complete and pre_connection or deadline_expired."""

    return all(
        attempt.outcome_class is not None and attempt.outcome_class in NOT_SENT_OUTCOMES
        for attempt in history
    )


def _response_message(response: httpx.Response) -> str:
    """Broker message for classification/storage, truncated; never headers."""

    message: str | None = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("message") is not None:
        message = str(body["message"])
    if message is None:
        message = response.text
    return message[:BROKER_MESSAGE_MAX_CHARS]


def _parse_json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _intent_mismatch(
    intent: OrderIntent,
    snapshot: BrokerOrderSnapshot,
    *,
    registered_at: datetime | None,
) -> str | None:
    """Why a looked-up broker record is NOT evidence for ``intent`` (D-07), or ``None``.

    An id alone is never ownership evidence: symbol, side, quantity and type must
    match and the broker ``created_at`` must not precede the local registration.
    """

    raw = snapshot.raw_payload
    if snapshot.client_order_id != intent.client_order_id:
        return "client_order_id"
    if snapshot.symbol != intent.symbol:
        return "symbol"
    if snapshot.side != intent.side:
        return "side"
    if raw.get("qty") is None or snapshot.quantity != _normalize_quantity(intent.quantity):
        return "quantity"
    if str(raw.get("type") or "") != intent.order_type.value:
        return "type"
    if registered_at is not None:
        try:
            created_at = _parse_datetime(raw.get("created_at"))
        except ValueError:
            created_at = None
        if created_at is None:
            return "created_at"
        if registered_at.tzinfo is None:
            registered_at = registered_at.replace(tzinfo=UTC)
        if created_at < registered_at:
            return "created_at"
    return None


class AlpacaClient:
    """Thin httpx-based client for Alpaca paper-order submission."""

    def __init__(
        self,
        settings: AlpacaBrokerSettings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        if not settings.api_key or not settings.api_secret:
            raise AlpacaAuthError(
                "Alpaca API credentials are not configured. "
                "Set TRADING_PLATFORM_BROKER__ALPACA__API_KEY and "
                "TRADING_PLATFORM_BROKER__ALPACA__API_SECRET."
            )
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
        )
        self._client.headers.update(
            {
                "APCA-API-KEY-ID": settings.api_key,
                "APCA-API-SECRET-KEY": settings.api_secret,
            }
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AlpacaClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        """POST one order through the single-attempt, durably logged path (D-12).

        Never goes through ``_request_with_retry``: an order POST is re-sent only
        while EVERY attempt so far (including attempts from earlier sessions) is
        established not sent (pre_connection or deadline_expired, complete). Any
        ambiguous outcome stops at once with ``AmbiguousOrderSubmissionError``.
        """

        log = current_attempt_log()
        if log is None:
            raise AttemptLogNotBoundError(
                "Refusing to POST an order without a bound durable attempt log (D-12)."
            )
        payload = {
            "symbol": intent.symbol,
            "qty": str(intent.quantity),
            "side": intent.side.value,
            "type": intent.order_type.value,
            "time_in_force": intent.time_in_force.value,
            "client_order_id": intent.client_order_id,
        }
        history: list[AttemptRecord] = list(log.existing_attempts())
        existing_class = classify_submission(history)
        if existing_class is not None and existing_class != SubmissionClass.NOT_SENT:
            raise AmbiguousOrderSubmissionError(
                "Order submission history is not clean "
                f"(submission_class={existing_class.value}); nothing was sent.",
                submission_class=existing_class,
                attempts=_attempt_summary(history),
                reason="history_not_clean",
            )

        retries = 0
        while True:
            number = log.begin_attempt()
            started_at = datetime.now(UTC)
            response: httpx.Response | None = None
            failure: Exception | None = None
            try:
                response = self._client.post("/v2/orders", json=payload)
            # BaseException (KeyboardInterrupt/SystemExit) is NOT caught: the attempt row
            # stays incomplete, which reads back as ambiguous.
            except Exception as exc:
                failure = exc

            if failure is not None:
                outcome = classify_attempt_exception(failure)
                self._complete_attempt(
                    log,
                    number,
                    history,
                    started_at,
                    outcome_class=outcome,
                    error_type=type(failure).__name__,
                )
                if outcome == AttemptOutcomeClass.PRE_CONNECTION:
                    if retries < self._settings.max_retries and _all_established_not_sent(history):
                        retries += 1
                        sleep_seconds = self._settings.retry_backoff_factor * (2 ** (retries - 1))
                        logger.warning(
                            "alpaca_order_post_retry_not_sent",
                            extra={
                                "context": {
                                    "attempt": number,
                                    "sleep_seconds": sleep_seconds,
                                    "error_type": type(failure).__name__,
                                }
                            },
                        )
                        time.sleep(sleep_seconds)
                        continue
                    raise OrderNotSentError(
                        "Order was not sent: every attempt failed before a connection was made.",
                        submission_class=SubmissionClass.NOT_SENT,
                        attempts=_attempt_summary(history),
                        reason=type(failure).__name__,
                    ) from failure
                raise AmbiguousOrderSubmissionError(
                    "Order submission outcome is ambiguous: the request may have reached "
                    f"the broker ({type(failure).__name__}); it will not be re-sent.",
                    submission_class=SubmissionClass.AMBIGUOUS,
                    attempts=_attempt_summary(history),
                    reason=type(failure).__name__,
                ) from failure

            assert response is not None
            message = _response_message(response)
            outcome = classify_http_response(response.status_code, message)
            body: dict[str, Any] | None = None
            error_type: str | None = None
            if outcome == AttemptOutcomeClass.ACCEPTED:
                body = _parse_json_object(response)
                if body is None:
                    outcome = AttemptOutcomeClass.AMBIGUOUS
                    error_type = "InvalidResponseBody"
            self._complete_attempt(
                log,
                number,
                history,
                started_at,
                outcome_class=outcome,
                http_status=response.status_code,
                error_type=error_type,
                broker_message=None if outcome == AttemptOutcomeClass.ACCEPTED else message,
            )
            if outcome == AttemptOutcomeClass.ACCEPTED:
                assert body is not None
                return _normalized_result(body)
            if outcome == AttemptOutcomeClass.DUPLICATE_REPORTED:
                return self._resolve_duplicate_reply(intent, log, history)
            if outcome == AttemptOutcomeClass.REJECTED:
                raise OrderRejectedError(
                    f"Order rejected by the broker (HTTP {response.status_code}).",
                    submission_class=SubmissionClass.REJECTED,
                    attempts=_attempt_summary(history),
                    http_status=response.status_code,
                    reason="http_4xx",
                )
            raise AmbiguousOrderSubmissionError(
                "Order submission outcome is ambiguous "
                f"(HTTP {response.status_code} / {error_type or 'transient_status'}); "
                "it will not be re-sent.",
                submission_class=SubmissionClass.AMBIGUOUS,
                attempts=_attempt_summary(history),
                http_status=response.status_code,
                reason=error_type or "transient_status",
            )

    def _complete_attempt(
        self,
        log: Any,
        number: int,
        history: list[AttemptRecord],
        started_at: datetime,
        *,
        outcome_class: AttemptOutcomeClass,
        http_status: int | None = None,
        error_type: str | None = None,
        broker_message: str | None = None,
    ) -> None:
        """Write the outcome; a failed write leaves the row incomplete (= ambiguous)."""

        try:
            log.complete_attempt(
                number,
                outcome_class=outcome_class,
                http_status=http_status,
                error_type=error_type,
                broker_message=broker_message,
            )
        except Exception as exc:
            history.append(AttemptRecord(attempt_number=number, started_at=started_at))
            raise AmbiguousOrderSubmissionError(
                "Order submission outcome could not be recorded; treated as ambiguous "
                "and it will not be re-sent.",
                submission_class=SubmissionClass.AMBIGUOUS,
                attempts=_attempt_summary(history),
                http_status=http_status,
                reason="attempt_outcome_not_recorded",
            ) from exc
        history.append(
            AttemptRecord(
                attempt_number=number,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                outcome_class=outcome_class,
                http_status=http_status,
                error_type=error_type,
                broker_message=broker_message,
            )
        )

    def _resolve_duplicate_reply(
        self, intent: OrderIntent, log: Any, history: list[AttemptRecord]
    ) -> OrderSubmissionResult:
        """A duplicate-id reply triggers exactly one lookup, never an inference (V-1)."""

        summary = _attempt_summary(history)
        try:
            snapshot = self.get_order_by_client_order_id(intent.client_order_id)
        except Exception as exc:
            raise AmbiguousOrderSubmissionError(
                "Duplicate client_order_id reported and the lookup failed; "
                "the order state is unknown.",
                submission_class=SubmissionClass.EXISTS_REPORTED,
                attempts=summary,
                http_status=422,
                reason="lookup_failed",
            ) from exc
        if snapshot is None:
            raise AmbiguousOrderSubmissionError(
                "Duplicate client_order_id reported but the broker has no such order.",
                submission_class=SubmissionClass.EXISTS_REPORTED,
                attempts=summary,
                http_status=422,
                reason="lookup_not_found",
            )
        mismatch = _intent_mismatch(intent, snapshot, registered_at=log.intent_registered_at())
        if mismatch is not None:
            raise AmbiguousOrderSubmissionError(
                f"Duplicate client_order_id reported but the broker record does not match "
                f"the local intent ({mismatch}); ownership is not proven.",
                submission_class=SubmissionClass.EXISTS_REPORTED,
                attempts=summary,
                http_status=422,
                reason="id_mismatch",
            )
        return _normalized_result(snapshot.raw_payload)

    def get_order_by_client_order_id(self, client_order_id: str) -> BrokerOrderSnapshot | None:
        """GET one order by client order id; ``None`` ONLY for HTTP 404.

        Retries like every other GET. 5xx, 422, auth and transport errors raise,
        never ``None`` (D-13): only a 404 is evidence of absence.
        """

        payload = self._request_with_retry(
            "GET",
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            allow_not_found=True,
        )
        if payload is _NOT_FOUND:
            return None
        if not isinstance(payload, dict):
            raise AlpacaClientError("Alpaca order lookup returned a non-object payload.")
        return _normalized_order_snapshot(payload)

    def list_orders(
        self,
        *,
        status: Literal["open", "closed", "all"] = "all",
    ) -> list[BrokerOrderSnapshot]:
        # No date bounding: the local fills/orders history is unbounded, so the broker
        # side must be too (UAT gap 2). before_order_id must not combine with after/until.
        items = self._paginate(
            endpoint="/v2/orders",
            base_params={
                "status": status,
                "direction": "desc",
                "limit": str(ALPACA_ORDERS_MAX_LIMIT),
            },
            cursor_param="before_order_id",
            page_size=ALPACA_ORDERS_MAX_LIMIT,
            max_pages=ALPACA_ORDERS_MAX_PAGES,
        )
        return [_normalized_order_snapshot(item) for item in items]

    def list_fills(self) -> list[BrokerFillSnapshot]:
        # No date bounding: the local fills/orders history is unbounded, so the broker
        # side must be too (UAT gap 2).
        items = self._paginate(
            endpoint="/v2/account/activities/FILL",
            base_params={
                "direction": "desc",
                "page_size": str(ALPACA_ACTIVITIES_MAX_PAGE_SIZE),
            },
            cursor_param="page_token",
            page_size=ALPACA_ACTIVITIES_MAX_PAGE_SIZE,
            max_pages=ALPACA_FILLS_MAX_PAGES,
        )
        return [_normalized_fill_snapshot(item) for item in items]

    def _paginate(
        self,
        *,
        endpoint: str,
        base_params: dict[str, str],
        cursor_param: str,
        page_size: int,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        """Fetch the COMPLETE listing via last-id cursors, or raise a typed error.

        The cursor key is added only once a cursor exists: live Alpaca answers 200 [] for a
        bogus/empty token, which would silently truncate the set.
        """
        seen_ids: set[str] = set()
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0

        while True:
            params = dict(base_params)
            if cursor is not None:
                params[cursor_param] = cursor
            payload = self._request_with_retry("GET", endpoint, params=params)
            pages += 1

            if not isinstance(payload, list):
                raise AlpacaPaginationStalledError(
                    endpoint=endpoint, cursor=cursor, detail="non-list page payload"
                )
            for item in payload:
                item_id = item.get("id") if isinstance(item, dict) else None
                if not isinstance(item_id, str) or not item_id:
                    raise AlpacaPaginationStalledError(
                        endpoint=endpoint, cursor=cursor, detail="item without a string id"
                    )
                if item_id in seen_ids:
                    raise AlpacaPaginationStalledError(
                        endpoint=endpoint, cursor=cursor, detail=f"duplicate id {item_id}"
                    )
                seen_ids.add(item_id)
                items.append(item)

            if len(payload) < page_size:
                return items

            next_cursor = payload[-1]["id"]
            if next_cursor == cursor:
                raise AlpacaPaginationStalledError(
                    endpoint=endpoint, cursor=cursor, detail="cursor did not advance"
                )
            if pages >= max_pages:
                raise AlpacaPaginationCapExceededError(
                    endpoint=endpoint,
                    pages_fetched=pages,
                    items_fetched=len(items),
                    max_pages=max_pages,
                )
            cursor = next_cursor

    def list_positions(self) -> list[BrokerPositionSnapshot]:
        payload = self._request_with_retry("GET", "/v2/positions")
        return [_normalized_position_snapshot(item) for item in payload]

    def get_account(self) -> BrokerAccountSnapshot:
        payload = self._request_with_retry("GET", "/v2/account")
        return _normalized_account_snapshot(payload)

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        # D-12: an order POST must never go through the generic retry loop.
        if method.upper() != "GET":
            raise ValueError(
                "_request_with_retry only supports GET; order POSTs use the "
                "single-attempt logged submit path."
            )
        attempts = 0
        last_error: Exception | None = None

        while attempts <= self._settings.max_retries:
            try:
                response = self._client.request(method, path, params=params)
                if allow_not_found and response.status_code == 404:
                    return _NOT_FOUND
                if response.status_code in (401, 403):
                    raise AlpacaAuthError(
                        f"Alpaca returned {response.status_code}. "
                        "Check the configured paper-trading credentials."
                    )
                if response.status_code in _TRANSIENT_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"Transient Alpaca response: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                if response.is_error:
                    raise AlpacaClientError(
                        f"Alpaca request failed with status {response.status_code}: {response.text}"
                    )
                return response.json()
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                attempts += 1
                if attempts <= self._settings.max_retries:
                    sleep_seconds = self._settings.retry_backoff_factor * (2 ** (attempts - 1))
                    logger.warning(
                        "alpaca_request_retry",
                        extra={
                            "context": {
                                "path": path,
                                "attempt": attempts,
                                "sleep_seconds": sleep_seconds,
                                "error": str(exc),
                            }
                        },
                    )
                    time.sleep(sleep_seconds)
                    continue
                break

        raise AlpacaClientError(
            f"Alpaca request failed after {self._settings.max_retries} retries: {last_error}"
        )


class AlpacaExecutionService(ExecutionService):
    """Provider-agnostic execution adapter backed by the Alpaca REST client."""

    def __init__(
        self,
        settings: AlpacaBrokerSettings,
        *,
        client: AlpacaClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or AlpacaClient(settings)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AlpacaExecutionService":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def describe(self) -> dict[str, Any]:
        return {
            "service": "execution",
            "status": "available",
            "provider": "alpaca",
            "base_url": self._settings.base_url,
        }

    def submit_order(self, intent: OrderIntent) -> OrderSubmissionResult:
        return self._client.submit_order(intent)
