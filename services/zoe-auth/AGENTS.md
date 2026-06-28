# services/zoe-auth/ — authentication service

## Purpose

Dedicated authentication service: user auth, OIDC provider, SSO, and touch-panel pairing flows.

## Ownership

- `main.py` — service entry. CRITICAL FILE.
- `api/`, `core/`, `models/` — service internals.
- `oidc/`, `sso/` — identity provider and single-sign-on flows (nginx proxies `/application/o/`, `/api/sso/`, `/jwks.json`).
- `touch_panel/` — device pairing for touch surfaces.
- `tests/` — service tests.

## Local Contracts

- Every Zoe turn pivots on "who is speaking now": speaker auth primary, touch/device pairing fallback. Changes here affect that contract for all surfaces.
- Credentials are keyed per user and per scope; never introduce global personal-data credentials.
- No hardcoded secrets; environment variables only.
- **First-password setup is gated.** `POST /api/auth/password/setup` (for accounts seeded `SETUP_REQUIRED`/NULL) requires proof: a one-time `setup_token` OR an authenticated admin session (`users.create`). Tokens are issued by `core/account_setup.py` — either the service-wide **bootstrap token** (`ZOE_AUTH_SETUP_TOKEN`; if unset, one is generated and logged at WARNING for the local operator) or a per-user token an admin mints via `POST /api/admin/users/{user_id}/setup-token`. First-run on the box still works via the logged/env bootstrap token; clients (zoe-ui) must collect and send `setup_token`. Token state is in-process (rare bootstrap flow); a restart re-issues it.
- **Auth rate limiting is real.** Login, passcode, and setup go through the shared `core/security.py` `rate_limiter` (IP + username sliding window, failure-counted; successful sign-ins never count). `_is_rate_limited()` in `core/sessions.py` / `core/passcode.py` delegates to it — do not stub it back to `return False`. Thresholds live in `RateLimiter.rules`; `rate_limiter.reset()` exists for tests/admin recovery.
- **Confidential OIDC clients must present `client_secret`.** In `oidc/router.py` token exchange, any client with a registered `client_secret_hash` is required to send a valid secret (PKCE alone is insufficient); public clients (no hash) still use PKCE only.

## Work Guidance

(empty)

## Verification

Service tests in `tests/`, then live login plus `/api/auth/` flow through nginx.

## Child DOX Index

No child AGENTS.md files.
