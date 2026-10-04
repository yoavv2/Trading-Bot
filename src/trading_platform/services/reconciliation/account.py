"""Owner-less, report-only account-level reconciliation (ACCT-01; D-08, D-09, D-11).

``reconcile_account`` compares the broker's orders, fills and positions with the
local records of EVERY owner plus recorded external activity, classifies the broker
activity through the shared evidence-based attribution (20.1-07), evaluates account
divergence against the latest broker-observed snapshot, and stores ONE result row in
``account_reconciliation_runs``. That table has no owner reference (R-31, J-3), so the
result is never attached to an arbitrary owner.

It is REPORT-ONLY: it assigns nothing to any owner, submits nothing, creates no
position, corrects no order and writes no execution event. It lifts no gate by itself;
only its stored result (read through ``latest_standalone_reconciliation``) is read by
gates. A broker history page-cap overflow is a stored, blocking, UNRESOLVED result
(``broker_history_exceeds_cap``), never a truncated list (D-11).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from trading_platform.core.logging import build_log_context, emit_structured_log, get_logger
from trading_platform.core.settings import Settings, load_settings
from trading_platform.db.models import (
    AccountReconciliationRun,
    AccountReconciliationStatus,
    PaperFill,
    PaperOrder,
    Position,
)
from trading_platform.db.session import session_scope
from trading_platform.services.account_baseline import latest_broker_observed_account_snapshot
from trading_platform.services.alpaca import AlpacaClient, AlpacaPaginationCapExceededError
from trading_platform.services.attribution import (
    AttributionAnomaly,
    AttributionResult,
    OrderClass,
    UnresolvedReason,
    classify_broker_activity,
)
from trading_platform.services.attribution_inputs import (
    load_local_intent_records,
    load_ownership_periods,
    load_recorded_external_order_ids,
)
from trading_platform.services.reconciliation.findings import Finding
from trading_platform.services.reconciliation.matcher import match_snapshots
from trading_platform.services.reconciliation.report import (
    BrokerStateSnapshot,
    _evaluate_account_divergence,
    _evaluate_threshold_breach,
    _finding_event_dict,
    _matcher_scope,
    _project_local_account,
    _project_local_fill,
    _project_local_order,
    _project_local_position,
    load_broker_state,
)
from trading_platform.services.reconciliation.snapshot import LocalPositionSnapshot

ACCOUNT_RECONCILIATION_TRIGGER = "account_reconciliation"


@dataclass(frozen=True)
class AccountReconciliationReport:
    """The stored account-level result as returned to the caller (no owner field)."""

    run_id: str
    as_of_session: str | None
    checked_at: str
    finding_count: int
    blocking_count: int
    blocks_execution: bool
    unresolved_reasons: tuple[str, ...] = ()
    unexplained_exposure: dict[str, str] = field(default_factory=dict)
    classification_summary: dict[str, Any] = field(default_factory=dict)
    findings: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scope": "account",
            "as_of_session": self.as_of_session,
            "checked_at": self.checked_at,
            "finding_count": self.finding_count,
            "blocking_count": self.blocking_count,
            "blocks_execution": self.blocks_execution,
            "unresolved_reasons": list(self.unresolved_reasons),
            "unexplained_exposure": dict(self.unexplained_exposure),
            "classification_summary": self.classification_summary,
            "findings": list(self.findings),
        }


def reconcile_account(
    *,
    as_of_session: date | None = None,
    settings: Settings | None = None,
    broker_client: AlpacaClient | None = None,
    broker_state: BrokerStateSnapshot | None = None,
    trigger_source: str = ACCOUNT_RECONCILIATION_TRIGGER,
    job_id: uuid.UUID | None = None,
) -> AccountReconciliationReport:
    """Run one report-only account-level reconciliation and store its result.

    ``job_id`` is an opaque originating-Job identifier (the caller, a Job handler, owns
    that dependency). The run row is committed ``pending`` BEFORE the broker is read and
    finalized exactly once: ``succeeded`` (possibly blocking), or ``failed`` with the
    error re-raised.
    """

    logger = get_logger("trading_platform.reconciliation")
    resolved_settings = settings or load_settings()
    checked_at = datetime.now(UTC)
    run_id = _create_run(
        resolved_settings,
        as_of_session=as_of_session,
        trigger_source=trigger_source,
        job_id=job_id,
    )

    try:
        effective_state: BrokerStateSnapshot | None = broker_state
        unresolved: tuple[UnresolvedReason, ...] = ()
        if effective_state is None:
            try:
                effective_state = load_broker_state(
                    settings=resolved_settings, broker_client=broker_client
                )
            except AlpacaPaginationCapExceededError:
                # D-11: an UNRESOLVED, blocking result -- never a truncated list.
                unresolved = (UnresolvedReason.BROKER_HISTORY_EXCEEDS_CAP,)

        findings: tuple[Finding, ...] = ()
        attribution = AttributionResult(unresolved_reasons=unresolved)
        account_divergence: dict[str, Any] = {}
        threshold_breach: list[dict[str, Any]] = []
        if effective_state is not None:
            with session_scope(resolved_settings) as session:
                (
                    findings,
                    attribution,
                    account_divergence,
                    threshold_breach,
                ) = _evaluate_account(
                    session,
                    effective_state,
                    platform_prefix=resolved_settings.execution.client_order_id_prefix,
                    failure_threshold=resolved_settings.execution.safety.repeated_failure_threshold,
                )

        finding_dicts = [_finding_event_dict(finding) for finding in findings]
        blocking_count = sum(1 for finding in findings if finding.blocks_execution)
        unresolved_values = [reason.value for reason in attribution.unresolved_reasons]
        blocks_execution = (
            bool(findings)
            or bool(account_divergence)
            or bool(threshold_breach)
            or attribution.blocks_execution
        )
        unexplained = {
            symbol: str(quantity) for symbol, quantity in attribution.unexplained_exposure.items()
        }
        classification = _classification_summary(attribution)
        result_summary: dict[str, Any] = {
            "stage": "completed",
            "scope": "account",
            "as_of_session": as_of_session.isoformat() if as_of_session is not None else None,
            "finding_count": len(findings),
            "blocking_count": blocking_count,
            "blocks_execution": blocks_execution,
            "account_divergence": account_divergence,
            "threshold_breach": threshold_breach,
            "attribution": attribution.to_dict(),
            "unresolved_reasons": unresolved_values,
        }
        _finalize_run(
            resolved_settings,
            run_id,
            status=AccountReconciliationStatus.SUCCEEDED,
            completed_at=checked_at,
            blocks_execution=blocks_execution,
            finding_count=len(findings),
            blocking_count=blocking_count,
            findings=finding_dicts,
            account_divergence=account_divergence,
            unexplained_exposure=unexplained,
            classification_summary=classification,
            unresolved_reasons=unresolved_values,
            result_summary=result_summary,
        )
    except Exception as exc:
        # Fail closed: a failed run is never clean (is_clean needs status succeeded).
        _finalize_run(
            resolved_settings,
            run_id,
            status=AccountReconciliationStatus.FAILED,
            completed_at=checked_at,
            blocks_execution=True,
            error_message=str(exc),
            result_summary={
                "stage": "failed",
                "scope": "account",
                "as_of_session": as_of_session.isoformat() if as_of_session is not None else None,
            },
        )
        logger.exception(
            "account_reconciliation_failed",
            extra={
                "context": build_log_context(
                    run_id=str(run_id),
                    session_date=as_of_session.isoformat() if as_of_session else None,
                    trigger_source=trigger_source,
                )
            },
        )
        raise

    emit_structured_log(
        logger,
        logging.INFO,
        "account_reconciliation_completed",
        run_id=str(run_id),
        session_date=as_of_session.isoformat() if as_of_session else None,
        trigger_source=trigger_source,
        blocking_count=blocking_count,
        blocks_execution=blocks_execution,
    )
    return AccountReconciliationReport(
        run_id=str(run_id),
        as_of_session=as_of_session.isoformat() if as_of_session is not None else None,
        checked_at=checked_at.isoformat(),
        finding_count=len(findings),
        blocking_count=blocking_count,
        blocks_execution=blocks_execution,
        unresolved_reasons=tuple(unresolved_values),
        unexplained_exposure=unexplained,
        classification_summary=classification,
        findings=tuple(finding_dicts),
    )


def _evaluate_account(
    session,
    broker_state: BrokerStateSnapshot,
    *,
    platform_prefix: str,
    failure_threshold: int,
) -> tuple[tuple[Finding, ...], AttributionResult, dict[str, Any], list[dict[str, Any]]]:
    """Read-only: load EVERY owner's local records, classify and match. Writes nothing."""

    local_orders = (
        session.execute(
            select(PaperOrder)
            .options(selectinload(PaperOrder.symbol_ref))
            .order_by(PaperOrder.created_at.asc())
        )
        .scalars()
        .all()
    )
    local_fills = (
        session.execute(
            select(PaperFill)
            .options(selectinload(PaperFill.symbol_ref))
            .order_by(PaperFill.filled_at.asc())
        )
        .scalars()
        .all()
    )
    local_positions = (
        session.execute(
            select(Position)
            .options(selectinload(Position.symbol_ref))
            .where(Position.status == "open")
            .order_by(Position.created_at.asc())
        )
        .scalars()
        .all()
    )
    latest_snapshot = latest_broker_observed_account_snapshot(session)

    attribution = classify_broker_activity(
        broker_orders=broker_state.orders,
        broker_fills=broker_state.fills,
        broker_positions=broker_state.positions,
        local_intents=load_local_intent_records(session),
        ownership_periods=load_ownership_periods(session),
        recorded_external_order_ids=load_recorded_external_order_ids(session),
        platform_prefix=platform_prefix,
    )
    # Account scope: orders owned by ANY strategy match their own local records, so only
    # explained external orders leave the matcher input (together with their exposure).
    explained_external = frozenset(
        order.broker_order_id
        for order in attribution.orders
        if order.order_class is OrderClass.RECORDED_EXTERNAL and order.anomaly is None
    )
    matcher_orders, matcher_fills, matcher_positions = _matcher_scope(
        broker_state, explained_external
    )

    local_position_snapshots = _aggregate_positions(
        [_project_local_position(position) for position in local_positions]
    )
    findings = match_snapshots(
        local_orders=[_project_local_order(order) for order in local_orders],
        local_fills=[_project_local_fill(fill) for fill in local_fills],
        local_positions=local_position_snapshots,
        broker_orders=matcher_orders,
        broker_fills=matcher_fills,
        broker_positions=matcher_positions,
    )
    account_divergence = _evaluate_account_divergence(
        latest_snapshot=_project_local_account(latest_snapshot)
        if latest_snapshot is not None
        else None,
        broker_account=broker_state.account,
        broker_positions=broker_state.positions,
        local_positions_present=bool(local_positions),
    )
    threshold_breach = _evaluate_threshold_breach(
        local_orders=local_orders,
        findings=findings,
        failure_threshold=failure_threshold,
    )
    return findings, attribution, account_divergence, threshold_breach


