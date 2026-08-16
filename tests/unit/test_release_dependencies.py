from __future__ import annotations

import pytest
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    UnavailableInfrastructureDeploymentAdapter,
)
from foundry_lite.application.ports.source_control_release import (
    SourceControlMergeMethod,
    UnavailableSourceControlReleasePort,
)
from foundry_lite.infrastructure.adapters.github_release import GitHubReleaseAdapter
from foundry_lite.infrastructure.adapters.render_deployment import RenderInfrastructureDeploymentAdapter
from foundry_lite.infrastructure.release_dependencies import build_governed_release_dependencies
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider
from sqlalchemy import create_engine


def test_empty_environment_keeps_external_delivery_disabled() -> None:
    dependencies = _build({})

    assert dependencies.config.is_source_control_enabled is False
    assert dependencies.config.is_deployment_enabled is False
    assert isinstance(dependencies.source_control_adapter, UnavailableSourceControlReleasePort)


@pytest.mark.parametrize("profile", ["staging", "production"])
def test_protected_runtime_requires_external_source_and_deployment(profile: str) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(ValueError, match="required governed source control"):
        build_governed_release_dependencies(
            engine,
            EnvSecretProvider(environ={}),
            {},
            runtime_profile=profile,
        )

    dependencies = build_governed_release_dependencies(
        engine,
        _secret_provider(),
        _full_environment(),
        runtime_profile=profile,
    )
    assert dependencies.config.is_source_control_required is True
    assert dependencies.config.is_deployment_required is True


def test_partial_or_required_external_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="partial"):
        _build({"FOUNDRY_LITE_GITHUB_RELEASE_OWNER": "example"})
    with pytest.raises(ValueError, match="required governed source control"):
        _build({"FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REQUIRED": "true"})
    with pytest.raises(ValueError, match="requires a source-control merge receipt"):
        _build(
            {
                "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID": "srv-example-service",
                "FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF": "render-token",
            }
        )


def test_full_configuration_pins_targets_and_requires_explicit_bypass_attestation() -> None:
    dependencies = _build(_full_environment())

    source = dependencies.source_control_adapter
    deploy = dependencies.infrastructure_deployment_adapter
    assert isinstance(source, GitHubReleaseAdapter)
    assert isinstance(deploy, RenderInfrastructureDeploymentAdapter)
    assert dependencies.config.source_repository is not None
    assert dependencies.config.source_repository.repository_id == 1234
    assert dependencies.config.source_base_ref == "main"
    assert dependencies.config.source_head_ref("orders") == "codex/orders"
    assert dependencies.config.source_merge_method is SourceControlMergeMethod.SQUASH
    assert source._config.allowed_merge_methods == (SourceControlMergeMethod.SQUASH,)
    assert source._config.minimum_approvals == 0
    assert source._config.is_bypass_policy_verified is False

    verified = _build(
        {
            **_full_environment(),
            "FOUNDRY_LITE_GITHUB_RELEASE_BYPASS_POLICY_VERIFIED": "true",
        }
    )
    verified_source = verified.source_control_adapter
    assert isinstance(verified_source, GitHubReleaseAdapter)
    assert verified_source._config.is_bypass_policy_verified is True


def test_registered_deployment_provider_swaps_without_application_changes() -> None:
    environment = {
        **_full_environment(),
        "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_PROVIDER": "kubernetes",
        "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_SERVICE_ID": "namespace/prod/deployment/foundry",
        "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_RELEASE_MODE": "immutable_artifact",
        "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_WORKLOAD_KIND": "deployment",
    }
    environment.pop("FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID")
    environment.pop("FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF")

    dependencies = build_governed_release_dependencies(
        create_engine("sqlite+pysqlite:///:memory:"),
        _secret_provider(),
        environment,
        deployment_adapter_factories={"kubernetes": lambda _secrets, _environ: _KubernetesTestAdapter()},
    )

    assert isinstance(dependencies.infrastructure_deployment_adapter, _KubernetesTestAdapter)
    assert dependencies.config.deployment_release_mode == "immutable_artifact"
    assert dependencies.config.deployment_workload_kind == "deployment"
    assert dependencies.live_authority.deployment_provider_name == "kubernetes"
    assert dependencies.live_authority.is_deployment_provider_live is True


