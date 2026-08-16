from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from foundry_lite.infrastructure.auth import (
    OAUTH_ISSUER_ENV,
    AuthProfileConfigurationError,
    HeaderTrustAuthProvider,
    JwtOidcAuthConfig,
    JwtOidcAuthProvider,
)
from foundry_lite_api.mcp_authorization_config import (
    DYNAMIC_CLIENT_APPLICATION_ID_ENV,
    GOVERNED_RELEASE_APPLICATION_ID_ENV,
    LOCAL_CONSENT_ROLES_ENV,
    MCP_AUTHORIZATION_SERVER_ENV,
    MCP_PUBLIC_BASE_URL_ENV,
    RESOURCE_AUDIENCE_SCOPE_PREFIX_ENV,
    governed_release_mcp_authority,
    mcp_authorization_config_from_env,
)
from jwt.algorithms import RSAAlgorithm

_ISSUER = "https://identity.example.test/tenant"
_PUBLIC_BASE = "https://foundry.example.test"
_APPLICATION = "release-app"
_RESOURCE = f"{_PUBLIC_BASE}/mcp/release/{_APPLICATION}"


def test_local_mcp_authorization_configuration_preserves_request_origin() -> None:
    config = mcp_authorization_config_from_env({}, HeaderTrustAuthProvider())

    assert config.authorization_servers("https://local.example.test/oauth") == ("https://local.example.test/oauth",)
    assert config.canonical_base_url("http://testserver/") == "http://testserver"


def test_public_local_mcp_requires_an_explicit_matching_oauth_issuer() -> None:
    with pytest.raises(AuthProfileConfigurationError, match=OAUTH_ISSUER_ENV):
        mcp_authorization_config_from_env(
            {MCP_PUBLIC_BASE_URL_ENV: _PUBLIC_BASE},
            HeaderTrustAuthProvider(),
        )

    with pytest.raises(AuthProfileConfigurationError, match="must exactly match"):
        mcp_authorization_config_from_env(
            {
                MCP_PUBLIC_BASE_URL_ENV: _PUBLIC_BASE,
                OAUTH_ISSUER_ENV: "https://other.example.test",
            },
            HeaderTrustAuthProvider(),
        )


def test_public_local_mcp_accepts_one_canonical_oauth_origin() -> None:
    config = mcp_authorization_config_from_env(
        {
            MCP_PUBLIC_BASE_URL_ENV: f"{_PUBLIC_BASE}/",
            OAUTH_ISSUER_ENV: f"{_PUBLIC_BASE}/",
        },
        HeaderTrustAuthProvider(),
    )

    assert config.public_base_url == _PUBLIC_BASE
    assert config.authorization_servers(_PUBLIC_BASE) == (_PUBLIC_BASE,)


def test_protected_mcp_authorization_requires_external_https_authorization_server() -> None:
    with pytest.raises(AuthProfileConfigurationError, match=MCP_AUTHORIZATION_SERVER_ENV):
        mcp_authorization_config_from_env(
            {"FOUNDRY_LITE_RUNTIME_PROFILE": "production"},
            HeaderTrustAuthProvider(),
        )


def test_external_mcp_authorization_configuration_binds_issuer_and_public_https_origin() -> None:
    config = mcp_authorization_config_from_env(
        {**_external_env(), RESOURCE_AUDIENCE_SCOPE_PREFIX_ENV: "mcp-audience"},
        _external_provider(),
    )

    assert config.authorization_servers("https://unused.local") == (_ISSUER,)
    assert config.canonical_base_url("http://internal:8000/") == _PUBLIC_BASE
    assert config.resource_audience_scope(_RESOURCE) == f"mcp-audience:{_RESOURCE}"


def test_resource_audience_scope_prefix_is_bounded() -> None:
    with pytest.raises(AuthProfileConfigurationError, match=RESOURCE_AUDIENCE_SCOPE_PREFIX_ENV):
        mcp_authorization_config_from_env(
            {**_external_env(), RESOURCE_AUDIENCE_SCOPE_PREFIX_ENV: "bad prefix"},
            _external_provider(),
        )


