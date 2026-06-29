"""Backup/restore preflight and restore-mode contract types.

Extracted from ``runtime_repository`` to keep that port module under the
application module-size budget. The runtime repository re-imports these names,
so existing ``from ...runtime_repository import BackupRestore...`` call sites and
the ``ports`` aggregator re-export are unaffected.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, TypedDict

RuntimeJsonObject = Mapping[str, object]
BackupRestorePreflightStatus = Literal["ready", "blocked"]
BackupRestoreVersionStatus = Literal["valid", "missing", "corrupt"]
BackupRestoreModeStatus = Literal["inactive", "paused", "blocked", "resume_approved"]
BackupRestoreValidationStatus = Literal["passed", "blocked"]


class BackupRestoreManifestIssue(TypedDict):
    """Restore-blocking mismatch between metadata DB and committed storage."""

    code: str
    datasetRef: str
    datasetId: str
    versionId: str
    manifestUri: str
    message: str
    details: RuntimeJsonObject


class BackupRestoreDatasetVersion(TypedDict):
    """Committed dataset version inventory entry for restore preflight."""

    datasetRef: str
    datasetId: str
    branch: str
    versionId: str
    versionNumber: int
    manifestUri: str
    rowCount: int
    byteSize: int
    storageProfile: str
    manifestFileCount: int
    manifestRowCount: int
    manifestByteSize: int
    manifestContentHashes: list[str]
    status: BackupRestoreVersionStatus
    issue: BackupRestoreManifestIssue | None


class BackupRestoreIndexPointer(TypedDict):
    """Active object index pointer that restore must preserve or rebuild."""

    objectTypeId: str
    objectTypeApiName: str
    activeIndexVersion: str


class BackupRestorePreflightReport(TypedDict):
    """Read-only backup/restore commit-point compatibility report."""

    generatedAt: str
    tenantId: str
    backupId: str
    status: BackupRestorePreflightStatus
    datasetVersions: list[BackupRestoreDatasetVersion]
    issues: list[BackupRestoreManifestIssue]
    activeIndexPointers: list[BackupRestoreIndexPointer]
    highWatermarks: RuntimeJsonObject
    temporalStrategy: RuntimeJsonObject
    searchRebuild: RuntimeJsonObject
    restoreTrafficGate: RuntimeJsonObject
    summary: RuntimeJsonObject


class BackupRestorePreflightSummary(TypedDict):
    """Latest preflight audit summary for recovery console overviews."""

    generatedAt: str
    backupId: str
    status: str
    datasetVersionCount: int
    issueCount: int
    activeIndexPointerCount: int


class BackupRestoreModeReport(TypedDict):
    """Durable restore-mode traffic and publisher gate status."""

    generatedAt: str
    tenantId: str
    restoreId: str
    backupId: str
    status: BackupRestoreModeStatus
    preflightStatus: str
    is_write_traffic_paused: bool
    is_outbox_publisher_paused: bool
    is_serving_traffic_open: bool
    is_post_restore_validation_required: bool
    is_operator_approval_required: bool
    blockingIssueCount: int
    reason: str
    highWatermarks: RuntimeJsonObject
    summary: RuntimeJsonObject


class BackupRestorePostRestoreValidationReport(TypedDict):
    """Durable post-restore closed-loop validation evidence."""

    generatedAt: str
    tenantId: str
    restoreId: str
    backupId: str
    validationId: str
    status: BackupRestoreValidationStatus
    preflightStatus: str
    findings: list[RuntimeJsonObject]
    highWatermarks: RuntimeJsonObject
    summary: RuntimeJsonObject


class BackupRestorePostRestoreValidationSummary(TypedDict):
    """Latest post-restore validation audit summary for recovery overview."""

    generatedAt: str
    restoreId: str
    backupId: str
    validationId: str
    status: str
    findingCount: int


class BackupRestoreRecoveryOverview(TypedDict):
    """Single read model for Operations/Recovery Console status."""

    generatedAt: str
    tenantId: str
    activeRestoreMode: BackupRestoreModeReport | None
    latestRestoreMode: BackupRestoreModeReport | None
    latestPreflight: BackupRestorePreflightSummary | None
    latestPostRestoreValidation: BackupRestorePostRestoreValidationSummary | None
    restoreTrafficGate: RuntimeJsonObject
    requiredOperatorActions: list[str]
    summary: RuntimeJsonObject
