"""TEMPORARY re-export shim — moved to trading_platform.services.execution.transition (12-03).

Deleted once all importers are repointed within this same plan (Task 2).
"""

from __future__ import annotations

from trading_platform.services.execution.transition import *  # noqa: F401,F403
from trading_platform.services.execution.transition import (
    IllegalOrderTransition,
    OrderTransitionRequest,
    OrderTransitionResult,
    apply_order_transition,
    resolve_transition_target,
)
