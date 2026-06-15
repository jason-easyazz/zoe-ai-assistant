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

## Work Guidance

(empty)

## Verification

Service tests in `tests/`, then live login plus `/api/auth/` flow through nginx.

## Child DOX Index

No child AGENTS.md files.
