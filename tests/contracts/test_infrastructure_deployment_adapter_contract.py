from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentAdapter,
    InfrastructureDeploymentCandidateQuery,
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentOutcomeUnknown,
    InfrastructureDeploymentRollbackRequest,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentStartRequest,
    UnavailableInfrastructureDeploymentAdapter,
)
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.infrastructure.adapters.render_deployment import (
    RenderHttpRequest,
    RenderHttpResponse,
    RenderInfrastructureDeploymentAdapter,
    RenderTransportError,
    UrllibRenderHttpTransport,
)
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider

SERVICE_ID = "srv-foundrylite123"
DEPLOY_ID = "dep-deploy123"
NEW_DEPLOY_ID = "dep-deploy456"
COMMIT_ID = "a" * 40
OTHER_COMMIT_ID = "b" * 40
TOKEN = "render-secret-token-that-must-never-leak"
SOURCE_REPOSITORY = "https://github.com/acme/platform"
SOURCE_BRANCH = "main"


@dataclass
class _SequenceTransport:
    outcomes: list[RenderHttpResponse | RenderTransportError]
    requests: list[RenderHttpRequest] = field(default_factory=list)

    def send(self, request: RenderHttpRequest) -> RenderHttpResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, RenderTransportError):
            raise outcome
        return outcome


@dataclass
class _RotatingSecretProvider:
    values: list[str]
    calls: list[str] = field(default_factory=list)
    profile_name: str = "rotating-test-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(AdapterFailureMode("get_secret", "validation", False, "test"),),
        )

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        self.calls.append(name)
        value = self.values.pop(0)
        return SecretValue(name=name, version=version or f"v{len(self.calls)}", value=value)


class _LeakyTransport:
    def send(self, request: RenderHttpRequest) -> RenderHttpResponse:
        raise RuntimeError(TOKEN)


class _LeakySecretProvider:
    profile_name = "leaky-test-secret"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        raise RuntimeError(TOKEN)


def _secret_provider() -> EnvSecretProvider:
    return EnvSecretProvider(
        env_aliases={"render_api_token": "RENDER_API_TOKEN"},
        environ={"RENDER_API_TOKEN": TOKEN},
    )


def _adapter(
    transport: _SequenceTransport,
    *,
    max_response_bytes: int = 1024 * 1024,
) -> RenderInfrastructureDeploymentAdapter:
    return RenderInfrastructureDeploymentAdapter(
        _secret_provider(),
        token_secret_ref="render_api_token",
        transport=transport,
        max_response_bytes=max_response_bytes,
    )


def _start_request(
    *,
    service_id: str = SERVICE_ID,
    commit_id: str = COMMIT_ID,
) -> InfrastructureDeploymentStartRequest:
    return InfrastructureDeploymentStartRequest(
        tenant_id="tenant-1",
        service_id=service_id,
        commit_id=commit_id,
        idempotency_key="deploy-idempotency-1",
        request_id="request-1",
        correlation_id="correlation-1",
    )


def _service_policy_request(
    *,
    service_id: str = SERVICE_ID,
) -> InfrastructureDeploymentServicePolicyRequest:
    return InfrastructureDeploymentServicePolicyRequest(
        tenant_id="tenant-1",
        service_id=service_id,
        request_id="request-1",
        correlation_id="correlation-1",
    )


def _get_request(
    *,
    service_id: str = SERVICE_ID,
    deploy_id: str = DEPLOY_ID,
) -> InfrastructureDeploymentGetRequest:
    return InfrastructureDeploymentGetRequest(
        tenant_id="tenant-1",
        service_id=service_id,
        deploy_id=deploy_id,
        request_id="request-1",
        correlation_id="correlation-1",
    )


def _rollback_request(
    *,
    target_deploy_id: str = DEPLOY_ID,
    target_commit_id: str = COMMIT_ID,
) -> InfrastructureDeploymentRollbackRequest:
    return InfrastructureDeploymentRollbackRequest(
        tenant_id="tenant-1",
        service_id=SERVICE_ID,
        target_deploy_id=target_deploy_id,
        target_commit_id=target_commit_id,
        idempotency_key="rollback-idempotency-1",
        request_id="request-1",
        correlation_id="correlation-1",
    )


