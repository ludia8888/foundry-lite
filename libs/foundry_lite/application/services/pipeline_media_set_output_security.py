"""Security inheritance validation for Graph v2 Media Set outputs."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.media_derivative_repository import (
    MediaDerivativeRecord,
)
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
)
from foundry_lite.application.services.pipeline_media_set_output_contracts import (
    PipelineMediaDerivativeSecurityMismatch,
    PipelineMediaOutputSourceCorrupt,
)


def require_derivative_security_inheritance(
    source_version: MediaItemVersionRecord,
    derivative: MediaDerivativeRecord,
) -> None:
    """Fail closed when a derivative drops or changes any committed source control."""

    weakened_fields = sorted(
        key for key, value in source_version.security_envelope.items() if derivative.security_envelope.get(key) != value
    )
    if not weakened_fields:
        return
    raise PipelineMediaDerivativeSecurityMismatch(
        "pipeline Media Set output derivative security envelope weakened its committed source",
        details={
            "mediaItemVersionId": source_version.media_item_version_id,
            "mediaDerivativeId": derivative.media_derivative_id,
            "weakenedFields": weakened_fields,
        },
    )


def require_item_security(
    item: Mapping[str, object],
    expected: Mapping[str, object],
    source_id: str,
) -> None:
    actual = item.get("securityEnvelope")
    if isinstance(actual, Mapping) and dict(actual) == dict(expected):
        return
    raise PipelineMediaOutputSourceCorrupt(
        "pipeline media artifact security envelope does not match committed truth",
        details={"sourceId": source_id},
    )
