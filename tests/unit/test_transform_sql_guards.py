from __future__ import annotations

import pytest
from foundry_lite.application.services.transform_sql_guards import validate_sql_transform_uses_declared_inputs
from foundry_lite.domain.errors import ValidationFailed


def test_sql_transform_guard_allows_declared_inputs() -> None:
    validate_sql_transform_uses_declared_inputs("select * from {{ input('raw.orders') }}")


def test_sql_transform_guard_blocks_raw_filesystem_reads() -> None:
    with pytest.raises(ValidationFailed) as exc_info:
        validate_sql_transform_uses_declared_inputs("select * from read_parquet('/tmp/orders.parquet')")

    assert exc_info.value.details["function"] == "read_parquet"


def test_sql_transform_guard_ignores_comments() -> None:
    validate_sql_transform_uses_declared_inputs(
        """
        -- read_csv('/tmp/commented.csv')
        /* read_json('/tmp/commented.json') */
        select * from {{ input('raw.orders') }}
        """
    )
