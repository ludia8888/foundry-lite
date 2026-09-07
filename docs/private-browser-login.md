# Private pilot browser login

## Scope and product boundary

This slice replaces fixed demo headers in the strict browser runtime with a real
Keycloak Authorization Code + PKCE session. It is a **single invited owner and
single app trial**, not general multi-tenant SaaS signup, a customer identity
directory, or proof that hosted ChatGPT completed a governed external deployment.
The same Workshop runtime still renders the operating application.

Existing Workshop v2 definitions remain readable with their original fingerprint
and screens. Generation still emits v3. This corrects the previous exact-v3-only
read check that rejected intact, previously generated apps. Hash mismatches,
pre-Workshop v1, and unknown versions remain blocked; no stored definition is
rewritten or silently regenerated to pass a gate.

Public behavior references:

- [Keycloak JavaScript adapter](https://www.keycloak.org/securing-apps/javascript-adapter):
  public client, exact redirects/origins, S256, memory-only credentials, refresh.
- [Foundry Workshop permissions](https://www.palantir.com/docs/foundry/workshop/concepts-permissions):
  access to a module does not replace permission to its underlying data/actions.
  This is a bounded behavioral reference, not full parity or pixel identity.

## Implemented path

The browser first reads `GET /api/auth/browser/config`. Only a server explicitly
using local demo/header-trust auth may enable the existing demo session. An
unconfigured strict server instead displays a recoverable login setup message
and does not mount protected screens. Config contains no owner email, subject,
credential, or token. Both browser auth responses are `Cache-Control: no-store`.

Configured browsers use the issuer's actual login screen and verify their bearer
through `GET /api/auth/browser/session`. The backend binds the signed subject,
client, app, and human Authorization Code grant to the configured private owner.
Spoofed context headers cannot change that identity.
The owner subject is also denied on every other client, including existing QA
operator clients; the browser accepts only the `viewer` platform role. Existing
operator subjects retain their original client permissions.
Access/refresh credentials stay in memory; the app persists only an allowlisted
return path, while the adapter manages temporary OAuth callback state. Logout
or failed refresh unmounts the protected screen. Editor links return to the app
runtime; this consumer identity does not receive Workshop builder privileges.

The private client is restricted to its app read/query/action endpoints and
existing permission-checked relationship reads. It cannot use global dataset,
project administration, release MCP, or WebSocket endpoints. Own-application
definition reads require an active app and active bound client, but no longer
require a developer/operator role. Existing data and Action checks remain.

## Operator provisioning

`scripts/operations/bootstrap_private_browser_login.py` runs only on the dedicated
Mac mini QA host. It creates an exact private public-client binding, excludes the
QA operator scope, requires S256, and adds only the exact app hostname to the
existing redirect policy. Public signup must already be disabled. An existing
unmarked user/client is never claimed. An existing owner's password is not reset.
The first credential is temporary and stored only in a private local handoff file;
the person must change it in the identity provider. It is never printed in receipts.
Identity provisioning does not itself deploy or grant application access.

`scripts/operations/private_browser_app_access.py` separately invokes the public,
audited resource use cases. It grants one project viewer membership and only the
app's declared `domain_actor_*` business roles. Stable idempotency keys make an
interrupted retry safe. It never marks a pending release as operating, alters
Ontology, or reports operator provisioning as a human OAuth execution.

Helm `browserAuth` carries the exact client, public origin, application, and owner
subject. All four must be set together with strict OIDC; otherwise startup fails.
The existing MCP clients and auth settings must be preserved during deployment.
The upgrade operator accepts `--browser-auth-values` so the owner binding and new
allowed client are applied in the same rollout as the API guard, not admitted on
an older verifier first. These value files are included in the upgrade receipt hash.

## Evidence and remaining proof

- `tests/unit/test_browser_auth.py` and `tests/unit/test_browser_auth_http.py`:
  strict defaults, signed identities, exact owner/client/app, stale and spoofed auth.
- `tests/unit/test_private_application_consumer.py`: consumer read without admin,
  and denial of other app/client/tenant or global app inventory.
- `tests/unit/test_private_browser_keycloak.py`,
  `tests/unit/test_private_browser_bootstrap.py`, and
  `tests/unit/test_private_browser_app_access.py`: bounded provisioning and replay.
- `tests/e2e-foundry/private-browser-login.spec.ts`: mocked IdP contract, S256/nonce,
  original-app return, private token handling, wrong-account denial, and mobile UI.
  This fixture is explicitly **not** a live IdP or hosted ChatGPT receipt.
- Generated SDK request contracts cover both new endpoints.

Live deployment, owner password selection, actual app/data rendering, and logout
must be observed separately before reporting this private app usable. General
invitation/revocation management, email delivery, self-service recovery, branded
IdP UI, and multi-app customer SSO remain outside this slice.
