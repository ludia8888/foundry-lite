from __future__ import annotations

import pytest

from scripts.operations.macmini_qa_guard import (
    ALLOWED_NAMESPACES,
    COLIMA_PROFILE,
    assert_namespace,
    assert_profile,
)


def test_macmini_qa_guard_allows_only_dedicated_namespaces() -> None:
    assert ALLOWED_NAMESPACES == {"foundry-qa", "foundry-qa-recovery"}
    for namespace in ALLOWED_NAMESPACES:
        assert_namespace(namespace)
    with pytest.raises(ValueError, match="namespace_not_allowed"):
        assert_namespace("default")


def test_macmini_qa_guard_allows_only_dedicated_colima_profile() -> None:
    assert_profile(COLIMA_PROFILE)
    with pytest.raises(ValueError, match="profile_not_allowed"):
        assert_profile("default")