def _response(
    status_code: int,
    body: bytes = b"",
    *,
    headers: dict[str, str] | None = None,
) -> RenderHttpResponse:
    return RenderHttpResponse(status_code=status_code, headers=headers or {}, body=body)


def _deploy_body(
    *,
    deploy_id: str = DEPLOY_ID,
    status: str = "queued",
    commit_id: str | None = COMMIT_ID,
    trigger: str = "api",
) -> bytes:
    payload: dict[str, object] = {
        "id": deploy_id,
        "status": status,
        "trigger": trigger,
        "createdAt": "2026-08-09T01:02:03Z",
        "updatedAt": "2026-08-09T01:03:03Z",
    }
    if commit_id is not None:
        payload["commit"] = {"id": commit_id}
    return json.dumps(payload).encode("utf-8")


def test_render_adapter_satisfies_the_typed_port() -> None:
    adapter: InfrastructureDeploymentAdapter = RenderInfrastructureDeploymentAdapter(
        _secret_provider(),
        token_secret_ref="render_api_token",
        transport=_SequenceTransport([]),
    )
    assert adapter.profile_name == "render-infrastructure-deployment"
    assert adapter.provider_name == "render"
    assert adapter.is_live_provider is True
    operations = {mode.operation for mode in adapter.failure_contract().modes}
    assert operations == {"get_service_policy", "start", "get", "list_candidates", "rollback"}


def test_unavailable_adapter_fails_closed_for_mutations() -> None:
    adapter: InfrastructureDeploymentAdapter = UnavailableInfrastructureDeploymentAdapter()
    with pytest.raises(AdapterError) as raised:
        adapter.start(_start_request())
    assert raised.value.failure.kind == "unavailable"
    assert raised.value.failure.is_retryable is False
    assert raised.value.failure.idempotency_key == "deploy-idempotency-1"


def test_unavailable_adapter_fails_closed_for_live_service_policy_reads() -> None:
    adapter: InfrastructureDeploymentAdapter = UnavailableInfrastructureDeploymentAdapter()
    with pytest.raises(AdapterError) as raised:
        adapter.get_service_policy(_service_policy_request())
    assert raised.value.failure.operation == "get_service_policy"
    assert raised.value.failure.kind == "unavailable"
    assert raised.value.failure.is_retryable is False


