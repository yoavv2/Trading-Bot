"""Closed vocabulary of broker-touching Job types (ACCT-01, 20.1-08).

Pure constants shared by the Job specs (catalog ``broker_effect``), the recovery
predicate (20.1-10), the handover checks (20.1-12) and the external-activity
record (20.1-09). This module imports nothing from ``jobs/`` (services never
import jobs); the Job type names are plain strings that equal the registered
``job_type`` values.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType

PAPER_SESSION_JOB_TYPE = "paper-session"
BROKER_ORDER_SYNC_JOB_TYPE = "broker-order-sync"
RECONCILIATION_JOB_TYPE = "reconciliation"


class BrokerEffect(StrEnum):
    """What a Job type does at the broker (closed set)."""

    NONE = "none"
    READS_BROKER = "reads_broker"
    SUBMITS_ORDERS = "submits_orders"


#: Closed mapping of every broker-touching Job type to its effect. A queued or running
#: Job of any of these types is "a broker-touching Job in flight" (handover check A1).
BROKER_TOUCHING_JOB_TYPES = MappingProxyType(
    {
        PAPER_SESSION_JOB_TYPE: BrokerEffect.SUBMITS_ORDERS,
        BROKER_ORDER_SYNC_JOB_TYPE: BrokerEffect.READS_BROKER,
        RECONCILIATION_JOB_TYPE: BrokerEffect.READS_BROKER,
    }
)

#: Job types that CHANGE local state from the broker's (they apply orders, fills or
#: submit). "After the latest broker-touching Job" rules look at these only: a standalone
#: reconciliation is report-only and never counts as the Job a later reconciliation must
#: follow. 20.1-09 extends both collections with ``record-external-activity``.
STATE_CHANGING_BROKER_JOB_TYPES = frozenset({PAPER_SESSION_JOB_TYPE, BROKER_ORDER_SYNC_JOB_TYPE})

__all__ = [
    "BROKER_ORDER_SYNC_JOB_TYPE",
    "BROKER_TOUCHING_JOB_TYPES",
    "PAPER_SESSION_JOB_TYPE",
    "RECONCILIATION_JOB_TYPE",
    "STATE_CHANGING_BROKER_JOB_TYPES",
    "BrokerEffect",
]
