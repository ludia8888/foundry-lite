from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from foundry_lite.infrastructure.adapters.render_deployment import (
    RenderHttpRequest,
    RenderHttpResponse,
)

from scripts.operations.run_governed_release_live_preflight import (
    ReadOnlyHttpRequest,
    ReadOnlyHttpResponse,
    main,
    run_live_preflight_from_environment,
    serialize_report,
)

APP_ID = "release-app"
PUBLIC_BASE = "https://foundry.example.test"
ISSUER = "https://identity.example.test"
RESOURCE = f"{PUBLIC_BASE}/mcp/release/{APP_ID}"
METADATA_URL = f"{PUBLIC_BASE}/.well-known/oauth-protected-resource/mcp/release/{APP_ID}"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URL = f"{ISSUER}/jwks"
OWNER = "acme"
REPOSITORY = "platform"
REPOSITORY_ID = 42
BASE_REF = "main"
SERVICE_ID = "srv-foundrylite123"
GITHUB_TOKEN = "github-token-must-not-leak"
RENDER_TOKEN = "render-token-must-not-leak"
REPO_URL = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}"
BRANCH_URL = f"{REPO_URL}/branches/{BASE_REF}"
RULES_URL = f"{REPO_URL}/rules/branches/{BASE_REF}?per_page=100&page=1"
PROTECTION_URL = f"{REPO_URL}/branches/{BASE_REF}/protection"


@dataclass
class _HttpTransport:
    responses: dict[str, list[ReadOnlyHttpResponse]]
    requests: list[ReadOnlyHttpRequest] = field(default_factory=list)

    def send(self, request: ReadOnlyHttpRequest) -> ReadOnlyHttpResponse:
        self.requests.append(request)
        return self.responses[request.url].pop(0)


@dataclass
class _RenderTransport:
    response: RenderHttpResponse
    requests: list[RenderHttpRequest] = field(default_factory=list)

    def send(self, request: RenderHttpRequest) -> RenderHttpResponse:
        self.requests.append(request)
        return self.response


def _env() -> dict[str, str]:
    return {
        "FOUNDRY_LITE_GOVERNED_RELEASE_APPLICATION_ID": APP_ID,
        "FOUNDRY_LITE_MCP_PUBLIC_BASE_URL": PUBLIC_BASE,
        "FOUNDRY_LITE_MCP_AUTHORIZATION_SERVER": ISSUER,
        "FOUNDRY_LITE_OIDC_ISSUER": ISSUER,
        "FOUNDRY_LITE_OIDC_AUDIENCE": RESOURCE,
        "FOUNDRY_LITE_OIDC_ALLOWED_CLIENT_IDS_JSON": '["https://chatgpt.com/oauth/release/client.json"]',
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID": str(REPOSITORY_ID),
        "FOUNDRY_LITE_GITHUB_RELEASE_OWNER": OWNER,
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY": REPOSITORY,
        "FOUNDRY_LITE_GITHUB_RELEASE_BASE_REF": BASE_REF,
        "FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF": "github-release-token",
        "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID": SERVICE_ID,
        "FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF": "render-release-token",
        "FOUNDRY_LITE_SECRET_GITHUB_RELEASE_TOKEN": GITHUB_TOKEN,
        "FOUNDRY_LITE_SECRET_RENDER_RELEASE_TOKEN": RENDER_TOKEN,
    }


def _response(payload: object, status: int = 200) -> ReadOnlyHttpResponse:
    return ReadOnlyHttpResponse(status, {}, json.dumps(payload).encode())


def _http_transport(
    *,
    metadata: dict[str, object] | None = None,
    discovery: dict[str, object] | None = None,
    jwks: dict[str, object] | None = None,
    repository: dict[str, object] | None = None,
) -> _HttpTransport:
    return _HttpTransport(
        {
            METADATA_URL: [_response(metadata or _metadata())],
            DISCOVERY_URL: [_response(discovery or _discovery())],
            JWKS_URL: [_response(jwks or {"keys": [{"kid": "key-1", "kty": "RSA"}]})],
            REPO_URL: [_response(repository or _repository())],
            BRANCH_URL: [_response({"name": BASE_REF, "protected": True, "commit": {"sha": "a" * 40}})],
            RULES_URL: [_response([{"type": "required_status_checks", "privateBody": "ignored"}])],
            PROTECTION_URL: [_response({}, status=404)],
        }
    )


