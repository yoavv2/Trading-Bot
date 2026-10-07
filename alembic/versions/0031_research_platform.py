"""Research platform foundation (plan ``01-IMPLEMENTATION-PLAN.md`` S0, data model section 6).

Applies only to research databases (``trading_research`` and throwaway test databases):
the main trading database is deliberately left at 0021 and is never upgraded by this work.

Adds, in dependency order:
* ``ai_drafts``, ``strategy_versions`` (append-only: trigger refuses UPDATE and DELETE),
  ``strategy_drafts``, ``asset_catalog``, ``asset_lists`` / ``asset_list_items``,
  ``data_freezes``, ``research_studies``, ``study_revisions``, ``research_freezes``,
  ``test_window_exposures``;
* ``research_run_links`` (1:1 research linkage of runs; the shared ``strategy_runs`` table and
  ORM model are untouched);
* ``backtest_trades.rounding_slack`` (nullable);
* ``daily_bars.volume`` widened to BIGINT (adjusted volume multiplies raw volume by every
  later split factor) and nullable ``split_factor`` / ``dividend_cash`` factor columns.

Migrations 0022-0030 are unchanged. No trigger, function or guard installed by them is
touched. Phase 21's frozen plan also named ``0031``; it renumbers if it ever resumes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0031_research_platform"
down_revision = "0030_phase20_1_evidence_update_guards"
branch_labels = None
depends_on = None

VERSION_GUARD_FUNCTION = "research_strategy_versions_append_only"
VERSION_GUARD_TRIGGER = "trg_strategy_versions_append_only"


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "ai_drafts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("output_spec_sha256", sa.String(64), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(48), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "strategy_versions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("strategy_id", _uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("yaml_text", sa.Text(), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_sha256", sa.String(64), nullable=False),
        sa.Column("history_required", sa.Integer(), nullable=False),
        sa.Column("history_minimum", sa.Integer(), nullable=False),
        sa.Column("scale_class", sa.String(32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column(
            "parent_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ai_draft_id", _uuid(), sa.ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("behaviour_differs_from_original", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "strategy_id", "version_no", name="uq_strategy_versions_strategy_id_version_no"
        ),
    )
    op.create_index("ix_strategy_versions_spec_sha256", "strategy_versions", ["spec_sha256"])

    # Append-only guard: an approved version is immutable. Ordinary DML only (the
    # table-owning role can still disable the trigger, as with the 0028-0030 guards).
    op.execute(
        f"""
        CREATE FUNCTION {VERSION_GUARD_FUNCTION}() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '23000',
                MESSAGE = 'strategy_versions is append-only: approved versions are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {VERSION_GUARD_TRIGGER}
        BEFORE UPDATE OR DELETE ON strategy_versions
        FOR EACH ROW EXECUTE FUNCTION {VERSION_GUARD_FUNCTION}();
        """
    )

    op.create_table(
        "strategy_drafts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("yaml_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column(
            "ai_draft_id", _uuid(), sa.ForeignKey("ai_drafts.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "parent_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
    )

    op.create_table(
        "asset_catalog",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(32), nullable=True),
        sa.Column("asset_type", sa.String(32), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("catalog_start", sa.Date(), nullable=True),
        sa.Column("catalog_end", sa.Date(), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("name_source", sa.String(32), nullable=True),
        sa.Column("names_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("provider", "ticker", name="uq_asset_catalog_provider_ticker"),
    )
    op.create_index("ix_asset_catalog_name", "asset_catalog", ["name"])

    op.create_table(
        "asset_lists",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        *_timestamps(),
    )
    op.create_table(
        "asset_list_items",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "list_id", _uuid(), sa.ForeignKey("asset_lists.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("list_id", "ticker", name="uq_asset_list_items_list_id_ticker"),
    )

    op.create_table(
        "data_freezes",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("assets", sa.JSON(), nullable=False),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("calendar_start", sa.Date(), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("integrity_summary", sa.JSON(), nullable=False),
        sa.Column("inputs_path", sa.Text(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
    )

    op.create_table(
        "research_studies",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="substantive"),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('smoke', 'substantive')", name="kind_closed"),
    )
    op.create_table(
        "study_revisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "study_id",
            _uuid(),
            sa.ForeignKey("research_studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("ranking_criteria_hash", sa.String(64), nullable=True),
        sa.Column(
            "data_freeze_id",
            _uuid(),
            sa.ForeignKey("data_freezes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("study_id", "revision_no", name="uq_study_revisions_study_id_revision_no"),
    )
    op.create_table(
        "research_freezes",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "study_revision_id",
            _uuid(),
            sa.ForeignKey("study_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("acceptance_json", sa.JSON(), nullable=False),
        sa.Column("ranking_criteria_hash", sa.String(64), nullable=False),
        sa.Column("code_sha", sa.String(48), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("calendar_start", sa.Date(), nullable=True),
        sa.Column("co_leading_choice_reason", sa.Text(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("study_revision_id", name="uq_research_freezes_study_revision_id"),
    )

    # Research linkage of runs lives in its own table: the shared ``strategy_runs`` table
    # and ORM model stay untouched (historical-revision tests and the 0029/0030 guards are
    # unaffected). One non-rerun final-test run per (revision, version, asset).
    op.create_table(
        "research_run_links",
        sa.Column(
            "run_id",
            _uuid(),
            sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "study_revision_id",
            _uuid(),
            sa.ForeignKey("study_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("asset", sa.String(20), nullable=True),
        sa.Column("window_role", sa.String(16), nullable=True),
        sa.Column("spec_sha256", sa.String(64), nullable=True),
        sa.Column("code_sha", sa.String(48), nullable=True),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column(
            "rerun_of", _uuid(), sa.ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_research_run_links_final_test_once",
        "research_run_links",
        ["study_revision_id", "strategy_version_id", "asset"],
        unique=True,
        postgresql_where=sa.text("window_role = 'final_test' AND rerun_of IS NULL"),
    )

    op.create_table(
        "test_window_exposures",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column(
            "study_id", _uuid(), sa.ForeignKey("research_studies.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "study_revision_id",
            _uuid(),
            sa.ForeignKey("study_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("strategy_id", _uuid(), nullable=True),
        sa.Column(
            "strategy_version_id",
            _uuid(),
            sa.ForeignKey("strategy_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "run_id", _uuid(), sa.ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("is_rerun", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("run_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("results_inspected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_test_window_exposures_asset_range_start_range_end",
        "test_window_exposures",
        ["asset", "range_start", "range_end"],
    )

    op.add_column(
        "backtest_trades",
        sa.Column("rounding_slack", sa.Numeric(precision=20, scale=6), nullable=True),
    )

    op.alter_column(
        "daily_bars",
        "volume",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.add_column(
        "daily_bars", sa.Column("split_factor", sa.Numeric(precision=20, scale=8), nullable=True)
    )
    op.add_column(
        "daily_bars", sa.Column("dividend_cash", sa.Numeric(precision=20, scale=8), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("daily_bars", "dividend_cash")
    op.drop_column("daily_bars", "split_factor")
    op.alter_column(
        "daily_bars",
        "volume",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.drop_column("backtest_trades", "rounding_slack")
    op.drop_index(
        "ix_test_window_exposures_asset_range_start_range_end", table_name="test_window_exposures"
    )
    op.drop_table("test_window_exposures")
    op.drop_index("uq_research_run_links_final_test_once", table_name="research_run_links")
    op.drop_table("research_run_links")
    op.drop_table("research_freezes")
    op.drop_table("study_revisions")
    op.drop_table("research_studies")
    op.drop_table("data_freezes")
    op.drop_table("asset_list_items")
    op.drop_table("asset_lists")
    op.drop_index("ix_asset_catalog_name", table_name="asset_catalog")
    op.drop_table("asset_catalog")
    op.drop_table("strategy_drafts")
    op.execute(f"DROP TRIGGER IF EXISTS {VERSION_GUARD_TRIGGER} ON strategy_versions")
    op.execute(f"DROP FUNCTION IF EXISTS {VERSION_GUARD_FUNCTION}()")
    op.drop_index("ix_strategy_versions_spec_sha256", table_name="strategy_versions")
    op.drop_table("strategy_versions")
    op.drop_table("ai_drafts")
