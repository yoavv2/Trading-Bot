"""Execution service package: contracts, order-transition, and idempotency logic.

Public re-exports keep ``from trading_platform.services.execution import X`` resolving
for existing consumers after the STRUCT-04 package split.
"""

from __future__ import annotations

# Paper-execution split (STRUCT-04 part 2, 12-04): the report/dataclass
# surface lives in the lightweight ``_paper_common.py`` and is re-exported
# eagerly (no heavy or cyclic imports). The submission/session/sync
# ENTRYPOINTS in ``submit_orders.py``/``sync_orders.py`` pull in the heavy,
# cyclic dependency graph (bootstrap, alpaca, reconciliation -- all of which
# import this package back), so they are loaded LAZILY via PEP 562
# ``__getattr__`` below. This keeps ``import trading_platform.services.execution``
# cheap and acyclic for the many consumers that only need the pure
# contracts/transition/idempotency surface, while still resolving
# ``from trading_platform.services.execution import run_paper_order_submission``.
from trading_platform.services.execution._paper_common import (
    PaperExecutionCandidate,
    PaperExecutionRunReport,
    PaperIntentDecision,
    PaperSessionPlan,
    PaperSessionRunReport,
    PaperStateSyncReport,
)
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

# name -> (submodule, attribute) for lazily-resolved paper-execution entrypoints.
_LAZY_PAPER_EXPORTS: dict[str, tuple[str, str]] = {
    "resolve_submission_session": ("submit_orders", "resolve_submission_session"),
    "run_paper_order_submission": ("submit_orders", "run_paper_order_submission"),
    "run_paper_session": ("submit_orders", "run_paper_session"),
    "schedule_reconciliation_after_partial_failure": (
        "submit_orders",
        "schedule_reconciliation_after_partial_failure",
    ),
    "build_paper_client_order_id": ("submit_orders", "build_client_order_id"),
    "sync_paper_state": ("sync_orders", "sync_paper_state"),
}


def __getattr__(name: str):
    target = _LAZY_PAPER_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    submodule = importlib.import_module(f"{__name__}.{target[0]}")
    return getattr(submodule, target[1])


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
    # Paper-execution entrypoints (STRUCT-04 part 2)
    "resolve_submission_session",
    "run_paper_order_submission",
    "run_paper_session",
    "sync_paper_state",
    "build_paper_client_order_id",
    "schedule_reconciliation_after_partial_failure",
    "PaperExecutionCandidate",
    "PaperExecutionRunReport",
    "PaperIntentDecision",
    "PaperSessionPlan",
    "PaperSessionRunReport",
    "PaperStateSyncReport",
]
