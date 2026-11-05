# auth-proxy

Company sign-in for the dashboard: [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) in front of portal-web, defined in
`compose.yaml` under the `sso` profile. Any OIDC provider works (Okta, Entra ID, Google Workspace, Keycloak).

1. Register an OIDC client with redirect URL `https://<dashboard host>/oauth2/callback`; include a `groups` claim if you want group-based roles.
2. In `.env`: `SSO_ISSUER_URL`, `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`, `SSO_COOKIE_SECRET` (`openssl rand -base64 32 | head -c 32`),
   `SSO_EMAIL_DOMAINS` (e.g. `example.com`), `SIF_AUTH_MODE=proxy`, and the roles: `SIF_PORTAL_ADMINS`, `SIF_PORTAL_OPERATORS` (emails or groups).
3. Remove portal-web's published port, then `podman-compose --profile sso up -d`.

Roles: **viewer** (everyone who can sign in) reads everything; **operator** kills tasks, reviews lessons and starts context syncs;
**admin** also changes teams, connections and handling. Every change is recorded with the person's email in the Audit log.
