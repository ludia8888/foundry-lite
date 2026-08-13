from __future__ import annotations

import pytest
from foundry_lite.application.ports.governed_release_delivery_config import (
    GovernedReleaseDeliveryConfig,
)
from foundry_lite.application.ports.source_control_release import SourceRepositoryRef


def _repository() -> SourceRepositoryRef:
    return SourceRepositoryRef(provider="github", repository_id=42, owner="example", name="foundry-lite")


def test_delivery_config_contract_derives_only_server_prefixed_safe_head_refs() -> None:
    config = GovernedReleaseDeliveryConfig(source_repository=_repository(), source_head_prefix="codex/")

    assert config.source_head_ref("release/orders-v2") == "codex/release/orders-v2"

    for unsafe in ("../main", "/main", "main.lock", "main@{1}", "main//next"):
        with pytest.raises(ValueError):
            config.source_head_ref(unsafe)


def test_delivery_config_contract_requires_every_protected_server_target() -> None:
    with pytest.raises(ValueError, match="source control"):
        GovernedReleaseDeliveryConfig(is_source_control_required=True)
    with pytest.raises(ValueError, match="deployment"):
        GovernedReleaseDeliveryConfig(is_deployment_required=True)
