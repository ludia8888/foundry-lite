"""Narrow service bundle consumed by the Source facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from foundry_lite.application.services.source_connection_test_service import SourceConnectionTestService
    from foundry_lite.application.services.source_lifecycle_service import SourceLifecycleService
    from foundry_lite.application.services.source_management_service import (
        SourceCdcObjectIndexService,
        SourceManagementService,
    )
    from foundry_lite.application.services.source_onboarding_service import SourceOnboardingService
    from foundry_lite.application.services.source_scheduler_service import SourceSchedulerService


class SourceWorkspaceServices(Protocol):
    """Services directly delegated to by ``SourceWorkspace``."""

    @property
    def source_onboarding(self) -> SourceOnboardingService: ...

    @property
    def source_management(self) -> SourceManagementService: ...

    @property
    def source_lifecycle(self) -> SourceLifecycleService: ...

    @property
    def source_connection_test(self) -> SourceConnectionTestService: ...

    @property
    def source_scheduler(self) -> SourceSchedulerService: ...

    @property
    def source_cdc_object_index(self) -> SourceCdcObjectIndexService: ...