def _metadata() -> dict[str, object]:
    return {
        "resource": RESOURCE,
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["osdk:connector:governed_release:execute"],
    }


def _discovery() -> dict[str, object]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": JWKS_URL,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
    }


def _repository() -> dict[str, object]:
    return {
        "id": REPOSITORY_ID,
        "name": REPOSITORY,
        "full_name": f"{OWNER}/{REPOSITORY}",
        "owner": {"login": OWNER},
        "archived": False,
        "disabled": False,
        "permissions": {"push": True, "admin": False},
        "privateBody": "provider-body-must-not-leak",
    }


def _render_transport(*, auto_deploy: bool = False, branch: str = BASE_REF) -> _RenderTransport:
    body = json.dumps(
        {
            "id": SERVICE_ID,
            "autoDeploy": auto_deploy,
            "repo": f"https://github.com/{OWNER}/{REPOSITORY}",
            "branch": branch,
            "type": "web_service",
            "suspended": "not_suspended",
            "privateBody": "render-body-must-not-leak",
        }
    ).encode()
    return _RenderTransport(RenderHttpResponse(200, {}, body))


def test_live_preflight_is_ready_and_every_provider_call_is_read_only() -> None:
    http = _http_transport()
    render = _render_transport()

    report = run_live_preflight_from_environment(_env(), transport=http, render_transport=render)
    serialized = serialize_report(report)

    assert report.is_ready is True
    assert report.status == "ready"
    assert report.evidence_origin == "local_or_injected"
    assert all(check.status == "ready" for check in report.checks)
    configuration = next(check for check in report.checks if check.name == "configuration")
    assert configuration.evidence["allowedOAuthClientCount"] == 1
    assert all(request.method == "GET" for request in http.requests)
    assert all(request.method == "GET" and request.body is None for request in render.requests)
    assert {request.url for request in http.requests} == {
        METADATA_URL,
        DISCOVERY_URL,
        JWKS_URL,
        REPO_URL,
        BRANCH_URL,
        RULES_URL,
        PROTECTION_URL,
    }
    assert GITHUB_TOKEN not in serialized
    assert RENDER_TOKEN not in serialized
    assert "provider-body-must-not-leak" not in serialized
    assert "render-body-must-not-leak" not in serialized
    assert {item.status for item in report.unverified} == {"unverified"}
    assert {item.name for item in report.unverified} == {
        "hosted_chatgpt_two_human_oauth",
        "governed_release_write_end_to_end",
    }


def test_mcp_metadata_must_bind_exact_resource_and_authorization_server() -> None:
    metadata = _metadata()
    metadata["resource"] = f"{PUBLIC_BASE}/mcp/release/other-app"

    report = run_live_preflight_from_environment(
        _env(),
        transport=_http_transport(metadata=metadata),
        render_transport=_render_transport(),
    )

    check = next(item for item in report.checks if item.name == "mcp_protected_resource")
    assert report.status == "blocked"
    assert check.code == "mcp_resource_mismatch"


def test_oidc_requires_authorization_code_pkce_s256_and_nonempty_jwks() -> None:
    discovery = _discovery()
    discovery["code_challenge_methods_supported"] = ["plain"]
    report = run_live_preflight_from_environment(
        _env(),
        transport=_http_transport(discovery=discovery),
        render_transport=_render_transport(),
    )
    check = next(item for item in report.checks if item.name == "oidc_discovery")
    assert check.code == "oidc_pkce_s256_not_supported"

    empty_jwks_report = run_live_preflight_from_environment(
        _env(),
        transport=_http_transport(jwks={"keys": []}),
        render_transport=_render_transport(),
    )
    empty_check = next(item for item in empty_jwks_report.checks if item.name == "oidc_discovery")
    assert empty_check.code == "oidc_jwks_empty_or_invalid"


