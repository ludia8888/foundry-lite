# OSDK Security Threat Model

This document fixes the current OSDK security boundary for the backend/API/SDK slice.
It is intentionally scoped to local OAuth, Developer Console-lite app scopes, local SDK
artifacts, and ObjectSet WebSocket/SSE subscriptions.

## Assets

- OAuth authorization codes, access tokens, rotating refresh tokens, and token family state.
- Developer Console OSDK applications, clients, resource grants, SDK versions, release channels, compatibility windows, and download tokens.
- ObjectSet subscription events, watermarks, object ids, object versions, and masked property payloads.
- Audit and Operations payloads that operators use to understand security decisions.

## Current Mitigations

- OSDK tokens must pass both user permission and application resource scope checks.
- Local OAuth uses authorization-code + PKCE, rotating refresh tokens, local access-token `jti`, and local JWKS key rotation with a retired-key grace window.
- Authorization-code replay and rotated refresh-token reuse are denied and written as audit evidence. Refresh-token reuse marks the whole session family compromised and revokes the active replacement token.
- OAuth authorize/token/refresh/revoke and WebSocket subscription connect paths are rate limited. SDK errors expose `RATE_LIMITED`, `retryAfterSeconds`, and retryability instead of hiding the failure.
- Audit/error payload tests recursively guard against raw access token, refresh token, authorization code, WebSocket bearer subprotocol token, and SDK artifact download token leakage.
- CORS and WebSocket Origin use an explicit local allowlist. Credentialed wildcard origins are not part of the current contract.
- Browser JSON APIs use bearer tokens in headers. They do not depend on ambient cookie authentication.
- Generated SDK code does not persist tokens to `localStorage`, `sessionStorage`, or `document.cookie`, and does not use raw HTML sinks such as `innerHTML` or `outerHTML`.
- WebSocket and SSE ObjectSet subscriptions are at-least-once. A client may receive duplicates after reconnect and should deduplicate by `watermark + objectId + objectVersion`. `out_of_date` means full resync is required.
- WebSocket `auto` transport may fall back to SSE only for transport availability failures. 401, 403, missing scope, and rate denial are terminal and are not hidden by fallback.

## Current Non-Goals

- No external NPM/PyPI publishing.
- No external IdP introspection, external refresh-token revocation, SCIM/group sync, or full cloud secret manager rotation lifecycle.
- No visual Developer Console, login/session UI, or cookie-backed browser session flow.
- No exactly-once subscription delivery. Exactly-once remains future; v1 is explicitly at-least-once.
- No full Palantir-grade external package lifecycle, multi-environment deployment compatibility window, or external registry promotion workflow.

## Future Controls

- External IdP introspection and revocation hooks for production OAuth profiles.
- Cookie/session UI with SameSite cookies and CSRF tokens if the product introduces ambient browser credentials.
- CSP enforcement and UI-level XSS tests for future visual Developer Console and login screens.
- WebSocket deployment hardening behind a production edge gateway with tenant-aware connection quotas and distributed rate limits.
- External registry adapters for NPM/PyPI with provenance, signed release metadata, and rollback/promote workflows.
