from __future__ import annotations

import pytest
from foundry_lite.application.services.aip.fde_object_tools import (
    _link_types,
    _optional_filter,
)
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError


@pytest.mark.parametrize("value", [None, "OrderCustomer", ["OrderCustomer", ""]])
def test_fde_search_around_link_types_reject_non_list_and_empty_values(value: object) -> None:
    with pytest.raises(FdePlatformToolError, match="linkTypes"):
        _link_types(value)


def test_fde_search_around_filter_requires_an_object_when_present() -> None:
    assert _optional_filter(None) is None
    assert _optional_filter({"property": "status"}) == {"property": "status"}
    with pytest.raises(FdePlatformToolError, match="filter"):
        _optional_filter(["not-an-object"])