@pytest.mark.parametrize("is_auto_deploy_enabled", [False, True])
def test_service_policy_reads_the_exact_service_auto_deploy_setting(
    is_auto_deploy_enabled: bool,
) -> None:
    body = json.dumps(
        {
            "id": SERVICE_ID,
            "autoDeploy": is_auto_deploy_enabled,
            "repo": SOURCE_REPOSITORY,
            "branch": SOURCE_BRANCH,
            "type": "web_service",
            "suspended": "not_suspended",
        }
    ).encode("utf-8")
    transport = _SequenceTransport([_response(200, body, headers={"x-render-request-id": "render-policy-request-1"})])

    observation = _adapter(transport).get_service_policy(_service_policy_request())

    assert observation.provider == "render"
    assert observation.service_id == SERVICE_ID
    assert observation.release_mode == "source_revision"
    assert observation.trigger_mode == ("automatic" if is_auto_deploy_enabled else "manual")
    assert observation.source_binding is not None
    assert observation.source_binding.provider == "github"
    assert observation.source_binding.repository_owner == "acme"
    assert observation.source_binding.repository_name == "platform"
    assert observation.source_binding.ref == SOURCE_BRANCH
    assert observation.workload_kind == "web_service"
    assert observation.is_suspended is False
    assert observation.provider_request_id == "render-policy-request-1"
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent.method == "GET"
    assert sent.url == f"https://api.render.com/v1/services/{SERVICE_ID}"
    assert sent.body is None


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {
                "id": "srv-another-service",
                "autoDeploy": False,
                "repo": SOURCE_REPOSITORY,
                "branch": SOURCE_BRANCH,
                "type": "web_service",
                "suspended": "not_suspended",
            },
            "provider_service_id_mismatch",
        ),
        ({"id": SERVICE_ID}, "provider_auto_deploy_policy_is_invalid"),
        (
            {
                "id": SERVICE_ID,
                "autoDeploy": "false",
                "repo": SOURCE_REPOSITORY,
                "branch": SOURCE_BRANCH,
                "type": "web_service",
                "suspended": "not_suspended",
            },
            "provider_auto_deploy_policy_is_invalid",
        ),
        (
            {
                "id": SERVICE_ID,
                "autoDeploy": False,
                "branch": SOURCE_BRANCH,
                "type": "web_service",
                "suspended": "not_suspended",
            },
            "provider_source_repository_is_invalid",
        ),
        (
            {
                "id": SERVICE_ID,
                "autoDeploy": False,
                "repo": "https://github.com/acme/platform?token=leak",
                "branch": SOURCE_BRANCH,
                "type": "web_service",
                "suspended": "not_suspended",
            },
            "provider_source_repository_is_invalid",
        ),
        (
            {
                "id": SERVICE_ID,
                "autoDeploy": False,
                "repo": SOURCE_REPOSITORY,
                "type": "web_service",
                "suspended": "not_suspended",
            },
            "provider_source_branch_is_invalid",
        ),
        (
            {
                "id": SERVICE_ID,
                "autoDeploy": False,
                "repo": SOURCE_REPOSITORY,
                "branch": SOURCE_BRANCH,
                "type": "cron_job",
                "suspended": "not_suspended",
            },
            "provider_service_type_is_not_web_service",
        ),
        (
            {
                "id": SERVICE_ID,
                "autoDeploy": False,
                "repo": SOURCE_REPOSITORY,
                "branch": SOURCE_BRANCH,
                "type": "web_service",
            },
            "provider_service_suspension_is_invalid",
        ),
    ],
)
def test_service_policy_fails_closed_for_unbound_or_ambiguous_provider_evidence(
    payload: dict[str, object],
    reason: str,
) -> None:
    transport = _SequenceTransport([_response(200, json.dumps(payload).encode("utf-8"))])

    with pytest.raises(AdapterError) as raised:
        _adapter(transport).get_service_policy(_service_policy_request())

    assert raised.value.failure.operation == "get_service_policy"
    assert raised.value.failure.kind == "validation"
    assert raised.value.failure.is_retryable is False
    assert raised.value.failure.details["reason"] == reason


def test_service_policy_rejects_service_path_injection_before_http() -> None:
    transport = _SequenceTransport([])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport).get_service_policy(_service_policy_request(service_id="../admin"))
    assert raised.value.failure.details["reason"] == "invalid_service_id"
    assert transport.requests == []


def test_start_posts_only_the_exact_commit_and_returns_the_provider_receipt() -> None:
    transport = _SequenceTransport(
        [_response(201, _deploy_body(), headers={"x-render-request-id": "render-request-1"})]
    )
    result = _adapter(transport).start(_start_request(commit_id=COMMIT_ID.upper()))

    assert result.outcome == "accepted"
    assert result.is_safe_to_retry is False
    assert result.observation is not None
    assert result.observation.deploy_id == DEPLOY_ID
    assert result.observation.commit_id == COMMIT_ID
    assert result.observation.status == "queued"
    assert result.observation.provider_request_id == "render-request-1"
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent.method == "POST"
    assert sent.url == f"https://api.render.com/v1/services/{SERVICE_ID}/deploys"
    assert json.loads(sent.body or b"") == {"commitId": COMMIT_ID}
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in repr(sent)


def test_start_202_is_outcome_unknown_and_is_never_retried_by_the_adapter() -> None:
    transport = _SequenceTransport([_response(202)])
    result = _adapter(transport).start(_start_request())

    assert result.outcome == "outcome_unknown"
    assert result.observation is None
    assert result.is_safe_to_retry is False
    assert result.reason == "queued_without_provider_receipt"
    assert len(transport.requests) == 1


def test_mutation_timeout_is_outcome_unknown_and_is_not_blindly_retried() -> None:
    transport = _SequenceTransport([RenderTransportError("timeout")])
    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        _adapter(transport).start(_start_request())

    assert len(transport.requests) == 1
    assert raised.value.is_safe_to_retry is False
    assert raised.value.failure.is_retryable is False
    assert raised.value.failure.kind == "timeout"
    assert raised.value.failure.details["knownNotCommitted"] is False
    assert raised.value.failure.details["safeToRetry"] is False


