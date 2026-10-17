---
type: design
title: Panel anywhere - any household pairs a touch panel to its own Zoe and takes it anywhere, through one shared end-to-end encrypted relay
date: 2026-10-16
status: design; phase F0 (the relay in the lab) built and run 2026-10-17 in labs/relay (see its README for results and the findings folded in below); nothing changed on any live system and no Zoe code changed. Every phase is lab-proven in a hermetic lab first, and each Zoe-side code change ships flag-dark.
evidence: docs/research/panel-anywhere-2026-10.md (sources tagged verified/secondary/unverified; snitun 0.47.0, hass-nabucasa 2.7.0 and frp v0.71.0 read in source)
builds_on: docs/architecture/panel-remote-access.md (PR #2080 - owner-only link, Wi-Fi onboarding, away mode, panel firewall), services/zoe-data/panel_kiosk_credential.py + routers/panel_kiosk.py (#2082), routers/panel_provision.py + routers/panel_auth.py (pairing, device tokens), routers/ha_control.py + auth.get_current_user (the guest fallback this design must not inherit)
depends_on: PR #2083 (still open) - the Headscale bundle scripts/setup/remote-access/headscale/ (the trip stopgap and the lab-harness pattern) lands with #2083; every reference to it below is marked so. #2080 is merged: panel-remote-access.md and its research are on main.
---

# Panel anywhere

> Owner goal (2026-10-16): *"i wanted something where users could link a touch panel to their zoe and then take it
> somewhere else in the world."*
>
> Evidence is in [panel-anywhere-2026-10.md](../research/panel-anywhere-2026-10.md) ("research N" below). The
> owner-only link (Headscale), Wi-Fi onboarding, away mode and the panel firewall are designed in
> [panel-remote-access.md](panel-remote-access.md) (PR #2080, "#2080 N" below) and are reused here, not repeated.
> Placeholders: `<relay-domain>` is the public relay name, `<hid>` a household's random relay id, `<box>` the
> household's Zoe box.

## 0. Answer first

- **The pattern is Nabu Casa's, confirmed in source** (research 1). Each Zoe box holds an outbound connection to a
  shared relay. The panel opens TLS to the relay with the household's name in SNI. The relay forwards the raw bytes
  without decrypting, and TLS ends on the box with a key generated there. Tenant isolation is decided by a
  **session master** that tells the relay which name belongs to which box. The relay software itself does no
  user authentication.
- **Adopt frp v0.71.0 as the relay and multiplexer** (Apache-2.0, one static binary per side, arm64 and amd64).
  Its `https` proxy is pure SNI passthrough (verified in source). Its server plugins let our session master reject
  or rewrite every login and proxy, so **the box never chooses its own name** (research 2.1). That is snitun's
  token model on a more permissive licence, with a QUIC option for the box leg. **snitun 0.47.0 is the runner-up**
  and the reference design (GPL-3.0, tenant binding by construction, Python, one TCP connection). The rest were
  rejected for a reason that matters here:
  - Pangolin, piko and Cloudflare's HTTP mode end TLS at the relay.
  - NetBird, Headscale and zrok private shares need a VPN or overlay app on the panel.
  - rathole and boringproxy are unmaintained.
  - inlets is closed.
  - sish is the fallback, with weaker name ownership.
- **The panel does not use a public certificate.** It connects with SNI `<hid>.zoe.internal`. `.internal` is
  reserved for private use and public CAs do not issue for it, so the box answers with a leaf from the
  **household's own CA**, installed on the panel at pairing (research 3.3). Provided pairing delivered that CA root
  authentically (section 6.1) and the panel's trust store is not tampered with, the relay operator cannot mint a
  certificate the panel would accept, even if it turned malicious, and the name never lands in Certificate
  Transparency. The same name maps to the box's LAN address at home and to the relay away. One origin and one
  certificate cover both, so nothing in the browser changes when the panel moves.
- **Public certificates only where Zoe does not manage the client:** phone browsers, webhooks, OAuth redirects. They
  get `<hid>.<relay-domain>` through ACME DNS-01: the key is generated on the box, and the TXT record is set by a
  narrow session-master API that can write only the caller's own record (Nabu Casa's method, verified). That
  name has a deliberately narrow surface. It comes later (phase R5), behind its own owner decision.
- **Client authentication is mandatory from the first live phase (R1), before zoe-data.** The box's relay listener
  requires a client certificate from the household CA (`ssl_verify_client on`), so a stranger who learns `<hid>` never
  reaches zoe-data (lab F0: nginx completes the handshake and answers 400 itself). Behind it, **relay ingress never maps to `guest`**: a relayed request with no valid
  device credential is refused (401), never downgraded to the anonymous guest that `auth.get_current_user` hands out
  on the LAN today (verified: with no credential it returns `guest`, and `routers/ha_control.py::_require_caller`
  accepts that guest for `POST /api/ha/control`).
- **A device token never carries a member over the relay.** Today `provision_confirm` binds the confirming member as
  the panel's `default` user and `_resolve_device_token_user` resolves the token AS that member (verified). Over the
  relay the token resolves only to the panel principal (guest-class + `panel_id`); anything personal or member-scoped
  needs fresh member auth at the panel (a voice-verified session or PIN, the owner's "voice is a password" rule).
- **Identity never comes from an address.**
  - Relayed requests arrive at nginx from the relay client's private Docker address. So the box's nginx marks them
    `X-Zoe-Ingress: relay`, and zoe-data treats them as "no address": the panel proves itself with the #2082 device
    token or kiosk credential, always together with a client certificate (mTLS) on the relay listener.
  - The relay client's local target is the relay nginx listener, **never zoe-data's `127.0.0.1:8000`**. A loopback
    peer passes `_is_internal_request` and so reaches every `require_internal_token` endpoint (header impersonation
    still needs the internal token; research 4).
- **One relay for every inbound need** (section 9): panels, the Zoe phone app later, webhooks and OAuth redirects all
  reach the box through the same SNI relay and the same session master. Anything else that needs to reach a
  household's box reuses them rather than building a second relay with its own encryption.
- **Voice:** relays belong next to the HOME, not the panel. Through Sydney, Perth is ~100 ms round trip, London
  ~320 ms and New York ~270 ms (research 6.1). The biggest cheap fix is a keep-alive HTTP session in the voice
  daemon: today every turn pays a new TCP connection plus a TLS handshake, about 2 round trips. Head-of-line
  blocking across the multiplexer is tail latency, not breakage; frp's QUIC transport removes it on the box leg.
- **The trip: finish Headscale (#2083, still open; the bundle lands with it) for the owner's panel now and build frp in parallel** (section 11). The
  Headscale bundle is lab-proven and needs no Zoe code or change to the server's port 443. It is not throwaway,
  because it stays as the operator's admin path. The frp fast path (F0 + R1, about 3-4 days now that mTLS and the never-guest rule are in R1) is a true subset of this
  design and should follow right behind it.

## 1. Rules that shape the design

1. **Local, private, fast** (VISION 2). The relay forwards ciphertext. Its operator may learn that a household
   exists, when it connects and how much it sends, never a word of a turn.
2. **Users never run code; pairing is the only setup** (owner, 2026-10-12; owner brief). A household gets no
   account, no VPN app and no port forward. Enabling the relay is one toggle on the box, and moving the panel means
   plug in, scan, and type the Wi-Fi password on the phone.
3. **Adopt, don't rebuild** (owner, 2026-10-05). We adopt the relay (frp), the Wi-Fi portal (wifi-connect), the ACME
   client and the TLS stack. Zoe-owned code is limited to:
   - the session-master plugin, which is a device registry we would build anyway;
   - the box's relay listener and ingress marker;
   - the pairing additions.
4. **Isolation by construction, not by hope.** Every name is assigned by the session master, and every login and
   proxy is checked. A missing plugin fails the relay's own preflight, so it does not start open (research 2.1:
   frp with no plugin allows everything).
5. **Never trust loopback, Cloudflare or relay-hop addresses as identity** (#2080 3.3, #2082). The device proves
   itself.
6. **Never expose zoe-data directly.** The internet reaches only nginx's relay listener. That listener requires a
   client certificate from R1 (never open, never `guest`), and the public name exposes only webhook and OAuth paths.

## 2. The stack

```
 panel (any Wi-Fi)                         relay host (public, regional)                    household box (behind CGNAT)
 ---------------------                     -------------------------------                  ------------------------------
 kiosk Chromium ─┐                         :443  stream front (ssl_preread, per-IP limits)   frpc (systemd, outbound)
 voice daemon  ──┼─ TLS, SNI <hid>.zoe.internal ──►  frps https vhost (SNI only) ══ mux ══►   │  TLS still encrypted
 kiosk gate    ──┘   (household CA leaf,          ▲   (QUIC or TLS-over-TCP, box leg)          ▼
                      client cert, from R1)       │                                         nginx relay listener (loopback port,
                                                  └── session master plugin (Login,          PROXY-protocol in): TLS ends HERE,
                                                      NewProxy, NewUserConn) = the           X-Zoe-Ingress: relay, client cert
                                                      device registry API                    ──► zoe-data (same app, same rules)
```

- **Relay host.** It runs:
  - an nginx `stream` front on 443, routing by SNI: relay names go to frps, everything else to the existing sites;
  - frps, using only the `https` vhost, with `subDomainHost` unused and every name set by the plugin;
  - the session-master plugin, a small HTTP service next to the device registry.

  A second public address for the relay avoids putting the stream front in front of the server's existing sites
  (decision D2).
- **Box.**
  - frpc runs as its own unit (arm64 binary), configured by Zoe:
    - one `https` proxy whose local target is the nginx relay listener;
    - `transport.tls.trustedCaFile` set, because frpc skips verifying the relay without it (research 2.1);
    - `proxyProtocolVersion = "v2"`;
    - `transport.protocol = "quic"` once lab cell V2 passes.
  - The nginx relay listener is a new `server` block on a loopback-published port with the household-CA leaf.
- **Panel.** The kiosk, the daemon and the gate all use `https://<hid>.zoe.internal`. The panel agent keeps
  `/etc/hosts` pointing that name at the box's LAN address at home and at the relay away (the #2080 D4 home-network
  switch). The household CA root is the trust anchor for that name.

## 3. Names and certificates

| Name | Who uses it | Certificate | Visible in CT | Who can impersonate the box |
|---|---|---|---|---|
| `<hid>.zoe.internal` | panels (and later the Zoe phone app): managed clients that carry the household CA | leaf from the household CA, issued on the box, key never leaves the box | no | only a holder of the household CA key, which lives on the box (root offline from R3, decision D8) |
| `<hid>.<relay-domain>` (R5, opt-in) | unmanaged clients: phone browser, third-party webhooks, OAuth redirects | Let's Encrypt via DNS-01; key on the box; TXT written by the session master's narrow API | yes | the relay operator (it controls the zone), so it gets a narrow surface plus a CT monitor on the box |

- **`<hid>`** is random, assigned by the session master at enrolment, and never derived from a name or address. It is
  rotatable (rotation = new name + panels re-learn it through their device-token-authenticated config call).
- **The household CA** (box-local, created when the relay is enabled) issues:
  - the box's leaf for `<hid>.zoe.internal`, also served on the LAN listener so the home path is identical;
  - the panels' client certificates (signed by hand from the panel's CSR in R1, by the pairing extension from R3).

  This is the private CA #2080 4.1 already planned, so the design does not grow a second one. The leaf is
  short-lived and renewed on a timer on the box.
- **Pinning on the panel.** The daemon trusts only the household CA for Zoe (`verify=<household-ca.pem>`), so a
  certificate from any other CA fails even if the panel's system store trusts it (lab cell T4). Chromium keeps the
  public roots, but no public CA issues for `.internal`. That is a residual worth stating: it rests on the
  reservation and CA practice, not on a technical pin (research 3.3).
- **Let's Encrypt at scale (R5).** Put the public relay zone on the Public Suffix List before roughly 40 households
  use public names, as Nabu Casa did for its zone. Otherwise the 50-certificates-per-registered-domain-per-week limit
  binds (research 3.2).
- **TLS-ALPN-01** through the SNI passthrough is a researched alternative that needs no DNS API (research 3.1). Lab
  cell C2 decides whether R5 uses it.

## 4. The relay and the session master

### 4.1 Enrolment: a household with no account

1. The owner turns on "Away access" on the box. The box generates an Ed25519 device key and a household CA.
2. The box calls the session master's `POST /relay/enrol` with its public key and an **enrolment code** issued by
   the operator (decision D3: operator-issued codes first, self-service later). The session master stores a `devices`
   row (type `zoe_unit`, no user), assigns `<hid>` and a relay region (by the box's latency pings, as hass-nabucasa
   does), and returns `<hid>`, the relay address and a **relay secret** (random, stored hashed).
3. Every later call to the session master is signed with the device key (short-lived device JWT, which the owner's
   plan already specifies). Through those calls the box rotates the relay secret and, in R5, sets ACME TXT records.

No human account is created.

### 4.2 Per-tenant authorization in frp

| frp op | Plugin rule | Failure |
|---|---|---|
| `Login` | `metas.hid` + `metas.secret` must match an active enrolled box; the box's prior session for that `hid` is kicked | reject |
| `NewProxy` | only `ProxyType == "https"`; the plugin **rewrites** `CustomDomains = ["<hid>.zoe.internal"]` (plus `<hid>.<relay-domain>` from R5) and clears `SubDomain`; it overwrites `BandwidthLimit` and `BandwidthLimitMode = "server"`; any other type (tcp, udp, http, stcp, xtcp) is refused | reject |
| `NewUserConn` | per-`hid` new-connection rate cap (a true concurrent cap is not possible in the plugin API: it has no close hook; lab F0 finding 1); a suspended household is refused | reject |
| (no plugin configured) | the relay's preflight refuses to start frps unless its config names the plugin for all three ops | frps does not start |

- frp calls plugins fail-closed per request (a transport error rejects) [verified]. Fail-open per configuration is
  covered by the preflight and by lab cell T2.
- Abuse limits:
  - per IP at the stream front (`limit_conn`);
  - per household in the plugin (bandwidth, connections);
  - per host by frps `userConnTimeout`;
  - suspension = the plugin refuses the next `Login`/`NewUserConn`, and the session master revokes the relay
    secret.
- What the relay host knows: `<hid>`, the box's and panels' public addresses, connection times and byte counts.
  What it never holds: a certificate key for any household name, a device token, or content. The plugin stores
  hashed relay secrets only.

### 4.3 Regions

The relay sits next to the HOME (research 6.1). The first region is Sydney. Another region is added when a household
outside Australia enrols, and the box picks a region at enrolment by measured latency and keeps it. A box moves region
only by re-enrolling, so no second relay ever claims its `<hid>`.

## 5. What the box must change

1. **A relay listener in the zoe-ui nginx.** It is a separate `server` block on a port published to loopback only,
   taking PROXY protocol from frpc. It:
   - serves the household-CA leaf for `<hid>.zoe.internal`;
   - requires a client certificate from the household CA (`ssl_verify_client on`) **from R1; there is no optional
     phase**, so nothing without a panel's certificate reaches zoe-data through the relay;
   - sets `X-Zoe-Ingress: relay` and `X-Zoe-Client-Cert-Fp $ssl_client_fingerprint` absolutely.

   The LAN server blocks clear both headers (`proxy_set_header X-Zoe-Ingress ""`), so a client cannot claim either
   ingress. The LAN listener also serves the same `<hid>.zoe.internal` leaf, so the home path verifies identically.
2. **zoe-data: relay ingress is "no address"** (flag `ZOE_RELAY_INGRESS`, default off).
   - A request whose TCP peer is a trusted proxy and that carries `X-Zoe-Ingress: relay`:
     - is never matched against `panels.ip_address`;
     - never satisfies `ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE=lan`;
     - reports `livekit_available` false.
   - **Never `guest`.** A relayed request that carries no valid device token or kiosk credential gets 401. The
     anonymous-guest fallback in `auth.get_current_user` (and the `ZOE_UNAUTHENTICATED_ROLE` override) never applies
     to relay ingress. A degraded auth (zoe-auth outage) is also refused over the relay rather than downgraded.
   - **The device credential resolves to the panel principal only:** guest-class with `panel_id`, whatever
     `panel_user_bindings` says. A `default` member binding is ignored on relay ingress, so the token cannot act as
     that member.
   - **Member-scoped or personal routes need fresh member auth at the panel:** a voice-verified short session or a
     PIN session. Home control over the relay (`/api/ha/control`, intents that act on the house) needs that verified
     member too, which is #2080's away-mode D6 enforced at the server.
   - The kiosk credential is allowed here. Unlike Cloudflare, the relay path is end-to-end, so its docstring rule
     changes from "never over a public tunnel" to "never over Cloudflare".
   - Every relayed panel request must carry a client-certificate fingerprint bound to the same `panel_id` as its token
     or credential.
3. **Origins.** `https://<hid>.zoe.internal` joins the allowed browser and push-socket origins, derived from
   `ZOE_RELAY_HOSTNAME` rather than hand-edited. Without it the push socket refuses the kiosk (the 2026-07-03
   incident class; lab cell T5 proves it is load-bearing).
4. **frpc's target is pinned.** A unit test reads the generated frpc config and fails unless the only local target is
   the relay listener. The negative fixture is a config pointing at `127.0.0.1:8000`.
5. **The public name (R5) is narrow.** Its server block serves only `/hooks/<id>` (each verifying its sender's
   signature) and `/oauth/callback`. Everything else returns 404 at nginx. The box runs a Certificate Transparency
   watch for its public name and alerts the owner on any certificate it did not request.
6. **Bind drift first.** As #2080 4.6 notes, the HA bridge and Postgres containers must be recreated from the current
   compose before anything new listens.

## 6. The panel: pairing, identity, revocation, Wi-Fi

### 6.1 Pairing (at home, extended)

**Before the pairing extension ships (R1, the owner's own panel):** the operator issues the panel's client
certificate by hand. The panel generates its key and CSR locally, the operator signs the CSR with the household CA on
the box, and only the certificate goes back to the panel. The relay listener's mTLS is therefore in force from R1;
the extension below automates the same step for every household.

Today's flow (`/api/panels/provision/*`) is trust on first use: Zoe generates the 6-character code and hands it to the
panel over the same unauthenticated LAN channel (`/request` returns it), with certificate checks off, so the code
proves nothing against a device in the middle (research 4). The extension authenticates the pairing through a channel
that attacker cannot see, the member's phone camera, and needs no typing and no tap on the panel:

1. **The panel generates, locally:** an ECDSA P-256 key pair (its client-certificate key), a 128-bit random
   pairing secret `s`, and a pairing id. Neither the secret nor anything derived from it, other than the MAC check in
   step 5, ever crosses the panel's network channel.
2. **The panel sends** `/request` with the pairing id and its public key only, and shows a QR encoding
   `https://<zoe>/pair#id=<pairing id>&k=<s>&fp=<sha256 of the panel public key>`. The secret travels in the URL
   fragment, which the phone's browser does not send to any server.
3. **The member's phone scans it.** The pairing page (served by Zoe to the member's signed-in session) reads the
   fragment and posts `id`, `s` and `fp` to `/confirm`, under the existing rule that confirmation needs a real member
   session.
4. **Zoe checks** that the public key received in step 2 hashes to `fp`. A mismatch means the panel's channel was
   tampered with: the attempt is burned and the phone says so. Zoe then signs the panel's client certificate and
   assembles the bundle: device token, household CA root, client certificate, `<hid>.zoe.internal`, relay address name.
5. **Zoe authenticates the bundle to the panel.** The poll response carries the bundle plus
   `HMAC-SHA256(key = HKDF(s, "zoe.pair.v1"), "zoe->panel" || pairing id || panel public key || bundle)`. The panel
   recomputes it with a constant-time compare and accepts nothing that fails. The role label and the panel key in the
   MAC bind it to this session and direction; a bundle from another run or for another key does not verify.
6. **Limits:**
   - one pairing id = one attempt;
   - the secret is single-use and dies with the code's existing TTL (`ZOE_PROVISION_CODE_TTL_S`, 300 s);
   - existing per-device request rate limits apply, and three failed confirmations lock the pairing id;
   - an uncollected bundle is revoked as today.

From then on the panel verifies Zoe with the pinned household CA only, never with `VERIFY_SSL=false`.

What this rests on: the phone's own channel to Zoe is authentic. At home today that is the member's LAN session; with R5
it is a publicly trusted name. A panel with no camera-capable phone nearby falls back to numeric comparison, which is
only acceptable inside an authenticated key agreement with commitments (research 4); it is not designed here.

Pairing away from home (a panel posted to a relative) needs the member's phone to reach Zoe remotely. It waits for the
phone path (R5 public name or the Zoe app); it is not in the first phases.

### 6.2 Identity on every request

| Caller | Proof over the relay | Grants |
|---|---|---|
| voice daemon | client certificate + `X-Device-Token` | the panel principal only (guest-class + `panel_id`), even if the panel has a `default` member binding |
| kiosk browser | client certificate + #2082 kiosk credential (cookie minted from the device token by the loopback gate) | the panel principal only |
| a member at the panel | client certificate + device credential + a fresh voice-verified session or PIN | that member, for that short session, under the existing rules |
| a client certificate with no device credential, or a credential with no matching certificate | refused (401) | nothing; never `guest` |
| anyone else who knows `<hid>` | no client certificate | nothing: nginx answers 400 and never reaches zoe-data (from R1; lab F0 S3) |

### 6.3 Revocation and a stolen panel

- **Stolen panel runbook** (owner, from the phone or the admin page):
  1. revoke the device token (existing endpoint): the daemon gets 401 at once, and the kiosk credential dies on its
     next request (#2082 re-checks the token);
  2. revoke the client certificate (zoe-data refuses its fingerprint at once, and the short-lived certificate is not
     renewed);
  3. set `panels.is_active = 0`.
- The panel's disk holds a device token, a client key, the household CA root and `<hid>`. Over the relay those
  give at most the panel principal (guest-class), never a member, because the relay ingress ignores the token's
  member binding (section 5). On the home LAN the same now holds: until the 2026-10-17 fix a device token with a
  `default` binding WAS a member credential (ledger line 2026-10-16); it now resolves to the panel principal on every
  route (`panel_auth._resolve_device_token_user`; `ZOE_DEVICE_TOKEN_BINDS_MEMBER=1` is a rollback only). Once revoked,
  none of them opens anything. The CA private key never lives on the panel, and knowing `<hid>` gets no further than
  the TLS handshake. If the owner wants the name gone too, rotating `<hid>` is one call.
- **Household-level revocation** is the session master's job: it suspends the box, and the next `Login` fails. The
  relay knows nothing about panels by design, so it cannot revoke one, and it does not need to.
- Device tokens get an `expires_at` and client certificates a short life; both are renewed by the panel through a
  device-token-authenticated call.

### 6.4 Wi-Fi away from home, and the status screen

The design reuses #2080's choice: balena wifi-connect (runner-up Comitup), started by the panel agent with a fresh
passphrase, the QR on the #2082 loopback kiosk gate, and the password typed on the phone. The gate's "link home" light
reads the relay (research 5):

- the handshake completes: green;
- frp's `unrecognized_name` alert: "Zoe's computer at home is offline or its internet is down";
- no TCP connection: "I can't reach the relay; this network may block it" (try the phone hotspot).

If the home/away guess is wrong, the gate tries the other mapping before showing red: a panel that thinks it is away
while at home still works through the relay (slower), and one that thinks it is home while away falls back to the relay.

Captive portals, WPA2-Enterprise and away mode are unchanged from #2080 5.4 and 4.5.

## 7. Voice over the WAN

- **Budget through a Sydney relay** (research 6), for a home ~53 ms from Sydney:

  | Panel in | Round trip | Added time-to-first-audio, 4 s clip at 5 Mbit/s, keep-alive |
  |---|---|---|
  | Perth | ~100 ms | ~+0.4 s |
  | Los Angeles | ~213 ms | ~+0.5 s |
  | London | ~320 ms | ~+0.6 s |

  Without keep-alive, add about 2 round trips per turn. These are arithmetic, not measurements; cell V1 replaces them.
- **Keep-alive first (R4).** Today `zoe_voice_daemon.py` calls `requests.post` per request, so every turn pays TCP to
  the relay plus a TLS 1.3 handshake. A module `requests.Session` reuses the connection. It is a voice-path change, so
  it is replay-gated (`voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock`), flag-dark and measured.
  It also helps the LAN path a little.
- **Head-of-line blocking.** It only matters when a bulk transfer shares the box leg with a turn. Lab cell V2 measures
  turn p95 under 1% loss with a concurrent upload, TCP against QUIC transport, and R4 switches to QUIC if it wins.
- **Opus upload** (#2080 P3) remains the lever for poor hotel uplinks, decided there.
- The interim voice budget #2080 D7 accepted (<= +0.5 s on >= 5 Mbit/s, <= +1 s on 2 Mbit/s or relayed) applies to
  the relay path too.

## 8. Who can read what

| Party | Sees | Can do | Cannot |
|---|---|---|---|
| Passive relay operator | `<hid>`, public addresses, timing, byte counts | nothing to content | read audio, transcripts or pages (TLS ends on the box) |
| Malicious relay operator | the same | deny service; route `<hid>` to itself; mint a public certificate for `<hid>.<relay-domain>` (R5 only) | (given an authentic pairing and an untampered panel trust store) present a certificate the panel accepts for `<hid>.zoe.internal`; complete mTLS as the panel; reach anything on the public name but webhook and OAuth paths (and the box's CT watch reports a foreign certificate) |
| Another household | its own `<hid>` | nothing to yours | claim your name (the plugin assigns names; `ErrRouterConfigConflict` on a live name) |
| Someone who knows your `<hid>` | that it exists | open a TCP connection to the relay | reach zoe-data at all (mTLS from R1: nginx answers 400; lab F0 S3) |
| Thief with the panel | until revoked, the panel principal over the relay (guest-class) | what a guest at the panel may do: household lists, guest chat, no home control | act as any member (the relay ignores the token's member binding; member routes need fresh voice or PIN); home control; anything after revocation |

## 9. One relay for every inbound need

| Need | Carried by |
|---|---|
| Panel to its Zoe (and later the Zoe phone app) | the SNI relay, `<hid>.zoe.internal`, TLS ending on the box |
| Any other app that talks to a household's Zoe interactively | the same SNI relay, with TLS to the box as the end-to-end layer: as a managed client trusting the household CA, or through the R5 public name. No second relay and no hand-built encryption layer |
| Webhooks and OAuth redirects for a household's Zoe | the R5 public name, terminating on the box, narrow surface (section 5, item 5) |
| Device identity, enrolment, revocation, relay secrets, ACME TXT | one device registry = the session master and the frp plugin |

Integrations outside Zoe that want to reach a household's box reuse this registry and relay; designing them is out of
scope for this record.

## 10. Phases and lab cells

Every cell has a negative control that must fail, and a cell that only proves rejection also has a positive control
that must succeed. "Baseline" marks an extra comparison; it is never the only control in a cell. The
lab is hermetic and follows #2083's harness (lands with #2083): Docker on an internal network, with a `MemAvailable` gate before starting
and teardown asserted.

### F0 / R0 - the relay in the lab (two fake households)

The lab runs:

- a stream front + frps v0.71.0 + a plugin stub;
- two "boxes", each frpc + nginx with its own household CA;
- a "panel" container (curl, Python `requests`, headless Chromium).

| Cell | Test | Negative control |
|---|---|---|
| T1 | box A's login + proxy get exactly `A.zoe.internal`; the panel reaches A's nginx through the relay | box A asking for `B.zoe.internal` (or `*.zoe.internal`, or a `tcp` proxy) is rejected; a client-set `customDomains` is overwritten |
| T2 | the relay preflight passes with the plugin configured | frps config without the plugin: preflight refuses to start it |
| T3 | a capture on the relay host shows only a ClientHello then ciphertext; no household key exists on the relay's filesystem | a deliberately TLS-terminating lab config makes the same capture readable (the probe can see plaintext when it exists) |
| T4 | the daemon (`verify=<household-ca>`) connects to `A.zoe.internal` | a leaf for the same name from another CA that IS in the panel's system store is refused |
| T5 | over the relay: `X-Zoe-Ingress: relay` reaches zoe-data; `is_panel_guest` is true with the kiosk credential and false for a plain guest; the push socket connects from the `.internal` origin | a client-sent `X-Zoe-Ingress: lan` is overwritten; without the origin entry the push socket is refused; a relayed guest never matches a `panels.ip_address` |
| T6 | the frpc config test passes for the generated config | the fixture pointing at `127.0.0.1:8000` fails the test |
| T7 | box offline: the gate shows "home offline" (`unrecognized_name`) | relay offline: it shows "can't reach the relay"; both up: green |
| T9 | frpc with `trustedCaFile` refuses a relay presenting a wrong certificate | without `trustedCaFile` it connects (the setting is load-bearing) |
| T10 | with a client certificate and the device token, a relayed request is the panel principal (positive control) | no client certificate: nginx answers 400 and nothing reaches zoe-data (the handshake itself completes; lab F0 S3); a certificate but no device credential: 401, and `POST /api/ha/control` is refused, never run as `guest` (the same credential-less request on the LAN listener is accepted today, which proves the probe reaches the route) |
| T11 | a device token whose panel has a `default` member binding resolves, over the relay, to the panel principal only; a fresh voice-verified or PIN session then reaches that member's routes | the same token on the LAN listener resolves as the member today (proves the binding is real in the fixture); over the relay a member route without the fresh session gets 401/403 |
| A1 | the plugin-imposed bandwidth cap holds when frpc asks for more | with the plugin's overwrite disabled, the client's own limit applies (the overwrite is what enforces it) |

### R1 - the owner's household live (the fast path, section 11)

Prerequisite cell, moved here from R3 because mTLS is mandatory from R1:

| Cell | Test | Negative control |
|---|---|---|
| C3 | kiosk Chromium presents its client certificate with no prompt (`AutoSelectCertificateForUrls`) | with the policy removed, Chromium shows the certificate picker or nginx answers 400, so `ssl_verify_client on` is load-bearing |

If C3 fails, R1 waits for the alternative: a loopback forwarder on the panel (the kiosk gate's process) that presents
the certificate for the kiosk's connections. R1 never ships with the relay listener open.

Steps:

1. recreate the drifted containers;
2. add the stream front or a second address on the relay host;
3. deploy frps + a single-household plugin (one relay secret);
4. create the household CA, the nginx relay listener (`ssl_verify_client on`) and frpc on the box;
5. on the panel: hosts switch, CA root, URLs, `VERIFY_SSL=true`, and a client certificate signed by hand from the
   panel's own CSR (section 6.1);
6. set the kiosk credential flag on, `ZOE_RELAY_INGRESS` on, and `ZOE_RELAY_HOSTNAME`.

Cells: T1-T7, T9, T10, T11 and C3 run against the live relay host on a lab "panel" before the real one. R1 does
not go live without T10, T11 and C3 passing. Then L6 from #2080, the
time-to-first-audio comparison against the LAN baseline, plus the replay gate.

### R2 - many households

- The session master moves into the device registry (enrolment codes, signed device calls, relay secrets, regions).
- Load cell L1: simulate N households (target 500) on the relay host size and record memory per frpc session, CPU,
  and the p95 of a turn-sized request; every simulated household must complete a round trip through its own name.
  Negative control: ten of the simulated households are given a wrong relay secret, and the harness must report
  exactly those ten as failed (it counts real sessions, not attempts). Baseline: frps memory with zero clients.
- Isolation cell: T1 against a registry of 500 households.

### R3 - pairing extension (automated client certificates for every household)

| Cell | Test | Negative control |
|---|---|---|
| P1 | pairing returns CA root + client certificate; relayed requests carry a fingerprint bound to the panel | a valid client certificate from household A presented with household B's device token is refused |
| P2 | through a transparent lab proxy that forwards every pairing message unchanged, pairing completes (positive control: the proxy itself breaks nothing) | the same proxy made active at BOTH ends - it replaces the panel's public key in `/request`, the CA root, the client certificate and the device token in the bundle, and replays a bundle from an earlier run - and every variant fails: Zoe refuses the key whose hash is not the QR's `fp`, the panel refuses every bundle whose MAC does not verify, and nothing is stored on either side |
| T8 | revoke the token: daemon 401, kiosk credential dead next request; revoke the certificate: handshake or fingerprint refused | before revocation the same calls succeed |

### R4 - voice

| Cell | Test | Negative control |
|---|---|---|
| V1 | `tc netem` RTT 100/320 ms on the lab panel, keep-alive vs not, 20 turns each: first-audio, total, and new TLS handshakes per turn (packet capture) | with keep-alive disabled, the capture must show a handshake on every turn (proves the cell sees reuse); netem adding 500 ms each way must make the budget check report a breach. Baseline: 0 ms netem reproduces LAN numbers. Plus the replay gate: no said-vs-did or per-stage regression |
| V2 | 1% loss on the box leg with a concurrent bulk upload: turn p95, TCP vs QUIC transport | the same 1% loss moved to the panel leg must show no TCP-vs-QUIC difference (proves the cell measures the box-leg mechanism). Baseline: 0% loss, the same upload |

### R5 - the public name (opt-in)

| Cell | Test | Negative control |
|---|---|---|
| C2 | Let's Encrypt staging issues `A.<relay-domain>` by DNS-01 through the narrow API (and, separately, by TLS-ALPN-01 through passthrough) | box A asking the API to write B's TXT record is refused |
| S1 | `/hooks/<id>` and `/oauth/callback` reach zoe-data | every other path on the public name gets 404 at nginx |
| CT1 | the box's CT watch alerts on a staging certificate it did not request | a certificate it did request raises no alert |

### R6 - more regions

A second relay region; the box chooses by latency at enrolment. Cell G1: a box enrolled in region 1 logs in and
serves its `<hid>` on region 1. Negative control: the same box cannot register its `<hid>` on region 2 (the plugin
consults the registry's region). Sensitivity check: with the plugin's region check disabled in the lab, the region-2
registration is accepted, which proves the check is what refuses it.

## 11. The owner's trip: fast path vs Headscale

| | Headscale stopgap (#2083, lands with #2083) | frp fast path (F0 then R1) |
|---|---|---|
| Ready | bundle lab-proven 65/65, pinned v0.29.4, clients already installed on both boxes | to build: the lab (F0), the nginx relay listener (a zoe-ui config PR), the `ZOE_RELAY_INGRESS` flag (a small zoe-data PR), frpc unit, the household CA |
| Touches the relay host's 443 | no (one grey-cloud DNS name, UDP 3478) | yes (stream front), or a second public address |
| Zoe code | none; the panel's tailnet address is registered (#2080 P1 interim) | two flag-dark PRs; the kiosk credential (#2082) turned on |
| Part of the product | no; it stays as the operator's admin path (SSH, an access path independent of the relay) | yes: every piece is R1 of this design; only the single-household plugin secret is replaced in R2 |
| Days | about 1 (operator steps) | about 3-4 including the lab, both PRs, the hand-signed client certificate and cells T10, T11, C3 |
| Shared work that carries over either way | recreate the drifted containers; apply the #2082 panel firewall (and narrow it on `tailscale0`) and the kiosk gate; the household/private CA; the panel's `/etc/hosts` home/away switch | the same |

**Recommendation:**

1. **Now:** the shared work, then Headscale for the trip.
2. **Same week:** F0 in the lab.
3. **After the trip, or during it once F0 passes and R1's cells pass on a lab panel:** move the owner's panel to the
   relay.

Headscale is never deleted. It is the operator's own path to every box. If the owner would rather build one path
only, F0 + R1 fits before the trip only if it starts at once and nothing slips. The honest risk is the relay host's
stream front, which changes how that server's existing sites are reached.

## 12. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | Relay software | **frp v0.71.0** (Apache-2.0, plugins rewrite names, QUIC option); runner-up snitun 0.47.0 (GPL-3.0, binding by construction, single TCP); fallback sish |
| D2 | Relay host | the existing server with a **second public address** for the relay (no stream front in front of the existing sites); a separate small VPS once more than a handful of households use it, so a relay compromise is not a platform compromise |
| D3 | How a household enrols | **operator-issued enrolment codes** first; open self-service later with rate limits |
| D4 | The relay domain | a **dedicated domain** separate from the platform's, with its public zone on the Public Suffix List before public names scale (R5) |
| D5 | Public name per household (webhooks, OAuth, phone browser) | **off by default**, opt-in per household in R5, narrow surface, CT watch |
| D6 | One relay for every inbound need | **yes**: one device registry = session master; every interactive path to a household's box goes through the SNI relay with TLS to the box; no second relay with its own encryption (section 9) |
| D7 | The trip | **Headscale now** (shared work first), frp F0 the same week, the panel moves to the relay after R1's cells pass |
| D8 | Household CA custody | box-local intermediate for issuing; root generated on the box and exported once to the owner's offline backup, then removed (R3) |
| D9 | mTLS on the relay listener | **required from R1**, no optional phase; relay ingress never maps to `guest`; if C3 fails, R1 waits for the loopback forwarder |
| D11 | A device token's member binding | **ignored over the relay** (panel principal only; members re-authenticate by voice or PIN). Whether to drop it on the LAN too is the ledger line of 2026-10-16 |
| D10 | Per-household limits | start at 20 Mbit/s and 64 new connections per 10 s per household (a concurrent cap needs a poller on frps' `curConns`, R2); revisit from R2's load cell |

## 13. Risks and what this does not decide

- **A malicious relay operator can still deny service** and, for the R5 public name, mint a certificate. The panel's
  `.internal` path does not depend on trusting it. The public name is narrow and watched.
- **`.internal` is a reservation, not a technical pin in Chromium.** A public CA that wrongly issued for it would break
  the property for the kiosk browser, though not for the daemon. The draft is not yet an RFC (research 3.3).
- **The relay host becomes infrastructure for other people's homes.** Uptime, upgrades (pinned frp, 14-day rule),
  backups of the registry and abuse handling are operator work. A relay outage means panels away from home go to
  "can't reach the relay"; at home the LAN path keeps working.
- **frp is configuration-sensitive:** no plugin means open. The preflight and cell T2 exist for that; keep both.
- **Licence.** frp is Apache-2.0. If D1 flips to snitun, its client runs as a separate process to keep zoe-data MIT
  (research 1.4, not legal advice).
- **Not covered:** pairing away from home and the phone as a remote client, both waiting on R5 or the Zoe app; IPv6
  on the relay; more than one Zoe box per household.
