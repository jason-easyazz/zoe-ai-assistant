---
type: research
title: Panel anywhere - a touch panel linked to its own Zoe from anywhere in the world, for many households (field research, 2026-10-16)
date: 2026-10-16
status: research-only; nothing built, nothing changed on any live system. The design is docs/architecture/panel-anywhere.md.
description: Research record behind the panel-anywhere design. It covers the Nabu Casa remote-UI pattern read from source (snitun 0.47.0, hass-nabucasa 2.7.0), the adopt candidates for a shared multi-tenant SNI-passthrough relay (frp, snitun, sish, rathole, zrok/OpenZiti, Pangolin/Newt, NetBird, inlets, piko, boringproxy, Cloudflare Tunnel, Headscale per household), certificates (ACME DNS-01 through a narrow relay API, TLS-ALPN-01, Let's Encrypt limits, the Public Suffix List, a private-use name no public CA issues for), pairing and identity on top of the #2082 device-token kiosk credential, the voice WAN budget through a Sydney relay, and why one relay should carry every inbound need.
builds_on: docs/architecture/panel-remote-access.md + docs/research/panel-remote-access-2026-10.md (PR #2080, owner-only link over Headscale), services/zoe-data/panel_kiosk_credential.py + routers/panel_kiosk.py (#2082), routers/panel_auth.py, routers/panel_provision.py, scripts/setup/remote-access/headscale/ (PR #2083)
---

# Panel anywhere: research (2026-10-16)

Owner goal (2026-10-16, verbatim): *"i wanted something where users could link a touch panel to their zoe and then
take it somewhere else in the world."*

Product constraints from the owner brief: any household pairs a panel to its own Zoe box; the panel then works on any
network (hotel Wi-Fi, a phone hotspot, CGNAT at either end) with no VPN app, no port forwarding and no account setup
beyond pairing; many households, isolated from each other; content (audio, transcripts, pages) stays end-to-end
encrypted so the relay operator cannot read it (VISION principle 2).

The design built on this record is [panel-anywhere.md](../architecture/panel-anywhere.md). Nothing was changed to write
it. Placeholders: `<relay-domain>` (the public relay name), `<hid>` (a household's opaque relay id), `<box>` (the
household's Zoe box). No addresses or household names appear here.

**Evidence tags.** **[verified]** = read in primary source code (opensrc cache, version pinned), an official doc, a
release API or a standard in this session. **[secondary]** = a search result, blog, mirror or a sub-agent's reading not
re-read by the author. **[unverified]** = reasoning or memory, to be proven in a lab cell.

## 1. The Nabu Casa remote-UI pattern, read from source

Sources: `snitun` 0.47.0 (PyPI, 2026-06-29) and `hass-nabucasa` 2.7.0 (PyPI, 2026-08-27), fetched with
`opensrc fetch pypi:snitun@0.47.0 pypi:hass-nabucasa@2.7.0` into `~/.opensrc/repos/github.com/NabuCasa/`.

### 1.1 What snitun is

- "End-to-End encryption with SNI proxy on top of a TCP multiplexer" [verified: snitun `README.md`]. The home
  instance ("client") holds one outbound TCP connection to the SniTun server and enters multiplexer mode; an external
  device opens TLS to the server; the server reads the SNI from the ClientHello and forwards the raw TLS bytes over the
  multiplexer; the client opens a local connection to its endpoint; TLS runs end to end between the device and the
  instance [verified: README sequence diagram; `snitun/server/listener_sni.py` `handle_connection` calls
  `parse_tls_sni` then `peer_manager.get_peer(hostname)`].
- The server never holds a certificate or a key for instance hostnames: it parses the ClientHello only
  (`snitun/server/sni.py`) [verified].
- **Single-port mode** shares one port between both kinds of connection: the first byte `0x16` (a TLS record) goes to
  the SNI proxy, a payload starting `gA` (a Fernet token) goes to the peer listener, anything else is closed
  [verified: `snitun/server/run.py` `SniTunServerSingle._handler`]. A dual-port mode (`SniTunServer`) and a threaded
  worker mode (`SniTunServerWorker`) also exist [verified: same file].
- **PROXY protocol inbound** (v1 and v2), off by default and documented as trustworthy only behind a known proxy
  [verified: `snitun/server/proxy_protocol.py` docstring; `proxy_protocol` flag in `run.py`]. The caller's address is
  passed to the instance inside the encrypted `New` message from protocol version 2 [verified: README].
- **Per-peer throttling** in bytes/second, set by the session master in the token [verified:
  `snitun/multiplexer/core.py` `_throttling`; `hass_nabucasa/remote.py` reads `data["throttling"]`].
- Multiplexer header encryption: `aes-cbc` (default), `aes-gcm` or `aes-gcm-siv`; the payload is expected to be TLS
  [verified: README]. The multiplexer cipher is not the end-to-end layer; the inner TLS is.

### 1.2 Instance identity: how the relay authenticates the box

- **The SniTun server does no user authentication** - the README says so in bold [verified: README, "Note: SniTun
  server does not perform any user authentication!"].
- A separate **session master** (Nabu Casa's closed cloud API) authenticates the instance and returns a **Fernet token**
  encrypted with a key the session master and the SniTun server share. The payload names `hostname`, optional `alias`
  list, `valid` (expiry), `aes_key`, `aes_iv`, `protocol_version`, `cipher` [verified: README "Fernet token"].
- The server decrypts the token, rejects it after `valid`, and runs an AES challenge-response proving the connecting
  client holds the key the session master put in the token [verified: `snitun/server/peer_manager.py`
  `create_peer`; README "Challenge/Response"].
- **Hostname binding is by construction**: the client never asks for a hostname; the server registers the peer under
  the hostname inside the token, and a new peer for the same hostname shuts the stale one down
  [verified: `peer_manager.py` `add_peer`]. So tenant isolation lives entirely in the session master's decision of which
  hostname to put in which instance's token.
- On the Home Assistant side the instance authenticates to the session master with the account's Cognito id token
  (`Authorization: Bearer <id_token>`) [verified: `hass_nabucasa/api.py` `_call_cloud_api`], calls
  `remote_access_register` (returns `domain`, `email`, `server`, `alias`) and `remote_access_snitun_token` with a
  locally generated AES key/IV (returns `token`, `valid`, `throttling`, `protocol_version`) [verified:
  `hass_nabucasa/instance_api.py`, `remote.py` `load_backend`, `_refresh_snitun_token`]. It refuses to connect when
  the subscription has expired [verified: `remote.py` raises `SubscriptionExpired`].
- The instance picks its relay by latency: it pings targets from `remote_access_ping_targets` and sends the results to
  `register` [verified: `instance_api.py` `ping_targets`, `remote.py` `_fetch_ping_results`]. The cloud's IoT channel
  can also ask an idle instance to connect (`remote_sni` handler) [verified: `hass_nabucasa/iot.py`].

### 1.3 Certificate issuance

- The instance runs its own ACME client against Let's Encrypt (`acme-v02.api.letsencrypt.org` in the service
  discovery defaults) and selects only the **`dns-01`** challenge [verified: `hass_nabucasa/const.py`; `acme.py`
  `_start_challenge`].
- The certificate **private key is generated on the instance** and written mode 600; the ACME account key is local
  too [verified: `acme.py` `_generate_csr`, `_load_account_key`].
- The DNS TXT record is set **through a narrow cloud API**: `remote_access_dns_challenge_set` /
  `remote_access_dns_challenge_remove` with body `{"txt": value}` - the instance never names the record, so the API can
  only write the challenge for the instance's own hostname [verified: `instance_api.py`].
- Nabu Casa put `ui.nabu.casa` on the Public Suffix List (submitted by Nabu Casa) [verified: PSL fetched 2026-10-16].
  That makes each instance hostname its own "registered domain" for Let's Encrypt's per-domain limit (section 3.3).
- Trust caveat [unverified, reasoning]: whoever controls the relay zone's DNS could pass a DNS-01 challenge for any
  instance hostname and mint a publicly trusted certificate for it, then terminate traffic. Nabu Casa's public material
  recommends watching Certificate Transparency logs [secondary: support.nabucasa.com search results; the page itself
  returned 403]. A public certificate under the relay operator's zone is therefore end-to-end against a passive relay,
  not against an operator who turns malicious. Section 3.4 is the answer for the panel.

### 1.4 Licences

- **snitun: GPL-3.0** [verified: `LICENSE`, PyPI classifier, GitHub API]. **hass-nabucasa: GPL-3.0-only**
  [verified: PyPI `license_expression`]. Zoe is MIT [verified: repo `LICENSE`].
- GPL-3.0 has no network-use clause (unlike AGPL), so running a SniTun server as a service does not by itself oblige
  publishing server changes [unverified: licence reading, not legal advice]. Importing snitun into zoe-data and
  distributing the result would make the combined program GPL-3.0; running the client as a separate process
  (its own unit) keeps zoe-data MIT [unverified: licence reading].
- Maintenance: snitun pushed 2026-09-01, 16 open issues, releases 0.45.2 (2026-01) to 0.47.0 (2026-06) [verified:
  GitHub API, PyPI]. 0.47.0 passes the 14-day age rule.

### 1.5 Verdict on the pattern

The owner's belief is right in shape and the source confirms each piece: outbound multiplexed connection from the
box, SNI routing without decryption, TLS ending on the box with a key generated there, DNS-01 through a narrow API,
and tenant isolation decided by a session master that the relay software itself does not contain. What it does not
give for free: the session master (Nabu Casa's is closed), protection against a malicious relay operator minting a
certificate (section 3.4), and a client that is not tied to aiohttp (snitun's ready client wraps an aiohttp server;
`snitun/client/connector.py` `EndpointConnector` forwards to a TCP endpoint and would need a small runner) [verified:
`snitun/utils/aiohttp_client.py`, `snitun/client/connector.py`].

## 2. Adopt candidates for the relay and multiplexer

Requirements: one shared relay on a public VPS (amd64, host nginx on 443), many households' boxes (arm64 Jetson,
often behind CGNAT) holding outbound connections, the panel reaching `<hid>.<name>` and only its own box, SNI
passthrough so the relay never decrypts, per-tenant authorization (a box cannot claim another household's name),
abuse limits, low operator burden. The 14-day rule: a release younger than 14 days is not adopted; the previous one is
named.

Most of this section comes from a sub-agent's source reading [secondary unless marked]; the frp claims the design
depends on were re-read by the author [verified, marked].

| Candidate | Licence | Release to adopt (age rule) | SNI passthrough | Per-tenant hostname ownership | Verdict |
|---|---|---|---|---|---|
| **frp** | Apache-2.0 [verified] | **v0.71.0** (2026-08-14); v0.71.1 (2026-10-14) is 2 days old [verified: release API]. ~110k stars, pushed 2026-10-15 [verified] | yes: `type = "https"` reads the ClientHello and replays the raw bytes [verified: `pkg/util/vhost/https.go` `GetHTTPSHostname`] | not built in (one shared `auth.token`); **server HTTP plugins** `Login` / `NewProxy` / `NewUserConn` can reject or rewrite, and a plugin error rejects the operation [verified: `pkg/plugin/server/manager.go` `handleMutableContent`] | **pick** |
| **snitun** | GPL-3.0 [verified] | 0.47.0 (2026-06-29) [verified] | yes [verified, section 1] | yes, by construction: hostname inside a session-master token [verified] | **runner-up / reference** |
| sish | MIT | v2.24.0 (2026-10-02); v2.24.1 is 7 days old | yes: `--sni-proxy`, `--sni-proxy-https` (`cmd/sish.go`) | SSH-key auth, no key-to-name binding; `--authentication-key-request-url` would carry our own logic | fallback, weaker isolation |
| rathole | Apache-2.0 | v0.5.0 (2023-10-01); only a `dev-latest` prerelease since | no SNI vhost (one service per name) | per-service token | unmaintained, wrong shape |
| zrok / OpenZiti | Apache-2.0 | v2.0.6 (2026-09-29) | public shares terminate at the zrok frontend; private shares need a zrok client on the panel | real accounts | heavy (controller, router, database), wrong shape |
| Pangolin / Newt | AGPL-3.0 + commercial dual licence (Pangolin), AGPL-3.0 (Newt) | Pangolin 1.24.0 (2026-09-30) | browser resources go through Traefik with automatic certificates, i.e. TLS ends at the relay | organisations; commercial edition gated by revenue | **fails end-to-end**; licence trap |
| NetBird (self-hosted) | BSD-3 client, AGPL-3.0 `management/`, `signal/`, `relay/` | v0.80.x (v0.81.x is under 14 days) | not an SNI relay: a WireGuard mesh; the panel needs a NetBird client | accounts, but a mesh-VPN model | wrong model (a VPN app on the panel) |
| inlets-pro | proprietary, binaries only | 0.12.0 | TCP/SNI in the product | not designed for many tenants | closed, per-seat cost |
| piko | MIT | v0.10.0 (2026-05-08) | no: an HTTP reverse proxy that terminates TLS (`server/proxy/httpproxy.go`) | best auth design (JWT `endpoints` claim, `pkg/auth/jwtverifier.go`) | fails end-to-end |
| boringproxy | MIT | v0.10.0 (2023-01-04), last commit 2023-05 | yes (`tls-termination` passthrough) | admin tokens, no tenant model | unmaintained |
| Cloudflare Tunnel per household | proprietary service | n/a | HTTP mode terminates TLS at the edge (#2080 research 2.4); TCP mode needs `cloudflared access` on the panel (#2080 research 2.7, verified in source there) | one tunnel per household on one account; a ~1,000-tunnel account cap [secondary, docs mirror] | fails end-to-end in HTTP mode; TCP mode = a client app per panel + every household's traffic on one Cloudflare account under its terms |
| Headscale per household | BSD-3-Clause | v0.29.4 (v0.29.5 is 3 days old; #2083 pins v0.29.4) [verified in #2083] | a mesh VPN, not an SNI relay | one instance = one tailnet, no multi-tenancy; N households = N control servers, N public names, N UDP 3478 listeners | wrong model at scale; **keep for operator admin** (section 7) |

### 2.1 frp, the parts the design depends on (re-read in source, v0.71.0)

Cache: `~/.opensrc/repos/github.com/fatedier/frp/v0.71.0`.

- **Passthrough.** `GetHTTPSHostname` wraps the connection in a shared conn, reads only the ClientHello to learn
  `ServerName`, and hands back a conn that replays the buffered bytes; no certificate or key on the relay. An unknown
  name gets a TLS `unrecognized_name` alert (`vhostFailed`) [verified: `pkg/util/vhost/https.go`]. That alert lets a
  panel tell "relay up, my box offline" from "relay unreachable" (design section 6).
- **Plugin semantics.** For `Login` and `NewProxy`, each configured plugin is called in turn; a transport error or a
  `Reject` aborts the operation, and a response with `Unchange: false` replaces the content [verified:
  `manager.go` `handleMutableContent`]. **With no plugin configured everything is allowed** (`len(plugins) == 0`
  returns the content unchanged) [verified: same function]: fail-closed per call, fail-open per configuration. The
  design's deploy check and a negative control cover that.
- **Server-assigned names.** Because a `NewProxy` plugin may return changed content, the plugin can overwrite
  `SubDomain` / `CustomDomains` with the names the session master assigned, so the box never picks its own name - the
  snitun token model reproduced on frp [verified: the content shape in `pkg/plugin/server/types.go`, the rewrite in
  `manager.go`]. Rejecting client-supplied `customDomains` matters: the vhost router matches wildcards
  (`*.example.com`), so an unvetted custom domain could shadow other households [secondary: sub-agent read of
  `pkg/util/vhost/router.go`]; a second registration of a live name fails with `ErrRouterConfigConflict` [verified:
  `router.go`].
- **Bandwidth.** `transport.bandwidthLimit` is client-set and enforced by the server only with
  `bandwidthLimitMode = "server"`; the default mode is `client` [verified: `pkg/config/v1/proxy.go`]. The plugin
  overwrites both. v0.71.1's notes mention a server-side bandwidth-limit fix for work connections [secondary], which is
  one more reason to re-check after its 14 days.
- **Box-to-relay transport.** `transport.protocol` accepts `tcp`, `kcp`, `quic`, `websocket`, `wss` [verified:
  `pkg/config/v1/client.go`]. TLS to the server is on by default since v0.50.0, but **the client skips verifying the
  server certificate unless `transport.tls.trustedCaFile` is set** [verified: `pkg/transport/tls.go` sets
  `InsecureSkipVerify = true` when no CA path is given]. The design sets it.
- **PROXY protocol.** frpc can send PROXY v1/v2 to the local service (`proxyProtocolVersion`) [verified:
  `pkg/config/v1/proxy.go`]; frps does not accept PROXY protocol inbound [secondary], so behind the host nginx the box
  sees the relay's own address, never the panel's. The design does not want the panel's address anyway (section 4).
- **Per-connection hook.** `NewUserConn` passes each incoming user connection (with `RemoteAddr`) to plugins
  [verified: `server/proxy/proxy.go`]: a place for per-household connection-rate limits.
- **Packaging.** Release assets for linux amd64, arm64, arm, arm_hf [secondary: release assets]. One static binary
  per side; households need only a config file.
- **Sharing 443 with the host nginx.** nginx `stream` with `ssl_preread on` and a `map $ssl_preread_server_name` can
  send relay names to frps' `vhostHTTPSPort` on loopback and everything else to the existing sites [secondary: nginx
  docs pattern, not tested here]. Moving the existing sites behind a stream front is a change to that server; a second
  public address for the relay avoids it.

### 2.2 frp vs snitun

| | frp v0.71.0 | snitun 0.47.0 |
|---|---|---|
| Licence | Apache-2.0 | GPL-3.0 (client as its own process to keep zoe-data MIT) |
| Tenant binding | our plugin rewrites names (fail-open if the plugin is not configured) | by construction (hostname inside the token) |
| Code we write | the plugin = the session-master API (we write that in either case) | the session master + a ~100-line client runner around `EndpointConnector` |
| Box-to-relay transport | TCP, QUIC, KCP, WebSocket | one TCP connection (head-of-line blocking across streams) |
| Panel's address at the box | not available behind the host nginx | available (PROXY in, encrypted `New` message out) |
| Operations | one static binary, Prometheus metrics, wide community use | Python asyncio, worker threads; proven at Nabu Casa's scale [secondary] |
| Fast path for one household | config files only, no code | a hand-minted Fernet token + the runner |

## 3. Certificates

### 3.1 The options

| Method | How it works through an SNI relay | Relay holds the key? | Notes |
|---|---|---|---|
| **DNS-01 through a narrow relay API** | the box runs ACME, asks the relay API to publish `_acme-challenge.<hid>.<relay-domain>` = value; the API writes only the caller's own record | no | what Nabu Casa does [verified, section 1.3]; works while the tunnel is down; the API is a small authenticated endpoint on the session master |
| TLS-ALPN-01 through the tunnel | the CA connects to `<hid>.<relay-domain>:443` with ALPN `acme-tls/1`; SNI passthrough delivers it to the box, which answers with the challenge certificate | no | needs no DNS API at all [unverified: RFC 8737 behaviour + passthrough reasoning, lab cell C2]; needs the box's ACME client to own the ALPN answer (lego or acme.sh support it; certbot does not [secondary]) and an nginx `ssl_preread` ALPN split on the box |
| HTTP-01 through the tunnel | needs a plain-HTTP vhost on the relay's port 80 routed to the box | no | an extra listener and path; frp `type = "http"` could carry it [secondary]; least attractive |
| Wildcard on the relay | relay terminates TLS | **yes** | violates the brief |

### 3.2 Let's Encrypt limits for many households

- 50 new certificates per registered domain per 7 days, refilling 1 every 202 minutes; registered domain is decided by
  the Public Suffix List; overrides can be requested for this limit and for new orders per account (300 per 3 hours),
  and take weeks [verified: https://letsencrypt.org/docs/rate-limits/].
- 5 certificates per exact set of names per 7 days, no override; 5 authorization failures per name per account per
  hour [verified: same page].
- Hence: put the public relay zone on the PSL before many households join (Nabu Casa's route, section 1.3), or request
  an override. A single household renewing every 60 days is far below every limit.
- Every publicly trusted certificate is logged in Certificate Transparency, so `<hid>.<relay-domain>` becomes
  discoverable [secondary: CT is standard practice]. `<hid>` is therefore random, never a family name.

### 3.3 The panel does not need a public certificate

- **`.internal` is reserved for private use** (ICANN board resolution, July 2024) and will never be delegated in the
  public DNS; the IETF draft expects public CAs not to issue for it and says TLS there needs a private CA
  [secondary: draft-davies-internal-tld-06 and ICANN coverage via search; the draft is not yet an RFC].
- An SNI relay routes on the SNI string, not on DNS. So a panel can connect to the relay's address with SNI
  `<hid>.zoe.internal` and the box can answer with a leaf from the **household's own CA** [unverified: reasoning,
  lab cell C1]. No public CA will issue for that name, so the relay operator cannot obtain a certificate the panel
  would accept, and the name never appears in Certificate Transparency.
- The panel is a managed device: the household CA root is installed at pairing (Chromium's NSS store for the kiosk,
  a CA bundle for the daemon, as #2080 section 4.1 already plans for its private CA).
- Name resolution on the panel: `/etc/hosts`, kept by the panel agent, maps `<hid>.zoe.internal` to the box's LAN
  address at home and to the relay's address away. Chromium's own resolver reads the hosts file [unverified: lab cell
  C1]; Chromium also has `--host-resolver-rules` [secondary]. Same name, same certificate, same browser origin in both
  places, so cookies and the push-socket origin do not change when the panel moves.
- The daemon can then pin by trusting **only** the household CA (`requests` `verify=<household-ca.pem>` excludes
  public roots) [verified: `requests` semantics are standard; the daemon passes `verify=VERIFY_SSL` today,
  `scripts/setup/zoe_voice_daemon.py`]. Chromium keeps trusting public roots too, but no public root can issue for
  `.internal`.
- Public certificates are still needed for clients Zoe does not manage: a phone browser, a third party's webhook, an
  OAuth redirect. Those use `<hid>.<relay-domain>` with a public certificate obtained by DNS-01 (section 3.1) and get a
  deliberately narrow surface (design section 5).

### 3.4 Client certificates (mTLS) on the relay listener

- With mTLS required on the box's relay listener (`ssl_verify_client on` against the household device CA), a stranger
  who knows `<hid>` cannot get past the TLS handshake, so the internet never reaches zoe-data through the relay
  [unverified: standard nginx behaviour, lab cell C3].
- The daemon presents a client certificate with `requests` `cert=` [verified: standard API]. The kiosk Chromium needs
  the certificate in its NSS store and the `AutoSelectCertificateForUrls` policy to pick it without a prompt
  [secondary: Chromium enterprise policy list; not tested on the panel; lab cell C3].

## 4. Pairing and identity

What exists [verified: code at origin/main c0c3743ac]:

- `routers/panel_provision.py`: the panel requests a 6-character code, a signed-in member confirms on the phone, the
  panel collects a device token with a per-attempt poll secret; codes expire; uncollected tokens are revoked.
- `routers/panel_auth.py`: device tokens are hashed, cached, can carry `expires_at`, and are revoked by
  `DELETE /api/panels/{panel_id}/token/{token_id}`.
- `panel_kiosk_credential.py` + `routers/panel_kiosk.py` (#2082, flag `ZOE_PANEL_KIOSK_CREDENTIAL`, default off): the
  loopback kiosk gate on the panel trades the device token for a 60 s single-use ticket, the browser posts it to
  `/api/panels/kiosk-enter`, zoe-data sets an HttpOnly, Secure, SameSite=Strict cookie that re-checks the device token
  on every use. **It refuses any request carrying a Cloudflare header** (`via_public_tunnel`), because the panel must
  never use Cloudflare's plaintext path.
- `panel_principal.py`: a guest is "the panel" by device token (`panel_id`), by the kiosk credential, or by
  `X-Real-IP` equal to a registered `panels.ip_address`; `X-Real-IP` is believed only from a peer inside
  `ZOE_TRUSTED_PROXY_NETS` (default loopback + `172.16.0.0/12`); `lan` scope mode trusts any private address with a
  guest session.
- `auth._is_internal_request`: any loopback TCP peer on zoe-data is an internal caller and passes every
  `require_internal_token` endpoint; asserting another user's identity by header additionally needs the internal token.

What a relay hop does to these [verified by reading the code; behaviour unverified until lab cell I1]:

- A relay client on the box opens a local connection to nginx; nginx sets `X-Real-IP $remote_addr`
  (`services/zoe-ui/nginx.d/locations.inc`), which is then the relay client's own address: a Docker gateway or
  container address inside `172.16.0.0/12`, i.e. **private**. So over the relay every caller looks like a private-LAN
  client: harmless for the registered-address path (nobody registers that address), wrong for `lan` scope mode and
  for `livekit_available` (`routers/voice_tts.py`), and the reason the design marks relay traffic explicitly.
- If the relay client ever pointed at zoe-data's `127.0.0.1:8000` directly, every relayed caller would be a loopback
  peer and **pass `_is_internal_request`**, so it would reach every endpoint guarded by `require_internal_token`
  (loopback OR token; used across `routers/system.py`, `routers/memories.py`, `routers/telegram_media.py` and others)
  [verified: `auth.py`]. Header impersonation is narrower: `resolve_acting_user` honours `X-Zoe-User-Id` only with a
  valid `X-Internal-Token`, never for loopback alone, and the intent-dispatch route needs the token too while
  `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN` is on [verified: `auth.py` `_has_valid_internal_token`, `resolve_acting_user`,
  the intent-dispatch guard]. Still categorical: the local target is nginx only.
- The kiosk credential's "no Cloudflare" rule does not fire on the relay (no Cloudflare header), which is right: the
  relay path is end-to-end. It must not be reused as "the internet" for the credential, only for address trust.
- No nginx location in `services/zoe-ui` allows or denies by source address [verified: grep for `allow`/`deny`/`geo`],
  so nothing at the nginx layer becomes reachable merely because the relay hop is private.
- The voice daemon calls `requests.post` per request with no `Session` [verified: `zoe_voice_daemon.py` lines with
  `requests.post(url, ...)`], so every turn opens a new TCP and TLS connection. On the LAN that is free; over a relay it
  is one extra RTT to the relay plus one end-to-end RTT for TLS 1.3 per turn (section 6).

Pairing authenticity: today's pairing runs with `VERIFY_SSL=false` and `--ignore-certificate-errors` on the LAN, i.e.
trust on first use, and the 6-character code is generated by Zoe and handed to the panel over that same unauthenticated
channel (`/request` returns it), so it cannot authenticate anything against a device in the middle [verified:
`routers/panel_provision.py`]. The standard fix is an **out-of-band channel the attacker cannot see**: the panel shows
a QR that carries a high-entropy secret and its public-key fingerprint; the member's phone scans it and hands both to
Zoe over the member's own signed-in session; Zoe then authenticates everything it sends the panel with that secret
(device linking in Signal and WhatsApp works this way) [unverified: general protocol knowledge; the design specifies it
in section 6.1 and lab cell P2 attacks it from both ends]. Short-code comparison (Bluetooth numeric comparison) is only
safe inside an authenticated key agreement with commitments, and is the fallback, not the plan.

## 5. Wi-Fi onboarding away from home

Unchanged from #2080 (research section 1, design section 3.2/5): balena wifi-connect (Apache-2.0, NetworkManager over
D-Bus, launched on demand with a fresh WPA2 passphrase; Bookworm untested), Comitup as the runner-up, the loopback
kiosk gate from #2082 as the local status screen that owns the hotspot QR. The only change for panel-anywhere is the
"link home" light: it checks the relay (TLS to `<hid>.zoe.internal` through the relay: handshake completes = home
online; frp's `unrecognized_name` alert = relay up, home offline; no TCP = relay unreachable) instead of
`tailscale status`.

## 6. Voice over the WAN

### 6.1 Round-trip times through a Sydney relay

| Panel location | Panel to Sydney relay | Sydney relay to home box | Path RTT via relay | Source |
|---|---|---|---|---|
| Perth | 46 ms | ~53 ms | ~100 ms | Sydney-Perth: [secondary: wondernetwork.com/pings/Sydney, 2026-10-16]; relay-to-home: the home line's measured 53 ms to Tailscale's Sydney region [#2080 research 2.8, live] |
| London | 266 ms | ~53 ms | ~320 ms | [secondary: same page] |
| Frankfurt | 302 ms | ~53 ms | ~355 ms | [secondary] |
| Los Angeles | 160 ms | ~53 ms | ~213 ms | [secondary] |
| New York | 219 ms | ~53 ms | ~272 ms | [secondary] |

- **Where the relay must be regional: next to the HOME, not the panel.** The box holds the long-lived leg; a European
  relay would only move the long haul onto the box leg (Europe to an Australian home ~270 ms either way). A household
  in Europe needs a European relay. The box chooses its relay by latency at registration, as hass-nabucasa does
  [verified, section 1.2].
- Compared with a direct mesh path (Headscale/Tailscale when NAT allows), a relay through Sydney adds the triangle:
  ~0 for a panel whose shortest path to the home goes via Sydney anyway, up to one Perth-Sydney-Perth detour (~90 ms)
  for a panel near the home [unverified: arithmetic].

### 6.2 What a turn costs

Worked with #2080's arithmetic (design section 4.3 there): added time-to-first-audio is roughly connection setup + one
RTT + upload bytes / uplink + the first sentence's download.

- **Connection setup today: 2 RTT per turn** (TCP to the relay, then TLS 1.3 end to end), because the daemon makes a
  new connection for every call (section 4). At London that is ~0.6 s per turn; with a keep-alive session it is paid
  once per idle period [unverified: arithmetic; lab cell V1 measures it].
- Upload: a 4 s clip as base64 WAV is ~171 KB: ~0.27 s at 5 Mbit/s, ~0.68 s at 2 Mbit/s [verified: arithmetic from
  #2080].
- Perth, 5 Mbit/s, keep-alive: ~0.1 + 0.27 ≈ +0.4 s. London, 5 Mbit/s, keep-alive: ~0.32 + 0.27 ≈ +0.6 s; without
  keep-alive ~+1.2 s [unverified: arithmetic].

### 6.3 Head-of-line blocking

- A TCP multiplexer (snitun's single connection, frp's default TCP + stream multiplexing) delivers every stream
  in order over one TCP connection, so a lost packet on the box-to-relay leg stalls every stream until it is
  retransmitted [unverified: standard TCP behaviour]. The panel-to-relay leg is a separate TCP connection per panel
  connection and is not shared.
- For voice this is a tail-latency issue, not a correctness one: a turn is one request and one streamed response, and
  home broadband is usually the low-loss leg. It matters when a bulk transfer shares the box leg with a turn (an Omi
  sync upload, page assets). frp's `transport.protocol = "quic"` removes cross-stream blocking on the box leg
  [verified: the option exists; the benefit is secondary, QUIC's design]; snitun has no such option.
- Opus upload (#2080 P3) shrinks the bytes on the panel leg by ~10x and is the bigger lever for poor hotel uplinks.

## 7. Headscale's remaining role

- Headscale is a single-tailnet control server [verified: #2080 research 2.2]. It is not the multi-tenant product: one
  shared instance would put every household on one tailnet, and one per household multiplies control servers,
  public names and UDP listeners.
- It still fits the **operator's own access**: SSH to the operator's boxes and the relay host, an admin path that does
  not depend on the relay, and the owner's own panel as a stopgap. #2083's bundle is lab-proven (65/65 cells,
  v0.29.4 pinned) and only needs deploying [verified: PR #2083 description].

## 8. One relay for every inbound need

Findings that shape design section 9 [unverified: design reasoning]:

- The SNI relay already carries any TLS client to the box end to end. A second relay for interactive app traffic,
  with its own hand-built encryption layer, would duplicate it; TLS to the box already gives authenticated encryption.
- The session master the SNI relay needs (section 1.2) is a device registry with device-key authentication. Any other
  system that needs to reach a household's box should reuse that registry and that relay rather than grow a parallel
  one. Integrations outside Zoe are out of scope for this record.
