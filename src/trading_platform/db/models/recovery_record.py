"""ORM model for append-only uncertain-outcome recovery records (REC-01, S-5, J-2).

One row is a per-intent classification, an absence-evidence item or an operator-recorded
broker statement. Resolution is computed from these rows plus local orders, the attempt
log and reconciliations, never stored. Rows are append-only (a database trigger rejects
DELETE and every UPDATE except the foreign keys' ``ON DELETE SET NULL``), so there is no
``updated_at``. ``strategy_public_id`` is a plain string, NOT a foreign key (NULL means
account level), so strategy lifecycle never touches the evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base


class RecoveryRecordKind(StrEnum):
    CLASSIFICATION = "classification"
    ABSENCE_EVIDENCE = "absence_evidence"
    BROKER_STATEMENT = "broker_statement"


class RecoveryClassification(StrEnum):
    """Closed per-intent recovery classification (03 sec.3.7 B; round 5: no statement class)."""

    NOTHING_SUBMITTED = "nothing_submitted"
    NOT_SENT = "not_sent"
    REJECTED_AT_SUBMISSION = "rejected_at_submission"
    FOUND_VERIFIED = "found_verified"
    NOT_FOUND = "not_found"
    UNRESOLVED = "unresolved"


class BrokerState(StrEnum):
    """Closed broker state of a found-and-verified intent."""

    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED_EXPIRED = "canceled_expired"
    REJECTED = "rejected"
    REPLACED = "replaced"


class UnresolvedReason(StrEnum):
    """Closed reasons an intent stays unresolved; each maps to ``outcome_uncertain_unresolved``."""

    LOOKUP_ERROR = "lookup_error"
    UNDOCUMENTED_NOT_FOUND_RESPONSE = "undocumented_not_found_response"
    PAGE_CAP_REACHED = "page_cap_reached"
    UNMAPPED_STATUS = "unmapped_status"
    ID_MISMATCH = "id_mismatch"
    EXECUTION_PATH_UNPROVEN = "execution_path_unproven"


class AbsenceEvidenceItem(StrEnum):
    """The four absence-evidence items (03 sec.3.7 B a-d)."""

    A_CLIENT_ORDER_ID_404 = "a_client_order_id_404"
    B_LIST_SCAN_NO_MATCH = "b_list_scan_no_match"
    C_NO_FILL_REFERENCE = "c_no_fill_reference"
    D_NO_EXPOSURE_CHANGE = "d_no_exposure_change"


class EvidenceResult(StrEnum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    ERROR = "error"


class BrokerStatementKind(StrEnum):
    NOT_RECEIVED = "not_received"
    ORDER_RECORD = "order_record"


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    rendered = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({rendered})"


class RecoveryRecord(Base):
    """One append-only recovery record (no strategy FK; only jobs and paper_orders FKs)."""

    __tablename__ = "recovery_records"
    __table_args__ = (
        # Naming convention renders ck_recovery_records_<name>.
        CheckConstraint(_in("kind", RecoveryRecordKind), name="kind"),
        CheckConstraint(
            "classification IS NULL OR " + _in("classification", RecoveryClassification),
            name="classification",
        ),
        CheckConstraint(
            "broker_state IS NULL OR " + _in("broker_state", BrokerState), name="broker_state"
        ),
        CheckConstraint(
            "unresolved_reason IS NULL OR " + _in("unresolved_reason", UnresolvedReason),
            name="unresolved_reason",
        ),
        CheckConstraint(
            "evidence_item IS NULL OR " + _in("evidence_item", AbsenceEvidenceItem),
            name="evidence_item",
        ),
        CheckConstraint(
            "evidence_result IS NULL OR " + _in("evidence_result", EvidenceResult),
            name="evidence_result",
        ),
        CheckConstraint(
            "statement IS NULL OR " + _in("statement", BrokerStatementKind), name="statement"
        ),
        CheckConstraint(
            "(kind = 'classification') = (classification IS NOT NULL)",
            name="classification_shape",
        ),
        CheckConstraint(
            "COALESCE(classification = 'found_verified', false) = (broker_state IS NOT NULL)",
            name="broker_state_shape",
        ),
        CheckConstraint(
            "COALESCE(classification = 'unresolved', false) = (unresolved_reason IS NOT NULL)",
            name="unresolved_reason_shape",
        ),
        CheckConstraint(
            "(kind = 'absence_evidence') = (evidence_item IS NOT NULL)"
            " AND (kind = 'absence_evidence') = (evidence_result IS NOT NULL)"
            " AND (kind = 'absence_evidence') = (observed_at IS NOT NULL)",
            name="absence_evidence_shape",
        ),
        CheckConstraint(
            "(kind = 'broker_statement') = (statement IS NOT NULL)"
            " AND (kind = 'broker_statement') = (reference IS NOT NULL)"
            " AND (kind = 'broker_statement') = (reason IS NOT NULL)"
            " AND (kind <> 'broker_statement'"
            " OR (btrim(reference) <> '' AND btrim(reason) <> ''))",
            name="broker_statement_shape",
        ),
        Index(
            "ix_recovery_records_paper_order_id_kind_created_at",
            "paper_order_id",
            "kind",
            "created_at",
        ),
        Index(
            "ix_recovery_records_strategy_public_id_created_at", "strategy_public_id", "created_at"
        ),
        Index(
            "uq_recovery_records_one_statement_per_intent",
            "paper_order_id",
            unique=True,
            postgresql_where=text("kind = 'broker_statement'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    paper_order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("paper_orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    strategy_public_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    broker_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    unresolved_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    evidence_item: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    statement: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reference: Mapped[str | None] = mapped_column(Text(), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
