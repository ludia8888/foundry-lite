"""Environment-resolved external Governed Release infrastructure bundle."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from sqlalchemy.engine import Engine

from foundry_lite.application.dependency_release import (
    GovernedReleaseDependencies,
    GovernedReleaseLiveAuthority,
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    UnavailableInfrastructureDeploymentAdapter,
)
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.source_control_release import (
    SourceControlMergeMethod,
    SourceRepositoryRef,
    UnavailableSourceControlReleasePort,
)
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.infrastructure.adapters.github_release import GitHubReleaseAdapter, GitHubReleaseConfig
from foundry_lite.infrastructure.adapters.render_deployment import RenderInfrastructureDeploymentAdapter
from foundry_lite.infrastructure.repositories.governed_release_live_attestation_repository import (
    SqlAlchemyGovernedReleaseLiveAttestationRepository,
)
from foundry_lite.infrastructure.repositories.release_delivery_repository import (
    SqlAlchemyReleaseDeliveryRepository,
)

_SOURCE_REQUIRED_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REQUIRED"
_DEPLOYMENT_REQUIRED_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_REQUIRED"
_GITHUB_REPOSITORY_ID_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID"
_GITHUB_OWNER_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_OWNER"
_GITHUB_REPOSITORY_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY"
_GITHUB_BASE_REF_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_BASE_REF"
_GITHUB_HEAD_PREFIX_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_HEAD_PREFIX"
_GITHUB_TOKEN_REF_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF"  # nosec B105 - env name, not a secret.
_GITHUB_MERGE_METHOD_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_MERGE_METHOD"
_GITHUB_MINIMUM_APPROVALS_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_MINIMUM_APPROVALS"
_GITHUB_BYPASS_POLICY_VERIFIED_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_BYPASS_POLICY_VERIFIED"
_RENDER_SERVICE_ID_ENV = "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID"
_RENDER_TOKEN_REF_ENV = "FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF"  # nosec B105 - env name, not a secret.
_RENDER_ENVIRONMENT_ENV = "FOUNDRY_LITE_RENDER_RELEASE_ENVIRONMENT"
_COLLECTOR_SOURCE_REVISION_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_COLLECTOR_SOURCE_REVISION"
_RENDER_SERVICE_ID_PATTERN = re.compile(r"^srv-[a-z0-9-]{3,64}$")


def build_governed_release_dependencies(
    engine: Engine,
    secret_provider: SecretProvider,
    environ: Mapping[str, str] | None = None,
    runtime_profile: RuntimeProfile | str | None = None,
    mcp_authority: GovernedReleaseMcpAuthority | None = None,
) -> GovernedReleaseDependencies:
    """Compose unavailable defaults or one fully pinned GitHub + Render pair."""

    source = os.environ if environ is None else environ
    profile = RuntimeProfile.from_value(runtime_profile or source.get("FOUNDRY_LITE_RUNTIME_PROFILE"))
    source_adapter, repository_ref = _source_control_adapter(secret_provider, source)
    deployment_adapter = _deployment_adapter(secret_provider, source)
    config = GovernedReleaseDeliveryConfig(
        source_repository=repository_ref,
        source_base_ref=source.get(_GITHUB_BASE_REF_ENV, "main"),
        source_head_prefix=source.get(_GITHUB_HEAD_PREFIX_ENV, "codex/"),
        source_merge_method=_merge_method(source),
        is_source_control_required=profile.is_protected or _boolean(source, _SOURCE_REQUIRED_ENV),
        deployment_service_id=_optional(source, _RENDER_SERVICE_ID_ENV),
        deployment_environment=source.get(_RENDER_ENVIRONMENT_ENV, "production"),
        is_deployment_required=profile.is_protected or _boolean(source, _DEPLOYMENT_REQUIRED_ENV),
    )
    return GovernedReleaseDependencies(
        config=config,
        source_control_adapter=source_adapter,
        infrastructure_deployment_adapter=deployment_adapter,
        delivery_repository=SqlAlchemyReleaseDeliveryRepository(engine),
        live_attestation_repository=SqlAlchemyGovernedReleaseLiveAttestationRepository(engine),
        live_authority=GovernedReleaseLiveAuthority(
            runtime_profile="protected" if profile.is_protected else profile.name,
            database_backend=engine.dialect.name,
            source_provider_profile=source_adapter.profile_name,
            deployment_provider_profile=deployment_adapter.profile_name,
            source_revision=(
                source.get(_COLLECTOR_SOURCE_REVISION_ENV, "").strip() or source.get("RENDER_GIT_COMMIT", "").strip()
            ),
            mcp_authority=mcp_authority or GovernedReleaseMcpAuthority(),
        ),
    )


def _source_control_adapter(
    secret_provider: SecretProvider,
    environ: Mapping[str, str],
) -> tuple[GitHubReleaseAdapter | UnavailableSourceControlReleasePort, SourceRepositoryRef | None]:
    values = _all_or_none(
        environ,
        (_GITHUB_REPOSITORY_ID_ENV, _GITHUB_OWNER_ENV, _GITHUB_REPOSITORY_ENV, _GITHUB_TOKEN_REF_ENV),
        "GitHub governed release",
    )
    if values is None:
        return UnavailableSourceControlReleasePort(), None
    token_ref = values[_GITHUB_TOKEN_REF_ENV]
    _require_resolvable_secret(secret_provider, token_ref, "GitHub governed release")
    repository = SourceRepositoryRef(
        provider="github",
        repository_id=_positive_integer(values[_GITHUB_REPOSITORY_ID_ENV], _GITHUB_REPOSITORY_ID_ENV),
        owner=values[_GITHUB_OWNER_ENV],
        name=values[_GITHUB_REPOSITORY_ENV],
    )
    config = GitHubReleaseConfig(
        repository=repository,
        installation_token_secret_ref=token_ref,
        allowed_base_refs=(environ.get(_GITHUB_BASE_REF_ENV, "main"),),
        allowed_head_ref_prefixes=(environ.get(_GITHUB_HEAD_PREFIX_ENV, "codex/"),),
        minimum_approvals=_nonnegative_integer(
            environ.get(_GITHUB_MINIMUM_APPROVALS_ENV, "0"),
            "minimum approvals",
        ),
        allowed_merge_methods=(_merge_method(environ),),
        is_bypass_policy_verified=_boolean(environ, _GITHUB_BYPASS_POLICY_VERIFIED_ENV),
    )
    return GitHubReleaseAdapter(config, secret_provider), repository


def _deployment_adapter(
    secret_provider: SecretProvider,
    environ: Mapping[str, str],
) -> RenderInfrastructureDeploymentAdapter | UnavailableInfrastructureDeploymentAdapter:
    values = _all_or_none(
        environ,
        (_RENDER_SERVICE_ID_ENV, _RENDER_TOKEN_REF_ENV),
        "Render governed release",
    )
    if values is None:
        return UnavailableInfrastructureDeploymentAdapter()
    token_ref = values[_RENDER_TOKEN_REF_ENV]
    _require_resolvable_secret(secret_provider, token_ref, "Render governed release")
    return RenderInfrastructureDeploymentAdapter(
        secret_provider,
        token_secret_ref=token_ref,
    )


def _all_or_none(
    environ: Mapping[str, str],
    names: tuple[str, ...],
    label: str,
) -> dict[str, str] | None:
    values = {name: value.strip() for name in names if (value := environ.get(name, "")).strip()}
    if not values:
        return None
    if len(values) != len(names):
        missing = sorted(set(names) - set(values))
        raise ValueError(f"{label} configuration is partial; missing {missing}")
    return values


def _merge_method(environ: Mapping[str, str]) -> SourceControlMergeMethod:
    value = environ.get(_GITHUB_MERGE_METHOD_ENV, SourceControlMergeMethod.SQUASH.value)
    try:
        return SourceControlMergeMethod(value)
    except ValueError as exc:
        raise ValueError("GitHub governed release merge method must be merge, squash, or rebase") from exc


def _boolean(environ: Mapping[str, str], name: str) -> bool:
    value = environ.get(name, "false").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _positive_integer(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{label} must be a positive integer")
    return parsed


def _nonnegative_integer(value: str, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return parsed


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    if not value:
        return None
    if name == _RENDER_SERVICE_ID_ENV and _RENDER_SERVICE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{_RENDER_SERVICE_ID_ENV} must be a valid Render service id")
    return value


def _require_resolvable_secret(secret_provider: SecretProvider, secret_ref: str, label: str) -> None:
    try:
        secret = secret_provider.get_secret(secret_ref)
    except Exception:  # noqa: BLE001 - secret-provider messages are untrusted and may contain credentials.
        raise ValueError(f"{label} secret reference cannot be resolved") from None
    if secret.name != secret_ref or not secret.version.strip() or not secret.value:
        raise ValueError(f"{label} secret reference resolved to invalid secret metadata")


__all__ = ["build_governed_release_dependencies"]
