"""Pins the mandatory pre-UAT inventory query of D-G1-A (20.1-37).

Step 0 of ``20.1-HUMAN-UAT.md`` makes a read-only inventory of non-terminal orders mandatory before
the real-stack UAT: the dev database was last at revision 0021, before the attempt log (0023) and
the operation intents (0027) exist, so every pre-existing non-terminal order there is a legacy
order. The query lives in that document between two marker comment lines; this test reads it from
there, so the documented text and the tested text cannot drift apart.

It pins that the query is one read-only SELECT over three tables (all created at or before
revision 0021, unlike the attempt log and the operation intents), that every column it reads is one
verified to exist at revision 0021, that its status list is exactly the non-terminal order states,
and that on a throwaway database inside a READ ONLY transaction it returns exactly the
non-terminal orders of every strategy.

Nothing here touches the configured database: the DB-backed test provisions its own throwaway
database through ``migrated_database`` and the query is never run anywhere else.
"""

from __future__ import annotations

import re
import sys
import textwrap
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support.migrated_db import migrated_database
from tests.support.recovery_fixtures import OTHER, OWNER, seed_intent, seed_paper_run

from trading_platform.db.models import OrderLifecycleState
from trading_platform.db.session import get_engine, session_scope
from trading_platform.services.execution.transition import _LEGAL_TRANSITIONS

HUMAN_UAT = (
    Path(__file__).resolve().parents[1]
    / ".planning"
    / "phases"
    / "20.1-operator-state-correctness"
    / "20.1-HUMAN-UAT.md"
)
START_MARKER = "<!-- uat-inventory-sql:start -->"
END_MARKER = "<!-- uat-inventory-sql:end -->"

TERMINAL_STATES = frozenset({"filled", "canceled", "rejected", "expired"})
NON_TERMINAL_STATES = frozenset(state.value for state in OrderLifecycleState) - TERMINAL_STATES

# Orders the broker has acknowledged carry a broker id; the pre-send and unknown ones do not.
BROKER_SIDE_STATES = frozenset(
    {
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELED,
        OrderLifecycleState.EXPIRED,
    }
)

# A read-only inventory has no business with any of these (INTO: SELECT ... INTO creates a table).
FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "ALTER",
    "DROP",
    "CREATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "MERGE",
    "INTO",
)

# Every column the query reads, with the revision that adds it (verified against alembic/versions;
# 0021_phase20_operations_safety is the revision the dev database was last at):
#   paper_orders:  id, strategy_run_id, client_order_id, broker_order_id, status, created_at: 0008;
#                  submission_attempt_count: 0010; status becomes the order_lifecycle_state enum in
#                  0013 (hence the ::text casts in the query, which hold at any revision)
#   strategy_runs: id, strategy_id, run_type: 0001 (run_type is an enum); job_id: 0020
#   strategies:    id, strategy_id: 0001
COLUMNS_AT_REVISION_0021: dict[str, frozenset[str]] = {
    "paper_orders": frozenset(
        {
            "id",
            "strategy_run_id",
            "client_order_id",
            "broker_order_id",
            "status",
            "created_at",
            "submission_attempt_count",
        }
    ),
    "strategy_runs": frozenset({"id", "strategy_id", "run_type", "job_id"}),
    "strategies": frozenset({"id", "strategy_id"}),
}

DOCUMENTED_COLUMNS = (
    "strategy",
    "paper_order_id",
    "client_order_id",
    "status",
    "broker_order_id",
    "submission_attempt_count",
    "created_at",
    "origin_run_type",
    "origin_job_id",
)


def _inventory_sql() -> str:
    """The query exactly as documented: the text between the two marker lines, fence stripped."""

    document = HUMAN_UAT.read_text(encoding="utf-8")
    assert document.count("uat-inventory-sql:start") == 1, "start marker must appear exactly once"
    assert document.count("uat-inventory-sql:end") == 1, "end marker must appear exactly once"
    lines = document.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == START_MARKER]
    ends = [index for index, line in enumerate(lines) if line.strip() == END_MARKER]
    assert len(starts) == 1 and len(ends) == 1, "each marker must be a line of its own"
    assert starts[0] < ends[0], "the start marker must come before the end marker"
    block = lines[starts[0] + 1 : ends[0]]
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    assert len(block) >= 3 and block[0].strip() == "```sql" and block[-1].strip() == "```", (
        "the query must sit inside a ```sql fence between the markers"
    )
    return textwrap.dedent("\n".join(block[1:-1])).strip()


def test_inventory_is_one_read_only_select() -> None:
    sql = _inventory_sql()

    assert sql.upper().startswith("SELECT"), sql
    for keyword in FORBIDDEN_KEYWORDS:
        assert not re.search(rf"\b{keyword}\b", sql, flags=re.IGNORECASE), (
            f"write keyword {keyword} in the inventory query"
        )
    assert "--" not in sql and "/*" not in sql, "no SQL comments in the inventory query"
    assert sql.count(";") == 1 and sql.endswith(";"), "exactly one statement, one terminating ';'"