def _aggregate_positions(
    positions: list[LocalPositionSnapshot],
) -> list[LocalPositionSnapshot]:
    """One local position per symbol across all owners (the matcher keys on symbol)."""

    by_symbol: dict[str, list[LocalPositionSnapshot]] = {}
    for position in positions:
        by_symbol.setdefault(position.symbol, []).append(position)
    aggregated: list[LocalPositionSnapshot] = []
    for symbol, group in by_symbol.items():
        if len(group) == 1:
            aggregated.append(group[0])
            continue
        quantity = sum((p.quantity for p in group), start=Decimal("0"))
        cost_basis = sum((p.cost_basis for p in group), start=Decimal("0"))
        average = (cost_basis / quantity) if quantity != 0 else Decimal("0")
        aggregated.append(
            LocalPositionSnapshot(
                symbol=symbol,
                quantity=quantity,
                average_entry_price=average,
                cost_basis=cost_basis,
                status="open",
            )
        )
    return aggregated


def _classification_summary(attribution: AttributionResult) -> dict[str, Any]:
    """Counts per class and per origin tag (bounded; no per-item lists)."""

    summary = attribution.to_dict()
    origin_tags: dict[str, int] = {}
    for order in attribution.unrecognized_orders:
        if order.origin_tag is not None:
            origin_tags[order.origin_tag.value] = origin_tags.get(order.origin_tag.value, 0) + 1
    anomalies: dict[str, int] = {anomaly.value: 0 for anomaly in AttributionAnomaly}
    for order in attribution.anomalies:
        if order.anomaly is not None:
            anomalies[order.anomaly.value] += 1
    return {
        "orders": summary["orders"],
        "fills": summary["fills"],
        "origin_tags": origin_tags,
        "anomalies": {key: count for key, count in anomalies.items() if count},
    }


