"""Thin facade entrypoints for operations console protocols workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import (
    ActionWritebackQueueResult,
    ActionWritebackRecoveryResult,
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    BackupRestoreRecoveryOverview,
)
from foundry_lite.application.services.outbox_publisher_service import OutboxPublishBatchResult
from foundry_lite.domain.context import RequestContext


class ActionWritebackReconciler(Protocol):
    def list_unresolved_action_writebacks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackQueueResult: ...

    def reconcile_action_writeback(
        self,
        writeback_id: str,
        *,
        remote_status: str | None = None,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]: ...

    def recover_action_writebacks(
        self,
        *,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> ActionWritebackRecoveryResult: ...

    def approve_action_writeback_recovery(
        self,
        writeback_id: str,
        *,
        approval_id: str,
        reason: str,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]: ...


class BackupRestoreReporter(Protocol):
    def create_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestoreArtifactReceipt: ...

    def restore_from_backup_artifact(
        self,
        *,
        ctx: RequestContext | None = None,
        artifact_ref: str,
        artifact_hash: str | None = None,
        restore_id: str | None = None,
        validation_id: str | None = None,
        should_run_post_restore_validation: bool = True,
    ) -> BackupRestoreArtifactRestoreReport: ...

    def execute_backup_artifact_restore(
        self,
        *,
        ctx: RequestContext | None = None,
        artifact_ref: str,
        artifact_hash: str | None = None,
        restore_id: str | None = None,
        validation_id: str | None = None,
        should_run_post_restore_validation: bool = True,
    ) -> BackupRestoreArtifactRestoreReport: ...

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport: ...

    def start_restore_mode(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
        restore_id: str | None = None,
    ) -> BackupRestoreModeReport: ...

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport: ...

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport: ...

    def run_post_restore_validation(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestorePostRestoreValidationReport: ...

    def recovery_overview(self, *, ctx: RequestContext | None = None) -> BackupRestoreRecoveryOverview: ...


class PromptArtifactReadPayload(Protocol):
    @property
    def artifact_id(self) -> str: ...

    @property
    def ai_run_id(self) -> str: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def export_marking(self) -> str: ...

    @property
    def plaintext(self) -> str: ...


class PromptArtifactReader(Protocol):
    def read_prompt_artifact(
        self, ctx: RequestContext, *, artifact_id: str, ai_run_id: str | None = None
    ) -> PromptArtifactReadPayload: ...


class OutboxPublisher(Protocol):
    def publish_pending_outbox(
        self,
        *,
        ctx: RequestContext | None = None,
        stream_name: str = "foundry-lite-outbox",
        limit: int = 100,
    ) -> OutboxPublishBatchResult: ...