def test_inventory_reads_only_the_order_tables() -> None:
    sql = _inventory_sql()

    sources = re.findall(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)",
        sql,
        flags=re.IGNORECASE,
    )
    # Every FROM / JOIN is a plain "table alias" source (no subquery, no schema-qualified name).
    assert len(sources) == len(re.findall(r"\b(?:FROM|JOIN)\b", sql, flags=re.IGNORECASE))
    assert {table for table, _alias in sources} == {"paper_orders", "strategy_runs", "strategies"}

    # Every alias.column the query reads is a column verified to exist at revision 0021.
    alias_to_table = {alias: table for table, alias in sources}
    referenced = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b", sql))
    assert referenced, "the query must read qualified columns"
    for alias, column in sorted(referenced):
        assert alias in alias_to_table, f"unknown alias {alias!r}"
        assert column in COLUMNS_AT_REVISION_0021[alias_to_table[alias]], (
            f"{alias_to_table[alias]}.{column} is not a verified revision-0021 column"
        )


def test_inventory_status_list_is_exactly_the_non_terminal_states() -> None:
    sql = _inventory_sql()

    literals = re.findall(r"'([^']*)'", sql)
    assert len(literals) == len(set(literals)), "a status is listed twice"
    assert set(literals) == NON_TERMINAL_STATES
    assert set(literals) == {state.value for state in OrderLifecycleState} - {
        "filled",
        "canceled",
        "rejected",
        "expired",
    }

    # The terminal set used above is the product's own: the states whose legal transitions lead
    # nowhere but back to themselves (the legacy self-import) in the order state machine.
    product_terminal = {
        state.value
        for state, edges in _LEGAL_TRANSITIONS.items()
        if all(target == state for target in edges.values())
    }
    assert product_terminal == TERMINAL_STATES


@pytest.fixture()
def inventory_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with migrated_database(monkeypatch, "uat_inventory") as name:
        yield name


def _seed_one_order_per_state() -> dict[str, dict[OrderLifecycleState, uuid.UUID]]:
    """INSERT-only: one order for every lifecycle state, for each of two strategies.

    A distinct ticker per order and a distinct broker id per broker-side order keep every unique
    index out of the way; ``created_at`` is never overridden (that would be a post-insert UPDATE).
    """

    seeded: dict[str, dict[OrderLifecycleState, uuid.UUID]] = {OWNER: {}, OTHER: {}}
    with session_scope() as session:
        for public_id, prefix in ((OWNER, "OWN"), (OTHER, "OTH")):
            run = seed_paper_run(session, None, public_id)
            for index, state in enumerate(OrderLifecycleState):
                order = seed_intent(
                    session,
                    run,
                    status=state,
                    attempts=(),
                    broker_order_id=(
                        f"{prefix.lower()}-{state.value}" if state in BROKER_SIDE_STATES else None
                    ),
                    ticker=f"{prefix}{index}",
                )
                seeded[public_id][state] = order.id
    return seeded


def test_inventory_lists_exactly_the_non_terminal_orders(inventory_db: str) -> None:
    sql = _inventory_sql()
    seeded = _seed_one_order_per_state()
    assert all(len(per_strategy) == len(OrderLifecycleState) for per_strategy in seeded.values())

    engine = get_engine()
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        # The read-only claim is itself under test: the session really is read-only.
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        rows: list[dict[str, Any]] = [
            dict(row) for row in connection.execute(text(sql)).mappings().all()
        ]
        connection.rollback()

    expected = {
        order_id
        for per_strategy in seeded.values()
        for state, order_id in per_strategy.items()
        if state.value in NON_TERMINAL_STATES
    }
    excluded = {
        order_id
        for per_strategy in seeded.values()
        for state, order_id in per_strategy.items()
        if state.value in TERMINAL_STATES
    }
    assert expected and excluded, "the arrangement must hold both kinds of order"

    returned = [row["paper_order_id"] for row in rows]
    assert len(returned) == len(set(returned)), "an order is listed twice"
    assert set(returned) == expected
    assert not set(returned) & excluded, "a terminal order is listed"

    owner_of = {
        order_id: public_id for public_id, per in seeded.items() for order_id in per.values()
    }
    state_of = {order_id: state for per in seeded.values() for state, order_id in per.items()}
    # Both strategies appear (the result is not the owner's orders only), each with its own orders.
    assert {row["strategy"] for row in rows} == {OWNER, OTHER}
    for row in rows:
        order_id = row["paper_order_id"]
        assert row["strategy"] == owner_of[order_id]
        assert row["status"] == state_of[order_id].value
        assert row["origin_run_type"] == "paper_execution"
        assert row["origin_job_id"] is None
        assert row["submission_attempt_count"] == 0
        if state_of[order_id] in BROKER_SIDE_STATES:
            assert row["broker_order_id"], "a broker-side order carries its broker id"
        else:
            assert row["broker_order_id"] is None

    assert tuple(rows[0]) == DOCUMENTED_COLUMNS
    # The documented ORDER BY: deterministic output (created_at ties broken by the order id).
    assert rows == sorted(rows, key=lambda row: (row["created_at"], row["paper_order_id"]))
