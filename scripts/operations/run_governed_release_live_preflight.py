"""Read-only live readiness checks for the hosted Governed Release path.

This command proves configuration and provider read access only.  It never
performs OAuth login, merge, deploy, rollback, or any other remote mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Literal, Protocol, TextIO, cast
from urllib.parse import SplitResult, quote, urlencode, urlsplit

from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentServicePolicyRequest,
)
from foundry_lite.infrastructure.adapters.render_deployment import (
    RenderHttpTransport,
    RenderInfrastructureDeploymentAdapter,
)
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "operations" / "governed_release_live_preflight.json"
PREFLIGHT_PATH_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_LIVE_PREFLIGHT_PATH"
RELEASE_SCOPE = "osdk:connector:governed_release:execute"
APPLICATION_ID_ENV = "FOUNDRY_LITE_GOVERNED_RELEASE_APPLICATION_ID"
PUBLIC_BASE_ENV = "FOUNDRY_LITE_MCP_PUBLIC_BASE_URL"
AUTHORIZATION_SERVER_ENV = "FOUNDRY_LITE_MCP_AUTHORIZATION_SERVER"
OIDC_ISSUER_ENV = "FOUNDRY_LITE_OIDC_ISSUER"
OIDC_AUDIENCE_ENV = "FOUNDRY_LITE_OIDC_AUDIENCE"
OIDC_ALLOWED_CLIENT_IDS_ENV = "FOUNDRY_LITE_OIDC_ALLOWED_CLIENT_IDS_JSON"
GITHUB_REPOSITORY_ID_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID"
GITHUB_OWNER_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_OWNER"
GITHUB_REPOSITORY_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY"
GITHUB_BASE_REF_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_BASE_REF"
GITHUB_TOKEN_REF_ENV = "FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF"  # nosec B105 - setting name.
RENDER_SERVICE_ID_ENV = "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID"
RENDER_TOKEN_REF_ENV = "FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF"  # nosec B105 - setting name.

_REQUIRED_ENV_SETTINGS: tuple[str, ...] = (
    PUBLIC_BASE_ENV,
    AUTHORIZATION_SERVER_ENV,
    OIDC_ISSUER_ENV,
    OIDC_AUDIENCE_ENV,
    OIDC_ALLOWED_CLIENT_IDS_ENV,
    GITHUB_REPOSITORY_ID_ENV,
    GITHUB_OWNER_ENV,
    GITHUB_REPOSITORY_ENV,
    GITHUB_TOKEN_REF_ENV,
    RENDER_SERVICE_ID_ENV,
    RENDER_TOKEN_REF_ENV,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_COORDINATE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SERVICE_ID = re.compile(r"^srv-[a-z0-9-]{3,64}$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

CheckStatus = Literal["ready", "blocked"]
EvidenceOrigin = Literal["live_provider_readback", "local_or_injected", "configuration_only"]


class PreflightFailure(RuntimeError):
    """Safe failure classification that contains no provider response body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PreflightConfigurationError(PreflightFailure):
    """A safe startup configuration failure."""

    def __init__(self, code: str, setting: str) -> None:
        super().__init__(code)
        self.setting = setting


@dataclass(frozen=True, slots=True)
class ReadOnlyHttpRequest:
    """Bounded GET request whose repr cannot expose authorization headers."""

    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    method: Literal["GET"] = "GET"
    timeout_seconds: float = 15.0
    max_response_bytes: int = _MAX_RESPONSE_BYTES


