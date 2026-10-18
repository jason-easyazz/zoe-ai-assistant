---
type: Runbook
title: The Multica board sits behind Zoe's sign-in (nginx auth_request), because upstream Multica has no OIDC login yet
description: Why native OIDC is not possible on any released Multica (evidence to v0.6.3), how the auth_request gate in front of /multica/, /multica-api/ and /_next/ works and who it admits, which callers are unaffected, the dev-code facts, the lab record, the owner steps with rollback, and the switch-to-native-OIDC backlog.
tags: [multica, nginx, auth, oidc, zoe-auth, security, runbook]
timestamp: 2026-10-19T00:00:00+08:00
---

# The Multica board sits behind Zoe's sign-in

## Why not native OIDC (checked 2026-10-19)

zoe-auth is an OIDC provider and already seeds a `multica` client (`services/zoe-auth/oidc/startup.py`, redirect
`<ZOE_BASE_URL>/multica/auth/callback`, from `MULTICA_OIDC_CLIENT_ID/SECRET`), and `docker-compose.modules.yml` passes
`OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` / `OAUTH_DISCOVERY_URL` to `multica-backend`. **No Multica release reads them.**

- The board runs `multica-backend` v0.3.1 (built 2026-05-15). The newest release is **v0.6.3 (2026-10-16)**; upstream `main`
  was `f896cc48` (2026-10-18).
- In v0.3.1, v0.6.3 and `main`, `server/internal/handler/auth.go` has exactly three logins: the email code
  (`/auth/send-code` + `/auth/verify-code`), the fixed dev code, and **Google only** - `GoogleLogin` reads
  `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` and posts to the hardcoded
  `https://oauth2.googleapis.com/token` and `https://www.googleapis.com/oauth2/v2/userinfo`. There is no issuer, discovery
  URL or `OIDC_*` / `OAUTH_*` variable anywhere in the server. `SELF_HOSTING_ADVANCED.md` on `main` lists Google OAuth as the
  only SSO.
