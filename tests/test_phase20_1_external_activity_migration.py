"""Phase 20.1 migration 0025 tests: external_broker_activity (EXT-01, D-10, S-4).

The table stores verified external broker snapshots as immutable evidence: no strategy
column, no foreign key to anything but the originating Job, closed sets and the hash
shape enforced by the database, uniqueness on (broker_order_id, content_hash) and a
trigger that rejects every UPDATE (except the FK's job_id null-out) and every DELETE.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.script import ScriptDirectory
from scripts.migrate import build_alembic_config
from tests.support.migrated_db import migrated_database

from trading_platform.core.settings import clear_settings_cache, load_settings
from trading_platform.db.models import ExternalBrokerActivity, Job
from trading_platform.db.session import clear_engine_cache, get_engine, session_scope

REVISION = "0025_phase20_1_external_broker_activity"
PREVIOUS_REVISION = "0024_phase20_1_account_reconciliation_runs"
TABLE = "external_broker_activity"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _fresh_caches() -> None:
    clear_settings_cache()
    clear_engine_cache()


@pytest.fixture()
def migrated_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "phase20_1_ext_activity") as name:
        yield name


def _insert(
    *,
    broker_order_id: str = "ord-1",
    content_hash: str = HASH_A,
    side: str = "buy",
    origin_tag: str = "external_format",
    reason: str = "manual trade at the broker",
    job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    with session_scope(load_settings()) as session:
        session.execute(
            text(
                f"INSERT INTO {TABLE} (id, job_id, broker_order_id, symbol, side, status, "
                "filled_qty, origin_tag, reason, order_snapshot, fills, content_hash) "
                "VALUES (:id, :job_id, :broker_order_id, 'AAPL', :side, 'filled', 10, "
                ":origin_tag, :reason, '{}', '[]', :content_hash)"
            ),
            {
                "id": row_id,
                "job_id": job_id,
                "broker_order_id": broker_order_id,
                "side": side,
                "origin_tag": origin_tag,
                "reason": reason,
                "content_hash": content_hash,
            },
        )
    return row_id


def _create_job() -> uuid.UUID:
    with session_scope(load_settings()) as session:
        job = Job(job_type="record-external-activity", payload={})
        session.add(job)
        session.flush()
        return job.id


def test_chain_is_linear_and_0025_follows_0024(migrated_db: str) -> None:
    script = ScriptDirectory.from_config(build_alembic_config())
    assert len(script.get_heads()) == 1
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PREVIOUS_REVISION
    assert REVISION in {r.revision for r in script.walk_revisions()}


def test_schema_has_no_strategy_column_and_only_a_job_foreign_key(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert "strategy_id" not in columns
    assert not [name for name in columns if "strategy" in name]
    assert set(columns) == {
        "id",
        "job_id",
        "broker_order_id",
        "client_order_id",
        "symbol",
        "side",
        "status",
        "qty",
        "filled_qty",
        "filled_avg_price",
        "broker_created_at",
        "successor_broker_order_id",
        "origin_tag",
        "reason",
        "order_snapshot",
        "fills",
        "content_hash",
        "created_at",
    }
    assert columns["job_id"]["nullable"]
    for required in ("broker_order_id", "symbol", "side", "status", "filled_qty", "reason"):
        assert not columns[required]["nullable"], required
    for required in ("order_snapshot", "fills", "content_hash", "origin_tag", "created_at"):
        assert not columns[required]["nullable"], required
    foreign_keys = inspector.get_foreign_keys(TABLE)
    assert {fk["referred_table"] for fk in foreign_keys} == {"jobs"}
    assert all(fk["options"].get("ondelete") == "SET NULL" for fk in foreign_keys)


@pytest.mark.parametrize(
    ("kwargs", "constraint"),
    [
        ({"origin_tag": "status_unmapped"}, "ck_external_broker_activity_origin_tag"),
        ({"origin_tag": ""}, "ck_external_broker_activity_origin_tag"),
        ({"side": "short"}, "ck_external_broker_activity_side"),
        ({"side": "BUY"}, "ck_external_broker_activity_side"),
        ({"reason": ""}, "ck_external_broker_activity_reason_not_blank"),
        ({"reason": "   "}, "ck_external_broker_activity_reason_not_blank"),
        ({"content_hash": "A" * 64}, "ck_external_broker_activity_content_hash_format"),
        ({"content_hash": "a" * 63}, "ck_external_broker_activity_content_hash_format"),
        ({"content_hash": "g" * 64}, "ck_external_broker_activity_content_hash_format"),
    ],
)
def test_closed_sets_and_shape_are_enforced_by_the_database(
    migrated_db: str, kwargs: dict[str, str], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _insert(**kwargs)


def test_closed_sets_accept_every_member(migrated_db: str) -> None:
    for index, origin in enumerate(
        ("external_format", "platform_format_unverified", "replaced_by_successor")
    ):
        _insert(broker_order_id=f"ord-{index}", origin_tag=origin)
    _insert(broker_order_id="ord-sell", side="sell")


def test_unique_order_and_hash_pair(migrated_db: str) -> None:
    _insert(broker_order_id="ord-1", content_hash=HASH_A)
    with pytest.raises(IntegrityError, match="uq_external_broker_activity_order_hash"):
        _insert(broker_order_id="ord-1", content_hash=HASH_A)
    # The same order with a different hash is a second row; the same hash on another order too.
    _insert(broker_order_id="ord-1", content_hash=HASH_B)
    _insert(broker_order_id="ord-2", content_hash=HASH_A)


@pytest.mark.parametrize(
    "assignment",
    [
        "reason = 'edited'",
        "content_hash = repeat('c', 64)",
        "filled_qty = 99",
        "symbol = 'MSFT'",
        "order_snapshot = '{\"x\": 1}'::json",
        "fills = '[1]'::json",
        "created_at = now() + interval '1 day'",
        "broker_order_id = 'other'",
    ],
)
def test_update_of_any_column_is_rejected(migrated_db: str, assignment: str) -> None:
    job_id = _create_job()
    row_id = _insert(job_id=job_id)
    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope(load_settings()) as session:
            session.execute(text(f"UPDATE {TABLE} SET {assignment} WHERE id = :id"), {"id": row_id})


def test_update_of_job_id_to_a_non_null_value_is_rejected(migrated_db: str) -> None:
    first = _create_job()
    second = _create_job()
    row_id = _insert(job_id=first)
    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope(load_settings()) as session:
            session.execute(
                text(f"UPDATE {TABLE} SET job_id = :job WHERE id = :id"),
                {"job": second, "id": row_id},
            )
    unlinked = _insert(broker_order_id="ord-2")
    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope(load_settings()) as session:
            session.execute(
                text(f"UPDATE {TABLE} SET job_id = :job WHERE id = :id"),
                {"job": first, "id": unlinked},
            )


def test_delete_is_rejected(migrated_db: str) -> None:
    row_id = _insert()
    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope(load_settings()) as session:
            session.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": row_id})
    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope(load_settings()) as session:
            session.execute(text(f"DELETE FROM {TABLE}"))
    with session_scope(load_settings()) as session:
        assert session.get(ExternalBrokerActivity, row_id) is not None


def test_job_delete_sets_job_id_to_null_and_nothing_else(migrated_db: str) -> None:
    job_id = _create_job()
    row_id = _insert(job_id=job_id)
    with session_scope(load_settings()) as session:
        before = session.get(ExternalBrokerActivity, row_id)
        assert before is not None
        snapshot = (before.reason, before.content_hash, before.created_at, before.job_id)
    assert snapshot[3] == job_id
    with session_scope(load_settings()) as session:
        session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    with session_scope(load_settings()) as session:
        row = session.get(ExternalBrokerActivity, row_id)
        assert row is not None
        assert row.job_id is None
        assert (row.reason, row.content_hash, row.created_at) == snapshot[:3]


def test_indexes_exist(migrated_db: str) -> None:
    inspector = inspect(get_engine(load_settings()))
    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes(TABLE)}
    assert indexes["ix_external_broker_activity_job_id"] == ["job_id"]
    assert indexes["ix_external_broker_activity_broker_order_id"] == ["broker_order_id"]
    assert indexes["ix_external_broker_activity_broker_order_id_created_at"] == [
        "broker_order_id",
        "created_at",
    ]


def test_downgrade_drops_table_function_and_reupgrade_restores_them(migrated_db: str) -> None:
    _fresh_caches()
    command.downgrade(build_alembic_config(), PREVIOUS_REVISION)
    _fresh_caches()
    assert TABLE not in inspect(get_engine(load_settings())).get_table_names()
    with session_scope(load_settings()) as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        functions = session.execute(
            text("SELECT proname FROM pg_proc WHERE proname = 'external_broker_activity_immutable'")
        ).all()
    assert version == PREVIOUS_REVISION
    assert functions == []

    _fresh_caches()
    command.upgrade(build_alembic_config(), "head")
    _fresh_caches()
    assert TABLE in inspect(get_engine(load_settings())).get_table_names()
    with pytest.raises(DBAPIError, match="immutable"):
        row_id = _insert()
        with session_scope(load_settings()) as session:
            session.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": row_id})


def test_orm_metadata_matches_migration(migrated_db: str) -> None:
    table = ExternalBrokerActivity.__table__
    inspector = inspect(get_engine(load_settings()))
    db_columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert set(db_columns) == {c.name for c in table.columns}
    for column in table.columns:
        assert db_columns[column.name]["nullable"] == column.nullable, column.name
    db_checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
    orm_checks = {
        str(c.name) for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    }
    assert db_checks == orm_checks
    assert len(db_checks) == 4
    db_uniques = {u["name"] for u in inspector.get_unique_constraints(TABLE)}
    orm_uniques = {
        str(c.name) for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"
    }
    assert db_uniques == orm_uniques == {"uq_external_broker_activity_order_hash"}
    db_indexes = {i["name"] for i in inspector.get_indexes(TABLE)}
    assert {str(i.name) for i in table.indexes} <= db_indexes

    with session_scope(load_settings()) as session:
        session.add(
            ExternalBrokerActivity(
                broker_order_id="ord-orm",
                symbol="AAPL",
                side="sell",
                status="filled",
                filled_qty=10,
                origin_tag="external_format",
                reason="orm round trip",
                order_snapshot={"id": "ord-orm"},
                fills=[],
                content_hash=HASH_A,
            )
        )
        session.flush()
        stored = session.execute(select(ExternalBrokerActivity)).scalar_one()
        assert stored.job_id is None
        assert stored.created_at is not None
