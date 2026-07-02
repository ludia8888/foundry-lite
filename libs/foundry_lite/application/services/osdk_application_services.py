"""Developer Console OSDK application service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.osdk_application_clients import OsdkApplicationClientService
from foundry_lite.application.services.osdk_application_idempotency import OsdkApplicationIdempotencyService
from foundry_lite.application.services.osdk_application_scope import OsdkApplicationScopeService
from foundry_lite.application.services.osdk_application_sdk import OsdkApplicationSdkService
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService


@dataclass(frozen=True)
class OsdkApplicationServices:
    """OSDK app scope, client, SDK lifecycle services plus compatibility entrypoint."""

    client: OsdkApplicationClientService
    entrypoint: OsdkApplicationService
    idempotency: OsdkApplicationIdempotencyService
    scope: OsdkApplicationScopeService
    sdk: OsdkApplicationSdkService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> OsdkApplicationServices:
        return cls(
            client=build_service(OsdkApplicationClientService, dependencies),
            entrypoint=build_service(OsdkApplicationService, dependencies),
            idempotency=build_service(OsdkApplicationIdempotencyService, dependencies),
            scope=build_service(OsdkApplicationScopeService, dependencies),
            sdk=build_service(OsdkApplicationSdkService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.client, self.entrypoint, self.idempotency, self.scope, self.sdk)
