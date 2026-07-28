"""Dataset serving-access guard for classification and retained principal contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


class DatasetClassificationPolicy(Protocol):
    def require_dataset_classification(self, ctx: RequestContext, classification: object) -> None: ...


def require_dataset_serving_access(
    *,
    ctx: RequestContext,
    policy: DatasetClassificationPolicy,
    classification: object,
    transaction_metadata: Mapping[str, object],
    version_id: str,
) -> None:
    """Fail closed when classification or unresolved principal membership denies access."""

    policy.require_dataset_classification(ctx, classification)
    contract = transaction_metadata.get("securityContract")
    if not isinstance(contract, Mapping):
        return
    principals = contract.get("allowedPrincipalSetIds")
    has_principals = isinstance(principals, (list, tuple)) and bool(principals)
    if not has_principals or ctx.has_role("admin"):
        return
    raise PermissionDenied(
        "dataset principal-set membership resolver is unavailable",
        details={
            "datasetVersionId": version_id,
            "enforcement": "admin_only_without_resolver",
        },
    )
