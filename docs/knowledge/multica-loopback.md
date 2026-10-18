---
type: Runbook
title: Multica is loopback-only - how the board is reached and the owner steps to apply it
description: Multica's API (8080) and UI (3000) publish on 127.0.0.1 only with signup closed; nginx reaches the containers by name on zoe-network; who still uses the loopback ports; the MULTICA_DEV_CODE requirement; and the exact owner commands to recreate the two containers and verify.
tags: [multica, nginx, docker, security, loopback, runbook]
timestamp: 2026-10-19T00:00:00+08:00
---

# Multica is loopback-only (security deep dive 2, S0-2)

## What changed (in the repo)

- `docker-compose.modules.yml`: `multica-backend` publishes `127.0.0.1:8080:8080` and `multica-web`
  `127.0.0.1:3000:3000` (were bare, i.e. every interface); `ALLOW_SIGNUP` is `false`.
- `services/zoe-ui/nginx.d/locations.inc`: `/multica-api/`, `/multica/` and `/_next/` proxy to
  `zoe-multica-backend:8080` / `zoe-multica-web:3000` on zoe-network (they used
  `host.docker.internal`, which cannot reach a loopback publish). The upstreams sit in `set $var` +
  `resolver 127.0.0.11`, so nginx still boots when the opt-in Multica containers are down
  (verified: UI answers, `/multica-api/` returns 502).
- Pinned by `tests/unit/test_compose_loopback_binds.py` and `tests/unit/test_nginx_delivery_layer.py`.

## How callers still reach Multica

| caller | path | affected? |
|---|---|---|
| zoe-data (`MULTICA_BASE_URL=http://localhost:8080`), `scripts/setup/populate_multica.py`, Cursor MCP (`MULTICA_API_URL=http://127.0.0.1:8080`) | host process to `127.0.0.1:8080` | no - loopback publish still serves them |
| Multica executor (`labs/flue-executor`), Omnigent | straight to the `multica` Postgres database (`zoe-database`); neither speaks Multica's HTTP API | no |
| Browsers (desktop, Pi kiosk) | `http(s)://zoe.local/multica/` and `/multica-api/` through zoe-ui nginx | no - nginx now uses container names |
| `zoe-multica-backend` -> zoe-data webhook | `ZOE_BOARD_WEBHOOK_URL` = `host.docker.internal:8000` (zoe-data on the host) | no - unrelated to the Multica ports |
| anything on the LAN using `zoe.local:8080` / `zoe.local:3000` directly | | **cut off by design**; use `/multica/` |

## The dev verification code (read this)

`APP_ENV` stays `development`: with no email backend (`RESEND_API_KEY` / SMTP unset), the dev
verification code is the only way anyone, including the owner, signs in, and it is ignored when
`APP_ENV=production`. The board is still reachable from the LAN through nginx, so the code is now the
real control. `MULTICA_DEV_CODE` in the root `.env` MUST be a long random value chosen by the owner,
never the upstream default. (Signup being closed means an unknown email cannot be created, but a
known member's email plus a guessable code is a login.)

Open question for the owner step: the upstream backend may expect a 6-digit code. After changing it,
confirm a login at `/multica/` works; if upstream rejects a long value, use the longest it accepts and
see the follow-up in `docs/knowledge/open-problems.md` (put the board behind an auth_request or an
email backend so the dev code is not the gate).

## Owner steps (apply live; the repo change alone does not touch the running containers)

Deploy reconciles `zoe-ui` (pre-flights nginx, then `docker compose up -d --no-deps zoe-ui` +
`docker restart zoe-ui`) when `services/zoe-ui/nginx.d` changes, so the nginx half lands by itself and is
safe before the Multica recreate (container-name routing works with the old publishes too).

1. Set a long random `MULTICA_DEV_CODE` in `~/assistant/.env` (do not paste it into chat or a commit).
2. Recreate the two containers:

   ```
   cd ~/assistant
   docker compose -f docker-compose.modules.yml up -d --no-deps --force-recreate multica-backend
   docker rm -f zoe-multica-web
   docker compose -f docker-compose.modules.yml up -d --no-deps multica-web
   ```

   `zoe-multica-web` was created by `docker run` (no compose labels), so compose cannot adopt it: remove
   it first (stateless). Naming the services on the command line activates their `multica` profile. The
   compose digest for `multica-web` is the image that was running (`f05d7572...`), so this changes
   ports/env only, not the Multica version.
3. Verify:

   ```
   ss -ltn | grep -E ':(8080|3000)\b'             # both 127.0.0.1 only, none on 0.0.0.0 / [::]
   curl -fsS http://127.0.0.1:8080/health         # backend ok
   docker inspect -f '{{.State.Health.Status}}' zoe-multica-backend   # healthy
   curl -fsSk https://localhost/multica-api/health   # through nginx
   curl -sk -o /dev/null -w '%{http_code}\n' https://localhost/multica/   # 200
   curl -m 3 -sS -o /dev/null http://<jetson-lan-ip>:8080/health   # from another LAN host: must fail
   ```

   Then open the board (`/multica/`), sign in with the new code and confirm issues load, and check
   `docker logs --tail 20 zoe-multica-backend` shows no errors. zoe-data's board sync reads
   `MULTICA_BASE_URL` from the host `.env` and needs no change.
4. Rollback: re-add the bare ports in the compose file and repeat step 2. Nginx needs no rollback.