def test_github_requires_exact_active_repository_and_push_or_admin_permission() -> None:
    repository = _repository()
    repository["permissions"] = {"push": False, "admin": False}

    report = run_live_preflight_from_environment(
        _env(),
        transport=_http_transport(repository=repository),
        render_transport=_render_transport(),
    )

    check = next(item for item in report.checks if item.name == "github_repository")
    assert check.code == "github_push_or_admin_permission_missing"


def test_render_existing_adapter_blocks_auto_deploy_or_wrong_binding() -> None:
    auto_report = run_live_preflight_from_environment(
        _env(),
        transport=_http_transport(),
        render_transport=_render_transport(auto_deploy=True),
    )
    auto_check = next(item for item in auto_report.checks if item.name == "render_service")
    assert auto_check.code == "render_auto_deploy_must_be_off"

    branch_report = run_live_preflight_from_environment(
        _env(),
        transport=_http_transport(),
        render_transport=_render_transport(branch="release"),
    )
    branch_check = next(item for item in branch_report.checks if item.name == "render_service")
    assert branch_check.code == "render_source_branch_mismatch"


def test_missing_secret_ref_resolution_is_blocked_without_leaking_reference_or_value() -> None:
    environ = _env()
    del environ["FOUNDRY_LITE_SECRET_GITHUB_RELEASE_TOKEN"]
    http = _http_transport()

    report = run_live_preflight_from_environment(environ, transport=http, render_transport=_render_transport())
    serialized = serialize_report(report)
    check = next(item for item in report.checks if item.name == "github_repository")

    assert check.code == "github_secret_unresolved"
    assert REPO_URL not in {request.url for request in http.requests}
    assert "github-release-token" not in serialized
    assert GITHUB_TOKEN not in serialized


def test_main_writes_redacted_json_and_returns_nonzero_when_blocked(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    stdout = StringIO()
    environ = _env()
    del environ["FOUNDRY_LITE_MCP_PUBLIC_BASE_URL"]

    exit_code = main(["--output", str(output)], environ=environ, stdout=stdout)
    payload = json.loads(output.read_text())

    assert exit_code == 1
    assert payload["status"] == "blocked"
    assert payload["checks"][0]["code"] == "missing_required_setting"
    assert json.loads(stdout.getvalue()) == payload
    assert GITHUB_TOKEN not in stdout.getvalue()
    assert RENDER_TOKEN not in stdout.getvalue()


def test_main_reports_every_missing_required_setting_at_once(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    stdout = StringIO()

    exit_code = main(["--output", str(output)], environ={}, stdout=stdout)
    payload = json.loads(output.read_text())
    configuration = payload["checks"][0]

    assert exit_code == 1
    assert configuration["code"] == "missing_required_settings"
    assert configuration["evidence"]["settings"] == [
        "FOUNDRY_LITE_GOVERNED_RELEASE_APPLICATION_ID",
        "FOUNDRY_LITE_MCP_PUBLIC_BASE_URL",
        "FOUNDRY_LITE_MCP_AUTHORIZATION_SERVER",
        "FOUNDRY_LITE_OIDC_ISSUER",
        "FOUNDRY_LITE_OIDC_AUDIENCE",
        "FOUNDRY_LITE_OIDC_ALLOWED_CLIENT_IDS_JSON",
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY_ID",
        "FOUNDRY_LITE_GITHUB_RELEASE_OWNER",
        "FOUNDRY_LITE_GITHUB_RELEASE_REPOSITORY",
        "FOUNDRY_LITE_GITHUB_RELEASE_TOKEN_SECRET_REF",
        "FOUNDRY_LITE_RENDER_RELEASE_SERVICE_ID",
        "FOUNDRY_LITE_RENDER_RELEASE_TOKEN_SECRET_REF",
    ]
    assert "setting" not in configuration["evidence"]
    assert json.loads(stdout.getvalue()) == payload
