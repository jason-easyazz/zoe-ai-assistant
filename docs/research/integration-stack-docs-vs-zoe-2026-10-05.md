---
type: Research
title: Integration and runtime stack - official docs vs Zoe's configuration (2026-10-05)
description: Documentation-grounded audit of 13 integration/runtime tools (Home Assistant, Music Assistant, Telegram via grammY, Cloudflare tunnel + Access, Docker Compose, systemd user units, nginx, uvicorn, Postgres, Chroma, GitHub Actions gates, plus a security-hygiene pass). Extends infra-data-config-2026-10-03 and flue-and-agent-runtimes-2026-10-03 with deltas only. Read-only; nothing restarted, recreated, committed or reconfigured; no secret values read or recorded.
tags: [research, docs-vs-config, home-assistant, music-assistant, telegram, grammy, cloudflare, docker, systemd, nginx, uvicorn, postgres, chroma, github-actions, security-hygiene]
timestamp: 2026-10-05T21:50:00+08:00
---

# Integration and runtime stack: official docs vs Zoe (2026-10-05)

Question: is Zoe using its integration and runtime tools the way their own documentation recommends?
Short answer: **the tuning is mostly sound; the weak spot is trust boundaries and credential scope, not performance.**
The top findings are mostly "who can reach it" or "what does it hold" problems (G1-G7 in the ranking below).

Companion records (not restated here): `docs/research/infra-data-config-2026-10-03.md` (A1-A12, D1-D11) and
`docs/research/flue-and-agent-runtimes-2026-10-03.md` (A1-A7). Where this record changes the status of one of
their items it says so.

## Method and evidence labels

- **[src]** file:line or a live read-only command run 2026-10-05 21:00-21:50 AWST (`docker inspect`, `systemctl --user cat/show`, `ss`, `stat`, `gh api` GET, `curl` GET/HEAD).
- **[doc]** a URL WebFetched on 2026-10-05; the sentence is from the page, as summarised by the fetch tool (Sources at the end).
- **[doc-gap]** the page was fetched but did not contain the statement; the claim is therefore not documentation-backed today.
- **[unverified]** inference, or something I could not read (Cloudflare dashboard, MA internals while stopped, root-only files).
- Secret handling: env names only, no values. Where a token shape appeared in a log it was counted and redacted in-line; nothing was copied out.

### Status updates to the two prior records (verified today)

