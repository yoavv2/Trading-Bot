"""ORM models for the research platform (migration ``0031_research_platform``).

Research-only tables. Nothing on the trading path reads or writes them. The
``strategy_versions`` table is append-only: migration 0031 installs a trigger
that refuses every UPDATE and DELETE, so an approved version can never change
and every research result keeps pointing at exactly what ran.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_platform.db.base import Base, TimestampedModel


class AssetCatalogEntry(TimestampedModel, Base):
    """One catalog row per (provider, ticker) from the provider's public ticker list.

    ``name`` and ``name_source`` come from the public Nasdaq Trader and SEC
    directories at sync time, or from Tiingo metadata when an asset is viewed.
    Catalog presence never proves usable coverage; readiness checks the bars.
    """

    __tablename__ = "asset_catalog"
    __table_args__ = (
        UniqueConstraint("provider", "ticker", name="uq_asset_catalog_provider_ticker"),
        Index("ix_asset_catalog_name", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    catalog_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    catalog_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    names_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssetList(TimestampedModel, Base):
    __tablename__ = "asset_lists"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)


class AssetListItem(Base):
    __tablename__ = "asset_list_items"
    __table_args__ = (
        UniqueConstraint("list_id", "ticker", name="uq_asset_list_items_list_id_ticker"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("asset_lists.id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DataFreeze(TimestampedModel, Base):
    """A frozen set of research inputs: assets, range, provider, digest, export path.

    Independent of studies (S0): a study revision later references a freeze id.
    After the freeze, any newer bar, symbol or session row inside its scope makes
    research Jobs refuse to run (``inputs_changed_after_freeze``).
    """

    __tablename__ = "data_freezes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    assets: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    calendar_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    integrity_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    inputs_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StrategyDraft(TimestampedModel, Base):
    """Mutable authoring state. Approval copies it into an immutable version."""

    __tablename__ = "strategy_drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    yaml_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    ai_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )


class StrategyVersion(Base):
    """Immutable, append-only approved strategy version (0031 trigger refuses UPDATE/DELETE)."""

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version_no", name="uq_strategy_versions_strategy_id_version_no"),
        Index("ix_strategy_versions_spec_sha256", "spec_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    yaml_text: Mapped[str] = mapped_column(Text, nullable=False)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    history_required: Mapped[int] = mapped_column(Integer, nullable=False)
    history_minimum: Mapped[int] = mapped_column(Integer, nullable=False)
    scale_class: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    ai_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
    )
    behaviour_differs_from_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AiDraft(TimestampedModel, Base):
    """Provenance of one assistant draft or revision request (bodies hold no secrets)."""

    __tablename__ = "ai_drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    output_spec_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    failure_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    # 0033: full provenance of one attempt (plan S5).
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_drafts.id", ondelete="SET NULL"), nullable=True
    )
    parent_ai_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
    )
    retry_of_ai_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
    )
    ledger_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("provider_request_ledger.id", ondelete="SET NULL"), nullable=True
    )
    base_yaml_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_yaml_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsupported_requests: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    stop_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    cache_read_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_seconds: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchStudy(TimestampedModel, Base):
    __tablename__ = "research_studies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="substantive")


class StudyRevision(Base):
    """Immutable settings snapshot of a study; results attach to the revision that ran."""

    __tablename__ = "study_revisions"
    __table_args__ = (
        UniqueConstraint("study_id", "revision_no", name="uq_study_revisions_study_id_revision_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ranking_criteria_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_freeze_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("data_freezes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchFreeze(Base):
    """Frozen candidate and acceptance criteria; prerequisite of the final test."""

    __tablename__ = "research_freezes"
    __table_args__ = (
        UniqueConstraint("study_revision_id", name="uq_research_freezes_study_revision_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("study_revisions.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(20), nullable=False)
    acceptance_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ranking_criteria_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_sha: Mapped[str | None] = mapped_column(String(48), nullable=True)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    calendar_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    co_leading_choice_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TestWindowExposure(Base):
    """Every final-test run or rerun, queried globally by asset and date overlap."""

    __tablename__ = "test_window_exposures"
    __table_args__ = (
        Index("ix_test_window_exposures_asset_range_start_range_end", "asset", "range_start", "range_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset: Mapped[str] = mapped_column(String(20), nullable=False)
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("research_studies.id", ondelete="SET NULL"), nullable=True
    )
    study_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("study_revisions.id", ondelete="SET NULL"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    is_rerun: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    run_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    results_inspected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResearchRunLink(Base):
    """Research linkage of a ``strategy_runs`` row, kept in its own table so the shared
    trading model and table stay untouched. One row per research run."""

    __tablename__ = "research_run_links"
    __table_args__ = (
        Index(
            "uq_research_run_links_final_test_once",
            "study_revision_id",
            "strategy_version_id",
            "asset",
            unique=True,
            postgresql_where=text("window_role = 'final_test' AND rerun_of IS NULL"),
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="CASCADE"), primary_key=True
    )
    study_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("study_revisions.id", ondelete="SET NULL"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    asset: Mapped[str | None] = mapped_column(String(20), nullable=True)
    window_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    spec_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_sha: Mapped[str | None] = mapped_column(String(48), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rerun_of: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderRequestLedgerEntry(Base):
    """One attempted authenticated provider request (migration 0032).

    Every process of this application inserts here before sending, under the
    ``DatabaseRequestBudget`` advisory lock, so the hourly, daily and monthly-symbol
    limits are shared. Requests made outside this application are not observable.
    """

    __tablename__ = "provider_request_ledger"
    __table_args__ = (
        Index("ix_provider_request_ledger_provider_attempted_at", "provider", "attempted_at"),
        Index(
            "ix_provider_request_ledger_provider_symbol_attempted_at",
            "provider",
            "symbol",
            "attempted_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    process_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudyEvaluation(Base):
    """Stored ``comparison.json`` of one evaluation of a study revision (0032)."""

    __tablename__ = "study_evaluations"
    __table_args__ = (
        Index("ix_study_evaluations_study_revision_id_scope", "study_revision_id", "scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("study_revisions.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    comparison_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_rerun: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StudyRevisionJob(Base):
    """A Job submitted by a study revision, with its role in the graph (0032)."""

    __tablename__ = "study_revision_jobs"
    __table_args__ = (
        Index("ix_study_revision_jobs_study_revision_id", "study_revision_id"),
        Index(
            "uq_study_revision_jobs_one_graph_per_scope",
            "study_revision_id",
            "scope",
            unique=True,
            postgresql_where=text("role = 'evaluate' AND is_rerun = false"),
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    study_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("study_revisions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    is_rerun: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
