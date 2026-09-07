"""Keycloak payloads for a single private browser pilot, without operator roles."""

from __future__ import annotations

from urllib.parse import urlsplit


def client_payload(
    client_id: str, application_id: str, tenant_id: str, public_base: str, audience: str
) -> dict[str, object]:
    parsed = urlsplit(public_base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("private_browser_origin_invalid")
    return {
        "clientId": client_id,
        "name": "비공개 업무 앱",
        "protocol": "openid-connect",
        "enabled": True,
        "publicClient": True,
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "fullScopeAllowed": False,
        "consentRequired": True,
        "redirectUris": [f"{public_base}/auth/callback"],
        "webOrigins": [public_base],
        "defaultClientScopes": ["profile", "email"],
        "optionalClientScopes": [],
        "attributes": {
            "foundry.private.application": application_id,
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": f"{public_base}/apps/{application_id}",
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
        },
        "protocolMappers": [
            {
                "name": "private-subject",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-sub-mapper",
                "consentRequired": False,
                "config": {"access.token.claim": "true"},
            },
            _claim("tenant_id", tenant_id),
            _claim("osdk_app_id", application_id),
            _claim("roles", '["viewer"]', "JSON"),
            _claim("aud", audience),
            _claim("human_grant", "true"),
            _claim("authorization_grant_type", "authorization_code"),
        ],
    }


def _claim(name: str, value: str, value_type: str = "String") -> dict[str, object]:
    return {
        "name": f"private-{name}",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-hardcoded-claim-mapper",
        "consentRequired": False,
        "config": {
            "claim.name": name,
            "claim.value": value,
            "jsonType.label": value_type,
            "access.token.claim": "true",
            "id.token.claim": "false",
            "userinfo.token.claim": "false",
        },
    }