@dataclass(frozen=True, slots=True)
class ReadOnlyHttpResponse:
    """Bounded response whose raw body is never part of report evidence."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class ReadOnlyHttpTransport(Protocol):
    """Injectable transport used by public OAuth and GitHub checks."""

    def send(self, request: ReadOnlyHttpRequest) -> ReadOnlyHttpResponse: ...


class HttpsReadOnlyTransport:
    """No-redirect HTTPS transport that can only issue GET requests."""

    def send(self, request: ReadOnlyHttpRequest) -> ReadOnlyHttpResponse:
        parsed = _clean_https_url(request.url)
        hostname = parsed.hostname
        if hostname is None:
            raise PreflightFailure("clean_https_url_required")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        connection = HTTPSConnection(hostname, parsed.port or 443, timeout=request.timeout_seconds)
        try:
            connection.request("GET", target, headers=dict(request.headers))
            response = connection.getresponse()
            body = response.read(request.max_response_bytes + 1)
            if len(body) > request.max_response_bytes:
                raise PreflightFailure("response_too_large")
            return ReadOnlyHttpResponse(response.status, dict(response.headers.items()), body)
        except PreflightFailure:
            raise
        except TimeoutError as exc:
            raise PreflightFailure("network_timeout") from exc
        except (HTTPException, OSError) as exc:
            raise PreflightFailure("network_unavailable") from exc
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class LivePreflightConfig:
    application_id: str
    public_base_url: str
    authorization_server: str
    oidc_issuer: str
    oidc_audience: str
    oidc_allowed_client_ids: frozenset[str]
    github_repository_id: int
    github_owner: str
    github_repository: str
    github_base_ref: str
    github_token_secret_ref: str = field(repr=False)
    render_service_id: str
    render_token_secret_ref: str = field(repr=False)

    @property
    def encoded_application_id(self) -> str:
        return quote(self.application_id, safe="")

    @property
    def resource(self) -> str:
        return f"{self.public_base_url}/mcp/release/{self.encoded_application_id}"

    @property
    def protected_resource_metadata_url(self) -> str:
        return f"{self.public_base_url}/.well-known/oauth-protected-resource/mcp/release/{self.encoded_application_id}"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: CheckStatus
    code: str
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UnverifiedBoundary:
    name: str
    status: Literal["unverified"]
    reason: str


@dataclass(frozen=True, slots=True)
class LivePreflightReport:
    schema_version: str
    status: CheckStatus
    is_ready: bool
    network_mode: Literal["read_only"]
    evidence_origin: EvidenceOrigin
    checks: tuple[PreflightCheck, ...]
    unverified: tuple[UnverifiedBoundary, ...]


def config_from_environment(
    environ: Mapping[str, str],
    *,
    application_id: str | None = None,
) -> LivePreflightConfig:
    """Build exact hosted release targets without resolving secret values."""

    app_id = (application_id or _required(environ, APPLICATION_ID_ENV)).strip()
    _require_pattern(app_id, _SAFE_ID, APPLICATION_ID_ENV)
    public_base = _origin(_required(environ, PUBLIC_BASE_ENV), PUBLIC_BASE_ENV)
    authorization_server = _https_url(_required(environ, AUTHORIZATION_SERVER_ENV), AUTHORIZATION_SERVER_ENV)
    issuer = _https_url(_required(environ, OIDC_ISSUER_ENV), OIDC_ISSUER_ENV)
    if issuer != authorization_server:
        raise PreflightConfigurationError("issuer_authorization_server_mismatch", OIDC_ISSUER_ENV)
    owner = _required(environ, GITHUB_OWNER_ENV)
    repository = _required(environ, GITHUB_REPOSITORY_ENV)
    base_ref = environ.get(GITHUB_BASE_REF_ENV, "main").strip()
    _require_pattern(owner, _SAFE_COORDINATE, GITHUB_OWNER_ENV)
    _require_pattern(repository, _SAFE_COORDINATE, GITHUB_REPOSITORY_ENV)
    _require_pattern(base_ref, _SAFE_REF, GITHUB_BASE_REF_ENV)
    service_id = _required(environ, RENDER_SERVICE_ID_ENV)
    _require_pattern(service_id, _SERVICE_ID, RENDER_SERVICE_ID_ENV)
    config = LivePreflightConfig(
        application_id=app_id,
        public_base_url=public_base,
        authorization_server=authorization_server,
        oidc_issuer=issuer,
        oidc_audience=_required(environ, OIDC_AUDIENCE_ENV),
        oidc_allowed_client_ids=_required_string_set(environ, OIDC_ALLOWED_CLIENT_IDS_ENV),
        github_repository_id=_positive_integer(_required(environ, GITHUB_REPOSITORY_ID_ENV)),
        github_owner=owner,
        github_repository=repository,
        github_base_ref=base_ref,
        github_token_secret_ref=_required(environ, GITHUB_TOKEN_REF_ENV),
        render_service_id=service_id,
        render_token_secret_ref=_required(environ, RENDER_TOKEN_REF_ENV),
    )
    if config.oidc_audience != config.resource:
        raise PreflightConfigurationError("oidc_audience_resource_mismatch", OIDC_AUDIENCE_ENV)
    return config


def run_live_preflight(
    config: LivePreflightConfig,
    secret_provider: EnvSecretProvider,
    *,
    transport: ReadOnlyHttpTransport | None = None,
    render_transport: RenderHttpTransport | None = None,
) -> LivePreflightReport:
    """Run all independent, read-only readiness checks."""

    evidence_origin: EvidenceOrigin = (
        "live_provider_readback" if transport is None and render_transport is None else "local_or_injected"
    )
    reader = transport or HttpsReadOnlyTransport()
    checks = (
        _ready("configuration", "exact_targets_configured", _configuration_evidence(config)),
        _capture("mcp_protected_resource", lambda: _mcp_metadata_evidence(config, reader)),
        _capture("oidc_discovery", lambda: _oidc_evidence(config, reader)),
        _capture("github_repository", lambda: _github_evidence(config, secret_provider, reader)),
        _capture("render_service", lambda: _render_evidence(config, secret_provider, render_transport)),
    )
    is_ready = all(check.status == "ready" for check in checks)
    return _report(checks, is_ready=is_ready, evidence_origin=evidence_origin)


def run_live_preflight_from_environment(
    environ: Mapping[str, str],
    *,
    application_id: str | None = None,
    transport: ReadOnlyHttpTransport | None = None,
    render_transport: RenderHttpTransport | None = None,
) -> LivePreflightReport:
    """Return a JSON-safe blocked report even when configuration is incomplete."""

    missing_settings = _missing_required_settings(environ, application_id=application_id)
    if missing_settings:
        return _configuration_blocked_report(missing_settings)
    try:
        config = config_from_environment(environ, application_id=application_id)
    except PreflightConfigurationError as exc:
        blocked = PreflightCheck(
            "configuration",
            "blocked",
            exc.code,
            {"setting": exc.setting},
        )
        dependent = tuple(
            PreflightCheck(name, "blocked", "configuration_not_ready")
            for name in ("mcp_protected_resource", "oidc_discovery", "github_repository", "render_service")
        )
        return _report((blocked, *dependent), is_ready=False, evidence_origin="configuration_only")
    provider = EnvSecretProvider(environ=environ)
    return run_live_preflight(config, provider, transport=transport, render_transport=render_transport)


def _missing_required_settings(
    environ: Mapping[str, str],
    *,
    application_id: str | None,
) -> tuple[str, ...]:
    names = _REQUIRED_ENV_SETTINGS
    if application_id is None:
        names = (APPLICATION_ID_ENV, *names)
    return tuple(name for name in names if not environ.get(name, "").strip())


def _configuration_blocked_report(settings: tuple[str, ...]) -> LivePreflightReport:
    evidence: dict[str, object] = {"settings": settings}
    if len(settings) == 1:
        evidence["setting"] = settings[0]
    blocked = PreflightCheck(
        "configuration",
        "blocked",
        "missing_required_setting" if len(settings) == 1 else "missing_required_settings",
        evidence,
    )
    dependent = tuple(
        PreflightCheck(name, "blocked", "configuration_not_ready")
        for name in ("mcp_protected_resource", "oidc_discovery", "github_repository", "render_service")
    )
    return _report((blocked, *dependent), is_ready=False, evidence_origin="configuration_only")


def serialize_report(report: LivePreflightReport) -> str:
    """Serialize only normalized evidence; raw responses and secrets are unreachable here."""

    return json.dumps(asdict(report), indent=2, sort_keys=True) + "\n"


def _configuration_evidence(config: LivePreflightConfig) -> Mapping[str, object]:
    return {
        "authorizationServer": config.authorization_server,
        "allowedOAuthClientCount": len(config.oidc_allowed_client_ids),
        "baseBranch": config.github_base_ref,
        "publicBaseUrl": config.public_base_url,
        "repository": f"{config.github_owner}/{config.github_repository}",
        "repositoryId": config.github_repository_id,
        "resource": config.resource,
        "serviceId": config.render_service_id,
    }


def _mcp_metadata_evidence(config: LivePreflightConfig, transport: ReadOnlyHttpTransport) -> Mapping[str, object]:
    payload = _mapping(_get_json(transport, config.protected_resource_metadata_url, {}, "mcp_metadata"))
    if payload.get("resource") != config.resource:
        raise PreflightFailure("mcp_resource_mismatch")
    if _string_tuple(payload.get("authorization_servers")) != (config.authorization_server,):
        raise PreflightFailure("mcp_authorization_server_mismatch")
    if _string_tuple(payload.get("bearer_methods_supported")) != ("header",):
        raise PreflightFailure("mcp_bearer_method_mismatch")
    if _string_tuple(payload.get("scopes_supported")) != (RELEASE_SCOPE,):
        raise PreflightFailure("mcp_release_scope_mismatch")
    return {
        "authorizationServer": config.authorization_server,
        "metadataUrl": config.protected_resource_metadata_url,
        "resource": config.resource,
    }


def _oidc_evidence(config: LivePreflightConfig, transport: ReadOnlyHttpTransport) -> Mapping[str, object]:
    discovery_url = f"{config.authorization_server}/.well-known/openid-configuration"
    discovery = _mapping(_get_json(transport, discovery_url, {}, "oidc_discovery"))
    if discovery.get("issuer") != config.oidc_issuer:
        raise PreflightFailure("oidc_discovery_issuer_mismatch")
    _require_supported(discovery, "response_types_supported", "code", "oidc_authorization_code_not_supported")
    _require_supported(discovery, "grant_types_supported", "authorization_code", "oidc_grant_not_supported")
    _require_supported(discovery, "code_challenge_methods_supported", "S256", "oidc_pkce_s256_not_supported")
    _https_endpoint(discovery, "authorization_endpoint")
    _https_endpoint(discovery, "token_endpoint")
    jwks_uri = _https_endpoint(discovery, "jwks_uri")
    jwks = _mapping(_get_json(transport, jwks_uri, {}, "oidc_jwks"))
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(key, Mapping) for key in keys):
        raise PreflightFailure("oidc_jwks_empty_or_invalid")
    return {
        "authorizationCode": True,
        "issuer": config.oidc_issuer,
        "jwksKeyCount": len(keys),
        "pkceMethod": "S256",
    }


def _github_evidence(
    config: LivePreflightConfig,
    secret_provider: EnvSecretProvider,
    transport: ReadOnlyHttpTransport,
) -> Mapping[str, object]:
    token = _resolve_secret(secret_provider, config.github_token_secret_ref, "github_secret_unresolved")
    headers = {
        "accept": "application/vnd.github+json",
        "authorization": f"Bearer {token}",
        "user-agent": "Foundry-lite/governed-release-live-preflight",
        "x-github-api-version": "2026-03-10",
    }
    repo_path = f"/repos/{quote(config.github_owner, safe='')}/{quote(config.github_repository, safe='')}"
    repository = _mapping(_get_json(transport, f"https://api.github.com{repo_path}", headers, "github_repo"))
    push_permission, admin_permission = _validate_github_repository(config, repository)
    branch_url = f"https://api.github.com{repo_path}/branches/{quote(config.github_base_ref, safe='')}"
    branch = _mapping(_get_json(transport, branch_url, headers, "github_branch"))
    is_protected, head_sha = _validate_github_branch(config, branch)
    rule_count = _github_rule_count(config, transport, headers, repo_path)
    classic_state = _github_classic_protection(config, transport, headers, repo_path)
    return {
        "adminPermission": admin_permission,
        "baseBranch": config.github_base_ref,
        "branchHeadSha": head_sha,
        "branchProtected": is_protected,
        "classicProtection": classic_state,
        "pushPermission": push_permission,
        "repository": f"{config.github_owner}/{config.github_repository}",
        "repositoryId": config.github_repository_id,
        "rulesRead": True,
        "rulesSeen": rule_count,
        "secretResolved": True,
    }


def _validate_github_repository(
    config: LivePreflightConfig,
    repository: Mapping[str, object],
) -> tuple[bool, bool]:
    owner = _mapping(repository.get("owner"))
    expected_full_name = f"{config.github_owner}/{config.github_repository}"
    if (
        repository.get("id") != config.github_repository_id
        or repository.get("name") != config.github_repository
        or repository.get("full_name") != expected_full_name
        or owner.get("login") != config.github_owner
    ):
        raise PreflightFailure("github_repository_binding_mismatch")
    if repository.get("archived") is not False:
        raise PreflightFailure("github_repository_archived_or_invalid")
    if repository.get("disabled") is not False:
        raise PreflightFailure("github_repository_disabled_or_invalid")
    permissions = _mapping(repository.get("permissions"))
    push_permission = permissions.get("push") is True
    admin_permission = permissions.get("admin") is True
    if not (push_permission or admin_permission):
        raise PreflightFailure("github_push_or_admin_permission_missing")
    return push_permission, admin_permission


def _validate_github_branch(
    config: LivePreflightConfig,
    branch: Mapping[str, object],
) -> tuple[bool, str]:
    if branch.get("name") != config.github_base_ref or not isinstance(branch.get("protected"), bool):
        raise PreflightFailure("github_base_branch_binding_mismatch")
    commit = _mapping(branch.get("commit"))
    head_sha = commit.get("sha")
    if not isinstance(head_sha, str) or _FULL_SHA.fullmatch(head_sha) is None:
        raise PreflightFailure("github_base_branch_head_invalid")
    return cast(bool, branch["protected"]), head_sha


def _github_rule_count(
    config: LivePreflightConfig,
    transport: ReadOnlyHttpTransport,
    headers: Mapping[str, str],
    repo_path: str,
) -> int:
    total = 0
    branch = quote(config.github_base_ref, safe="")
    for page in range(1, 11):
        query = urlencode({"per_page": 100, "page": page})
        url = f"https://api.github.com{repo_path}/rules/branches/{branch}?{query}"
        rows = _sequence(_get_json(transport, url, headers, "github_rules"))
        total += len(rows)
        if len(rows) < 100:
            return total
    raise PreflightFailure("github_rules_page_limit_exceeded")


def _github_classic_protection(
    config: LivePreflightConfig,
    transport: ReadOnlyHttpTransport,
    headers: Mapping[str, str],
    repo_path: str,
) -> str:
    branch = quote(config.github_base_ref, safe="")
    url = f"https://api.github.com{repo_path}/branches/{branch}/protection"
    response = _send(transport, ReadOnlyHttpRequest(url=url, headers=headers), "github_branch_protection")
    if response.status_code == 404:
        return "absent"
    if response.status_code != 200:
        raise PreflightFailure(f"github_branch_protection_http_{response.status_code}")
    _mapping(_decode_json(response.body, "github_branch_protection"))
    return "present"


def _render_evidence(
    config: LivePreflightConfig,
    secret_provider: EnvSecretProvider,
    transport: RenderHttpTransport | None,
) -> Mapping[str, object]:
    _resolve_secret(secret_provider, config.render_token_secret_ref, "render_secret_unresolved")
    adapter = RenderInfrastructureDeploymentAdapter(
        secret_provider,
        token_secret_ref=config.render_token_secret_ref,
        transport=transport,
    )
    try:
        observation = adapter.get_service_policy(
            InfrastructureDeploymentServicePolicyRequest(
                tenant_id="governed-release-live-preflight",
                service_id=config.render_service_id,
                request_id="governed-release-live-preflight",
                correlation_id="governed-release-live-preflight",
            )
        )
    except Exception:  # Provider error text/body may contain secrets and must not enter the report.
        raise PreflightFailure("render_service_policy_read_failed") from None
    if observation.source_repository_owner != config.github_owner:
        raise PreflightFailure("render_repository_owner_mismatch")
    if observation.source_repository_name != config.github_repository:
        raise PreflightFailure("render_repository_name_mismatch")
    if observation.source_branch != config.github_base_ref:
        raise PreflightFailure("render_source_branch_mismatch")
    if observation.service_type != "web_service":
        raise PreflightFailure("render_service_not_web_service")
    if observation.is_suspended:
        raise PreflightFailure("render_service_suspended")
    if observation.is_auto_deploy_enabled:
        raise PreflightFailure("render_auto_deploy_must_be_off")
    return {
        "autoDeploy": False,
        "repository": f"{observation.source_repository_owner}/{observation.source_repository_name}",
        "serviceId": observation.service_id,
        "serviceType": observation.service_type,
        "sourceBranch": observation.source_branch,
        "suspended": False,
        "secretResolved": True,
    }


def _capture(name: str, operation: Callable[[], Mapping[str, object]]) -> PreflightCheck:
    try:
        return _ready(name, "read_only_check_passed", operation())
    except PreflightFailure as exc:
        return PreflightCheck(name, "blocked", exc.code)
    except Exception:  # Injected transports are untrusted; never serialize raw exception text.
        return PreflightCheck(name, "blocked", "unexpected_read_failure")


def _ready(name: str, code: str, evidence: Mapping[str, object]) -> PreflightCheck:
    return PreflightCheck(name, "ready", code, evidence)


def _report(
    checks: tuple[PreflightCheck, ...],
    *,
    is_ready: bool,
    evidence_origin: EvidenceOrigin,
) -> LivePreflightReport:
    return LivePreflightReport(
        schema_version="governed-release-live-preflight/v1",
        status="ready" if is_ready else "blocked",
        is_ready=is_ready,
        network_mode="read_only",
        evidence_origin=evidence_origin,
        checks=checks,
        unverified=(
            UnverifiedBoundary(
                "hosted_chatgpt_two_human_oauth",
                "unverified",
                "Requires two distinct human users to complete hosted ChatGPT OAuth and approval.",
            ),
            UnverifiedBoundary(
                "governed_release_write_end_to_end",
                "unverified",
                "No merge, activation, deploy, status completion, or rollback is executed by this preflight.",
            ),
        ),
    )


def _get_json(
    transport: ReadOnlyHttpTransport,
    url: str,
    headers: Mapping[str, str],
    label: str,
) -> object:
    response = _send(transport, ReadOnlyHttpRequest(url=url, headers=headers), label)
    if response.status_code != 200:
        raise PreflightFailure(f"{label}_http_{response.status_code}")
    return _decode_json(response.body, label)


def _send(
    transport: ReadOnlyHttpTransport,
    request: ReadOnlyHttpRequest,
    label: str,
) -> ReadOnlyHttpResponse:
    _clean_https_url(request.url)
    try:
        response = transport.send(request)
    except PreflightFailure:
        raise
    except Exception:
        raise PreflightFailure(f"{label}_transport_failed") from None
    if not 100 <= response.status_code <= 599:
        raise PreflightFailure(f"{label}_status_invalid")
    if len(response.body) > request.max_response_bytes:
        raise PreflightFailure(f"{label}_response_too_large")
    return response


def _decode_json(body: bytes, label: str) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PreflightFailure(f"{label}_json_invalid") from None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PreflightFailure("provider_object_invalid")
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise PreflightFailure("provider_list_invalid")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PreflightFailure("provider_string_list_invalid")
    return tuple(value)


def _require_supported(payload: Mapping[str, object], field_name: str, value: str, code: str) -> None:
    if value not in _string_tuple(payload.get(field_name)):
        raise PreflightFailure(code)


def _https_endpoint(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise PreflightFailure(f"oidc_{field_name}_invalid")
    _clean_https_url(value)
    return value.rstrip("/")


def _resolve_secret(provider: EnvSecretProvider, secret_ref: str, code: str) -> str:
    try:
        secret = provider.get_secret(secret_ref)
    except Exception:
        raise PreflightFailure(code) from None
    if secret.name != secret_ref or not secret.version or not secret.value.strip():
        raise PreflightFailure(code)
    return secret.value


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise PreflightConfigurationError("missing_required_setting", name)
    return value


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise PreflightConfigurationError("invalid_positive_integer", GITHUB_REPOSITORY_ID_ENV) from None
    if parsed < 1:
        raise PreflightConfigurationError("invalid_positive_integer", GITHUB_REPOSITORY_ID_ENV)
    return parsed


def _required_string_set(environ: Mapping[str, str], name: str) -> frozenset[str]:
    raw = _required(environ, name)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise PreflightConfigurationError("invalid_json_string_array", name) from None
    if not isinstance(parsed, list) or not parsed:
        raise PreflightConfigurationError("nonempty_json_string_array_required", name)
    values = {item.strip() for item in parsed if isinstance(item, str) and item.strip()}
    if len(values) != len(parsed):
        raise PreflightConfigurationError("nonempty_json_string_array_required", name)
    return frozenset(values)


def _require_pattern(value: str, pattern: re.Pattern[str], setting: str) -> None:
    if pattern.fullmatch(value) is None or ".." in value:
        raise PreflightConfigurationError("unsafe_setting_value", setting)


def _origin(value: str, setting: str) -> str:
    normalized = value.rstrip("/")
    try:
        parsed = _clean_https_url(normalized)
    except PreflightFailure:
        raise PreflightConfigurationError("https_origin_required", setting) from None
    if parsed.path not in {"", "/"} or parsed.query:
        raise PreflightConfigurationError("https_origin_required", setting)
    return normalized


def _https_url(value: str, setting: str) -> str:
    normalized = value.rstrip("/")
    try:
        parsed = _clean_https_url(normalized)
    except PreflightFailure:
        raise PreflightConfigurationError("clean_https_url_required", setting) from None
    if parsed.query:
        raise PreflightConfigurationError("clean_https_url_required", setting)
    return normalized


def _clean_https_url(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise PreflightFailure("clean_https_url_required")
    return parsed


def _parser(environ: Mapping[str, str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only hosted Governed Release live readiness checks.")
    parser.add_argument("--application-id")
    parser.add_argument("--output", type=Path, default=Path(environ.get(PREFLIGHT_PATH_ENV, "") or DEFAULT_OUTPUT))
    return parser


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    transport: ReadOnlyHttpTransport | None = None,
    render_transport: RenderHttpTransport | None = None,
    stdout: TextIO | None = None,
) -> int:
    source = os.environ if environ is None else environ
    args = _parser(source).parse_args(argv)
    report = run_live_preflight_from_environment(
        source,
        application_id=args.application_id,
        transport=transport,
        render_transport=render_transport,
    )
    serialized = serialize_report(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    (stdout or sys.stdout).write(serialized)
    return 0 if report.is_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
