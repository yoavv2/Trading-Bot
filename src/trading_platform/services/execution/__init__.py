"""Execution service package: contracts, order-transition, and idempotency logic.

Public re-exports keep ``from trading_platform.services.execution import X`` resolving
for existing consumers after the STRUCT-04 package split.
"""

from __future__ import annotations

from trading_platform.services.execution.contracts import (
    ExecutionOrderStatus,
    ExecutionService,
    OrderIntent,
    OrderSide,
    OrderSubmissionResult,
    OrderTimeInForce,
    OrderType,
    PlaceholderExecutionService,
)
from trading_platform.services.execution.idempotency import (
    DerivedOrderIdentity,
    MaterialOrderIdentity,
    build_client_order_id,
    build_intent_hash,
    build_material_order_identity,
    derive_order_identity,
)
from trading_platform.services.execution.transition import (
    IllegalOrderTransition,
    OrderTransitionRequest,
    OrderTransitionResult,
    apply_order_transition,
    resolve_transition_target,
)

__all__ = [
    "ExecutionOrderStatus",
    "ExecutionService",
    "OrderIntent",
    "OrderSide",
    "OrderSubmissionResult",
    "OrderTimeInForce",
    "OrderType",
    "PlaceholderExecutionService",
    "DerivedOrderIdentity",
    "MaterialOrderIdentity",
    "build_client_order_id",
    "build_intent_hash",
    "build_material_order_identity",
    "derive_order_identity",
    "IllegalOrderTransition",
    "OrderTransitionRequest",
    "OrderTransitionResult",
    "apply_order_transition",
    "resolve_transition_target",
]
