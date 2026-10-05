"""``ScriptedAlpaca``: a fake Alpaca paper broker at the HTTP TRANSPORT level (20.1-13).

The platform's real ``AlpacaClient`` runs unchanged (attempt log, submission classes, status
mapping, pagination, lookups); only the bytes on the wire are scripted. ``transport()`` returns an
``httpx.MockTransport`` that serves exactly the endpoints the clients call:

``GET /v2/orders`` (status, limit, before_order_id), ``POST /v2/orders``,
``GET /v2/orders:by_client_order_id``, ``GET /v2/orders/{id}``, ``GET /v2/positions``,
``GET /v2/account``, ``GET /v2/account/activities/FILL`` (page_size, page_token) and, only so a
missed price seam is visible, ``GET /v2/stocks/{symbol}/trades/latest`` (counted in
``data_calls``; a scenario that never expects a market-data call asserts it is 0).
``DELETE /v2/orders*`` records a cancel call and answers 405: the platform has no cancel path, so
any such call fails loudly (``cancel_calls`` must stay 0).

The module imports nothing from the project (only ``httpx``) so it can be reused by UAT-support
scripts. Time comes from the injectable ``now`` callable (default: the real UTC wall clock).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

import httpx

TERMINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected", "replaced"})

#: Behaviors of a scripted order POST.
ACCEPT = "accept"
READ_TIMEOUT_AFTER_CREATE = "read_timeout_after_create"
READ_TIMEOUT_WITHOUT_CREATE = "read_timeout_without_create"
CONNECT_ERROR = "connect_error"
HTTP_500 = "http_500"
DUPLICATE_422 = "duplicate_422"


@dataclass
class _PostRule:
    behavior: str
    status: str = "new"  # for ACCEPT: the status of the created order
    client_order_id: str | None = None
    ordinal: int | None = None  # 1-based among POST attempts
    symbol: str | None = None
    remaining: int = 1
    fill_price: Decimal | None = None


def _dec(value: Decimal | int | float | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass
class ScriptedAlpaca:
    """In-memory broker state plus scripting and call counters."""

    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    default_price: Decimal = Decimal("100")
    prices: dict[str, Decimal] = field(default_factory=dict)
    default_status: str = "new"  # status of an unscripted accepted POST

    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    fills: list[dict[str, Any]] = field(default_factory=list)
    account: dict[str, str] = field(
        default_factory=lambda: {
            "cash": "100000",
            "buying_power": "100000",
            "equity": "100000",
        }
    )
    position_overrides: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)
    hidden: set[str] = field(default_factory=set)
    rules: list[_PostRule] = field(default_factory=list)

    post_count: int = 0  # order POSTs that reached the broker
    post_attempts: int = 0  # every order POST handed to the transport (incl. connect errors)
    post_client_order_ids: list[str] = field(default_factory=list)
    get_log: list[str] = field(default_factory=list)
    cancel_calls: int = 0
    data_calls: int = 0
    unknown_calls: list[str] = field(default_factory=list)
    calls_total: int = 0
    _seq: int = 0

    # ------------------------------------------------------------------ wiring

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def http_client(self, base_url: str = "https://paper-api.alpaca.markets") -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=self.transport())

    # --------------------------------------------------------------- scripting

    def script_post(
        self,
        behavior: str = ACCEPT,
        *,
        status: str = "new",
        client_order_id: str | None = None,
        ordinal: int | None = None,
        symbol: str | None = None,
        times: int = 1,
        fill_price: Decimal | int | str | None = None,
    ) -> None:
        """Script the behavior of order POSTs: by client_order_id, by ordinal, by symbol or the
        next ``times`` POSTs. Rules are consulted in the order they were added."""

        self.rules.append(
            _PostRule(
                behavior=behavior,
                status=status,
                client_order_id=client_order_id,
                ordinal=ordinal,
                symbol=symbol,
                remaining=times,
                fill_price=_dec(fill_price) if fill_price is not None else None,
            )
        )

    def set_price(self, symbol: str, price: Decimal | int | str) -> None:
        self.prices[symbol] = _dec(price)

    def price_of(self, symbol: str) -> Decimal:
        return self.prices.get(symbol, self.default_price)

    def set_account(
        self,
        *,
        cash: Decimal | int | str | None = None,
        buying_power: Decimal | int | str | None = None,
        equity: Decimal | int | str | None = None,
    ) -> None:
        if cash is not None:
            self.account["cash"] = str(cash)
        if buying_power is not None:
            self.account["buying_power"] = str(buying_power)
        if equity is not None:
            self.account["equity"] = str(equity)

    def set_position(self, symbol: str, qty: Decimal | int | str, avg_price: Decimal | int | str) -> None:
        self.position_overrides[symbol] = (_dec(qty), _dec(avg_price))

    def hide_order(self, order_ref: str) -> None:
        """Never listed, 404 on every lookup (the order may or may not exist)."""

        order = self.find(order_ref)
        self.hidden.add(order["id"] if order is not None else order_ref)
        self.hidden.add(order_ref)

    def clear_hidden(self) -> None:
        self.hidden.clear()

    # ------------------------------------------------------------ order state

    def find(self, order_ref: str) -> dict[str, Any] | None:
        """An order by broker id or client_order_id (hidden orders included)."""

        if order_ref in self.orders:
            return self.orders[order_ref]
        for order in self.orders.values():
            if order["client_order_id"] == order_ref:
                return order
        return None

    def order_for(self, order_ref: str) -> dict[str, Any]:
        order = self.find(order_ref)
        assert order is not None, f"scripted broker has no order {order_ref!r}"
        return order

    def _stamp(self) -> str:
        return self.now().isoformat()

    def _new_order(
        self,
        *,
        client_order_id: str,
        symbol: str,
        qty: str,
        side: str,
        status: str = "new",
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        stamp = self._stamp()
        order = {
            "id": str(uuid.uuid4()),
            "client_order_id": client_order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "status": status if status in ("new", "accepted") else "new",
            "submitted_at": stamp,
            "created_at": stamp,
            "updated_at": stamp,
            "filled_at": None,
            "filled_qty": "0",
            "filled_avg_price": None,
            "replaced_by": None,
            "replaces": None,
        }
        self.orders[order["id"]] = order
        return order

    def fill_order(
        self,
        order_ref: str,
        qty: Decimal | int | str | None = None,
        price: Decimal | int | str | None = None,
    ) -> dict[str, Any]:
        """Fill ``qty`` (default: the rest) at ``price`` (default: the symbol price)."""

        order = self.order_for(order_ref)
        total = _dec(order["qty"])
        done = _dec(order["filled_qty"])
        amount = _dec(qty) if qty is not None else total - done
        assert amount > 0 and done + amount <= total, "fill exceeds the order quantity"
        fill_price = _dec(price) if price is not None else self.price_of(order["symbol"])
        self._seq += 1
        stamp = self._stamp()
        self.fills.append(
            {
                "id": f"{self._seq:08d}::{uuid.uuid4()}",
                "activity_type": "FILL",
                "order_id": order["id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "qty": str(amount),
                "price": str(fill_price),
                "transaction_time": stamp,
                "type": "fill",
            }
        )
        previous_value = done * _dec(order["filled_avg_price"] or "0")
        new_done = done + amount
        order["filled_qty"] = str(new_done)
        order["filled_avg_price"] = str((previous_value + amount * fill_price) / new_done)
        order["status"] = "filled" if new_done == total else "partially_filled"
        order["filled_at"] = stamp if new_done == total else None
        order["updated_at"] = stamp
        self.prices.setdefault(order["symbol"], fill_price)
        return order

    def partial_fill(
        self, order_ref: str, qty: Decimal | int | str, price: Decimal | int | str | None = None
    ) -> dict[str, Any]:
        return self.fill_order(order_ref, qty, price)

    def terminal(self, order_ref: str, status: str) -> dict[str, Any]:
        assert status in TERMINAL_STATUSES
        order = self.order_for(order_ref)
        order["status"] = status
        order["updated_at"] = self._stamp()
        return order

    def replace_order(self, order_ref: str, successor_ref: str) -> None:
        order = self.order_for(order_ref)
        successor = self.order_for(successor_ref)
        order["status"] = "replaced"
        order["replaced_by"] = successor["id"]
        successor["replaces"] = order["id"]

    def inject_external_order(
        self,
        symbol: str,
        side: str,
        qty: Decimal | int | str,
        status: str = "filled",
        *,
        price: Decimal | int | str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """A manual / non-platform order. ``status`` filled -> fully filled; any terminal status
        other than filled -> terminal with no fill; ``new``/``accepted`` -> an OPEN order."""

        order = self._new_order(
            client_order_id=client_order_id or str(uuid.uuid4()),
            symbol=symbol,
            qty=str(qty),
            side=side,
        )
        if status == "filled":
            self.fill_order(order["id"], price=price)
        elif status in TERMINAL_STATUSES:
            order["status"] = status
        else:
            order["status"] = status
        return order

    # --------------------------------------------------------------- derived

    def positions(self) -> list[dict[str, str]]:
        net: dict[str, tuple[Decimal, Decimal]] = {}
        for fill in self.fills:
            symbol = fill["symbol"]
            qty, cost = net.get(symbol, (Decimal("0"), Decimal("0")))
            amount = _dec(fill["qty"])
            price = _dec(fill["price"])
            if fill["side"] == "buy":
                net[symbol] = (qty + amount, cost + amount * price)
            else:
                avg = cost / qty if qty else price
                net[symbol] = (qty - amount, cost - amount * avg)
        for symbol, override in self.position_overrides.items():
            net[symbol] = (override[0], override[0] * override[1])
        out: list[dict[str, str]] = []
        for symbol in sorted(net):
            qty, cost = net[symbol]
            if qty == 0:
                continue
            avg = cost / qty
            current = self.price_of(symbol)
            out.append(
                {
                    "symbol": symbol,
                    "qty": str(qty),
                    "avg_entry_price": str(avg),
                    "cost_basis": str(cost),
                    "market_value": str(qty * current),
                    "current_price": str(current),
                    "side": "long" if qty > 0 else "short",
                }
            )
        return out

    # ------------------------------------------------------------- assertions

    def posts_for(self, client_order_id: str) -> int:
        return self.post_client_order_ids.count(client_order_id)

    def assert_budget(self, expected_posts: int) -> None:
        assert self.post_count == expected_posts, (
            f"scripted broker saw {self.post_count} order POSTs, the test declared {expected_posts}"
        )
        assert self.cancel_calls == 0, "the platform must never cancel a broker order (J-2)"
        assert self.data_calls == 0, "a market-data endpoint was called (price seam missed)"
        assert not self.unknown_calls, f"unscripted broker calls: {self.unknown_calls}"

    # -------------------------------------------------------------- transport

    def _json(self, status_code: int, body: Any) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls_total += 1
        method = request.method.upper()
        path = request.url.path
        params = dict(request.url.params)
        if method == "DELETE":
            self.cancel_calls += 1
            return self._json(405, {"message": "scripted broker: cancel is not allowed"})
        if method == "POST" and path == "/v2/orders":
            return self._post_order(request)
        if method == "GET":
            self.get_log.append(f"{path}?{request.url.query.decode()}")
            return self._get(path, params, request)
        self.unknown_calls.append(f"{method} {path}")
        return self._json(501, {"message": "scripted broker: unscripted call"})

    # -- POST

    def _match_rule(self, client_order_id: str, symbol: str) -> _PostRule | None:
        for rule in self.rules:
            if rule.remaining <= 0:
                continue
            if rule.client_order_id is not None and rule.client_order_id != client_order_id:
                continue
            if rule.ordinal is not None and rule.ordinal != self.post_attempts:
                continue
            if rule.symbol is not None and rule.symbol != symbol:
                continue
            rule.remaining -= 1
            return rule
        return None

    def _post_order(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        client_order_id = body["client_order_id"]
        self.post_attempts += 1
        rule = self._match_rule(client_order_id, body["symbol"])
        behavior = rule.behavior if rule is not None else ACCEPT
        status = rule.status if rule is not None else self.default_status
        if behavior == CONNECT_ERROR:
            raise httpx.ConnectError("scripted connection refused", request=request)
        self.post_count += 1
        self.post_client_order_ids.append(client_order_id)
        if behavior == READ_TIMEOUT_WITHOUT_CREATE:
            raise httpx.ReadTimeout("scripted read timeout (no order created)", request=request)
        if behavior == HTTP_500:
            return self._json(500, {"message": "scripted internal error"})
        existing = self.find(client_order_id)
        if behavior == DUPLICATE_422:
            if existing is None:
                self._new_order(
                    client_order_id=client_order_id,
                    symbol=body["symbol"],
                    qty=str(body["qty"]),
                    side=body["side"],
                    order_type=body.get("type", "market"),
                    time_in_force=body.get("time_in_force", "day"),
                )
            return self._json(422, {"message": "client_order_id must be unique"})
        if existing is not None:
            return self._json(422, {"message": "client_order_id must be unique"})
        order = self._new_order(
            client_order_id=client_order_id,
            symbol=body["symbol"],
            qty=str(body["qty"]),
            side=body["side"],
            order_type=body.get("type", "market"),
            time_in_force=body.get("time_in_force", "day"),
        )
        if behavior == READ_TIMEOUT_AFTER_CREATE:
            raise httpx.ReadTimeout("scripted read timeout (order created)", request=request)
        if status == "filled":
            price = rule.fill_price if rule is not None else None
            self.fill_order(order["id"], price=price)
        elif status not in ("new", "accepted"):
            order["status"] = status
        return self._json(200, dict(order))

    # -- GET

    def _get(self, path: str, params: dict[str, str], request: httpx.Request) -> httpx.Response:
        if path == "/v2/orders":
            return self._list_orders(params)
        if path == "/v2/orders:by_client_order_id":
            order = self.find(params.get("client_order_id", ""))
            if order is None or self._is_hidden(order):
                return self._json(404, {"message": "order not found"})
            return self._json(200, dict(order))
        if path.startswith("/v2/orders/"):
            order = self.orders.get(unquote(path.removeprefix("/v2/orders/")))
            if order is None or self._is_hidden(order):
                return self._json(404, {"message": "order not found"})
            return self._json(200, dict(order))
        if path == "/v2/positions":
            return self._json(200, self.positions())
        if path == "/v2/account":
            return self._json(200, self._account_body())
        if path == "/v2/account/activities/FILL":
            return self._list_fills(params)
        if path.startswith("/v2/stocks/") and path.endswith("/trades/latest"):
            self.data_calls += 1
            symbol = unquote(path.removeprefix("/v2/stocks/").removesuffix("/trades/latest"))
            return self._json(
                200, {"symbol": symbol, "trade": {"p": float(self.price_of(symbol)), "t": self._stamp()}}
            )
        self.unknown_calls.append(f"GET {path}")
        return self._json(501, {"message": "scripted broker: unscripted call"})

    def _is_hidden(self, order: dict[str, Any]) -> bool:
        return order["id"] in self.hidden or order["client_order_id"] in self.hidden

    def _account_body(self) -> dict[str, str]:
        long_value = sum(
            (_dec(p["market_value"]) for p in self.positions() if p["side"] == "long"),
            start=Decimal("0"),
        )
        return {
            **self.account,
            "long_market_value": str(long_value),
            "short_market_value": "0",
        }

    def _list_orders(self, params: dict[str, str]) -> httpx.Response:
        status = params.get("status", "open")
        limit = int(params.get("limit", "50"))
        visible = [o for o in self.orders.values() if not self._is_hidden(o)]
        ordered = sorted(visible, key=lambda o: (o["created_at"], o["id"]), reverse=True)
        if status == "open":
            ordered = [o for o in ordered if o["status"] not in TERMINAL_STATUSES]
        elif status == "closed":
            ordered = [o for o in ordered if o["status"] in TERMINAL_STATUSES]
        cursor = params.get("before_order_id")
        if cursor is not None:
            ids = [o["id"] for o in ordered]
            ordered = ordered[ids.index(cursor) + 1 :] if cursor in ids else []
        return self._json(200, [dict(o) for o in ordered[:limit]])

    def _list_fills(self, params: dict[str, str]) -> httpx.Response:
        page_size = int(params.get("page_size", "100"))
        ordered = sorted(self.fills, key=lambda f: f["id"], reverse=True)
        cursor = params.get("page_token")
        if cursor is not None:
            ids = [f["id"] for f in ordered]
            ordered = ordered[ids.index(cursor) + 1 :] if cursor in ids else []
        return self._json(200, ordered[:page_size])
