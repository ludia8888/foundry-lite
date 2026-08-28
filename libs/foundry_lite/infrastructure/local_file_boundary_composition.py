"""Compose local filesystem adapters behind application-owned ports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.dependencies import (
    MediaSourceWorkspace,
    OntologyDefinitionReader,
    OsdkDownloadTokenSigner,
    OsdkReleaseArtifactStore,
    SourceUploadStagingStore,
    TransformSourceStore,
)
from foundry_lite.infrastructure.adapters.local_media_source_workspace import LocalMediaSourceWorkspace
from foundry_lite.infrastructure.adapters.local_ontology_definition_reader import (
    LocalOntologyDefinitionReader,
)
from foundry_lite.infrastructure.adapters.local_osdk_download_token_signer import (
    LocalOsdkDownloadTokenSigner,
)
from foundry_lite.infrastructure.adapters.local_osdk_release_artifact_store import (
    LocalOsdkReleaseArtifactStore,
)
from foundry_lite.infrastructure.adapters.local_source_upload_staging_store import (
    LocalSourceUploadStagingStore,
)
from foundry_lite.infrastructure.adapters.local_transform_source_store import (
    LocalTransformSourceStore,
)


@dataclass(frozen=True)
class LocalFileBoundaryAdapters:
    """Filesystem-backed adapters assembled once for one runtime root."""

    media_source_workspace: MediaSourceWorkspace
    ontology_definition_reader: OntologyDefinitionReader
    osdk_download_token_signer: OsdkDownloadTokenSigner
    osdk_release_artifact_store: OsdkReleaseArtifactStore
    source_upload_staging_store: SourceUploadStagingStore
    transform_source_store: TransformSourceStore


def local_file_boundary_adapters(root: Path) -> LocalFileBoundaryAdapters:
    """Build the local file adapters without leaking their implementations upward."""

    return LocalFileBoundaryAdapters(
        media_source_workspace=LocalMediaSourceWorkspace(root),
        ontology_definition_reader=LocalOntologyDefinitionReader(),
        osdk_download_token_signer=LocalOsdkDownloadTokenSigner(root / "osdk-download-token-secret.key"),
        osdk_release_artifact_store=LocalOsdkReleaseArtifactStore(root / "osdk-artifacts"),
        source_upload_staging_store=LocalSourceUploadStagingStore(root),
        transform_source_store=LocalTransformSourceStore(root),
    )