@pytest.mark.parametrize(
    ("provider_status", "canonical_status", "is_terminal", "is_successful"),
    [
        ("created", "queued", False, False),
        ("queued", "queued", False, False),
        ("build_in_progress", "building", False, False),
        ("pre_deploy_in_progress", "preparing", False, False),
        ("update_in_progress", "deploying", False, False),
        ("live", "live", True, True),
        ("deactivated", "deactivated", True, False),
        ("build_failed", "failed", True, False),
        ("pre_deploy_failed", "failed", True, False),
        ("update_failed", "failed", True, False),
        ("canceled", "canceled", True, False),
        ("future_provider_status", "unknown", False, False),
    ],
)
def test_get_maps_every_documented_render_status(
    provider_status: str,
    canonical_status: str,
    is_terminal: bool,
    is_successful: bool,
) -> None:
    transport = _SequenceTransport([_response(200, _deploy_body(status=provider_status))])
    observation = _adapter(transport).get(_get_request())

    assert observation.provider_status == provider_status
    assert observation.status == canonical_status
    assert observation.is_terminal is is_terminal
    assert observation.is_successful is is_successful
    assert transport.requests[0].url.endswith(f"/services/{SERVICE_ID}/deploys/{DEPLOY_ID}")


def test_list_candidates_uses_a_bounded_window_and_filters_the_exact_commit() -> None:
    created_after = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    rows = [
        {"deploy": json.loads(_deploy_body(deploy_id=DEPLOY_ID, commit_id=COMMIT_ID)), "cursor": "one"},
        {"deploy": json.loads(_deploy_body(deploy_id=NEW_DEPLOY_ID, commit_id=OTHER_COMMIT_ID)), "cursor": "two"},
    ]
    transport = _SequenceTransport([_response(200, json.dumps(rows).encode("utf-8"))])
    query = InfrastructureDeploymentCandidateQuery(
        tenant_id="tenant-1",
        service_id=SERVICE_ID,
        commit_id=COMMIT_ID,
        created_after=created_after,
        created_before=created_after + timedelta(minutes=10),
        request_id="request-1",
        correlation_id="correlation-1",
        limit=20,
    )

    candidates = _adapter(transport).list_candidates(query)

    assert [candidate.deploy_id for candidate in candidates] == [DEPLOY_ID]
    url = transport.requests[0].url
    assert url.startswith(f"https://api.render.com/v1/services/{SERVICE_ID}/deploys?")
    assert "createdAfter=2026-08-09T01%3A00%3A00Z" in url
    assert "createdBefore=2026-08-09T01%3A10%3A00Z" in url
    assert "limit=20" in url


def test_list_candidates_rechecks_provider_created_at_in_the_requested_window() -> None:
    created_after = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    stale = json.loads(_deploy_body(deploy_id=DEPLOY_ID, commit_id=COMMIT_ID))
    stale["createdAt"] = "2020-01-01T00:00:00Z"
    rows = [{"deploy": stale, "cursor": "stale"}]
    transport = _SequenceTransport([_response(200, json.dumps(rows).encode("utf-8"))])
    query = InfrastructureDeploymentCandidateQuery(
        tenant_id="tenant-1",
        service_id=SERVICE_ID,
        commit_id=COMMIT_ID,
        created_after=created_after,
        created_before=created_after + timedelta(minutes=10),
        request_id="request-1",
        correlation_id="correlation-1",
        limit=20,
    )

    assert _adapter(transport).list_candidates(query) == ()


