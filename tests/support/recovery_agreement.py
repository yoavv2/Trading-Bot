"""The ONE consumer-agreement assertion for the recovery predicate (20.1-26, used by 29 and 30).

Every consumer of "is this strategy's uncertain outcome resolved?" reads the same predicate
(``services.recovery``): the strategy gate (``strategy_recovery_status``), the account / A5
read (``account_recovery_status`` and ``_check_a5``), the Job recovery read R3
(``GET /api/v1/jobs/{id}/recovery``) and the execution-operation read. This helper asserts, in
one place, that they agree on the same database state, and that no order is listed twice for
one Job and no registering Job is left as ``execution_path_unproven``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

from trading_platform.core import clock
from trading_platform.core.settings import load_settings
from trading_platform.db.session import session_scope
from trading_platform.services.paper_account_checks import _check_a5
from trading_platform.services.recovery import (
    GateCode,
    RecoveryStatus,
    UnresolvedReason,
    account_recovery_status,
    strategy_recovery_status,
)


def assert_recovery_consumers_agree(
    client: TestClient,
    *,
    strategy_id: str,
    linked_job_ids: Sequence[uuid.UUID],
    registering_flagged_job_ids: Sequence[uuid.UUID] = (),
    operation_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> GateCode | None:
    """Assert the consumers agree and return the strategy gate (so a test can record a history)."""

    observed = now or clock.now_utc()
    with session_scope(load_settings()) as session:
        strategy: RecoveryStatus = strategy_recovery_status(session, strategy_id, now=observed)
        account = account_recovery_status(session, now=observed)
        a5_passed = _check_a5(session, now=observed).passed

    # (1) the strategy gate equals the account subject's gate for that strategy
    subject = next((s for s in account.subjects if s.strategy_id == strategy_id), None)
    if subject is None:
        assert strategy.gate_code is None and strategy.resolved, (
            "the account read has no subject for the strategy but the strategy gate is "
            f"{strategy.gate_code}"
        )
    else:
        assert subject.gate_code == strategy.gate_code, (subject.gate_code, strategy.gate_code)

    # (2) A5 passes exactly when the account-wide predicate is resolved
    assert a5_passed is account.resolved, (a5_passed, account.resolved)

    # (4) no (job, order) pair twice; at most one Job-level entry per Job
    order_pairs = [(i.job_id, i.intent_id) for i in strategy.intents if i.intent_id is not None]
    assert len(order_pairs) == len(set(order_pairs)), f"an order is listed twice: {order_pairs}"
    job_level = [i.job_id for i in strategy.intents if i.intent_id is None]
    assert len(job_level) == len(set(job_level)), f"a Job-level entry is duplicated: {job_level}"

    # (5) a Job that registered or retried an order is never execution_path_unproven
    for job_id in registering_flagged_job_ids:
        offending = [
            i
            for i in strategy.intents
            if i.job_id == job_id
            and i.intent_id is None
            and i.unresolved_reason is UnresolvedReason.EXECUTION_PATH_UNPROVEN
        ]
        assert not offending, f"Job {job_id} registered an order yet reads execution_path_unproven"

    # (6) the strategy gate is outcome_unresolved exactly when some strategy intent blocks
    any_blocking = any(i.blocking for i in strategy.intents)
    assert (strategy.gate_code is GateCode.OUTCOME_UNRESOLVED) is any_blocking, (
        strategy.gate_code,
        [(str(i.intent_id), i.classification.value, i.blocking) for i in strategy.intents],
    )

    # (3) R3 per linked Job agrees with the strategy read
    for job_id in linked_job_ids:
        response = client.get(f"/api/v1/jobs/{job_id}/recovery")
        assert response.status_code == 200, response.text
        body = response.json()
        expected = sorted(
            (
                str(i.intent_id) if i.intent_id is not None else "",
                i.classification.value,
                i.blocking,
            )
            for i in strategy.intents
            if i.job_id == job_id
        )
        actual = sorted(
            (item["intent_id"] or "", item["classification"], item["blocking"])
            for item in body["intents"]
        )
        assert actual == expected, (actual, expected)
        job_blocking = any(item["blocking"] for item in body["intents"])
        assert (body["gate_code"] == GateCode.OUTCOME_UNRESOLVED.value) is job_blocking, body

    # (7) the operation's unresolved intents are the blocking strategy intents linked to it
    if operation_id is not None:
        response = client.get(f"/api/v1/execution-operations/{operation_id}")
        assert response.status_code == 200, response.text
        operation: dict[str, Any] = response.json()
        listed = {
            item["paper_order_id"]
            for item in operation["unresolved_intents"]
            if item["paper_order_id"] is not None
        }
        linked_orders = {
            str(intent["paper_order_id"])
            for intent in operation["intents"]
            if intent.get("paper_order_id")
        }
        blocking_linked = {
            str(i.intent_id)
            for i in strategy.intents
            if i.intent_id is not None and i.blocking and str(i.intent_id) in linked_orders
        }
        assert listed == blocking_linked, (listed, blocking_linked)

    return strategy.gate_code
