# Deferred Items — Phase 11 (Query Performance)

## From 11-03 (Query Index Usage / PERF-03)

**Broker-fill dedup query in `paper_execution.py` (`_ingest_paper_fills`, ~line 1747) reads the entire `paper_fills` table unconditionally, on every sync.**

```python
existing_fill_ids = set(session.execute(select(PaperFill.broker_fill_id)).scalars().all())
```

This is not an index gap: `paper_fills.broker_fill_id` already has a named unique
index (`uq_paper_fills_broker_fill_id`, from the model's `UniqueConstraint`).
EXPLAIN evidence (`tests/test_query_index_usage.py`, seeded to ~40k rows,
`enable_seqscan=off` forced comparison) shows a forced Index Only Scan over that
index costs ~2365 cost units versus ~1454 for the Seq Scan Postgres already
chooses by default — Postgres is *correctly* picking the Seq Scan for this query
shape, and no index addition changes that, because the query touches every row
in the table regardless of any index. Confirmed empirically before writing the
test, not assumed.

The actual performance problem is architectural, not indexing: this query grows
linearly with the *entire historical* fill count on every single sync call, when
it only needs to check membership for the current broker-reported fill batch
(typically a handful of rows per sync). The real fix is to scope the query to
only the fills relevant to the current sync, e.g.:

```python
broker_fill_ids = {f.broker_fill_id for f in broker_fills}
existing_fill_ids = set(
    session.execute(
        select(PaperFill.broker_fill_id).where(PaperFill.broker_fill_id.in_(broker_fill_ids))
    ).scalars().all()
)
```

This would make the query genuinely selective (turning the existing unique index
into a real win, `Index Only Scan` instead of `Seq Scan`, and making the runtime
independent of total historical fill count).

**Why not fixed in 11-03:** `paper_execution.py` is outside this plan's declared
`files_modified` scope (alembic migration + `db/models/paper_order.py` /
`paper_fill.py` / `position.py` + `tests/test_query_index_usage.py` only) --
PERF-03 authorizes adding indices, not rewriting queries. A follow-up plan should
make this query-shape change and add a regression test asserting the dedup query
scales with sync-batch size, not total historical fill count (e.g. a query-count
or EXPLAIN-selectivity assertion analogous to PERF-01's approach for paper
preflight).
