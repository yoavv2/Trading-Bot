"""TEMPORARY re-export shim for the paper-execution package split.

STRUCT-04 part 2 (12-04): the submission/session logic moved to
``services/execution/submit_orders.py`` and the broker-state sync logic moved
to ``services/execution/sync_orders.py`` (shared dataclasses in
``_paper_common.py``). This shim keeps not-yet-repointed consumers importing
while Task 2 repoints them one file at a time; it is DELETED at the end of
Task 2 and is NOT shipped.
"""

from __future__ import annotations

from trading_platform.services.execution.submit_orders import *  # noqa: F401,F403
from trading_platform.services.execution.sync_orders import *  # noqa: F401,F403