- Upstream work, all **open / unmerged**: issue [#1014](https://github.com/multica-ai/multica/issues/1014) "Add OIDC login
  support" (since 2026-04-14), PRs [#5791](https://github.com/multica-ai/multica/pull/5791) and
  [#5792](https://github.com/multica-ai/multica/pull/5792) "Add generic OIDC authentication",
  [#8129](https://github.com/multica-ai/multica/pull/8129) "make OIDC endpoints configurable" (`OIDC_CLIENT_ID/SECRET/
  REDIRECT_URI`, `OIDC_AUTHORIZE_URL/TOKEN_URL/USERINFO_URL`, `OIDC_SCOPES`; explicit endpoints, no discovery, and it does NOT
  add a way to turn the code login off). The earlier PR #2005 was closed unmerged.
- No release can disable the code login either; the only knobs are `ALLOW_SIGNUP`, `ALLOWED_EMAILS`,
  `ALLOWED_EMAIL_DOMAINS` and `DISABLE_WORKSPACE_CREATION`.

So an image upgrade buys nothing for sign-in, and the board is gated in front instead.

## The gate

Every request to `/multica/`, `/multica-api/` and `/_next/` (HTTP and the WebSocket handshake) runs an nginx `auth_request`
to `internal` location `/_zoe/multica-gate` -> zoe-data `GET /api/multica-ui/gate` and is proxied only on a 2xx
(`services/zoe-ui/nginx.d/locations.inc`). The pattern is the Music Assistant UI gate's (#2124); the token mechanics are the
shared `auth_handoff.SignedTokens`.

| answer | when |
|---|---|
| 204 | a valid board cookie, or the `X-Session-ID` of a signed-in **adult household member** |
| 401 | no credential, an invalid / expired session, a guest session, a panel device token, the `ZOE_UNAUTHENTICATED_ROLE=family-admin` promotion |
| 403 | signed in but not positively an adult: a child role, a `member_modes` minor row, an unknown role, a missing account, a failed lookup |
| 5xx | zoe-auth or zoe-data down: nginx answers 500 - closed, never open |

"Adult household member" is `panel_principal.is_member` (no guest, no promotion, no auth-outage principal), no
`panel_id` / `device_token_role` (the wall panel is whoever stands at it, children included), and
`manner_block.eligible` (an `auth_users` role in `ADULT_ROLES` and no minor / kid `member_modes` row; any lookup failure
says no). Code: `services/zoe-data/multica_ui_gate.py`, `services/zoe-data/routers/multica_ui.py`.

**The browser flow.** Zoe's session is a header from localStorage, which a navigation, an asset load or a WebSocket cannot
carry. A refused board PAGE (`/multica/...`) is redirected by nginx to `/board.html?next=<the page>`. That page loads
`js/auth.js` (no member session -> Zoe sign-in, then back), calls `POST /api/multica-ui/session`, which sets the
`zoe_multica_ui` cookie three times (Path `/multica/`, `/multica-api/`, `/_next/`; HttpOnly, SameSite=Strict, Secure over
https, 8 h), and then goes to `next` (only a same-origin `/multica/` path is followed). A 403 shows "The board is for adult
household members". API and asset requests are never redirected - they get the bare 401 / 403.

Multica's own login (the dev code) is then a **second step that only an adult member can reach**: before the gate, anyone on
the LAN could POST `/multica-api/auth/verify-code`.

**Settings** (zoe-data env, read per request): `ZOE_MULTICA_UI_GATE` (default on; `off` = the gate answers 204 to all, the
rollback), `ZOE_MULTICA_UI_TTL_S` (default 28800, floor 300), `ZOE_MULTICA_UI_SECRET` (default: a key derived from
`ZOE_INTERNAL_TOKEN` for this purpose only; set or change it to revoke every cookie at once).

**Limits, stated plainly.** A Zoe sign-out does not revoke a minted cookie (the 8 h TTL, or a secret rotation, does). Open the
board on the host the board's own API is built for (`http://zoe.local/multica/`; `NEXT_PUBLIC_API_URL` is
`http://zoe.local/multica-api`): cookies are host-only, so a board opened on another host name or over https has the same
API-origin problem it had before this gate.

## Who is not affected

| caller | path | through nginx? |
|---|---|---|
| zoe-data board sync (`MULTICA_BASE_URL=http://localhost:8080`), `scripts/setup/populate_multica.py`, Cursor MCP | host to `127.0.0.1:8080` | no |
| Multica executor (`labs/flue-executor`), Omnigent | the `multica` Postgres database | no |
| `multica-backend` -> zoe-data webhook | `host.docker.internal:8000/api/agent/board/webhook` | no |
| `scripts/maintenance/multica_health_report.py` `web_proxy` / `api_proxy` | through nginx, anonymous | yes - now counts the gate's 401 / 302-to-launcher as healthy (`gated: true`) and a 2xx as `gated: false` |

## The dev code (verified in upstream source)

`MULTICA_DEV_VERIFICATION_CODE` is honoured only when it is **exactly six digits** and `APP_ENV` is not `production`
(`isSixDigitCode` in v0.3.1 and v0.6.3). Any other value - a long random string included - silently disables the fixed code;
the per-request code from `/auth/send-code` is then printed to the backend log (`[DEV] Verification code for ...`, no email
backend). Guessing is bounded upstream (a stored code dies after 5 wrong attempts and a new one can be requested once a minute
per email, so roughly 5 guesses a minute against a million codes), but a known or default code (`888888`) is a login for
anyone who reaches the endpoint. Behind the gate only an adult member reaches it; keep the code random anyway.

## Lab record

`labs/multica-signin-gate/` (hand-run `run.sh`): the real nginx conf + `dist/` from the worktree, the real gate router with the
real adults-only check on Postgres, and the **live Multica images by digest** (backend `3eab9527...`, web `f05d7572...`) on an
internal network; a zoe-auth stand-in answers `/api/auth/user`. Cells and the result are in its README.

## Owner steps

Nothing to recreate, no image change, no Multica schema change, no `.env` edit required.

1. Merge. `deploy.yml` restarts zoe-data (new route) and reconciles `zoe-ui` (pre-flighted nginx + container restart). Between
   the two the board answers 500 (closed) for a few seconds.
2. Verify (from the Jetson):

   ```
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/multica-api/health    # 401 (gated)
   curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://127.0.0.1/multica/   # 302 .../board.html?next=/multica/
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/_zoe/multica-gate     # 404 (internal)
   curl -fsS http://127.0.0.1:8080/health                                           # backend still answers on loopback
   python3 scripts/maintenance/multica_health_report.py | grep -A3 -E '"(web|api)_proxy"'   # "gated": true
   ```

   Then in a browser signed in to Zoe as an adult: open `http://zoe.local/multica/` - it passes through `/board.html` and
   lands on the board; sign in to Multica with the dev code as before. A gate error shows in zoe-data's log under
   `~/.zoe-logs/` (not journald).
3. Recommended, separately: a fresh random **six-digit** `MULTICA_DEV_CODE` in `~/assistant/.env`, then recreate
   `multica-backend` (the env is fixed at create time): `docker compose -f docker-compose.modules.yml up -d --no-deps
   --force-recreate multica-backend`.

**Rollback.** Fast: `ZOE_MULTICA_UI_GATE=off` in `services/zoe-data/.env`, then `systemctl --user restart zoe-data` (the gate
answers 204 to everyone; nginx unchanged). Full: revert the PR and let deploy reconcile `zoe-ui`.

## When upstream ships OIDC (backlog)

Tracked as the board's "Multica OIDC - activate when Multica ships it" backlog issue (`scripts/setup/populate_multica.py`).
When a release carries generic OIDC: back up the board first (`docker exec zoe-database pg_dump -U zoe -Fc multica >
~/backups/multica-$(date +%F).dump`), pin the new backend + web digests, point it at zoe-auth (`http://zoe-auth:8002` on
zoe-network for the backchannel, the browser-facing issuer through nginx at `http://zoe.local`), restrict who may sign in on
the zoe-auth side (adult members only, as this gate does), prove it in a lab against the real images, then retire this gate.
