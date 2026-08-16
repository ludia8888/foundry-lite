"""Environment-resolved external Governed Release infrastructure bundle."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping

from sqlalchemy.engine import Engine

from foundry_lite.application.dependency_release import (
    GovernedReleaseDependencies,
    GovernedReleaseLiveAuthority,
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.ports.governed_release_delivery_config import (
    DeploymentReleaseMode,
    GovernedReleaseDeliveryConfig,
)
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    UnavailableInfrastructureDeploymentAdapter,
)
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.source_control_release import (
    SourceControlMergeMethod,
    SourceControlReleasePort,
    SourceRepositoryRef,
    UnavailableSourceControlReleasePort,
)
from foundry_lite.application.runtime_profile import RuntimeProfile
from foundry_lite.infrastructure.governed_release_runtime_adapters import (
    GitHubReleaseAdapter,
    GitHubReleaseConfig,
    KubernetesDeploymentConfig,
    KubernetesInfrastructureDeploymentAdapter,
    RenderInfrastructureDeploymentAdapter,
)
from foundry_lite.infrastructure.repositories.governed_release_live_attestation_repository import (
    SqlAlchemyGovernedReleaseLiveAttestationRepository,
)
from foundry_lite.infrastructure.repositories.release_delivery_repository import (
    SqlAlchemyReleaseDeliveryRepository,
)

_SOURCE_REQUIRED_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REQUIRED"
_SOURCE_PROVIDER_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_PROVIDER"
_SOURCE_REPOSITORY_ID_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REPOSITORY_ID"
_SOURCE_OWNER_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_OWNER"
_SOURCE_REPOSITORY_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REPOSITORY"
_SOURCE_BASE_REF_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_BASE_REF"
_SOURCE_HEAD_PREFIX_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_HEAD_PREFIX"
_SOURCE_MERGE_METHOD_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_MERGE_METHOD"
_DEPLOYMENT_REQUIRED_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_REQUIRED"
_DEPLOYMENT_PROVIDER_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_PROVIDER"
_DEPLOYMENT_SERVICE_ID_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_SERVICE_ID"
_DEPLOYMENT_ENVIRONMENT_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_ENVIRONMENT"
_DEPLOYMENT_RELEASE_MODE_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_RELEASE_MODE"
_DEPLOYMENT_WORKLOAD_KIND_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_WORKLOAD_KIND"
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
_KUBERNETES_NAMESPACE_ENV = "FOUNDRY_LITE_KUBERNETES_RELEASE_NAMESPACE"
_KUBERNETES_DEPLOYMENT_NAME_ENV = "FOUNDRY_LITE_KUBERNETES_RELEASE_DEPLOYMENT_NAME"
_KUBERNETES_CONTAINER_NAME_ENV = "FOUNDRY_LITE_KUBERNETES_RELEASE_CONTAINER_NAME"
_KUBERNETES_IMAGE_REPOSITORY_ENV = "FOUNDRY_LITE_KUBERNETES_RELEASE_IMAGE_REPOSITORY"
_KUBERNETES_TIMEOUT_ENV = "FOUNDRY_LITE_KUBERNETES_RELEASE_TIMEOUT_SECONDS"
_COLLECTOR_SOURCE_REVISION_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_COLLECTOR_SOURCE_REVISION"
_RENDER_SERVICE_ID_PATTERN = re.compile(r"^srv-[a-z0-9-]{3,64}$")
DeploymentAdapterFactory = Callable[
    [SecretProvider, Mapping[str, str]],
    InfrastructureDeploymentAdapter,
]
SourceControlAdapterFactory = Callable[
    [SecretProvider, Mapping[str, str], SourceRepositoryRef],
    SourceControlReleasePort,
]


def build_governed_release_dependencies(
    engine: Engine,
    secret_provider: SecretProvider,
    environ: Mapping[str, str] | None = None,
    runtime_profile: RuntimeProfile | str | None = None,
    mcp_authority: GovernedReleaseMcpAuthority | None = None,
    source_control_adapter_factories: Mapping[str, SourceControlAdapterFactory] | None = None,
    deployment_adapter_factories: Mapping[str, DeploymentAdapterFactory] | None = None,
) -> GovernedReleaseDependencies:
    """Compose provider adapters through an explicit, fail-closed registry."""

    source = os.environ if environ is None else environ
    profile = RuntimeProfile.from_value(runtime_profile or source.get("FOUNDRY_LITE_RUNTIME_PROFILE"))
    source_adapter, repository_ref = _source_control_adapter(
        secret_provider,
        source,
        source_control_adapter_factories,
    )
    deployment_adapter = _deployment_adapter(secret_provider, source, deployment_adapter_factories)
    deployment_provider = _deployment_provider(source)
    config = GovernedReleaseDeliveryConfig(
        source_repository=repository_ref,
        source_base_ref=_source_base_ref(source),
        source_head_prefix=_source_head_prefix(source),
        source_merge_method=_source_merge_method(source),
        is_source_control_required=profile.is_protected or _boolean(source, _SOURCE_REQUIRED_ENV),
        deployment_service_id=_deployment_service_id(source, deployment_provider),
        deployment_environment=_deployment_environment(source),
        deployment_release_mode=_deployment_release_mode(source),
        deployment_workload_kind=source.get(_DEPLOYMENT_WORKLOAD_KIND_ENV, "web_service").strip(),
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
            source_provider_name=source_adapter.provider_name,
            deployment_provider_name=deployment_adapter.provider_name,
            is_source_provider_live=source_adapter.is_live_provider,
            is_deployment_provider_live=deployment_adapter.is_live_provider,
            source_revision=(
                source.get(_COLLECTOR_SOURCE_REVISION_ENV, "").strip() or source.get("RENDER_GIT_COMMIT", "").strip()
            ),
            mcp_authority=mcp_authority or GovernedReleaseMcpAuthority(),
        ),
    )


def _source_control_adapter(
    secret_provider: SecretProvider,
    environ: Mapping[str, str],
    factories: Mapping[str, SourceControlAdapterFactory] | None,
) -> tuple[SourceControlReleasePort, SourceRepositoryRef | None]:
    provider = _source_provider(environ)
    if provider is None:
        return UnavailableSourceControlReleasePort(), None
    repository = _source_repository(environ, provider)
    registry: dict[str, SourceControlAdapterFactory] = {"github": _github_source_control_adapter}
    if factories is not None:
        registry.update({_provider_key(key): value for key, value in factories.items()})
    factory = registry.get(provider)
    if factory is None:
        raise ValueError(f"governed release source provider '{provider}' is not registered")
    adapter = factory(secret_provider, environ, repository)
    if adapter.provider_name != provider:
        raise ValueError("governed release source adapter provider identity does not match its registry key")
    return adapter, repository


def _github_source_control_adapter(
    secret_provider: SecretProvider,
    environ: Mapping[str, str],
    repository: SourceRepositoryRef,
) -> GitHubReleaseAdapter:
    values = _all_or_none(
        environ,
        (_GITHUB_TOKEN_REF_ENV,),
        "GitHub governed release",
    )
    if values is None:
        raise ValueError("GitHub governed release token configuration is missing")
    token_ref = values[_GITHUB_TOKEN_REF_ENV]
    _require_resolvable_secret(secret_provider, token_ref, "GitHub governed release")
    config = GitHubReleaseConfig(
        repository=repository,
        installation_token_secret_ref=token_ref,
        allowed_base_refs=(_source_base_ref(environ),),
        allowed_head_ref_prefixes=(_source_head_prefix(environ),),
        minimum_approvals=_nonnegative_integer(
            environ.get(_GITHUB_MINIMUM_APPROVALS_ENV, "0"),
            "minimum approvals",
        ),
        allowed_merge_methods=(_source_merge_method(environ),),
        is_bypass_policy_verified=_boolean(environ, _GITHUB_BYPASS_POLICY_VERIFIED_ENV),
    )
    return GitHubReleaseAdapter(config, secret_provider)


def _deployment_adapter(
    secret_provider: SecretProvider,
    environ: Mapping[str, str],
    factories: Mapping[str, DeploymentAdapterFactory] | None,
) -> InfrastructureDeploymentAdapter:
    provider = _deployment_provider(environ)
    if provider is None:
        return UnavailableInfrastructureDeploymentAdapter()
    registry: dict[str, DeploymentAdapterFactory] = {
        "kubernetes": _kubernetes_deployment_adapter,
        "render": _render_deployment_adapter,
    }
    if factories is not None:
        registry.update({_provider_key(key): value for key, value in factories.items()})
    factory = registry.get(provider)
    if factory is None:
        raise ValueError(f"governed release deployment provider '{provider}' is not registered")
    adapter = factory(secret_provider, environ)
    if adapter.provider_name != provider:
        raise ValueError("governed release deployment adapter provider identity does not match its registry key")
    return adapter


def _render_deployment_adapter(
    secret_provider: SecretProvider,
    environ: Mapping[str, str],
) -> RenderInfrastructureDeploymentAdapter:
    values = _all_or_none(
        environ,
        (_RENDER_SERVICE_ID_ENV, _RENDER_TOKEN_REF_ENV),
        "Render governed release",
        aliases={_RENDER_SERVICE_ID_ENV: _DEPLOYMENT_SERVICE_ID_ENV},
    )
    if values is None:
        raise ValueError("Render governed release configuration is missing")
    token_ref = values[_RENDER_TOKEN_REF_ENV]
    _require_resolvable_secret(secret_provider, token_ref, "Render governed release")
    return RenderInfrastructureDeploymentAdapter(
        secret_provider,
        token_secret_ref=token_ref,
    )


def _kubernetes_deployment_adapter(
    _secret_provider: SecretProvider,
    environ: Mapping[str, str],
) -> KubernetesInfrastructureDeploymentAdapter:
    names = (
        _DEPLOYMENT_SERVICE_ID_ENV,
        _KUBERNETES_NAMESPACE_ENV,
        _KUBERNETES_DEPLOYMENT_NAME_ENV,
        _KUBERNETES_CONTAINER_NAME_ENV,
        _KUBERNETES_IMAGE_REPOSITORY_ENV,
    )
    values = _all_or_none(environ, names, "Kubernetes governed release")
    if values is None:
        raise ValueError("Kubernetes governed release configuration is missing")
    source_provider = _source_provider(environ)
    if source_provider is None:
        raise ValueError("Kubernetes governed release requires a configured source provider")
    source_repository = _source_repository(environ, source_provider)
    return KubernetesInfrastructureDeploymentAdapter(
        KubernetesDeploymentConfig(
            namespace=values[_KUBERNETES_NAMESPACE_ENV],
            service_id=values[_DEPLOYMENT_SERVICE_ID_ENV],
            deployment_name=values[_KUBERNETES_DEPLOYMENT_NAME_ENV],
            container_name=values[_KUBERNETES_CONTAINER_NAME_ENV],
            image_repository=values[_KUBERNETES_IMAGE_REPOSITORY_ENV],
            source_provider=source_provider,
            source_owner=source_repository.owner,
            source_repository=source_repository.name,
            source_ref=_source_base_ref(environ),
            timeout_seconds=_bounded_float(
                environ.get(_KUBERNETES_TIMEOUT_ENV, "15"),
                _KUBERNETES_TIMEOUT_ENV,
                minimum=0.1,
                maximum=30.0,
            ),
        )
    )


def _all_or_none(
    environ: Mapping[str, str],
    names: tuple[str, ...],
    label: str,
    aliases: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    alias_map = aliases or {}
    values = {
        name: value.strip()
        for name in names
        if (value := environ.get(alias_map.get(name, name), environ.get(name, ""))).strip()
    }
    if not values:
        return None
    if len(values) != len(names):
        missing = sorted(set(names) - set(values))
        raise ValueError(f"{label} configuration is partial; missing {missing}")
    return values


def _source_provider(environ: Mapping[str, str]) -> str | None:
    explicit = environ.get(_SOURCE_PROVIDER_ENV, "").strip()
    if explicit:
        return _provider_key(explicit)
    generic_values = (
        environ.get(_SOURCE_REPOSITORY_ID_ENV, ""),
        environ.get(_SOURCE_OWNER_ENV, ""),
        environ.get(_SOURCE_REPOSITORY_ENV, ""),
    )
    if any(value.strip() for value in generic_values):
        raise ValueError(f"{_SOURCE_PROVIDER_ENV} is required with generic source coordinates")
    github_values = (
        environ.get(_GITHUB_REPOSITORY_ID_ENV, ""),
        environ.get(_GITHUB_OWNER_ENV, ""),
        environ.get(_GITHUB_REPOSITORY_ENV, ""),
        environ.get(_GITHUB_TOKEN_REF_ENV, ""),
    )
    return "github" if any(value.strip() for value in github_values) else None


def _source_repository(environ: Mapping[str, str], provider: str) -> SourceRepositoryRef:
    names = (_SOURCE_REPOSITORY_ID_ENV, _SOURCE_OWNER_ENV, _SOURCE_REPOSITORY_ENV)
    aliases = (
        {
            _SOURCE_REPOSITORY_ID_ENV: _GITHUB_REPOSITORY_ID_ENV,
            _SOURCE_OWNER_ENV: _GITHUB_OWNER_ENV,
            _SOURCE_REPOSITORY_ENV: _GITHUB_REPOSITORY_ENV,
        }
        if provider == "github"
        else None
    )
    values = _all_or_none(environ, names, f"{provider} governed release", aliases=aliases)
    if values is None:
        raise ValueError(f"{provider} governed release repository configuration is missing")
    return SourceRepositoryRef(
        provider=provider,
        repository_id=_positive_integer(values[_SOURCE_REPOSITORY_ID_ENV], _SOURCE_REPOSITORY_ID_ENV),
        owner=values[_SOURCE_OWNER_ENV],
        name=values[_SOURCE_REPOSITORY_ENV],
    )


def _source_base_ref(environ: Mapping[str, str]) -> str:
    return environ.get(_SOURCE_BASE_REF_ENV, environ.get(_GITHUB_BASE_REF_ENV, "main")).strip()


def _source_head_prefix(environ: Mapping[str, str]) -> str:
    return environ.get(_SOURCE_HEAD_PREFIX_ENV, environ.get(_GITHUB_HEAD_PREFIX_ENV, "codex/")).strip()


def _source_merge_method(environ: Mapping[str, str]) -> SourceControlMergeMethod:
    value = environ.get(
        _SOURCE_MERGE_METHOD_ENV,
        environ.get(_GITHUB_MERGE_METHOD_ENV, SourceControlMergeMethod.SQUASH.value),
    )
    try:
        return SourceControlMergeMethod(value)
    except ValueError as exc:
        raise ValueError("governed release source merge method must be merge, squash, or rebase") from exc


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


def _bounded_float(value: str, label: str, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def _deployment_provider(environ: Mapping[str, str]) -> str | None:
    explicit = environ.get(_DEPLOYMENT_PROVIDER_ENV, "").strip()
    if explicit:
        return _provider_key(explicit)
    render_values = (environ.get(_RENDER_SERVICE_ID_ENV, ""), environ.get(_RENDER_TOKEN_REF_ENV, ""))
    return "render" if any(value.strip() for value in render_values) else None


def _provider_key(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", normalized) is None:
        raise ValueError("governed release deployment provider name is invalid")
    return normalized


def _deployment_service_id(environ: Mapping[str, str], provider: str | None) -> str | None:
    value = environ.get(_DEPLOYMENT_SERVICE_ID_ENV, "").strip()
    if not value and provider == "render":
        value = environ.get(_RENDER_SERVICE_ID_ENV, "").strip()
    if not value:
        return None
    if provider == "render" and _RENDER_SERVICE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{_DEPLOYMENT_SERVICE_ID_ENV} must be a valid Render service id")
    return value


def _deployment_environment(environ: Mapping[str, str]) -> str:
    return environ.get(
        _DEPLOYMENT_ENVIRONMENT_ENV,
        environ.get(_RENDER_ENVIRONMENT_ENV, "production"),
    ).strip()


def _deployment_release_mode(environ: Mapping[str, str]) -> DeploymentReleaseMode:
    value = environ.get(_DEPLOYMENT_RELEASE_MODE_ENV, "source_revision").strip()
    if value not in {"source_revision", "immutable_artifact"}:
        raise ValueError(f"{_DEPLOYMENT_RELEASE_MODE_ENV} must be source_revision or immutable_artifact")
    return "source_revision" if value == "source_revision" else "immutable_artifact"


def _require_resolvable_secret(secret_provider: SecretProvider, secret_ref: str, label: str) -> None:
    try:
        secret = secret_provider.get_secret(secret_ref)
    except Exception:  # noqa: BLE001 - secret-provider messages are untrusted and may contain credentials.
        raise ValueError(f"{label} secret reference cannot be resolved") from None
    if secret.name != secret_ref or not secret.version.strip() or not secret.value:
        raise ValueError(f"{label} secret reference resolved to invalid secret metadata")


__all__ = ["build_governed_release_dependencies"]