def test_list_candidates_follows_cursor_pages_before_proving_exact_absence() -> None:
    created_after = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
    first_page = [
        {
            "deploy": json.loads(_deploy_body(deploy_id=f"dep-unrelated-{index:02d}", commit_id=OTHER_COMMIT_ID)),
            "cursor": f"cursor-{index:02d}",
        }
        for index in range(20)
    ]
    second_page = [{"deploy": json.loads(_deploy_body(deploy_id=DEPLOY_ID, commit_id=COMMIT_ID)), "cursor": "exact"}]
    transport = _SequenceTransport(
        [
            _response(200, json.dumps(first_page).encode("utf-8")),
            _response(200, json.dumps(second_page).encode("utf-8")),
        ]
    )
    query = InfrastructureDeploymentCandidateQuery(
        tenant_id="tenant-1",
        service_id=SERVICE_ID,
        commit_id=COMMIT_ID,
        created_after=created_after,
        created_before=created_after + timedelta(minutes=10),
        request_id="request-1",
        correlation_id="correlation-1",
        limit=20,
    )

    candidates = _adapter(transport).list_candidates(query)

    assert [candidate.deploy_id for candidate in candidates] == [DEPLOY_ID]
    assert len(transport.requests) == 2
    assert "cursor=cursor-19" in transport.requests[1].url


def test_rollback_posts_a_verified_target_and_returns_a_new_deploy_receipt() -> None:
    transport = _SequenceTransport(
        [_response(201, _deploy_body(deploy_id=NEW_DEPLOY_ID, status="created", trigger="rollback"))]
    )
    result = _adapter(transport).rollback(_rollback_request())

    assert result.outcome == "accepted"
    assert result.rollback_target_deploy_id == DEPLOY_ID
    assert result.observation is not None
    assert result.observation.deploy_id == NEW_DEPLOY_ID
    assert result.observation.trigger == "rollback"
    sent = transport.requests[0]
    assert sent.url == f"https://api.render.com/v1/services/{SERVICE_ID}/rollback"
    assert json.loads(sent.body or b"") == {"deployId": DEPLOY_ID}


def test_rollback_rejects_a_response_that_reuses_the_target_deploy_id() -> None:
    transport = _SequenceTransport([_response(201, _deploy_body(deploy_id=DEPLOY_ID, trigger="rollback"))])
    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        _adapter(transport).rollback(_rollback_request())

    assert raised.value.failure.details["reason"] == "rollback_did_not_return_new_deploy"
    assert raised.value.failure.is_retryable is False


@pytest.mark.parametrize(
    ("commit_id", "trigger", "reason"),
    [
        (OTHER_COMMIT_ID, "rollback", "provider_commit_mismatch"),
        (COMMIT_ID, "api", "provider_rollback_trigger_mismatch"),
    ],
)
def test_rollback_rejects_a_receipt_not_bound_to_the_approved_revision(
    commit_id: str,
    trigger: str,
    reason: str,
) -> None:
    transport = _SequenceTransport(
        [
            _response(
                201,
                _deploy_body(
                    deploy_id=NEW_DEPLOY_ID,
                    commit_id=commit_id,
                    trigger=trigger,
                ),
            )
        ]
    )

    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        _adapter(transport).rollback(_rollback_request())

    assert raised.value.failure.details["reason"] == reason
    assert raised.value.failure.is_retryable is False


@pytest.mark.parametrize(
    ("service_id", "commit_id", "reason"),
    [
        ("../admin", COMMIT_ID, "invalid_service_id"),
        ("srv-valid123", "main", "exact_commit_sha_required"),
        ("https://evil.example", COMMIT_ID, "invalid_service_id"),
    ],
)
def test_start_rejects_path_injection_and_non_exact_revisions_before_http(
    service_id: str,
    commit_id: str,
    reason: str,
) -> None:
    transport = _SequenceTransport([])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport).start(_start_request(service_id=service_id, commit_id=commit_id))

    assert raised.value.failure.kind == "validation"
    assert raised.value.failure.details["reason"] == reason
    assert raised.value.failure.details["knownNotCommitted"] is True
    assert transport.requests == []


def test_get_rejects_an_invalid_deploy_id_before_http() -> None:
    transport = _SequenceTransport([])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport).get(_get_request(deploy_id="../../secret"))
    assert raised.value.failure.details["reason"] == "invalid_deploy_id"
    assert transport.requests == []