def test_registered_source_provider_swaps_without_application_changes() -> None:
    environment = {
        **_full_environment(),
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_PROVIDER": "gitlab",
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REPOSITORY_ID": "4321",
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_OWNER": "platform",
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REPOSITORY": "foundry-lite",
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_BASE_REF": "trunk",
        "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_HEAD_PREFIX": "release/",
    }
    for name in (
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID",
        "FOUNDRY_LITE_GITHUB_RELEASE_OWNER",
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY",
        "FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF",
    ):
        environment.pop(name)

    dependencies = build_governed_release_dependencies(
        create_engine("sqlite+pysqlite:///:memory:"),
        _secret_provider(),
        environment,
        source_control_adapter_factories={
            "gitlab": lambda _secrets, _environ, _repository: _GitLabTestAdapter(),
        },
    )

    assert isinstance(dependencies.source_control_adapter, _GitLabTestAdapter)
    assert dependencies.config.source_repository is not None
    assert dependencies.config.source_repository.provider == "gitlab"
    assert dependencies.config.source_repository.repository_id == 4321
    assert dependencies.config.source_base_ref == "trunk"
    assert dependencies.config.source_head_ref("orders") == "release/orders"
    assert dependencies.live_authority.source_provider_name == "gitlab"
    assert dependencies.live_authority.is_source_provider_live is True


def test_registered_source_provider_identity_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="source adapter provider identity"):
        build_governed_release_dependencies(
            create_engine("sqlite+pysqlite:///:memory:"),
            _secret_provider(),
            {
                **_full_environment(),
                "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_PROVIDER": "gitlab",
                "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REPOSITORY_ID": "4321",
                "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_OWNER": "platform",
                "FOUNDRY_LITE_GOVERNED_RELEASE_SOURCE_REPOSITORY": "foundry-lite",
            },
            source_control_adapter_factories={
                "gitlab": lambda _secrets, _environ, _repository: _WrongSourceProviderTestAdapter(),
            },
        )


def test_registered_deployment_provider_identity_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="provider identity"):
        build_governed_release_dependencies(
            create_engine("sqlite+pysqlite:///:memory:"),
            _secret_provider(),
            {
                **_full_environment(),
                "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_PROVIDER": "kubernetes",
                "FOUNDRY_LITE_GOVERNED_RELEASE_DEPLOYMENT_SERVICE_ID": "deployment/foundry",
            },
            deployment_adapter_factories={
                "kubernetes": lambda _secrets, _environ: _WrongProviderTestAdapter(),
            },
        )


def test_live_authority_receives_only_the_validated_public_mcp_binding() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    mcp_authority = GovernedReleaseMcpAuthority(
        application_id="release-app",
        public_base_url="https://foundry.example.test/",
        authorization_server_issuer="https://identity.example.test/",
        oauth_audience="https://foundry.example.test/mcp/release/release-app",
        allowed_client_ids=("client-b", "client-a"),
    )

    dependencies = build_governed_release_dependencies(
        engine,
        _secret_provider(),
        _full_environment(),
        mcp_authority=mcp_authority,
    )

    assert dependencies.live_authority.mcp_authority == mcp_authority
    assert dependencies.live_authority.mcp_authority.allowed_client_ids == ("client-a", "client-b")


def test_invalid_merge_method_and_attestation_are_rejected() -> None:
    with pytest.raises(ValueError, match="merge method"):
        _build({**_full_environment(), "FOUNDRY_LITE_GITHUB_RELEASE_MERGE_METHOD": "fast-forward"})
    with pytest.raises(ValueError, match="must be a boolean"):
        _build({**_full_environment(), "FOUNDRY_LITE_GITHUB_RELEASE_BYPASS_POLICY_VERIFIED": "sometimes"})


def test_external_secret_refs_and_render_service_id_fail_closed_at_startup() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(ValueError, match="GitHub governed release secret reference"):
        build_governed_release_dependencies(
            engine,
            EnvSecretProvider(environ={}),
            _full_environment(),
            runtime_profile="production",
        )
    with pytest.raises(ValueError, match="valid Render service id"):
        _build({**_full_environment(), "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID": "not-a-service"})


def _build(environ: dict[str, str]):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    return build_governed_release_dependencies(engine, _secret_provider(), environ)


def _secret_provider() -> EnvSecretProvider:
    return EnvSecretProvider(
        environ={
            "FOUNDRY_LITE_SECRET_GITHUB_TOKEN": "test-github-token",
            "FOUNDRY_LITE_SECRET_RENDER_TOKEN": "test-render-token",
        }
    )


def _full_environment() -> dict[str, str]:
    return {
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID": "1234",
        "FOUNDRY_LITE_GITHUB_RELEASE_OWNER": "example",
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY": "foundry-lite",
        "FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF": "github-token",
        "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID": "srv-example-service",
        "FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF": "render-token",
    }


class _KubernetesTestAdapter(UnavailableInfrastructureDeploymentAdapter):
    profile_name = "kubernetes-infrastructure-deployment"
    provider_name = "kubernetes"
    is_live_provider = True


class _WrongProviderTestAdapter(_KubernetesTestAdapter):
    provider_name = "wrong-provider"


class _GitLabTestAdapter(UnavailableSourceControlReleasePort):
    profile_name = "gitlab-source-control-release"
    provider_name = "gitlab"
    is_live_provider = True


class _WrongSourceProviderTestAdapter(_GitLabTestAdapter):
    provider_name = "wrong-provider"
