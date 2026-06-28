# config/ — deployment configuration

## Purpose

Host deployment configuration: tunnel config, nginx fragments, module registry, web-push keys, and certificates. This doc records what lives here — never the values.

## Ownership

- `cloudflared/` and `cloudflared-config.yml` — Cloudflare tunnel configuration.
- `nginx/` — nginx configuration fragments (the main config is `services/zoe-ui/nginx.conf`).
- `modules.yaml` — module registry consumed by the module compose generator.
- `vapid_public.pem` / `vapid_private.pem`, `cert.pem` — web-push VAPID keys and certificate material.
- `DEPLOYMENT_MANIFEST.json` — deployment manifest.

## Local Contracts

- NEVER document, echo, or commit secret values (keys, credentials, tokens). Secrets stay in `.env` or per-user credential storage; this tree may hold key files only when the deployment requires it and they must never be pasted into docs, code, or chat.
- Rotating VAPID keys invalidates all stored push subscriptions; expect HTTP 410 from stale endpoints and clear the `push_subscriptions` table after rotation.
- Credentials are per user and per scope; no personal-data credentials in global env vars.

## Work Guidance

(empty)

## Verification

(empty)

## Child DOX Index

No child AGENTS.md files.
