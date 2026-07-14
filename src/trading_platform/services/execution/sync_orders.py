"""Broker-state sync logic: orders, fills, positions, and account snapshots.

STRUCT-04 part 2 (12-04): sync-side split of the former monolithic
`services/paper_execution.py`. Submission + session orchestration lives in
the sibling `submit_orders.py`; shared dataclasses and cross-cutting helpers
live in `_paper_common.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountSnapshot,
    PaperFill,
    PaperOrder,
    Position,
    StrategyRun,
)
from trading_platform.db.session import session_scope
from trading_platform.services.alpaca import (
    AlpacaClient,
    BrokerAccountSnapshot,
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
)
from trading_platform.services.bootstrap import ensure_strategy_record
from trading_platform.services.execution._paper_common import (
    PaperStateSyncReport,
    _broker_transition_event,
    _ensure_symbol,
)
from trading_platform.services.execution.transition import (
    OrderTransitionRequest,
    apply_order_transition,
    resolve_transition_target,
)
from trading_platform.strategies.registry import StrategyRegistry, build_default_registry

_PAPER_FILL_DEDUP_CHUNK_SIZE = 1_000


def sync_paper_state(
    strategy_id: str | None = None,
    *,
    as_of_session: date,
    settings: Settings | None = None,
    registry: StrategyRegistry | None = None,
    broker_client: AlpacaClient | None = None,
) -> PaperStateSyncReport:
    resolved_settings = settings or load_settings()
    resolved_registry = registry or build_default_registry(resolved_settings)
    resolved_strategy_id = (
        strategy_id or resolved_settings.execution.paper_session_runner.default_strategy_id
    )
    strategy = resolved_registry.resolve(resolved_strategy_id)
    synced_at = datetime.now(UTC)

    owns_broker_client = broker_client is None
    client = broker_client or AlpacaClient(resolved_settings.broker.alpaca)

    try:
        broker_orders = client.list_orders()
        broker_fills = client.list_fills()
        broker_positions = client.list_positions()
        broker_account = client.get_account()

        with session_scope(resolved_settings) as session:
            strategy_record = ensure_strategy_record(session, strategy.metadata)
            local_orders = (
                session.execute(
                    select(PaperOrder)
                    .join(StrategyRun, StrategyRun.id == PaperOrder.strategy_run_id)
                    .where(StrategyRun.strategy_id == strategy_record.id)
                    .order_by(PaperOrder.created_at.asc())
                )
                .scalars()
                .all()
            )
            local_orders_by_broker_id = {
                order.broker_order_id: order for order in local_orders if order.broker_order_id
            }
            local_orders_by_client_id = {
                order.client_order_id: order for order in local_orders if order.client_order_id
            }

            orders_synced = _sync_paper_orders(
                session,
                broker_orders,
                local_orders_by_broker_id=local_orders_by_broker_id,
                local_orders_by_client_id=local_orders_by_client_id,
                synced_at=synced_at,
            )
            fills_ingested = _ingest_paper_fills(
                session,
                broker_fills,
                local_orders_by_broker_id=local_orders_by_broker_id,
            )
            positions_opened, positions_closed = _sync_positions_from_broker(
                session,
                strategy_record.id,
                broker_positions,
                as_of_session=as_of_session,
                synced_at=synced_at,
            )
            snapshot = _record_broker_account_snapshot(
                session,
                strategy_record.id,
                broker_account,
                broker_positions,
                synced_at=synced_at,
            )

            return PaperStateSyncReport(
                strategy_id=resolved_strategy_id,
                session_date=as_of_session.isoformat(),
                synced_at=synced_at.isoformat(),
                orders_synced=orders_synced,
                fills_ingested=fills_ingested,
                positions_opened=positions_opened,
                positions_closed=positions_closed,
                open_positions=len(broker_positions),
                account_snapshot_id=str(snapshot.id),
            )
    finally:
        if owns_broker_client:
            client.close()


def _sync_paper_orders(
    session,
    broker_orders: list[BrokerOrderSnapshot],
    *,
    local_orders_by_broker_id: dict[str, PaperOrder],
    local_orders_by_client_id: dict[str, PaperOrder],
    synced_at: datetime,
) -> int:
    synced_count = 0
    for broker_order in broker_orders:
        local_order = local_orders_by_client_id.get(broker_order.client_order_id)
        if local_order is None:
            local_order = local_orders_by_broker_id.get(broker_order.broker_order_id)
        if local_order is None:
            continue

        if not local_order.broker_order_id:
            local_order.broker_order_id = broker_order.broker_order_id
        transition_event = _broker_transition_event(broker_order.status)
        transition_target = resolve_transition_target(
            from_state=local_order.status,
            event_type=transition_event,
        )
        if transition_target is not None and transition_target != local_order.status:
            apply_order_transition(
                local_order.id,
                OrderTransitionRequest(
                    strategy_run_id=local_order.strategy_run_id,
                    event_type=transition_event,
                    details={
                        "broker_order_id": broker_order.broker_order_id,
                        "broker_status": broker_order.broker_status,
                    },
                    event_at=broker_order.updated_at,
                ),
                session=session,
            )
        local_order.broker_status = broker_order.broker_status
        local_order.submitted_at = broker_order.submitted_at or local_order.submitted_at
        local_order.filled_at = broker_order.filled_at
        local_order.canceled_at = broker_order.canceled_at
        local_order.last_broker_update_at = broker_order.updated_at
        local_order.last_synced_at = synced_at
        local_order.broker_payload = broker_order.raw_payload

        if local_order.broker_order_id:
            local_orders_by_broker_id[local_order.broker_order_id] = local_order
        synced_count += 1
    return synced_count


def _ingest_paper_fills(
    session,
    broker_fills: list[BrokerFillSnapshot],
    *,
    local_orders_by_broker_id: dict[str, PaperOrder],
) -> int:
    existing_fill_ids = _load_existing_paper_fill_ids(session, broker_fills)
    ingested = 0

    for broker_fill in broker_fills:
        if broker_fill.broker_fill_id in existing_fill_ids:
            continue
        local_order = local_orders_by_broker_id.get(broker_fill.broker_order_id)
        if local_order is None:
            continue

        session.add(
            PaperFill(
                paper_order_id=local_order.id,
                symbol_id=local_order.symbol_id,
                broker_fill_id=broker_fill.broker_fill_id,
                broker_order_id=broker_fill.broker_order_id,
                side=broker_fill.side.value,
                quantity=broker_fill.quantity,
                price=broker_fill.price,
                filled_at=broker_fill.filled_at,
                broker_payload=broker_fill.raw_payload,
            )
        )
        existing_fill_ids.add(broker_fill.broker_fill_id)
        if local_order.filled_at is None or broker_fill.filled_at > local_order.filled_at:
            local_order.filled_at = broker_fill.filled_at
        ingested += 1

    return ingested


def _load_existing_paper_fill_ids(
    session,
    broker_fills: list[BrokerFillSnapshot],
) -> set[str]:
    """Load only fill IDs relevant to this broker response, in bounded statements."""
    broker_fill_ids = sorted({broker_fill.broker_fill_id for broker_fill in broker_fills})
    existing_fill_ids: set[str] = set()
    for offset in range(0, len(broker_fill_ids), _PAPER_FILL_DEDUP_CHUNK_SIZE):
        chunk_ids = broker_fill_ids[offset : offset + _PAPER_FILL_DEDUP_CHUNK_SIZE]
        existing_fill_ids.update(
            session.execute(
                select(PaperFill.broker_fill_id).where(PaperFill.broker_fill_id.in_(chunk_ids))
            )
            .scalars()
            .all()
        )
    return existing_fill_ids


def _sync_positions_from_broker(
    session,
    strategy_row_id: uuid.UUID,
    broker_positions: list[BrokerPositionSnapshot],
    *,
    as_of_session: date,
    synced_at: datetime,
) -> tuple[int, int]:
    existing_open_positions = (
        session.execute(
            select(Position).where(
                Position.strategy_id == strategy_row_id,
                Position.status == "open",
            )
        )
        .scalars()
        .all()
    )
    existing_by_symbol = {
        position.symbol_ref.ticker: position for position in existing_open_positions
    }
    opened = 0
    closed = 0

    for broker_position in broker_positions:
        symbol_row = _ensure_symbol(session, broker_position.symbol)
        existing_position = existing_by_symbol.pop(broker_position.symbol, None)
        if existing_position is None:
            session.add(
                Position(
                    strategy_id=strategy_row_id,
                    symbol_id=symbol_row.id,
                    status="open",
                    quantity=broker_position.quantity,
                    average_entry_price=broker_position.average_entry_price,
                    cost_basis=broker_position.cost_basis,
                    opened_session_date=as_of_session,
                    opened_at=synced_at,
                )
            )
            opened += 1
            continue

        existing_position.quantity = broker_position.quantity
        existing_position.average_entry_price = broker_position.average_entry_price
        existing_position.cost_basis = broker_position.cost_basis
        existing_position.status = "open"
        existing_position.opened_session_date = (
            existing_position.opened_session_date or as_of_session
        )
        existing_position.opened_at = existing_position.opened_at or synced_at
        existing_position.closed_session_date = None
        existing_position.closed_at = None

    for stale_position in existing_by_symbol.values():
        stale_position.status = "closed"
        stale_position.closed_session_date = as_of_session
        stale_position.closed_at = synced_at
        closed += 1

    return opened, closed


def _record_broker_account_snapshot(
    session,
    strategy_row_id: uuid.UUID,
    broker_account: BrokerAccountSnapshot,
    broker_positions: list[BrokerPositionSnapshot],
    *,
    synced_at: datetime,
) -> AccountSnapshot:
    gross_exposure = sum(
        (abs(position.market_value) for position in broker_positions), start=Decimal("0")
    )
    snapshot = AccountSnapshot(
        strategy_id=strategy_row_id,
        source_run_id=None,
        snapshot_source="broker_sync",
        snapshot_at=synced_at,
        cash=broker_account.cash,
        gross_exposure=gross_exposure,
        total_equity=broker_account.equity,
        buying_power=broker_account.buying_power,
        open_positions=len(broker_positions),
    )
    session.add(snapshot)
    session.flush()
    return snapshot
