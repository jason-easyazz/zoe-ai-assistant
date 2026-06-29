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
- **First-password setup is gated.** `POST /api/auth/password/setup` (for accounts seeded `SETUP_REQUIRED`/NULL) requires proof: a one-time `setup_token` OR an authenticated admin session (`users.create`). Tokens come from `core/account_setup.py` — a **one-time bootstrap token** (`ZOE_AUTH_SETUP_TOKEN`, else generated and logged at WARNING for the local operator; it is burned after one successful setup and a fresh one is rotated+logged), or a per-user token an admin mints via `POST /api/admin/users/{user_id}/setup-token`. First-run on the box still works via the logged/env bootstrap token; clients (zoe-ui) must collect and send `setup_token`. Token state is in-process (rare bootstrap flow); a restart re-issues it.
- **Auth brute-force throttle must never cause victim/NAT lockout.** Login, passcode, and setup go through the shared `core/security.py` throttle (failure-counted; successful sign-ins never count). It is **per-IP progressive backoff** (`delay_for`, applied by the async API endpoints — slows a hammering IP, never denies a valid credential from a clean IP) plus a **(IP, username)-pair** volumetric hard block (`is_hard_blocked` / `is_limited`, used by the sync `_is_rate_limited` gates in `core/sessions.py` / `core/passcode.py`). Never reintroduce a username-global or IP-global hard deny (it would let an attacker lock a victim out from other IPs, or lock a whole NAT). Thresholds live in `RateLimiter.throttle_rules`; admins clear buckets via `POST /api/admin/rate-limit/reset` (`rate_limiter.reset_for`), which also clears the per-user DB advisory counters. Do not stub `_is_rate_limited()` back to `return False`.
- **No user-global credential lockout.** `verify_password` (`core/auth.py`) verifies the password BEFORE any `locked_until` check, and `verify_passcode` (`core/passcode.py`) has no `failed_attempts >= max` deny — a CORRECT credential is never refused because of failed attempts the user may not have made (that is a victim-lockout vector). `auth_users.failed_login_attempts/locked_until` and `passcodes.failed_attempts` are kept for audit/admin visibility only; enforcement is the per-IP/(IP,user) throttle above. Do not re-add a per-user lock that denies a valid credential.
- **Confidential OIDC clients must present `client_secret`.** In `oidc/router.py` token exchange, any client with a registered `client_secret_hash` is required to send a valid secret (PKCE alone is insufficient); public clients (no hash) still use PKCE only.

## Work Guidance

(empty)

## Verification

Service tests in `tests/`, then live login plus `/api/auth/` flow through nginx.

## Child DOX Index

No child AGENTS.md files.
