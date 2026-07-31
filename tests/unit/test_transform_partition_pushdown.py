from __future__ import annotations

from foundry_lite.application.services.transform_partition_pushdown import infer_sql_partition_filters


def test_sql_partition_pushdown_infers_single_input_where_equality() -> None:
    filters = infer_sql_partition_filters(
        "select id from {{ input('raw.orders') }} where bucket = 'b' and region = 'west'"
    )

    assert filters == {"raw.orders": {"bucket": "b", "region": "west"}}


def test_sql_partition_pushdown_supports_input_alias_and_quoted_identifier() -> None:
    filters = infer_sql_partition_filters("select o.id from {{ input('raw.orders') }} as o where o.\"bucket\" = 'b'")

    assert filters == {"raw.orders": {"bucket": "b"}}


def test_sql_partition_pushdown_ignores_or_and_multi_input_queries() -> None:
    with_or = infer_sql_partition_filters(
        "select id from {{ input('raw.orders') }} where bucket = 'b' or region = 'west'"
    )
    multi_input = infer_sql_partition_filters(
        "select * from {{ input('raw.orders') }} join {{ input('raw.customers') }} on true where bucket = 'b'"
    )

    assert with_or == {}
    assert multi_input == {}


def test_sql_partition_pushdown_bails_on_self_join_repeated_input() -> None:
    """A self-join references the single input more than once, so no predicate is safe.

    De-duplicating the input refs made a self-join look like a single-input query,
    and the WHERE predicate was pushed down against the one dataset — pruning rows
    for the wrong side of the join and returning silently wrong results.
    """
    self_join = infer_sql_partition_filters(
        "select a.id from {{ input('raw.orders') }} a "
        "join {{ input('raw.orders') }} b on a.parent_id = b.id "
        "where a.bucket = 'b'"
    )

    assert self_join == {}