def test_api_token_and_provider_body_are_redacted_from_failures() -> None:
    echoed_body = json.dumps({"error": TOKEN}).encode("utf-8")
    transport = _SequenceTransport([_response(401, echoed_body)])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport).start(_start_request())

    rendered = json.dumps(raised.value.details, sort_keys=True)
    assert TOKEN not in rendered
    assert TOKEN not in str(raised.value)
    assert raised.value.failure.kind == "authentication"
    assert raised.value.failure.is_retryable is False


def test_untrusted_transport_and_secret_errors_cannot_leak_credentials() -> None:
    transport_adapter = RenderInfrastructureDeploymentAdapter(
        _secret_provider(),
        token_secret_ref="render_api_token",
        transport=_LeakyTransport(),
    )
    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as transport_failure:
        transport_adapter.start(_start_request())
    assert TOKEN not in str(transport_failure.value)
    assert TOKEN not in json.dumps(transport_failure.value.details)
    assert transport_failure.value.__cause__ is None

    secret_adapter = RenderInfrastructureDeploymentAdapter(
        _LeakySecretProvider(),
        token_secret_ref="render_api_token",
        transport=_SequenceTransport([]),
    )
    with pytest.raises(AdapterError) as secret_failure:
        secret_adapter.start(_start_request())
    assert TOKEN not in str(secret_failure.value)
    assert TOKEN not in json.dumps(secret_failure.value.details)
    assert secret_failure.value.__cause__ is None


def test_rate_limit_is_a_known_rejection_with_retry_after_evidence() -> None:
    transport = _SequenceTransport([_response(429, headers={"retry-after": "7"})])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport).start(_start_request())

    assert not isinstance(raised.value, InfrastructureDeploymentOutcomeUnknown)
    assert raised.value.failure.kind == "rate_limited"
    assert raised.value.failure.is_retryable is True
    assert raised.value.failure.details["knownNotCommitted"] is True
    assert raised.value.failure.details["retryAfterSeconds"] == 7.0


def test_provider_server_error_after_mutation_dispatch_is_outcome_unknown() -> None:
    transport = _SequenceTransport([_response(503)])
    with pytest.raises(InfrastructureDeploymentOutcomeUnknown) as raised:
        _adapter(transport).start(_start_request())
    assert raised.value.failure.is_retryable is False
    assert raised.value.failure.details["knownNotCommitted"] is False


def test_read_transport_timeout_is_retryable_because_it_cannot_mutate() -> None:
    transport = _SequenceTransport([RenderTransportError("timeout")])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport).get(_get_request())
    assert not isinstance(raised.value, InfrastructureDeploymentOutcomeUnknown)
    assert raised.value.failure.kind == "timeout"
    assert raised.value.failure.is_retryable is True


def test_secret_is_resolved_again_for_each_provider_request() -> None:
    secrets = _RotatingSecretProvider(["token-one", "token-two"])
    transport = _SequenceTransport(
        [
            _response(200, _deploy_body()),
            _response(200, _deploy_body()),
        ]
    )
    adapter = RenderInfrastructureDeploymentAdapter(
        secrets,
        token_secret_ref="render_api_token",
        transport=transport,
    )

    adapter.get(_get_request())
    adapter.get(_get_request())

    assert secrets.calls == ["render_api_token", "render_api_token"]
    assert [request.headers["authorization"] for request in transport.requests] == [
        "Bearer token-one",
        "Bearer token-two",
    ]


def test_timeout_and_response_size_are_bounded() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        RenderInfrastructureDeploymentAdapter(
            _secret_provider(),
            token_secret_ref="render_api_token",
            transport=_SequenceTransport([]),
            timeout_seconds=31,
        )

    transport = _SequenceTransport([_response(200, b"{}")])
    with pytest.raises(AdapterError) as raised:
        _adapter(transport, max_response_bytes=1).get(_get_request())
    assert raised.value.failure.details["reason"] == "response_too_large"


def test_default_transport_rejects_every_non_render_origin_before_network() -> None:
    request = RenderHttpRequest(
        method="GET",
        url="https://evil.example/v1/services/srv-stolen/deploys",
        headers={"authorization": f"Bearer {TOKEN}"},
    )
    with pytest.raises(RenderTransportError) as raised:
        UrllibRenderHttpTransport().send(request)
    assert raised.value.kind == "unavailable"
    assert TOKEN not in str(raised.value)
