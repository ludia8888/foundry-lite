"""Small dependency bundles shared by the application composition root."""

from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import (
    MetadataRepository,
    ObjectIndexRepository,
    ObjectIndexRowHashRepository,
    ObjectReadRepository,
    ObjectSetRepository,
    TransactionManager,
)
from foundry_lite.application.ports.destructive_development_admin import DestructiveDevelopmentAdmin
from foundry_lite.application.ports.mcp_rate_limiter import McpRateLimiter
from foundry_lite.application.ports.oauth_session_repository import OAuthSessionRepository, OAuthTokenIssuer
from foundry_lite.application.ports.osdk_application_repository import OsdkApplicationRepository
from foundry_lite.application.ports.osdk_download_token_signer import OsdkDownloadTokenSigner
from foundry_lite.application.ports.osdk_release_artifact_store import OsdkReleaseArtifactStore
from foundry_lite.application.ports.search_adapter import SearchAdapter
from foundry_lite.application.ports.secret_provider import SecretProvider, SecretVault
from foundry_lite.security.policy import PolicyService


@dataclass(frozen=True)
class PathDependencies:
    root: Path
    storage_root: Path


@dataclass(frozen=True)
class SecurityDependencies:
    engine: TransactionManager
    policy: PolicyService
    metadata_repository: MetadataRepository
    destructive_development_admin: DestructiveDevelopmentAdmin
    osdk_application_repository: OsdkApplicationRepository
    osdk_download_token_signer: OsdkDownloadTokenSigner
    osdk_release_artifact_store: OsdkReleaseArtifactStore
    oauth_session_repository: OAuthSessionRepository
    oauth_token_issuer: OAuthTokenIssuer
    secret_provider: SecretProvider
    secret_vault: SecretVault
    mcp_rate_limiter: McpRateLimiter


@dataclass(frozen=True)
class ObjectDependencies:
    object_index_repository: ObjectIndexRepository
    object_index_row_hash_repository: ObjectIndexRowHashRepository
    object_read_repository: ObjectReadRepository
    object_set_repository: ObjectSetRepository
    search_adapter: SearchAdapter


__all__ = ["ObjectDependencies", "PathDependencies", "SecurityDependencies"]
