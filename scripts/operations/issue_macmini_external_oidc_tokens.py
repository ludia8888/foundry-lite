"""Issue fresh QA-only Authorization Code + PKCE tokens through external Keycloak HTTPS."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import stat
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from foundry_lite.application.services.aip.governed_release_authorization import (
    GOVERNED_RELEASE_SCOPE,
)

from scripts.operations.macmini_qa_guard import (
    QA_ROOT,
    assert_host_boundary,
    utc_now,
    write_json_receipt,
)

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_REDIRECT_URI = "https://chatgpt.com/aip/foundry-lite-enterprise-qa/callback"


@dataclass(frozen=True, slots=True)
class IssuedClient:
    client_id: str
    registration_uri: str
    registration_token: str


@dataclass(slots=True)
class _Form:
    action: str = ""
    method: str = "get"
    fields: dict[str, str] = field(default_factory=dict)
    buttons: set[str] = field(default_factory=set)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[_Form] = []
        self._current: _Form | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "form":
            self._current = _Form(action=values.get("action", ""), method=values.get("method", "get").lower())
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None and values.get("name"):
            self._current.fields[values["name"]] = values.get("value", "")
        elif tag == "button" and self._current is not None and values.get("name"):
            self._current.buttons.add(values["name"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


class _OAuthCallback(Exception):
    def __init__(self, code: str, state: str) -> None:
        super().__init__("oauth_callback_captured")
        self.code = code
        self.state = state


class _BoundedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, identity_origin: str, expected_state: str) -> None:
        super().__init__()
        self.identity_origin = identity_origin
        self.expected_state = expected_state

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlsplit(target)
        if _is_callback(parsed):
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
            code_value = _single(query, "code")
            state_value = _single(query, "state")
            raise _OAuthCallback(code_value, state_value)
        if _origin(target) != self.identity_origin:
            raise RuntimeError("macmini_oidc_authorization_redirect_invalid")
        return super().redirect_request(req, fp, code, msg, headers, target)


def issue(args: argparse.Namespace) -> tuple[dict[str, object], IssuedClient]:
    assert_host_boundary()
    identity_origin = _https_origin(args.identity_base_url)
    audience = _https_url(args.audience)
    credentials = _private_principals(args.principals_file)
    discovery = _json_request(f"{identity_origin}/realms/foundry-lite/.well-known/openid-configuration")
    client = _register_client(discovery)
    try:
        tokens = {
            role: _authorization_code_token(discovery, client.client_id, identity_origin, audience, username, password)
            for role, (username, password) in credentials.items()
        }
    except BaseException:
        delete_client(client)
        raise
    _write_token("author-token", tokens["author"])
    _write_token("reviewer-token", tokens["reviewer"])
    receipt = _receipt(identity_origin, audience, client.client_id)
    target = QA_ROOT / "evidence" / args.run_id / "external-oidc-token-issuance.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json_receipt(target, receipt)
    return receipt, client


def delete_client(client: IssuedClient) -> bool:
    request = urllib.request.Request(
        client.registration_uri,
        method="DELETE",
        headers={"authorization": f"Bearer {client.registration_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - URI came from trusted HTTPS IdP.
            return response.status in {200, 204}
    except urllib.error.HTTPError as exc:
        return exc.code == 404


def _register_client(discovery: dict[str, object]) -> IssuedClient:
    endpoint = _discovery_url(discovery, "registration_endpoint")
    payload = json.dumps(
        {
            "client_name": "Foundry-lite enterprise QA one-time client",
            "client_uri": "https://chatgpt.com",
            "redirect_uris": [_REDIRECT_URI],
            # Public PKCE clients intentionally have no client secret.
            "token_endpoint_auth_method": "none",  # nosec B105
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
        separators=(",", ":"),
    ).encode()
    value = _json_request(endpoint, payload, "application/json")
    client_id = _required_text(value, "client_id")
    registration_uri = _https_url(_required_text(value, "registration_client_uri"))
    registration_token = _required_text(value, "registration_access_token")
    return IssuedClient(client_id, registration_uri, registration_token)


def _authorization_code_token(
    discovery: dict[str, object],
    client_id: str,
    identity_origin: str,
    audience: str,
    username: str,
    password: str,
) -> str:
    verifier = _pkce_verifier()
    state = secrets.token_urlsafe(24)
    authorize_url = _authorize_url(discovery, client_id, audience, verifier, state)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        _BoundedRedirect(identity_origin, state),
    )
    code = _login_and_consent(opener, authorize_url, username, password, state)
    return _exchange_code(discovery, client_id, code, verifier)


def _authorize_url(discovery: dict[str, object], client_id: str, audience: str, verifier: str, state: str) -> str:
    endpoint = _discovery_url(discovery, "authorization_endpoint")
    scopes = f"openid profile email {GOVERNED_RELEASE_SCOPE} mcp-audience:{audience}"
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "scope": scopes,
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{endpoint}?{query}"


def _login_and_consent(
    opener: urllib.request.OpenerDirector,
    authorize_url: str,
    username: str,
    password: str,
    state: str,
) -> str:
    body, current_url = _open_page(opener, urllib.request.Request(authorize_url))
    login = _form(body, required_field="password")
    login.fields.update({"username": username, "password": password})
    try:
        body, current_url = _submit_form(opener, current_url, login)
        consent = _form(body, required_button="accept")
        consent.fields["accept"] = "Yes"
        _submit_form(opener, current_url, consent)
    except _OAuthCallback as callback:
        if callback.state != state:
            raise RuntimeError("macmini_oidc_authorization_state_invalid") from callback
        return callback.code
    raise RuntimeError("macmini_oidc_authorization_callback_missing")


def _submit_form(opener: urllib.request.OpenerDirector, current_url: str, form: _Form) -> tuple[bytes, str]:
    target = urllib.parse.urljoin(current_url, form.action)
    if _origin(target) != _origin(current_url) or form.method != "post":
        raise RuntimeError("macmini_oidc_authorization_form_invalid")
    request = urllib.request.Request(
        target,
        data=urllib.parse.urlencode(form.fields).encode(),
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    return _open_page(opener, request)


def _exchange_code(discovery: dict[str, object], client_id: str, code: str, verifier: str) -> str:
    endpoint = _discovery_url(discovery, "token_endpoint")
    payload = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        }
    ).encode()
    token = _json_request(endpoint, payload, "application/x-www-form-urlencoded")
    return _required_text(token, "access_token")


def _open_page(opener: urllib.request.OpenerDirector, request: urllib.request.Request) -> tuple[bytes, str]:
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            current_url = response.geturl()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("macmini_oidc_authorization_unavailable") from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("macmini_oidc_authorization_response_too_large")
    return body, current_url


def _form(raw: bytes, *, required_field: str | None = None, required_button: str | None = None) -> _Form:
    parser = _FormParser()
    try:
        parser.feed(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise RuntimeError("macmini_oidc_authorization_form_invalid") from exc
    matches = [
        form
        for form in parser.forms
        if (required_field is None or required_field in form.fields)
        and (required_button is None or required_button in form.buttons)
    ]
    if len(matches) != 1:
        raise RuntimeError("macmini_oidc_authorization_form_invalid")
    return matches[0]


def _json_request(url: str, payload: bytes | None = None, content_type: str | None = None) -> dict[str, object]:
    headers = {"accept": "application/json"}
    if content_type is not None:
        headers["content-type"] = content_type
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST" if payload is not None else "GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - URL is validated as HTTPS.
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("macmini_oidc_https_request_failed") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("macmini_oidc_https_response_too_large")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("macmini_oidc_https_json_invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("macmini_oidc_https_json_invalid")
    return value


def _private_principals(raw_path: str) -> dict[str, tuple[str, str]]:
    path = Path(raw_path)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or resolved.parent != QA_ROOT / "state" or stat.S_IMODE(resolved.stat().st_mode) != 0o600:
        raise ValueError("macmini_oidc_principals_file_invalid")
    values = dict(line.split("=", 1) for line in resolved.read_text(encoding="utf-8").splitlines() if "=" in line)
    expected = {"author_username", "author_password", "reviewer_username", "reviewer_password"}
    if values.keys() != expected or any(not value or len(value) > 512 for value in values.values()):
        raise ValueError("macmini_oidc_principals_file_invalid")
    return {
        "author": (values["author_username"], values["author_password"]),
        "reviewer": (values["reviewer_username"], values["reviewer_password"]),
    }


def _write_token(name: str, token: str) -> None:
    target = QA_ROOT / "state" / name
    temporary = QA_ROOT / "state" / f".{name}.{os.getpid()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _receipt(identity_origin: str, audience: str, client_id: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "passed",
        "issuedAt": utc_now(),
        "identityOrigin": identity_origin,
        "audience": audience,
        "clientId": client_id,
        "authorizationCodePkce": True,
        "distinctQaAccounts": True,
        "tokenFilesMode": "0600",
        "rawTokensStoredInEvidence": False,
        "rawPasswordsStoredInEvidence": False,
    }


def _discovery_url(discovery: dict[str, object], key: str) -> str:
    return _https_url(_required_text(discovery, key))


def _required_text(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item) > 4096:
        raise RuntimeError("macmini_oidc_response_invalid")
    return item


def _https_origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("macmini_oidc_identity_origin_invalid")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _https_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("macmini_oidc_https_url_invalid")
    return value


def _origin(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _is_callback(parsed: urllib.parse.SplitResult) -> bool:
    expected = urllib.parse.urlsplit(_REDIRECT_URI)
    return (parsed.scheme, parsed.netloc, parsed.path) == (expected.scheme, expected.netloc, expected.path)


def _single(values: dict[str, list[str]], key: str) -> str:
    matches = values.get(key, [])
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError("macmini_oidc_authorization_callback_invalid")
    return matches[0]


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--identity-base-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--principals-file", required=True)
    try:
        receipt, _ = issue(parser.parse_args(argv))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"schemaVersion": 1, "status": "failed", "reason": type(exc).__name__}))
        return 1
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