def test_external_mcp_authorization_projects_exact_live_release_authority() -> None:
    provider = _external_provider(
        allowed_client_ids=frozenset({"client-b", "client-a"}),
    )
    config = mcp_authorization_config_from_env(_external_env(), provider)

    authority = governed_release_mcp_authority(config, provider)

    assert authority.is_live_eligible is True
    assert authority.application_id == _APPLICATION
    assert authority.public_base_url == _PUBLIC_BASE
    assert authority.authorization_server_issuer == _ISSUER
    assert authority.oauth_audience == _RESOURCE
    assert authority.allowed_client_ids == ("client-a", "client-b")
    assert authority.release_resource(_APPLICATION) == _RESOURCE


def test_external_mcp_authority_fails_closed_on_application_or_audience_drift() -> None:
    config = mcp_authorization_config_from_env(_external_env(), _external_provider())
    wrong_audience = _external_provider(audience="https://foundry.example.test/mcp/release/other-app")

    missing_application = governed_release_mcp_authority(
        mcp_authorization_config_from_env(
            {key: value for key, value in _external_env().items() if key != GOVERNED_RELEASE_APPLICATION_ID_ENV},
            _external_provider(),
        ),
        _external_provider(),
    )
    mismatched_audience = governed_release_mcp_authority(config, wrong_audience)

    assert missing_application.is_live_eligible is False
    assert mismatched_audience.is_live_eligible is False


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        (MCP_AUTHORIZATION_SERVER_ENV, "http://identity.example.test"),
        (MCP_AUTHORIZATION_SERVER_ENV, "https://user@identity.example.test"),
        (MCP_AUTHORIZATION_SERVER_ENV, "https://identity.example.test?tenant=a"),
        (MCP_PUBLIC_BASE_URL_ENV, "https://foundry.example.test/api"),
        (MCP_PUBLIC_BASE_URL_ENV, "not-a-url"),
        (GOVERNED_RELEASE_APPLICATION_ID_ENV, "release/app"),
    ],
)
def test_external_mcp_authorization_rejects_unsafe_urls(setting: str, value: str) -> None:
    expected = "clean application id" if setting == GOVERNED_RELEASE_APPLICATION_ID_ENV else "clean absolute HTTPS URL"
    with pytest.raises(AuthProfileConfigurationError, match=expected):
        mcp_authorization_config_from_env({**_external_env(), setting: value}, _external_provider())


def test_external_mcp_authorization_requires_strict_matching_oidc_provider() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="requires FOUNDRY_LITE_AUTH_PROFILE"):
        mcp_authorization_config_from_env(_external_env(), HeaderTrustAuthProvider())

    with pytest.raises(AuthProfileConfigurationError, match="must match"):
        mcp_authorization_config_from_env(
            _external_env(),
            _external_provider(issuer="https://other-identity.example.test"),
        )


def test_external_mcp_authorization_requires_public_origin_and_human_grant_evidence() -> None:
    without_public = {MCP_AUTHORIZATION_SERVER_ENV: _ISSUER}
    with pytest.raises(AuthProfileConfigurationError, match=MCP_PUBLIC_BASE_URL_ENV):
        mcp_authorization_config_from_env(without_public, _external_provider())

    with pytest.raises(AuthProfileConfigurationError, match="human grant"):
        mcp_authorization_config_from_env(
            _external_env(),
            _external_provider(human_grant_claim=None, human_grant_value=None),
        )
    with pytest.raises(AuthProfileConfigurationError, match="authorization-code grant"):
        mcp_authorization_config_from_env(
            _external_env(),
            _external_provider(grant_type_claim=None, grant_type_value=None),
        )


def test_external_mcp_authorization_requires_a_usable_jwks_key() -> None:
    provider = JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=_ISSUER,
            audience="foundry-lite",
            jwks={"keys": []},
            session_claim="sid",
            oauth_session_authority="issuer",
            human_grant_claim="gty",
            human_grant_value="authorization_code",
            grant_type_claim="gty",
            grant_type_value="authorization_code",
            allowed_client_ids=frozenset({"https://chatgpt.com/oauth/release/client.json"}),
        )
    )

    with pytest.raises(AuthProfileConfigurationError, match="JWKS verification key"):
        mcp_authorization_config_from_env(_external_env(), provider)


def test_external_mcp_authorization_requires_an_allowed_oauth_client() -> None:
    with pytest.raises(AuthProfileConfigurationError, match="allowed OAuth client id"):
        mcp_authorization_config_from_env(
            _external_env(),
            _external_provider(allowed_client_ids=frozenset()),
        )