def _create_run(
    settings: Settings,
    *,
    as_of_session: date | None,
    trigger_source: str,
    job_id: uuid.UUID | None,
) -> uuid.UUID:
    with session_scope(settings) as session:
        run = AccountReconciliationRun(
            job_id=job_id,
            status=AccountReconciliationStatus.PENDING.value,
            trigger_source=trigger_source,
            as_of_session=as_of_session,
            result_summary={
                "stage": "pending",
                "scope": "account",
                "as_of_session": as_of_session.isoformat() if as_of_session else None,
            },
        )
        session.add(run)
        session.flush()
        return run.id


def _finalize_run(
    settings: Settings,
    run_id: uuid.UUID,
    *,
    status: AccountReconciliationStatus,
    completed_at: datetime,
    blocks_execution: bool,
    result_summary: dict[str, Any],
    finding_count: int = 0,
    blocking_count: int = 0,
    findings: list[dict[str, Any]] | None = None,
    account_divergence: dict[str, Any] | None = None,
    unexplained_exposure: dict[str, str] | None = None,
    classification_summary: dict[str, Any] | None = None,
    unresolved_reasons: list[str] | None = None,
    error_message: str | None = None,
) -> None:
    with session_scope(settings) as session:
        run = session.get(AccountReconciliationRun, run_id)
        if run is None:
            raise LookupError(f"Missing account_reconciliation_run '{run_id}'.")
        run.status = status.value
        run.completed_at = completed_at
        run.blocks_execution = blocks_execution
        run.finding_count = finding_count
        run.blocking_count = blocking_count
        run.findings = findings or []
        run.account_divergence = account_divergence or {}
        run.unexplained_exposure = unexplained_exposure or {}
        run.classification_summary = classification_summary or {}
        run.unresolved_reasons = unresolved_reasons or []
        run.result_summary = result_summary
        run.error_message = error_message
