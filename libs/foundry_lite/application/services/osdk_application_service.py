"""Application service helpers for osdk application service workflows."""

from __future__ import annotations

from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_application_artifacts import (
    artifact_payload as artifact_payload,
)
from foundry_lite.application.services.osdk_application_artifacts import (
    write_release_artifact as write_release_artifact,
)
from foundry_lite.application.services.osdk_application_clients import OsdkApplicationClientMixin
from foundry_lite.application.services.osdk_application_idempotency import OsdkApplicationIdempotencyMixin
from foundry_lite.application.services.osdk_application_records import scope_for as scope_for
from foundry_lite.application.services.osdk_application_scope import OsdkApplicationScopeMixin
from foundry_lite.application.services.osdk_application_sdk import OsdkApplicationSdkMixin
from foundry_lite.application.services.osdk_application_types import OsdkRuntimeAuditBoundary


class OsdkApplicationService(
    OsdkApplicationScopeMixin,
    OsdkApplicationClientMixin,
    OsdkApplicationSdkMixin,
    OsdkApplicationIdempotencyMixin,
    CoreService,
):
    required_dependencies = ("engine", "policy", "osdk_application_repository", "root")
    required_collaborators = ("runtime_service",)
    runtime_service: OsdkRuntimeAuditBoundary