def test_local_dynamic_client_registration_is_opt_in_per_application() -> None:
    disabled = mcp_authorization_config_from_env({}, HeaderTrustAuthProvider())
    enabled = mcp_authorization_config_from_env(
        {DYNAMIC_CLIENT_APPLICATION_ID_ENV: _APPLICATION},
        HeaderTrustAuthProvider(),
    )

    assert disabled.dynamic_registration_application_id() is None
    assert enabled.dynamic_registration_application_id() == _APPLICATION


def test_local_consent_roles_are_opt_in_and_parsed_as_a_role_list() -> None:
    default = mcp_authorization_config_from_env({}, HeaderTrustAuthProvider())
    configured = mcp_authorization_config_from_env(
        {LOCAL_CONSENT_ROLES_ENV: "admin, data_engineer"},
        HeaderTrustAuthProvider(),
    )

    assert default.consent_roles() == ()
    assert configured.consent_roles() == ("admin", "data_engineer")

    with pytest.raises(AuthProfileConfigurationError, match=LOCAL_CONSENT_ROLES_ENV):
        mcp_authorization_config_from_env({LOCAL_CONSENT_ROLES_ENV: "Not A Role!"}, HeaderTrustAuthProvider())


def test_local_consent_roles_never_apply_once_an_external_idp_owns_identity() -> None:
    external = mcp_authorization_config_from_env(
        {**_external_env(), LOCAL_CONSENT_ROLES_ENV: "admin"},
        _external_provider(),
    )

    assert external.consent_roles() == ()

    with pytest.raises(AuthProfileConfigurationError, match=LOCAL_CONSENT_ROLES_ENV):
        mcp_authorization_config_from_env(
            {
                "FOUNDRY_LITE_RUNTIME_PROFILE": "production",
                MCP_AUTHORIZATION_SERVER_ENV: _ISSUER,
                MCP_PUBLIC_BASE_URL_ENV: _PUBLIC_BASE,
                LOCAL_CONSENT_ROLES_ENV: "admin",
            },
            _external_provider(),
        )


def test_dynamic_client_registration_is_refused_outside_the_local_authorization_server() -> None:
    external = mcp_authorization_config_from_env(
        {**_external_env(), DYNAMIC_CLIENT_APPLICATION_ID_ENV: _APPLICATION},
        _external_provider(),
    )

    assert external.dynamic_registration_application_id() is None

    with pytest.raises(AuthProfileConfigurationError, match=DYNAMIC_CLIENT_APPLICATION_ID_ENV):
        mcp_authorization_config_from_env(
            {
                "FOUNDRY_LITE_RUNTIME_PROFILE": "production",
                MCP_AUTHORIZATION_SERVER_ENV: _ISSUER,
                MCP_PUBLIC_BASE_URL_ENV: _PUBLIC_BASE,
                DYNAMIC_CLIENT_APPLICATION_ID_ENV: _APPLICATION,
            },
            _external_provider(),
        )


def _external_env() -> dict[str, str]:
    return {
        MCP_AUTHORIZATION_SERVER_ENV: _ISSUER,
        MCP_PUBLIC_BASE_URL_ENV: _PUBLIC_BASE,
        GOVERNED_RELEASE_APPLICATION_ID_ENV: _APPLICATION,
    }


def _external_provider(
    *,
    issuer: str = _ISSUER,
    audience: str = _RESOURCE,
    human_grant_claim: str | None = "gty",
    human_grant_value: str | None = "authorization_code",
    grant_type_claim: str | None = "gty",
    grant_type_value: str | None = "authorization_code",
    allowed_client_ids: frozenset[str] = frozenset({"https://chatgpt.com/oauth/release/client.json"}),
) -> JwtOidcAuthProvider:
    public_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    jwk = dict(RSAAlgorithm.to_jwk(public_key, as_dict=True))
    jwk["kid"] = "mcp-authorization-test-key"
    return JwtOidcAuthProvider(
        JwtOidcAuthConfig(
            issuer=issuer,
            audience=audience,
            jwks={"keys": [jwk]},
            session_claim="sid",
            oauth_session_authority="issuer",
            human_grant_claim=human_grant_claim,
            human_grant_value=human_grant_value,
            grant_type_claim=grant_type_claim,
            grant_type_value=grant_type_value,
            allowed_client_ids=allowed_client_ids,
        )
    )
