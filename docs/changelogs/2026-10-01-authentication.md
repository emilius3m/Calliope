# 2026-10-01 — Administrator authentication

Calliope's frontend now shows first-account setup and sign-in before mounting
private pages. The installation-wide administrator uses a server-local,
one-time setup code and a password of at least 12 characters. The header
provides sign-out and account/password management.

The same authentication boundary covers all API routes, files, SSE events,
MCP and API documentation. Server-side opaque sessions use HttpOnly/SameSite
cookies, Secure by default, a 12-hour absolute expiry and one-hour idle expiry.
Mutations require a custom request header and an approved browser origin.
Login/setup attempts are rate limited; password changes revoke every existing
session/API token. Open event streams check revocation before delivering data.

Credentials and hashed sessions are stored separately from project data in
`auth.db`, excluded from project exports. Server-local recovery and dedicated
MCP/API-token commands are available through `python -m calliope.auth`.

Validation: the complete backend suite passed 690 tests; the authentication
suite passed 37 tests after adding an authenticated video-range check.
Frontend type checking, production build and SSR storage checks passed.
Browser checks covered first-account setup, sign-in and sign-out on an
isolated installation.