| Prior item | Status 2026-10-05 |
|---|---|
| infra A10 (`LimitNOFILE=65536` on zoe-data) | **APPLIED.** `/proc/<pid>/limits` shows 65536; 35 fds open. |
| infra A8 (log bounds) | Daemon default armed; compose `x-logging` landed. Live: **only `zoe-ui` and `zoe-auth` carry `max-size 10m / max-file 3`**; the other 9 running containers still show `map[]`. |
| infra A3/D6 (#1727 loopback binds) | **Still not applied.** `zoe-database` `0.0.0.0:5432`, `homeassistant-mcp-bridge` `0.0.0.0:8007` (+ `[::]`), image still `pgvector/pgvector:pg17`. Compose is correct (`127.0.0.1:` on both). |
| infra A9 (memory caps) | Not applied: every container `Memory=0`. |
| music-assistant idle reap | **Live and has fired once** (`MA_REAP stop idle_min=6255`, 2026-10-05 01:06 AWST). `zoe-music-assistant` is `Exited (0)` since then; the 10-minute timer logs `keep container already stopped`. |

---

## 1. Home Assistant (REST/WebSocket API, tokens, `homeassistant-mcp-bridge`, ESPHome/localtuya)

Live: HA core **2026.5.2** in container `homeassistant` (image tag `:stable`, local image is old; the ha-2026-9 runbook is pending) **[src]** `docker exec ... homeassistant.const`.

### Settings against the documentation

| Setting | Zoe | Documentation | Verdict |
|---|---|---|---|
| Container networking | bridge `zoe-network`, ports 8123 published, `privileged=true`, `label=disable`, no `/run/dbus` mount | Install doc recommends `--privileged`, `--network=host`, `-v /run/dbus:/run/dbus:ro`; "enable D-Bus access ... and proper network functionality including service discovery" **[doc]** | Deliberate deviation (nginx and the bridge reach HA by name `homeassistant:8123`). Cost: no mDNS, so ESPHome/Sonos-style discovery cannot work; every client is added by IP. Acceptable, but record it as a decision. |
| Shutdown timeout | `StopTimeout=nil` (10 s default) | "The default 10-second Docker shutdown window may be insufficient ... increase the timeout to 60 seconds via `--stop-timeout 60`" **[doc]** | **Hit.** HA log at 2026-10-04 14:35: "could not validate that the sqlite3 database ... was shutdown cleanly" and "Ended unfinished session (id=69 from 2026-08-10 ...)" i.e. an unclean stop on 08-10 as well **[src]** `homeassistant/home-assistant.log`. |
| `http.trusted_proxies` | `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.1` with `use_x_forwarded_for: true` | "Reverse proxy IP addresses or CIDR networks that are allowed to set X-Forwarded-For" **[doc]**; the same page requires the network address, not a host address | The two big ranges make every LAN host and every container a "proxy", so any of them can set the client IP HA records. Because the container is on bridge networking, HA already logs `172.18.0.1` (the Docker gateway) for host-originated calls **[src]** (`http.ban ... from 172.18.0.1`, 2026-09-25/26); a LAN client that arrives through the published port is, by the same mechanism, probably also `172.18.0.1` **[unverified]**. |
| Ban policy | `login_attempts_threshold` unset, no `ip_bans.yaml` | "Set this to -1 to disable automatic bans" **[doc]** (default value not given on the page: **[doc-gap]**) | No ban file exists, so nothing is banned today. Trap to record: if a threshold is ever set, the Docker gateway `172.18.0.1` is the address that gets banned, which would lock out the bridge's host-side traffic. |
| Long-lived access tokens | Two on user "Zoe Admin" (group `system-admin`): `zoe-data` created 2026-04-04, `zoe-voice-rotated-2026` created 2026-05-10; expiry 315,360,000 s **[src]** read with a metadata-only extraction from `.storage/auth` inside the container | "valid for 10 years"; "The access token string is not saved in Home Assistant; you must record it in a secure place"; "created for the current user" **[doc]** | Both tokens are admin and 10-year. The bridge uses only `GET /api/states`, `GET /api/services`, `GET /api/config` and `POST /api/services/...` **[src]** `services/homeassistant-mcp-bridge/main.py`. Whether the 04-04 token is still used is **[unverified]** (HA does not record per-request use for LLATs). |
| REST usage | bridge opens a new `httpx.AsyncClient()` per call (`main.py:109`), 10 s timeout, no retry; every list is a full `GET /api/states` | REST: `GET /api/states/<entity_id>` for one entity; `POST /api/services/<domain>/<service>` with optional `?return_response` **[doc]** | Works: 45 entities, 12 KB, **21 ms** measured on `/entities`. Not a problem at this size. |
| WebSocket API | **not used anywhere** (grep for `api/websocket`, `subscribe_entities`, `subscribe_events` across `services/` and `config/` is empty) | `subscribe_events`, `get_states`, `call_service`, `ping/pong`, `coalesce_messages` **[doc]**. The page contains no statement recommending WS over REST and no rate limits (**[doc-gap]**) | An offered, unused feature. Not a documented requirement. Matters only if presence/state freshness is ever wanted without polling. |
| HA custom components | `hacs`, `localtuya`, `auth_oidc`, `zoe_conversation` | HA logs "custom integration ... has not been tested by Home Assistant" for all four **[src]** | Expected. `ecosystem-watch-2026-09-26.md` already tracks the localtuya/2026.9 break. |

### ESPHome and localtuya (the issues in the logs)

- **ESPHome** `lva-88a29e0a953f @ 192.168.1.61`: HA logs one `Can't connect to ESPHome API` at start and then backs off silently **[src]**. The docs say HA "maintains a persistent connection to each ESPHome device and will automatically attempt to reconnect" and re-connects quickly "when mDNS is available" **[doc]**. With bridge networking there is no mDNS, so this entry reconnects only on HA's own backoff. Nothing listens on 6053 on that Pi (`log-review-docker-2026-10-04.md`). Remove the entry (Settings > Devices & services > Delete **[doc]**) until a satellite really runs there.
- **localtuya** `Error 28841002: IoT Core service subscription has expired`: localtuya's README says Cloud API calls "are performed only at startup, and when a local_key update is needed" and that "The Cloud API account configuration is not mandatory (LocalTuya can work also without it)" **[doc]**. So the error repeats at every HA start but does not break local control. It will break the moment a plug is re-paired in the Tuya app and its `local_key` changes. Decide now: renew the trial, or accept that re-pairing needs a manual key refresh.

### Pitfalls the docs warn about that Zoe hits

1. 10-s stop window on a SQLite-backed recorder (unclean shutdown, twice).
2. Over-broad `trusted_proxies` combined with a published port.

### Highest-value fix: HA

**Give the bridge its own non-admin HA user and token, then delete the 04-04 `zoe-data` token** (and the `zoe-voice-rotated-2026` one after the swap). Rationale: the bridge process is, until #1727 lands, reachable unauthenticated from the LAN **[src]** (`ss`: `0.0.0.0:8007`), and it holds an admin 10-year credential.
Verify: create user `zoe-bridge` (non-admin), mint a token, and run the five calls above with `curl` against HA (all must return 200; **[unverified]** that `/api/config` is allowed to non-admins - if not, drop that call from the bridge); then `docker restart homeassistant-mcp-bridge` and `GET :8007/` returns 200; finally confirm the old token returns 401. Rollback: re-add the old token to `.env`.

Secondary (same HA restart): `stop_grace_period: 60s` once HA is brought under compose (note the live container has no compose labels and `privileged=true`, which the compose service does not set: reconcile before any recreate or the first recreate silently drops `privileged`), and narrow `trusted_proxies` to the exact nginx/bridge container addresses, not the two /12 and /16 ranges.

---

## 2. Music Assistant (API, providers, YouTube Music re-auth, idle reap)

Live: container `zoe-music-assistant` is the `ghcr.io/music-assistant/server:stable` image at **MA 2.8.7** (sha256:eef3ee78..., built 2026-05-08) **[src]** (`docker image inspect`, MA log line "version 2.8.7"). Compose pins **2.10.3** by digest **[src]** `docker-compose.modules.yml:75`. `network_mode: host`, `/data` bind to `~/.zoe/music-assistant`. State: **stopped by the reaper since 01:06 AWST today.**

### Settings against the documentation

| Setting | Zoe | Documentation | Verdict |
|---|---|---|---|
| Network mode | host | "Network mode must be set to host for MA to discover and stream to players" **[doc]** | Matches. Consequence: ports 8095 (web/API) and 8097 (stream) land on the LAN. The core-settings page says the webserver bind "default `0.0.0.0` binds to all interfaces" and offers a bind-address setting **[doc]**. |
| API access | `POST {MA}/api` `{"command","args"}` with `Authorization: Bearer $MUSIC_ASSISTANT_TOKEN` **[src]** `music_service.py:77-84,129-131` | `POST /api` JSON-RPC with `message_id`, `command`, `args`; "Create a long lived access token in the MA UI via Settings > Profile" **[doc]** | Matches, except Zoe never sends `message_id` (the page lists it as a required field; MA evidently tolerates its absence **[unverified]**). |
| Per-user restriction | single token | "Each user can be restricted to a specific set of players" and sources; admin can manage tokens and sessions **[doc]** | Offered, unused: a read-only `now-playing` poller and the reaper could hold a token scoped to one user instead of the admin one **[unverified]** which user owns the current token. |
| Event stream | HTTP polling: panel `now-playing` every 5 s, history every 300 s, reaper every 10 min **[src]** `docs/knowledge/music-assistant-idle-reap.md` | The fetched pages document only the HTTP endpoint; they say nothing about a WebSocket or events (**[doc-gap]**) | Cannot grade this from today's docs. |
| Image channel | `stable` tag on the live container; explicit release tag + digest in compose | "Do not switch between stable and beta channels by changing image tags; do not move databases between beta and stable instances" **[doc]** | Compose is right (release tag, not a channel). The live container still runs the old `:stable` digest. |
| Capabilities | none added | SMB/NFS in-container mounts need `SYS_ADMIN`, `DAC_READ_SEARCH`, `apparmor:unconfined`, which "significantly reduce container isolation" **[doc]** | Zoe adds none: correct. |

### YouTube Music provider (the re-auth problem)

- Docs: Premium required; "Cookie authentication is the only way"; "Cookies expire after some time. If YT Music stops working and you see `401: Unauthorized` or `Unable to fetch PO Token for web_music client` in the MA log, run the cookie steps again"; "If you use a Family Account, setting up a dedicated account for MA will help maximise cookie life"; "No more than three concurrent streams" **[doc]**.
- **The docs page is stale against what works on this box:** it still names PO-token server image **1.2.1**, while Zoe runs `bgutil-ytdlp-pot-provider:2.0.0` because the client/server majors must match (the 2026-09-25 outage, `docker-compose.modules.yml` comments) **[doc]** vs **[src]**. Trust Zoe's runbook over the provider page on this point.
- **Measured:** MA's log shows **303** `Error loading provider(instance) ytmusic--HemJN6vc: User does not have Youtube Music Premium (will be retried later)` lines, one every ~2 min from 06:35 to 17:06 UTC on 10-04 **[src]** `~/.zoe/music-assistant/musicassistant.log`. MA's retry loop is by design; the provider was down for the whole last uptime window.
- **Idle-reap precondition is not met:** `music-assistant-idle-reap.md` says to enable only when "YouTube Music is either not configured ... or has been observed to survive a plain `docker restart`", and states MA "lists no ytmusic provider" on 10-04. `settings.json` has a `ytmusic` provider entry and the log shows instance `ytmusic--HemJN6vc` failing right up to the stop **[src]**. The reap is live regardless. Impact is small today (the provider was already failing), but the next `docker start` reloads it, and the docs' cookie-expiry and dedicated-account guidance applies to the re-auth.

### Highest-value fix: MA

**Recreate MA on the pinned 2.10.3 while it is already stopped** (the reaper has bought a zero-extra-downtime window), following the B0.12 recipe in `music-ytdlp-js-runtime.md` (zoe-data first, then recreate). Why: the live 2.8.7 is the version whose three GHSAs the compose comment says 2.10.3 closes, "which matters because `network_mode:host` puts :8095 on the LAN" **[src]**; it is also the only way the new `log-opts`/`x-logging` reach this container.
Verify: `docker inspect -f '{{.Config.Image}} {{.HostConfig.LogConfig}}' zoe-music-assistant` shows the digest and `max-size 10m`; `curl :8095/info` reports 2.10.3; `music_jsruntime_probe.sh` GREEN; panel Reconnect (QR) still works. Rollback: restore the store backup (the 2.10 settings migration is one-way, per the compose comment).
Also decide whether `ZOE_MA_IDLE_REAP` stays on while ytmusic is configured; if yes, amend the runbook's precondition so it matches the box.

---

## 3. Telegram via the Flue sidecar `labs/flue-zoe-telegram-2x/`

Facts **[src]**: the bot library is **grammY 1.44.0** (`package.json`), not telegraf. Transport is **long polling via `bot.start({allowed_updates:['message']})`**, not a webhook, and not `@grammyjs/runner` (`src/app.ts:141-142`, `src/telegram.ts:4-9,45`). Unit `flue-zoe-telegram.service` (drop-in points at `-2x`, `NODE_OPTIONS=--network-family-autoselection-attempt-timeout=1500`, `MemoryMax=1G`, `MemorySwapMax=0`). Watchdog timer polls `/health` every 60 s and restarts the unit on 503.

### Settings against the documentation

| Topic | Zoe | Documentation | Verdict |
|---|---|---|---|
| Long polling vs webhook | long polling; deleteWebhook then start; no 409 retry storm | "If you don't have a good reason to use webhooks ... there are no major drawbacks to long polling"; "it's not possible to get updates via long polling while an outgoing Webhook is set" **[doc]**; webhooks need a reply inside grammY's 10-s window and a re-send races **[doc]** | Correct. A webhook would also need a Cloudflare Access **Bypass** on `zoe.the411.life` ("Bypass does not enforce any Access security controls and requests are not logged" **[doc]**), so polling is the better fit. Keep it. |
| Concurrency | `bot.start()` = sequential | Sequential is "very predictable"; the runner plugin matters only at "over 50 million updates daily (>500/second)" **[doc]** | Do **not** adopt the runner. But sequential means **one stuck brain call blocks every family member's Telegram**: `askZoeAs` calls `fetch(DATA_URL/api/chat/?stream=false)` with **no abort signal** (`src/brain.ts:229`), so the bound is Node's own headers timeout. |
| Errors | handlers wrap their own `try/catch`; no `bot.catch` | Default handler "stops the bot if it was started by `bot.start()` and then re-throws"; `HttpError` wraps a network failure under `.error` **[doc]** | Acceptable because the watchdog restarts a stopped bot, but see the token leak below. |
| Rate limits | one reply per turn; no throttler or auto-retry | per chat "avoid sending more than one message per second", 20 msgs/min per group, ~30/s broadcast; "Do not ignore 429 errors. (This could lead to a ban.)" **[doc]** | Fine at household scale. Only a proactive fan-out loop could ever approach a limit. |
| Message size | replies sent verbatim, **no split** (no `4096`/chunk logic in `src/`) | `sendMessage` text is "1-4096 characters after entities parsing" **[doc]** | A long brain answer makes `ctx.reply` throw a Bot API 400; the handler logs it and the user gets nothing. |
| Typing indicator | none (`sendChatAction` not called) | action lasts ~5 s per call **[doc-gap]** (not returned by the fetch) | Offered, unused. Gemma turns take seconds. |
| Files | voice notes capped 8 MiB (`MAX_NOTE_BYTES`) and 60 s; `getFile` size checked before download | "getFile ... only work with files of up to 20 MB"; bots "can currently send files ... up to 50 MB" **[doc]** | Within limits; replies as OGG/Opus voice. Good. |
| Node connectivity | NODE_OPTIONS attempt-timeout 1500 | Default `autoSelectFamilyAttemptTimeout` is 250 ms; the CLI flag exists; minimum 10 **[doc]** | Matches the documented knob. The `docs/knowledge` Happy-Eyeballs note is consistent with the Node docs. |
| Update retention | startup does not drop pending | unconfirmed updates are kept "not ... longer than 24 hours" **[doc]** | Right: nothing is lost across a restart under 24 h. |

### Pitfall that bites: the bot token reaches the journal

At boot on 2026-10-04 14:35:38 the unit logged `FetchError: request to https://api.telegram.org/bot<REDACTED>/deleteWebhook failed, reason: getaddrinfo EAI_AGAIN` **[src]** (`journalctl --user`: exactly 1 token-shaped line in the retained journal; 0 in `~/.zoe-logs`). Cause: `console.error('Telegram poll error:', err)` (`src/app.ts:173`) prints grammY's wrapped `HttpError.error`, whose message embeds the URL with the token. `downloadTelegramFile` is careful about this (`src/telegram.ts:51-78`); the polling path is not. The memory note "rotate the token (leaked in journald)" is therefore not closed, and the leak recurs on every boot where DNS is not ready. OWASP's logging cheat sheet lists "Access tokens", "Encryption keys and other primary secrets" and "Database connection strings" as data that must not be logged **[doc]**.

The same boot shows a second doc-grounded issue: `deleteWebhook()` has no retry, so a DNS race at boot marks `polling=false`; the watchdog first fires at `OnBootSec=2min` and then every 60 s **[src]**, so Telegram is down for 2+ minutes after each reboot. `network-online.target` cannot fix it here: the systemd manual says a service that "strictly require[s] a configured network connection should pull in `network-online.target`" **[doc]**, but the page does not list it for **user** instances (**[doc-gap]**, and this unit is a user unit with `After=network.target`, which the manual calls "only very weakly defined").

### Highest-value fix: Telegram

**Redact before logging, retry the start, then rotate the token.** (1) A single `scrubTelegramError(err)` that replaces `/bot\d+:[\w-]+/` in `message`, `stack` and nested `.error`, used at `app.ts:173` and wherever `bot.api.*` errors are logged; (2) wrap `deleteWebhook()+bot.start()` in a bounded backoff loop (1, 2, 4, ... 30 s) so a boot-time DNS race self-heals in seconds instead of waiting for the watchdog; (3) rotate via BotFather and `journalctl --user --vacuum-time` past 2026-10-04.
Verify: a unit test using `test/helpers/mock-telegram.ts` that makes `deleteWebhook` fail and asserts no `bot<digits>:` string reaches `console.error` (negative control: remove the scrub, test goes red); after rotation `journalctl --user | grep -c 'bot[0-9]\{8,\}:'` returns 0.
Bundle in the same PR (all in `labs/flue-zoe-telegram-2x`, deploy.yml auto-restarts the unit): `AbortSignal.timeout(90_000)` on `askZoeAs`, `sendChatAction('typing')` before the call, split replies at 4096.

---

## 4. Cloudflare tunnel and Access

Live: `zoe-cloudflared` (cloudflared **2026.9.3**, protocol `quic`, `--no-autoupdate`, 4 connections to `per01`/`mel01`) with a locally-managed `config/cloudflared-config.yml` (not git-tracked; hostnames only below) **[src]**.

| Hostname | Origin | Edge behaviour measured with `curl` today |
|---|---|---|
| `zoe.the411.life` | `http://zoe-ui:80` | `302` to `the411.cloudflareaccess.com` (Access login), including `/health` |
| `buildzoe.the411.life` | `http://zoe-omnigent:6767` ("NO auth of its own, so the Access policy is the only gate", config comment) | `302` to Access login |
| `ssh.the411.life` | `ssh://localhost:22` | **`530`, not `302`** |

### Settings against the documentation

| Setting | Zoe | Documentation | Verdict |
|---|---|---|---|
| `--protocol` | default (`auto`, resolves to quic) | `auto` "will automatically configure the `quic` protocol. If cloudflared is unable to establish UDP connections, it will fallback to using the http2 protocol" **[doc]**; troubleshooting: "idle sessions can be more sensitive to network devices that aggressively time out UDP traffic" and "Test with cloudflared set to `protocol: http2`" **[doc]** | **Hit.** 24 WRN/ERR lines in the last 24 h, all `timeout: no recent network activity` (datagram manager or accept QUIC stream) followed by re-registration **[src]** `docker logs zoe-cloudflared`; 43 such stream timeouts since the container started 09-26. The `cloudflared-config.yml` comment about "HTTP/3 off at the edge" addresses browser-to-edge QUIC; `--protocol` is a different knob (cloudflared-to-edge) and is unset. |
| UDP receive buffer | `net.core.rmem_max = 212992` | quic-go warning "failed to sufficiently increase receive buffer size (was 208 kiB, wanted 7168 kiB, got 416 kiB)" appears in the container log **[src]**; the docs say it is "generally not impactful" and the fix is `net.core.rmem_max` in `/etc/sysctl.d/` **[doc]** | Known, benign per docs; root-only sysctl if wanted. |
| `originRequest.access` | **unused** | "Requires cloudflared to validate the Cloudflare Access JWT prior to proxying traffic to your origin" (`required`, `teamName`, `audTag`) **[doc]**; origin-side validation of `Cf-Access-Jwt-Assertion` is the documented belt-and-braces **[doc]** | Offered, unused. The Omnigent origin has no auth of its own, so the edge policy is a single point of failure. |
| `originRequest` timeouts | `connectTimeout 90s`, `tlsTimeout 30s`, `keepAliveTimeout 90s`, `keepAliveConnections 100`, `tcpKeepAlive 30s` | defaults are 30 s / 10 s / 1m30s / 100 / 30 s **[doc]** | Three are redundant copies of defaults. `connectTimeout 90s` against an origin one Docker hop away only hides an origin that is down for 90 s. |
| `cert.pem` | mounted read-only into the running container (`docker-compose.yml`, cloudflared service) | `cert.pem` "Authenticates your cloudflared instance to perform administrative tunnel operations" (create/delete tunnels, change DNS, configure routing), is "valid for at least 10 years, and the service token it contains is valid until revoked", and is "Not required for running existing tunnels" **[doc]** | **Over-provisioned.** An account-level, effectively non-expiring credential sits in a container that only needs the per-tunnel credentials file. |
| Credentials file | `config/d417c04a-....json` is `644`; a second copy `config/cloudflared-credentials.json` is `400` **[src]** `stat` | the credentials file "functions as a token authenticating the tunnel" **[doc]** | World-readable tunnel token on a shared dev box. (The container runs as uid 65532, which is probably why it was opened to `644`.) |
| Health | no healthcheck | `/ready` "returns 200 if and only if it has an active connection to Cloudflare's network"; in containers the metrics server defaults to `0.0.0.0:<port>` **[doc]** (from the Observability page via search) | Unused. |
| Edge timeout | `proxy_read_timeout 900s` in nginx | Cloudflare's 524 default is **125 seconds**, only Enterprise can raise it **[doc]**; WebSockets are closed "when no data is transmitted in either direction for a period of time" and the guidance is "Implement a keepalive" **[doc]** | `docs/knowledge/mac-virtual-panel.md` §4 says 100 s; the current doc says 125 s. The 900 s nginx values are moot behind Cloudflare; streams survive only because bytes keep flowing. Whether `/ws/` sends pings is **[unverified]**. |

### Service tokens and the Mac-panel doc (§4)

The doc's design (path-scoped Access app + **Service Auth** policy + `CF-Access-Client-Id/Secret`) matches the docs: "set the policy action to Service Auth; otherwise, Access will prompt for an identity provider login", tokens have an admin-chosen duration with expiry notice "a week before", rotation keeps the Client ID with an overlap window where "both secrets work", deletion revokes **[doc]**. Two doc-derived corrections to §4: (a) the "Risk [unverified]: two Access apps ... separate session cookies" is moot for the service path, because under service-token auth "Access does not return a CF_Authorization cookie to the client" **[doc]** (the daemon must send both headers on every call, which it does); (b) Access evaluates "Bypass and Service Auth policies ... first", then Block, then Allow **[doc]**, so the path-scoped Service Auth app must not be shadowed by a broader Bypass.

### Highest-value fix: Cloudflare

**Close or gate `ssh.the411.life`.** A `530` instead of the `302` the other two hostnames return indicates there is no Access application in front of it **[unverified]** (I cannot read the Zero Trust dashboard; `sshd_config` sets only `KbdInteractiveAuthentication no`, so `PasswordAuthentication` falls to the OpenSSH default; `sshd -T` needs root **[unverified]**). Cloudflare's SSH guide pairs the tunnel with Access ("SSH with Access for Infrastructure ... SSH certificates with Access policies and command logging") **[doc]**. Either delete the `ssh://` ingress rule (the home VPN path in the Mac-panel doc already exists) or put an Access app with an email-allow policy in front.
Verify: `curl -I https://ssh.the411.life` returns `302` to `*.cloudflareaccess.com` (or the hostname no longer resolves); `cloudflared access ssh --hostname ssh.the411.life` prompts for Access login. Rollback: re-add the ingress line.
Next two, same file set: drop the `cert.pem` mount (verify the tunnel re-registers; rollback = re-add the line) and add `originRequest.access` for `buildzoe` once the app's AUD tag is copied from the dashboard (verify: a request with a forged `Cf-Access-Jwt-Assertion` is rejected at cloudflared, a real browser session still loads).

---

## 5. Docker Compose and the Docker engine

Live **[src]** (`docker inspect`): 11 running containers (plus MA stopped). Restart policy `unless-stopped` on all. Healthchecks present on zoe-auth, potoken, multica-backend, zoe-database, the HA bridge and zoe-smb-drop; **absent on `homeassistant`, `zoe-ui`, `zoe-cloudflared`, `zoe-omnigent`, `zoe-multica-web`**. `CapDrop=[]`, `SecurityOpt=[]` and `ReadonlyRootfs=false` on all; `homeassistant` is `Privileged` with `label=disable`.

| Setting | Zoe | Documentation | Verdict |
|---|---|---|---|
| Port publishing | short syntax; 8 services on `0.0.0.0`: 80/443, 8002 (auth), 8080 + 3000 (multica), 8123 (HA), 445/139 (samba), plus 5432/8007 not yet recreated | short syntax "binds to all network interfaces (0.0.0.0)" unless a host IP is given **[doc]**; published ports are "diverted before it goes through the ufw firewall settings. Packets are routed before the firewall rules can be applied" **[doc]** | New nuance for the "no host firewall" gap: **a ufw rule would not protect any published container port** (it would protect host-native `:8000`, `:3579`, `:3582`). Container ports need loopback binds or `DOCKER-USER` rules. `:8002` and `:8080/:3000` are probably unnecessary publishes: nginx is on `zoe-network` and already reaches `zoe-auth:8002` by name, yet reaches multica via `host.docker.internal:8080/3000` (`nginx.d/locations.inc:234`). Pointing those two locations at `zoe-multica-backend:8080` / `zoe-multica-web:3000` would allow `127.0.0.1:` publishes. **[unverified]** whether any host-side client uses 8002/8080/3000. |
| Image pinning | `cloudflared:latest  # pinned: sha256:...` and `home-assistant:stable  # pinned: sha256:...` | compose supports `image: name@sha256:...` (as `zoe-database`, `zoe-ui`, `music-assistant` do) | **The pin is only in a comment** for cloudflared and HA: the actual reference floats. `livekit-server:latest` and `keeper.sh:latest` also float. |
| Restart vs health | `unless-stopped` | Restart policies react to the process exiting; "If you manually stop a container, the restart policy is ignored until the Docker daemon restarts or the container is manually restarted" **[doc]**; the page says nothing linking health status to restarts (**[doc-gap]**) | The six healthchecks are observational. A hung (not exited) HA or nginx is not restarted by Docker. Consistent with the MA reap relying on `docker stop` sticking. |
| Shutdown window | default 10 s everywhere | "stop_grace_period ... Default value is 10 seconds ... before sending SIGKILL" **[doc]** | Only HA (60 s recommended) is a documented outlier. Postgres uses `SIGINT` (fast shutdown) so it is fine. |
| Secrets | `env_file: .env` on the HA bridge | "Environment variables are often available to all processes, and it can be difficult to track access. They can also be printed in logs ... Using secrets mitigates these risks"; secrets are mounted at `/run/secrets/<name>` **[doc]** | **Hit, see G2.** Env names inside the bridge container: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `HA_ACCESS_TOKEN`, `ZOE_HA_VOICE_TOKEN`, plus TTS/whisper knobs **[src]** `docker exec homeassistant-mcp-bridge env | cut -d= -f1`. The bridge's code needs only `HA_*` and `ZOE_HA_VOICE_*`. |
| User | `zoe-auth` and the bridge run as uid 0; `zoe-omnigent` 1000, `potoken` `node`, `multica-web` `nextjs`, cloudflared 65532 | `user:` "Overrides the user ... If it's not set, then root" **[doc]** | Two root containers, one of which has its code bind-mounted read-write (`./services/homeassistant-mcp-bridge:/app`). |
| Memory/log limits | see status table | `--memory-swap` equal to `--memory` means no swap; unset with `--memory` allows swap equal to the limit; without `-m` an OOM can make the kernel "kill the host system's processes" **[doc]** | Confirms the A9 design (`memswap = 1.5x`). Nothing new. |
| Postgres image | `shm_size` default 64 MB | Docker Hub: "include `shm_size: 128mb`" in compose **[doc]** | Trivial; batch with A3. |

### Highest-value fix: Docker

**Replace `env_file: .env` on the HA bridge with an explicit `environment:` list** (`HA_BASE_URL`, `HA_ACCESS_TOKEN`, `ZOE_HA_VOICE_INGRESS_URL`, `ZOE_HA_VOICE_TOKEN`, `PYTHONUNBUFFERED`), and apply it in the same recreate as #1727. Result: a LAN-reachable (today) or compromised bridge no longer holds three third-party LLM API keys and the LiveKit secret.
Verify: `docker exec homeassistant-mcp-bridge env | cut -d= -f1 | sort` shows only the five names; `GET :8007/` 200; a voice light command still works end to end; add a compose-lint test next to `tests/unit/test_compose_loopback_binds.py` that fails if any service uses `env_file: .env` unless allow-listed. Rollback: restore the line.

---

## 6. systemd user units

Read via `systemctl --user cat/show` **[src]**.

| Setting | Zoe | Documentation | Verdict |
|---|---|---|---|
| Restart | `Restart=always`, `RestartSec=5`, default start-limit | long-running services: "`on-failure`" is the recommended mode; start-limit "restricts how many restart attempts can occur within the `StartLimitIntervalSec=` window" **[doc]** | With `RestartSec=5` the default 5-in-10-s limit can never trip (5 restarts take 25 s), so a unit that fails at boot loops forever and only `zoe-crash-loop-watch` notices **[unverified: default interval taken from general knowledge, not the fetched page]**. Harmless for the voice units; it is the right behaviour for always-on services. |
| Watchdog | none; Telegram uses an external 60-s timer | `WatchdogSec=` requires the service to send `WATCHDOG=1` via `sd_notify` **[doc]** | The external timer is a legitimate substitute; the cost is the 2-min `OnBootSec` blind spot noted in section 3. |
| Sandboxing | `NoNewPrivileges`, `RestrictRealtime` only; `systemd-analyze --user security`: flue-zoe-telegram **9.5 UNSAFE**, zoe-data **9.8 UNSAFE** | "Many sandboxing features requiring file system namespacing are unavailable in unprivileged user services ... most such settings function when combined with `PrivateUsers=true`" **[doc]** | Do not chase the score: `ProtectSystem=`/`PrivateTmp=` mostly do not work in a `--user` manager. The compensating control must be elsewhere (loopback binds, container isolation, firewall). `ProcSubset=pid` is the one cheap, safe addition **[doc]**. |
| Timers | oneshots with `OnBootSec`+`OnUnitActiveSec`; calendar timers with `Persistent=true` for export/regression; `zoe-serena-pregate-restart` has `Persistent=false` | `OnUnitActiveSec` is monotonic; `Persistent=` applies only to calendar timers; default `AccuracySec` is 1 minute **[doc]** | All correct, including `Persistent=false` for the pre-gate restart (a catch-up restart at the wrong time would be harmful). |
| Resource control | `MemorySwapMax=0`, caps, drop-ins | covered in infra D2/A2 | No new doc delta. |
| `EnvironmentFile=-/home/zoe/.hermes/.env` on zoe-data | still loaded | n/a | Hermes is retired (memory: "retirement MEASURED"). That file is mode 600 but still pours its variables into the live API process. Remove the line after confirming nothing reads those names. |

Highest-value fix: none urgent here; the one with a measured payoff is the Telegram start-retry (section 3). Drop the Hermes `EnvironmentFile` as housekeeping (verify with `systemctl --user show zoe-data -p EnvironmentFiles` and a `/health` poll).

---

## 7. nginx in `zoe-ui` (1.31.6)

| Setting | Zoe | Documentation | Verdict |
|---|---|---|---|
| Stream proxying | `/api/`: `proxy_buffering off`, `proxy_request_buffering off`, `gzip off`, `proxy_read_timeout 900s` | "When buffering is disabled, the response is passed to a client synchronously, immediately as it is received"; the `X-Accel-Buffering` header can also toggle it **[doc]** | Correct for NDJSON/SSE. |
| WebSocket upgrade | `map $http_upgrade $connection_upgrade { default upgrade; '' close; }` on `/api/`, `/ws/` | exactly the map pattern in the WebSocket page; default 60 s idle close, raised with `proxy_read_timeout` or app pings **[doc]** | Correct. |
| Upstream keepalive | none | since **1.29.7** keepalive to upstreams is on by default (32/worker) and `proxy_http_version` defaults to 1.1; for HTTP "the Connection header field should be cleared" **[doc]** | **The map above is why no keepalive happens:** every non-upgrade request to `/api/` sends `Connection: close` to uvicorn (the proxy module's own default is also `Connection close`) **[doc]**. This completes infra's "no upstream keepalive (52 TIME_WAIT)" note with its cause. It is also why the uvicorn 5-s keep-alive race cannot occur today. Changing `'' close` to `'' ""` would enable reuse and re-open that race (set `keepalive_timeout` in the upstream below uvicorn's 5 s). Gain is small (local TCP connect); leave unless a measurement shows connect cost. |
| Retry semantics | defaults | `proxy_next_upstream error timeout`; "requests with a non-idempotent method (POST, LOCK, PATCH) are not passed to the next server if a request has been sent" **[doc]** | Safe: no double-POST on a restart. |
| Client IP | `X-Real-IP $remote_addr`, `X-Forwarded-For $proxy_add_x_forwarded_for`; no `real_ip` module config | `set_real_ip_from` "defines trusted addresses that are known to send correct replacement addresses" **[doc]** | Behind Cloudflare, `$remote_addr` is the cloudflared container, so rate limits/audit see one client for every remote user. `CF-Connecting-IP` is read directly in `routers/voice_tts.py:6192` (spoofable from the LAN; only gates a LiveKit availability flag). |
| Rate limiting | none | `limit_req_zone`/`limit_req` (`rate`, `burst`, `limit_req_dry_run`) **[doc]** | Offered, unused. Would only matter on the Access-bypassed paths (none today) or the LAN. |
| `client_max_body_size` | `25m` on `/api/` only | default 1m elsewhere **[unverified this fetch]** | Fine (Telegram voice cap is 8 MiB at the sidecar; the sidecar does not go through nginx). |

Highest-value fix: none for performance. For trust, see G7 (nginx's header blanking only protects the nginx path).

---

## 8. uvicorn / FastAPI in zoe-data

Facts **[src]**: single worker, `--host 0.0.0.0 --port 8000`, backlog 2048, `Recv-Q 0 / Send-Q 2048` on the listener, `/health` 200 in 4.7 ms, 90 threads, RSS 1.4 GB, `LimitNOFILE 65536`, 35 fds.

- Docs: keep-alive "Defaults to 5 seconds"; `--limit-concurrency` answers "an immediate 503 ... not queued"; uvicorn recommends a process manager for multi-worker, and for proxy headers "Only trust clients you can actually trust" **[doc]**. This confirms infra's "not recommended" list (a stalled loop is not helped by a 503 limiter; one worker is by design).
- **New, doc-grounded gap: the proxy boundary is not a boundary.** The code assumes nginx is the only caller: `routers/skybridge.py:51` ("X-Real-IP is set ABSOLUTELY by nginx ... trustworthy") and nginx blanking `X-Internal-Token`/`X-Zoe-User-Id` (`nginx.d/locations.inc:134-135`). But `:8000` is bound on all interfaces and **answers on the LAN address** (`curl http://192.168.1.218:8000/health` returned 200 today, as did `:3579` and `:3582`). A LAN host can therefore send its own `X-Real-IP` straight to zoe-data, defeating the panel "device check" in skybridge (impact today: a household display name, since the identity gate behind it still needs the internal token) **[src]**. uvicorn's `--forwarded-allow-ips` default (127.0.0.1) is **[doc-gap]** on the fetched pages.
- The Flue brain (`:3579`) is token-gated (`src/auth.ts`, fail-closed); the Telegram sidecar (`:3582`) mounts an **unauthenticated** `/agents/zoe` router for a placeholder agent that is "NEVER DISPATCHED" and whose model is `placeholder/none` (`src/agents/zoe.ts`, `src/app.ts:201`), so it can only create store rows, but it is LAN-reachable surface with no purpose.

Highest-value fix: see G7 (a measured host firewall allow-list; remove the unused `/agents/zoe` mount).

---

## 9. Postgres container (17.10, `pgvector/pgvector:pg17`)

Live settings **[src]** (`pg_settings`): `shared_buffers` 128 MB, `work_mem` 4 MB, `effective_cache_size` 4 GB, `random_page_cost` 4, `effective_io_concurrency` 1, `jit` on, `log_min_duration_statement` -1, `idle_in_transaction_session_timeout` 0, `ssl` off, `password_encryption` scram-sha-256. `pg_hba.conf`: `trust` for loopback and replication loopback, then `host all all all scram-sha-256`. `/dev/shm` 64 MB, stop signal SIGINT.

| Topic | Documentation | Verdict |
|---|---|---|
| `shared_buffers` | "25% of the memory in your system" for a dedicated server; larger needs more `max_wal_size` **[doc]** | 128 MB is intentionally below that on a RAM-gated shared box (DB is 40 MB). Fine. |
| `random_page_cost` | default 4.0; "if your data is likely to be completely in cache, such as when the database is smaller than the total server memory ... decreasing random_page_cost might be appropriate"; setting it equal to `seq_page_cost` "makes sense if the database is entirely cached in RAM" **[doc]** | A 40 MB database on NVMe fits that case; `random_page_cost = 1.1` is documentation-supported. Effect today is tiny (the hot scans are non-sargable, infra D9). |
| `effective_io_concurrency` | SSDs "can often process many concurrent requests, so the best value might be in the hundreds" **[doc]** | Default 1 on NVMe; irrelevant at this size. |
| `idle_in_transaction_session_timeout` | "ensure that idle sessions do not hold locks ... an open transaction prevents vacuuming away recently-dead tuples"; do **not** set `statement_timeout` globally **[doc]** | Already in infra A3; the doc warning about `statement_timeout` is the new bit: set it per role/pool, never in `postgresql.conf`. `idle_session_timeout` is explicitly discouraged for pooled clients **[doc]**, which fits asyncpg's pool. |
| `pg_hba.conf` | "earlier records will have tight connection match parameters ... later records will have looser match parameters and stronger authentication"; example restricts to a `/32` **[doc]** | `host all all all scram-sha-256` is the loosest possible match. Together with the `0.0.0.0` publish it is a LAN password-guess surface (A3 closes the publish; narrowing the rule to the `zoe-network` subnet is defence in depth). `trust` on loopback is the Docker image's documented behaviour **[doc]** ("a password will be required if connecting from a different host/container"). |
| Image / volume | mount at `/var/lib/postgresql/data` for 17 **[doc]** | Correct. Compose pins `0.8.6-pg17@sha256`, live is older (A3). |

Highest-value fix: A3 stands (recreate with loopback publish and the PG flags). Add `random_page_cost=1.1` and `shm_size: 128mb` to the same `command:`/compose edit. Verify: `docker port zoe-database` shows `127.0.0.1`; `show random_page_cost` returns 1.1; the two background queries' `EXPLAIN ANALYZE` are unchanged or better.

---

## 10. Chroma persistence (embedded `PersistentClient`, chromadb 1.5.9)

Documentation (via the Chroma docs/cookbook search results): the persistent client "stores data locally in a directory" and is "intended for local development and testing. For production, prefer a server-backed Chroma instance"; "Chroma is thread-safe but not process-safe. You should avoid multiple processes writing to the same local path" **[doc, via search summary of docs.trychroma.com and cookbook.chromadb.dev]**. The direct docs URL I tried returned 404.

Zoe: embedded in zoe-data, which is the right call on a RAM-gated box (server mode adds a process). The documented trade-off is single-writer discipline, and Zoe mostly keeps it: backups copy via the sqlite backup API, `export_memory_store.py` opens `mode=ro`, compaction is requested from zoe-data in-process "a second chroma client must not delete the live collection" **[src]** `scripts/maintenance/zoe-nightly-dreaming.py:71-73`. **One exception:** `memory_quality_snapshot()` in the same script opens a **second `PersistentClient`** on the live `~/.mempalace` from another process every night (`zoe-nightly-dreaming.py:21-25`, via `scripts/lib/palace_client.py`). Opening a persistent client can run migrations and touch WAL/segment metadata, so "read-only intent" is not "read-only". Link to the 1.x-segfault history is **[unverified]**.

Highest-value fix: switch `memory_quality_snapshot()` to the same `sqlite3 mode=ro` reader as `export_memory_store.py` (or ask zoe-data's index-health endpoint it already calls). Verify: run the dreaming unit and compare `stat` mtimes of `~/.mempalace/chroma.sqlite3` and the HNSW segment directories before/after (unchanged); the snapshot counts equal the old method's on the same day.

---

## 11. GitHub Actions gates

Facts **[src]** (`gh api` GET): repo `jason-easyazz/zoe-ai-assistant` is **public**; branch protection on `main` requires **only `validate` and `secret-scan`** (strict up-to-date on, admins enforced, 0 required approvals, conversation resolution required, no force pushes); there are **no rulesets**; fork-PR approval policy is `first_time_contributors`; default `GITHUB_TOKEN` is read-only; `sha_pinning_required: false`; one self-hosted runner `jetson-orin` (labels `self-hosted, Linux, ARM64, jetson`) running as a system service with `MemoryMax=infinity`. `validate` and `secret-scan` run on `ubuntu-latest`; `deploy` (push to main, environment `production`), `self-hosted-tests` (schedule/dispatch) and `voice-gate`'s device job run on the Jetson. Actions are tag-pinned (`actions/checkout@v7`, `github-script@v9`, `setup-python@v7`, `GitGuardian/ggshield-action@v1.55.0`).

| Topic | Documentation | Verdict |
|---|---|---|
| Required checks | "Required status checks must have a successful, skipped, or neutral status" **[doc]**; same job name in two workflows "can cause ambiguous status check results" **[doc]** | `voice-gate`, `greptile-gate` and `pr-hygiene` are **not required**, contradicting the memory note that Greptile is "sole required gate". A skipped required check passes, which is why `validate`'s own `if:` conditions deserve a periodic audit. |
| Self-hosted + public repo | "Self-hosted runners should almost never be used for public repositories ... any user can open pull requests against the repository and compromise the environment" **[doc]** | Zoe's mitigation is real and well documented in `voice-gate.yml` (base-SHA checkout under `pull_request_target`, device job only for same-repo PRs or a maintainer label) and `validate` is on GitHub-hosted runners. The doc-recommended hardening not present: **ephemeral/JIT runners** ("GitHub only assigns one job to a runner") **[doc]**, a stricter fork-approval policy, and any resource cap on the runner (infra A7). |
| `pull_request_target` | "The `pull_request_target` and `workflow_run` triggers, when used with the checkout of an untrusted pull request, expose the repository to security compromises" **[doc]** | `voice-gate.yml` checks out the **base** SHA only: compliant. Keep that invariant pinned by a test. |
| Action pinning | "Pinning an action to a full-length commit SHA is currently the only way to use an action as an immutable release" **[doc]** | All four third-party/first-party actions are tag-pinned. `GitGuardian/ggshield-action` is the one third-party action on the secret-scan path. |
| Token scope | "set the default permission for the GITHUB_TOKEN to read access only" **[doc]** | Done (`default_workflow_permissions: read`). |

Highest-value fix: **require `voice-gate` once it is cheap enough to be reliable, or document why not**; separately raise the fork-approval policy to "all external contributors" (a settings toggle, zero cost to Zoe's own PRs) and SHA-pin `ggshield-action`. Verify: `gh api .../branches/main/protection` lists the context; a PR from a fork shows "Approve and run" before any job starts.

---

## 12. Security hygiene (consolidated, no secret values)

| # | Finding | Evidence | Documentation |
|---|---|---|---|
| H1 | **zoe-auth logs a live bootstrap setup token at WARNING on every start** (`core.account_setup`), into the container's json-file log (3 x 10 MB) readable by anyone in the `docker` group. The code comment says it is meant for `journalctl`, which is not where a container logs. | `docker logs zoe-auth` line 2 (value not read into this record); `services/zoe-auth/core/account_setup.py:6-18,94`. Design intent is sound (races for first-password claims); the sink is not. | OWASP: access tokens, passwords and "primary secrets" are data to remove or mask from logs **[doc]**. Docker: env vars "can be printed in logs ... without your knowledge"; use mounted secrets **[doc]**. Fix: write the token to a 0600 file under `./data` (or `docker exec`-only print) instead of the logger, and expire it. |
| H2 | HA bridge holds third-party LLM keys + LiveKit secret it never uses (root, code mounted rw, LAN-exposed until #1727). | G2 above. | Compose secrets/least-privilege **[doc]**. |
| H3 | Telegram bot token reaches the journal on network errors. | Section 3; 1 token-shaped line in the retained journal (2026-10-04 14:35:38). | OWASP **[doc]**. |
| H4 | `cert.pem` (10-year, account-level) mounted in the tunnel container; tunnel credentials `644`. | Section 4. | Cloudflare local-tunnel terms **[doc]**. |
| H5 | File modes: repo-root `.env` is **`664`** (group-writable, world-readable) while every service `.env`, `.hermes/.env`, `ssl/zoe.key` are `600`; `homeassistant/secrets.yaml` is `644`; `ssl/zoe.crt` and its backup are `777`. All are git-ignored. | `stat` today. The root `.env` is the file that holds the database password, HA token and LiveKit secret (names per `docker-compose.yml`). | General least privilege; no doc needed to justify `chmod 600`. Check that compose still reads it (it runs as the same user). |
| H6 | HA: two admin 10-year LLATs; over-broad `trusted_proxies`. | Section 1. | HA auth docs and `http` docs **[doc]**. |
| H7 | Cloudflare Access policies themselves cannot be read from the box: whether `zoe.the411.life` has any Bypass/path rule, the service-token expiries, and whether `ssh.` has an app are **[unverified]**. Edge responses show Access on `zoe.` and `buildzoe.`. | `curl` today. | Bypass "does not enforce any Access security controls and requests are not logged" **[doc]**; token expiry alerts a week prior **[doc]**. Action for the operator: export the Access app/policy list and the token expiry dates into `docs/knowledge/`. |
| H8 | Container users and privileges: two root containers, HA privileged with SELinux labels off, no `cap_drop`/`read_only`/`no-new-privileges` anywhere. | `docker inspect`. | Compose `user`, `cap_drop`, `read_only`, `security_opt` **[doc]**. HA's privilege is documented as recommended; the others are cheap wins (`cap_drop: [ALL]` + `read_only` for the bridge, `user:` for auth). |
| H9 | Retired-service secrets still injected: `EnvironmentFile=-/home/zoe/.hermes/.env` into zoe-data. | `systemctl --user cat zoe-data`. | Least privilege. |

---

## Gaps ranked

Impact x effort, with the proof that would close each. G-numbers are referenced above. "S" = under an hour, "M" = a PR plus an operator step.

| Rank | Gap | Impact | Effort | Proof it is closed |
|---|---|---|---|---|
| G1 | `ssh.the411.life` appears to have no Access app (530 vs 302) | High if password auth is on | S (dashboard) | `curl -I` returns 302 to Access or the ingress is gone; `cloudflared access ssh` prompts |
| G2 | HA bridge env contains 3 LLM API keys + LiveKit secret (`env_file: .env`), root, LAN-exposed until #1727 | High | S (batch with A3) | `docker exec ... env` shows only `HA_*`/`ZOE_HA_VOICE_*`; compose-lint test |
| G3 | Telegram token in journal on errors; boot DNS race leaves Telegram down 2+ min | High (credential) + Med (availability) | S | redaction unit test with negative control; `grep -c` = 0 after rotation; start-retry test |
| G4 | Tunnel container holds `cert.pem`; creds `644`; no `originRequest.access` on the no-auth Omnigent origin | Med | S | tunnel re-registers without the mount; forged JWT rejected at cloudflared |
| G5 | HA tokens: two admin 10-year LLATs for a 4-endpoint client | Med | S | non-admin token passes the 5 calls; old tokens return 401 |
| G6 | MA still on 2.8.7 (three GHSAs, host network); stopped now = free window; reap precondition unmet while ytmusic is configured | Med | M | image digest + `/info` = 2.10.3; probe GREEN; log-opts present |
| G7 | zoe-data `:8000` (and `:3579`, `:3582`) reachable from the LAN while code trusts nginx-set headers; unused unauthenticated `/agents/zoe` on the Telegram sidecar | Med | M (measure callers first) | nftables allow-list from a 24 h `ss` sample; `curl http://192.168.1.218:8000/` from another LAN host fails; route removed |
| G8 | Telegram: no timeout on a sequential brain call, no typing action, no 4096 split | Med | S | stalled-mock test unblocks the next chat in 90 s; long reply arrives in 2 parts |
| G9 | Nightly second-process `PersistentClient` on the live palace | Med (rare, severe) | S | palace file mtimes unchanged across the dreaming run |
| G10 | Compose pins that exist only in comments (cloudflared, HA); `:latest` livekit/keeper | Low-Med | S | `docker inspect` image ref contains `@sha256:` |
| G11 | cloudflared QUIC timeouts (24 warnings/day); `--protocol` unset | Low-Med | S | WRN count per 24 h before/after `--protocol http2` |
| G12 | GitHub: `voice-gate` not required; fork approval `first_time_contributors`; actions tag-pinned; runner not ephemeral/capped | Med (public repo + self-hosted) | S-M | protection lists the context; fork PR needs approval |
| G13 | HA 10-s stop window (unclean recorder shutdowns), compose drift (`privileged`), trusted_proxies ranges | Low-Med | S-M | no "shutdown cleanly" warning after a restart; ranges narrowed |
| G14 | Repo-root `.env` mode 664; `secrets.yaml` 644; `zoe.crt` 777; zoe-auth setup token in docker logs | Low-Med | S | `stat` shows 600; log line gone |
| G15 | Postgres `random_page_cost`, `shm_size`, `pg_hba` breadth | Low | S (with A3) | `show` values; `pg_hba` narrowed |
| G16 | localtuya cloud error and a dead ESPHome entry in HA | Low (noise; real if a Tuya plug is re-paired) | S | no warnings at HA start |
| G17 | nginx `Connection close` map, `host.docker.internal` for multica, 3 published ports probably unneeded | Low | S-M | measured connect-time delta; `docker port` shows loopback |

## Recommendation

1. **Today (operator, minutes, no restarts):** G1 (Cloudflare dashboard: look at the Access app list, gate or delete `ssh.`), H5 `chmod 600` on the root `.env` and `secrets.yaml`, and export the Access app/policy list and service-token expiry dates into `docs/knowledge/` (H7).
2. **One PR to the Telegram sidecar** (G3 + G8): redaction, start-retry, timeout, typing, 4096 split; rotate the bot token afterward. This is the only item where a doc-grounded fix also removes a measured outage (2+ min of Telegram down after each reboot).
3. **One PR to compose + the operator recreate that A3 already needs** (G2, G4, G10, G15 and G13's `stop_grace_period`): bridge `environment:` allow-list, drop the `cert.pem` mount, real digests for cloudflared/HA, `command:` PG flags. Do it in the same window as #1727 so the bridge and Postgres are recreated once.
4. **MA recreate on 2.10.3 while the reaper has it stopped** (G6), after deciding the reap/ytmusic question.
5. **Then the slower trust-boundary work** (G7, G12): a measured host-firewall allow-list for `:8000/:3579/:3582` (remembering that ufw does not cover published container ports), and the GitHub settings toggles.

What not to do, with the documentation behind it: do not add `@grammyjs/runner` or webhooks (grammY: long polling is the default recommendation; runner only past ~500 updates/s); do not chase systemd sandbox scores on `--user` units (the manual says most namespacing options do not work there); do not move Chroma to server mode on this RAM budget (it is a documented trade-off, mitigated by single-writer discipline, G9); do not turn on uvicorn `--limit-concurrency` (immediate 503, unqueued).

## Not measured / limits

- Cloudflare Zero Trust dashboard, HA UI, MA UI and Tuya console were not read; every claim about their contents is **[unverified]** or inferred from edge responses.
- MA is stopped, so its current provider state, token owner and `/info` were not read; the provider history is from its log up to 17:06 UTC on 10-04.
- `sshd -T`, `iptables`, and root-owned files were not readable; HA's token metadata was read through the container with secret fields excluded.
- The Chroma "docs" statements come from search-result summaries of the official docs/cookbook because the direct docs URL returned 404; treat them as [doc, indirect].
- Telegram `sendChatAction` duration, uvicorn's `--forwarded-allow-ips` default, HA's `login_attempts_threshold` default and the MA WebSocket/events API were not present on the fetched pages.
- No timing change was measured for any proposed fix; each fix lists the check that would measure it.

## Sources (all fetched 2026-10-05)

Home Assistant: https://developers.home-assistant.io/docs/api/rest/ , https://developers.home-assistant.io/docs/api/websocket/ , https://developers.home-assistant.io/docs/auth_api/ , https://www.home-assistant.io/integrations/http/ , https://www.home-assistant.io/installation/linux/ , https://www.home-assistant.io/integrations/esphome/ , https://github.com/rospogrigio/localtuya
Music Assistant: https://www.music-assistant.io/installation/ , https://www.music-assistant.io/api/ , https://www.music-assistant.io/music-providers/youtube-music/ , https://www.music-assistant.io/settings/user-management/ , https://www.music-assistant.io/settings/core/ , https://developers.music-assistant.io/ (no API docs on the fetched page)
Telegram / grammY / Node: https://grammy.dev/guide/deployment-types , https://grammy.dev/plugins/runner , https://grammy.dev/advanced/flood , https://grammy.dev/guide/errors.html , https://core.telegram.org/bots/faq , https://core.telegram.org/bots/api , https://nodejs.org/docs/latest-v22.x/api/net.html
Cloudflare: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/cloudflared-parameters/run-parameters/ , .../cloudflared-parameters/origin-parameters/ , .../do-more-with-tunnels/local-management/local-tunnel-terms/ , .../do-more-with-tunnels/local-management/create-local-tunnel/ , .../troubleshoot-tunnels/common-errors/ , .../use-cases/ssh/ , https://developers.cloudflare.com/tunnel/monitoring/ (via search) , https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/ , https://developers.cloudflare.com/cloudflare-one/access-controls/policies/ , https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/ , https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/ , https://developers.cloudflare.com/network/websockets/
Docker: https://docs.docker.com/engine/containers/resource_constraints/ , https://docs.docker.com/engine/containers/start-containers-automatically/ , https://docs.docker.com/reference/compose-file/services/ , https://docs.docker.com/compose/how-tos/use-secrets/ , https://docs.docker.com/engine/network/packet-filtering-firewalls/ , https://hub.docker.com/_/postgres
systemd: https://man7.org/linux/man-pages/man5/systemd.service.5.html , .../systemd.exec.5.html , .../systemd.timer.5.html , https://man7.org/linux/man-pages/man7/systemd.special.7.html
nginx: https://nginx.org/en/docs/http/websocket.html , .../ngx_http_upstream_module.html , .../ngx_http_proxy_module.html , .../ngx_http_realip_module.html , .../ngx_http_limit_req_module.html
uvicorn: https://uvicorn.dev/deployment/ , https://uvicorn.dev/server-behavior/
PostgreSQL 17: https://www.postgresql.org/docs/17/runtime-config-query.html , .../runtime-config-resource.html , .../runtime-config-client.html , .../auth-pg-hba-conf.html
Chroma (indirect, via search): https://docs.trychroma.com/docs/run-chroma/clients , https://cookbook.chromadb.dev/core/clients/ , https://cookbook.chromadb.dev/running/deployment-patterns/
GitHub: https://docs.github.com/en/actions/reference/security/secure-use , https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches , https://docs.github.com/en/actions/reference/runners/self-hosted-runners
OWASP: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
