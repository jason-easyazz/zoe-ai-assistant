---
type: design
title: Panel remote access - the touch panel on another Wi-Fi, linked back to Zoe over a private mesh with no new account
date: 2026-10-16
status: design only; nothing built, nothing changed on the panel or the Jetson. Every phase is lab-proven on a lab node before the live panel, and each Zoe-side code change ships flag-dark.
evidence: docs/research/panel-remote-access-2026-10.md (field research with sources tagged verified/secondary, plus the code survey and the read-only live state)
builds_on: docs/knowledge/mac-virtual-panel.md (the one remote panel that exists - over the Cloudflare tunnel), services/zoe-data/panel_principal.py (#2068), services/zoe-data/routers/panel_auth.py + services/zoe-data/routers/panel_provision.py (device tokens, QR pairing), docs/architecture/identity-presence-engine.md (panel = room), docs/architecture/samantha-evolution-plan.md W12 (the direct link)
---

# Panel remote access

> Owner ask (2026-10-16): *"Is there a way we can connect the touch panel back to Zoe over the internet? We'd
> need the ability to connect the panel to a new Wi-Fi and something like Tailscale to connect over a different
> network."*
>
> Sources, tags and the code survey are in [panel-remote-access-2026-10.md](../research/panel-remote-access-2026-10.md)
> (cited below as "research N"). Addresses, network names and host names below are placeholders:
> `<zoe-host>.<tailnet-domain>` for Zoe's MagicDNS name, `<home-LAN-IP>` for today's address, `<panel-id>` for the
> registered panel. Owner follow-up (same day): *prefer a link home that needs no new account*; the owner's existing
> cloud server (root, host nginx) can be the public box a self-hosted control server needs.

## 0. Answer first

- **Recommended, with no new account: Headscale on the owner's existing cloud server + balena wifi-connect for
  Wi-Fi onboarding.** Headscale is the open-source control server for the same Tailscale clients already on both
  boxes, so the data plane is identical: audio, transcripts and pages travel end-to-end WireGuard-encrypted between
  the panel and the Jetson, and the control server only learns device metadata (research 2.1, 2.2). The owner
  already runs a server with root and host nginx, so the "needs a public box" cost that would make Headscale a
  runner-up disappears. It needs one DNS name that is NOT behind Cloudflare's proxy (Headscale's own docs say its
  control protocol does not work through Cloudflare's proxy or tunnel), TCP 443 and UDP 3478 (research 2.2).
- **Reference: Tailscale's hosted service** is still the least work and adds Tailnet Lock and `tailscale cert`,
  but it is a new account. If the owner later accepts one, the same clients re-login to it unchanged.
- **Second no-new-account route: Cloudflare Tunnel in TCP pass-through mode** on the existing tunnel. The panel's
  TLS to nginx rides inside the stream and cloudflared only copies bytes, so Cloudflare sees ciphertext, not
  content (research 2.7, verified in source). It is quick to stand up and needs no UDP, but every byte detours
  through Cloudflare's edge, connections drop on edge restarts, and Zoe can no longer tell the panel by address at
  all (section 3.3). A fallback path, not the primary.
- **Not the Cloudflare HTTP proxy** (what the Mac virtual panel uses): Cloudflare terminates TLS at its edge, so
  every voice clip, reply and transcript is plaintext there (research 2.4). **Not WireGuard through a home port
  forward:** the home line shows the signs of carrier-grade NAT (the second hop is in `100.64.0.0/10` and the
  router's port-mapping reply carries no valid public address) [live, read-only]. Signs, not proof: if the router's
  own WAN address confirms CGNAT, nothing at the house can accept inbound connections and Headscale behind a home
  forward is out too. The design does not depend on the answer: the pick needs no inbound connection to the house.
- **Tailscale 1.104.1 is already installed on both boxes** (active, enabled, never logged in: `NeedsLogin`)
  [live, 2026-10-16], so phase 1 is a control server, a policy file, two logins, one TLS name and a config switch.
- **Zoe must change in four places:** the panel's URL and TLS name; how the kiosk BROWSER proves it is the panel
  (today only its source IP does, which a tunnel changes); a real "can't reach Zoe" screen instead of a boot loop;
  and an away mode that turns off the features that assume the panel is in the house (AirPlay speaker, room
  presence, "turn off the lights" meaning this house).
- **One security fix is needed before the panel ever joins a foreign network**, independent of everything else:
  the panel listens on every interface with no firewall (SSH with password login on, the voice daemon's
  `/activate` with no checks, the wake agent, AirPlay) [live]. On a friend's or hotel Wi-Fi, any device on that
  network could start a recording that the daemon then sends to Zoe with the panel's device token. Phase 1 closes
  this with a host firewall (loopback, SSH from owner devices over the tailnet, AirPlay from the home subnet at
  home) and loopback-only helpers; #2082 shipped a first version that is still too wide on the tailnet (section 4.6).

## 1. Rules that shape the design

1. **Local, private, fast** (VISION 2). Content stays end-to-end encrypted between our two boxes. A coordination
   service may learn that two devices exist and where they connect from; it must never be able to read a turn.
2. **Panel: voice first, touch second, no keyboard** (VISION 8). Anything typed (a Wi-Fi password) is typed on a
   phone. The panel speaks what is happening and shows a QR.
3. **Connecting = QR on the panel, finish on the phone** (VISION 8). The Wi-Fi step uses the same shape.
4. **Users never run code** (owner, 2026-10-12). Nobody in the household touches a terminal, an admin console or
   a config file to move the panel. The one-time tailnet setup is an operator step; after that, moving the panel
   is: plug in, scan, type the Wi-Fi password on the phone.
5. **Adopt, don't rebuild** (owner, 2026-10-05). The mesh, the Wi-Fi portal and the certificates are adopted.
   Zoe's own code changes are limited to identity, configuration, the status screen and away mode. The legacy
   `wifi-setup` mode in `scripts/setup/touchscreen/provision-server.py` is retired, not extended.
6. **Never expose zoe-data publicly.** No Tailscale Funnel, no port forward, no `tailscale serve` in front of
   zoe-data (section 4.6 explains why `serve` is specifically dangerous here).

## 2. What is true today

Read-only on 2026-10-16 (`ssh zoe-pi`, the Jetson shell), plus the code at `origin/main` d8b957d3e.

| Area | Today | Source |
|---|---|---|
| Mesh VPN | Tailscale 1.104.1 on Jetson and panel from the vendor apt repo, `tailscaled` active + enabled, `NeedsLogin`, 0 peers. The Jetson kernel has `CONFIG_TUN=y` and `/dev/net/tun`; there is no in-kernel WireGuard (`CONFIG_WIREGUARD` not set), so Tailscale runs its userspace WireGuard on both | live |
| How the panel finds Zoe | kiosk `/opt/TouchKio/config.json` `url` = `https://<home-LAN-IP>/touch/home.html?...`; voice daemon `ZOE_URL=https://<home-LAN-IP>` | live |
| TLS | nginx serves a self-signed `zoe.crt` with `server_name _`; the daemon runs `VERIFY_SSL=false`, the kiosk runs `--ignore-certificate-errors` | `services/zoe-ui/nginx.conf`, `start-kiosk.sh:62`, live env |
| Unreachable Zoe | `start-kiosk.sh` pings the host 60 times, then exits 1 so systemd restarts it: a blank screen and a restart loop, no message | `scripts/setup/touchscreen/start-kiosk.sh` |
| Panel listeners | `0.0.0.0`: 22 (SSH, `PasswordAuthentication yes`), 7000 (AirPlay), 7777 (daemon `/health` + `/activate`, no checks on the `pi` platform), 8765 (screen-wake agent); UDP 5353, 319/320 (nqptp), 41641 (tailscaled). No nftables ruleset, no ufw. DevTools 9222 is loopback (Chromium ignores the `--remote-debugging-address=0.0.0.0` the live launcher still passes) | live; `docs/knowledge/mac-virtual-panel.md` section 2 |
| Jetson listeners | `0.0.0.0`: 80/443 (zoe-ui nginx in Docker), **8000 (zoe-data, host-native)**, 8123 (Home Assistant), and - against the merged compose (#1727, loopback-only), because the containers were never recreated - **8007 (the HA MCP bridge, no inbound auth; it answered the panel over the LAN)** and **5432 (Postgres)**; llama and Serena are loopback | live `ss -ltn`, `docker ps`; `docker-compose.yml:147-155`; open-problems 2026-10-04 |
| Home line | second hop in `100.64.0.0/10`; the router answers NAT-PMP/PCP but reports no valid public address (signs of carrier-grade NAT, not proof); UDP works; the NAT mapping does not vary by destination ("easy" NAT, good for direct paths); nearest Tailscale relay region Sydney at 53 ms RTT | live: TTL-limited pings, `tailscale netcheck` (addresses not recorded) |
| Voice daemon identity | `X-Device-Token` on every call; an unbound token resolves to guest with `panel_id` stamped | `routers/panel_auth.py:_resolve_device_token_user`; `auth.py:get_current_user` |
| Kiosk browser identity | a guest session only. It is "the panel" when its address equals the registered `panels.ip_address` (`panel_principal.is_panel_guest` path 2; `skybridge` display identity; the push socket's guest policy uses `panels.allow_guest`) | `services/zoe-data/panel_principal.py`, `routers/skybridge.py:58-62`, `main.py:_resolve_subscribable_panel` |
| Browser origins | the push socket and CORS admit `ALLOWED_ORIGINS` + this host's own LAN IP origin + `ZOE_ALLOWED_WS_ORIGINS`. A new host name for the kiosk page is a new origin and is refused until added (the 2026-07-03 dead-kiosk incident class) | `main.py:_allowed_browser_origins`, `_self_lan_origins` |
| Pairing | `/api/panels/provision/*` mints a kiosk device token after a signed-in member confirms a 6-character code on their phone (QR in `provision.html`) | `routers/panel_provision.py` |
| Legacy Wi-Fi portal | `provision-server.py --mode wifi-setup` (AP on 192.168.4.1, nmcli scan/connect, `portal.html`). Not installed on the live panel | `scripts/setup/touchscreen/README.md` |
| The one remote panel | the Mac virtual panel reaches zoe-data through the Cloudflare tunnel + an Access service token; `panel_principal` refuses any request carrying a Cloudflare header as "not the panel" | `docs/knowledge/mac-virtual-panel.md`; `panel_principal._via_public_tunnel` |

## 3. The recommended stack and why

### 3.1 The link: Headscale on the owner's server (no new account)

Ranked under the owner's constraint "no new account". Tailscale's hosted service is the reference column.

| Criterion | **Headscale on the owner's server** (pick) | Cloudflare TCP pass-through (2nd) | WireGuard hub on the owner's server (3rd) | WireGuard via a home port forward | Tailscale hosted (reference) |
|---|---|---|---|---|---|
| New account | none (existing server) | none (existing Cloudflare account) | none | none | **yes** |
| Content privacy | end-to-end WireGuard; relays forward ciphertext | inner TLS end-to-end; Cloudflare sees ciphertext + Access metadata (verified in source) | end-to-end WireGuard | end-to-end WireGuard | end-to-end WireGuard |
| What leaves the house | device names, keys, endpoints - to OUR server | each connection's timing and size and the service-token identity, to Cloudflare | encrypted packets via our server | nothing | metadata + client connection logs to Tailscale |
| Path | direct when NAT allows (the home NAT is "easy"); else relayed by our server's DERP (~50 ms each way from the house) or Tailscale's Sydney region | always via Cloudflare's edge | always via our server | direct | direct, else Tailscale relays |
| Hotel / CGNAT / UDP-blocked networks | works (DERP over 443) | works (outbound 443 only) | only if UDP to the server is allowed | **likely not possible: the home line shows CGNAT signs** (unconfirmed) | works |
| Zoe can tell the panel by address | yes, by tailnet address - as strong as the control server (section 4.2) | **no**: every caller arrives as the cloudflared container | yes, by tunnel address | yes | yes |
| HTTPS name for the panel | no `tailscale cert`: a private CA (section 4.1) | today's self-signed cert inside the stream, or the private CA | private CA | private CA | `tailscale cert` |
| A compromised control server can add nodes | **yes: no Tailnet Lock** (research 2.2) | n/a (Access decides) | n/a (static keys) | n/a | no, with Tailnet Lock |
| Operator burden | a .deb, a config file, an nginx block, a grey-cloud DNS name, UDP 3478, backups, upgrades (pre-1.0) | an ingress rule, an Access app + service token, a `cloudflared access tcp` unit on the panel | keys by hand, routing, firewall, no NAT traversal | router + an ISP public address | one account, one policy file |
| Household burden | none | none | none | none | none |

**Deciding reasons for Headscale on the owner's server:** it is the only no-new-account option that keeps content
end-to-end private, lets the panel and the Jetson talk directly when the networks allow (the home side is "easy"
NAT), survives hotel and CGNAT networks through its relay, and reuses the Tailscale clients already installed. The
server exists with root, so the cost is operator time (install, a DNS name, backups, upgrades), not a new box or
account. Its two gaps are designed around: no Tailnet Lock (section 4.6: the Jetson's own firewall plus the device
token at the application layer; the residual is what an anonymous guest reaches, plus, until P3, the panel's
address-based household-list rights and display name if the server hands a rogue node the panel's address), and no
`tailscale cert` (section 4.1: a private CA).

**Cloudflare TCP pass-through is second** (section 3.3). It is the fastest to stand up and needs nothing new, but
all traffic takes the edge detour, connections drop at Cloudflare restarts, and Zoe loses the panel's address as an
identity signal entirely, which makes the device-token kiosk session (section 4.2) a precondition rather than a
later polish. Kept as the fallback path for when the server or the control plane is down.

**WireGuard is last.** A home port forward is likely impossible on this line (CGNAT signs, unconfirmed); a hub on the owner's server
works but relays everything through it with hand-managed keys, which Headscale does better on the same box. The
Jetson has no in-kernel WireGuard; `wireguard-go` (userspace) is packaged for its Ubuntu (research 2.3).

**Beside the server's other roles.** The owner is designing that server as Zoe's front door for apps and connectors
(public webhooks, OAuth redirects) and plans an outbound, end-to-end encrypted device relay on it. The tailnet fits
beside both: the server joins the tailnet as `tag:front-door`, so a webhook or OAuth callback that lands there
reaches the Jetson over the private link and the house never accepts an inbound connection from the internet.
Panel link now; app and connector ingress later, through one narrow listener on the Jetson (decision D8). The
device relay is an application-level channel for devices that are not tailnet members; it does not need to carry
the panel.

### 3.2 Wi-Fi onboarding: balena wifi-connect (runner-up Comitup)

The panel has one Wi-Fi radio. All headless options work the same way: when no known network is reachable, the
panel becomes a small access point, a phone joins it, a captive page lists the networks the panel saw, and the
person types the password on the phone (research 1).

- **balena wifi-connect** (Apache-2.0, a single Rust binary, actively released: v4.11.90 on 2026-10-02) drives
  NetworkManager over D-Bus, which the panel already runs. It takes the hotspot name and a WPA2 passphrase as
  launch options, serves the captive page, saves the chosen network as an ordinary NetworkManager connection, and
  brings the hotspot back by itself when the join fails (research 1.1, verified). It is launched on demand, which
  fits Zoe: the panel agent decides "no network" and starts it with a fresh random passphrase, so the status
  screen owns the QR and the spoken line. Its custom-UI directory option lets the phone page carry Zoe's wording.
  Gap: its install script targets older Raspbian; Bookworm is not documented, so the lab proves it (cell W1).
- **Comitup** is the runner-up (GPL-2.0, Debian package, NetworkManager-based, last push 2026-08). It runs as an
  always-on service that falls back to its hotspot by itself, which is good, but on Bookworm it needs an updated
  `python3-networkmanager` from its own repository, its hotspot security is not documented, and its AP naming and
  LED "locate" button are built for headless boards (research 1.2). Choose it if wifi-connect fails the lab.
- **Not chosen:** RaspAP (a full router admin console on hostapd/dnsmasq/lighttpd: far more surface than a panel
  needs, and it competes with NetworkManager), BLE / Improv Wi-Fi (the Improv site itself says the browser flow does
  not work on iOS, and the spec has no transport encryption for the credentials), WPS (needs the venue's router
  button; PIN mode is brute-forceable), the legacy `wifi-setup` mode in `provision-server.py` (ours, unmaintained,
  fixed `192.168.4.1`, no captive-portal answer). A hand-rolled `nmcli device wifi hotspot` + captive page
  is what wifi-connect already is, so it is not rebuilt.

The QR is the standard Wi-Fi join code (`WIFI:T:WPA;S:<ap-name>;P:<per-session password>;;`), which both the iOS
and Android cameras turn into "Join network" (research 1.6). The hotspot gets a fresh random password each time
it starts, so the AP is never open.

### 3.3 Cloudflare TCP pass-through: what it would take

- **Shape.** On the existing tunnel, an ingress rule `hostname: <tcp-host>` with `service: tcp://zoe-ui:443`; an
  Access application with a Service Auth policy for one service token; on the panel, `cloudflared access tcp
  --hostname <tcp-host> --url 127.0.0.1:<port> --service-token-id ... --service-token-secret ...` as a systemd unit
  (`Restart=always`). cloudflared 2026.10.1 is in Cloudflare's apt repository for bookworm arm64 (research 2.7).
- **Privacy, verified in source.** The client carries each TCP connection as binary WebSocket frames to the edge;
  the origin cloudflared dials `zoe-ui:443` and copies bytes both ways. Nothing parses the payload or terminates the
  inner TLS, and no PROXY protocol or forwarding header is added on this path (research 2.7).
- **Timeouts and restarts.** Cloudflare closes idle WebSockets after an undocumented time and restarts edge servers
  without notice; the origin cloudflared pings every 54 s. A dropped stream kills that one TCP connection and the
  next one re-dials. The push socket already reconnects; a voice turn caught by a restart fails and is retried (the
  daemon retries transient errors). No `:8007` ingress: the panel does not use the HA bridge
  (`VOICE_ROUTE_MODE=direct`) and the bridge has no inbound auth.
- **Keeping the panel's URL.** Away from home the panel cannot reach `<home-LAN-IP>`. Options: (a) one stable name,
  e.g. `zoe.home.arpa`, in the kiosk URL and `ZOE_URL`, which `/etc/hosts` on the panel maps to `127.0.0.1` away and
  to `<home-LAN-IP>` at home (switched by `location_mode`, section 4.5); the local listener on 443 (needs
  `CAP_NET_BIND_SERVICE`); the name added to `ZOE_ALLOWED_WS_ORIGINS`; a certificate for it from the private CA.
  (b) A local DNAT rule on the panel rewriting `<home-LAN-IP>:443` to the local listener while away: keeps today's
  URL, but silently lies about where packets go and must never be active at home. Recommend (a).
- **Security, read from the code.**
  - nginx sets `X-Real-IP $remote_addr` absolutely on every zoe-data location (`/api/`, `/ws/`, `/health`,
    `/.well-known/agent.json`) and blanks `X-Internal-Token` / `X-Zoe-User-Id` on `/api/` and `/ws/`
    (`services/zoe-ui/nginx.d/locations.inc`). So a service-token holder **cannot forge `X-Real-IP`** through nginx
    and **cannot pass `_is_internal_request`** (zoe-data's TCP peer is the nginx container, not loopback, and the
    internal token is blanked).
  - What does change: every tunnel caller arrives as the cloudflared container's private Docker address, and the
    Cloudflare headers `_via_public_tunnel` relies on are absent (a client could add them itself, which only makes
    it "not the panel"). So the panel can never be identified by address here - never register the cloudflared
    address as a panel, or every token holder becomes "the panel"; `ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE=lan` would treat
    any token holder with a guest session as the panel (private address), so `lan` must stay off; and
    `livekit_available` reads client-supplied `cf-connecting-ip` / `X-Forwarded-For` first (cosmetic).
  - **Precondition:** the ingress points at nginx only. A `tcp://` rule to `host.docker.internal:8000` would make the
    cloudflared container zoe-data's direct peer, inside the default trusted proxy range (`172.16.0.0/12`), so a
    client-supplied `X-Real-IP` would be believed and the caller could claim a registered panel's address. That rule
    is categorical, not mitigated: pinning `ZOE_TRUSTED_PROXY_NETS` to nginx protects `panel_principal` (it goes
    through `trusted_client_ip`), and since #2082 so does `routers/skybridge.py`'s display-identity check, but a
    direct peer inside the trusted range is believed by design. Pinning the proxy range is still worth doing, through a fixed address for nginx on
    `zoe-network` rather than whatever Docker assigned (container addresses drift).

## 4. What Zoe must change

### 4.1 The panel's address and TLS

- One name everywhere: `https://<zoe-host>.<tailnet-domain>` in the kiosk `config.json` `url` and the daemon's
  `ZOE_URL` (and Omi sync, if it runs on the panel). **The panel uses the tailnet name at home too.** One path means
  one identity (section 4.2) and one thing to test; on the home Wi-Fi Tailscale finds a direct LAN path, so the cost
  is WireGuard overhead on a LAN hop (measured in P1, section 6).
- Certificate. **With Headscale (the pick)** there is no `tailscale cert` (Headscale does not implement the DNS
  hook it needs; research 2.2), so a small private certificate authority does it: one root key kept offline by the
  operator, its root certificate installed on the panel (Chromium's NSS store for the kiosk; `REQUESTS_CA_BUNDLE`
  or the system store for the daemon), and a leaf certificate for `<zoe-host>.<tailnet-domain>` (the MagicDNS
  name) served by the zoe-ui nginx as a second `server` block selected by SNI. The **LAN endpoint gets a leaf from
  the same CA** with `<home-LAN-IP>` as an IP subject-alternative name (an IP SAN is ordinary X.509; the Jetson's LAN
  address must be a DHCP reservation so the certificate stays true), so the home fallback (section 8) keeps working
  once `VERIFY_SSL=true` and the kiosk's certificate bypass are gone. The self-signed block stays only as the
  default for names nobody should use. Renewal is a yearly operator step with a reminder, or short-lived leaves
  from an intermediate key kept on the Jetson, on a timer. Nothing is published: a private CA writes no Certificate Transparency entry.
  **With hosted Tailscale (reference)**, `tailscale cert` issues a Let's Encrypt certificate for the `.ts.net` name
  instead, renewed by a timer; that name is published in Certificate Transparency logs (research 2.1, decision D5).
- Then `VERIFY_SSL=true` on the daemon and drop `--ignore-certificate-errors` from the kiosk for that host - today's
  "trust any certificate" stops being needed.
- `ZOE_ALLOWED_WS_ORIGINS` gains `https://<zoe-host>.<tailnet-domain>`; without it the push socket refuses the
  kiosk (the 2026-07-03 incident). `ZOE_BASE_URL` (the base for pairing links) stays the LAN/public value: the
  phone confirming a pairing is not on the tailnet.

### 4.2 Panel identity over the tunnel: the device, not the address

Today the kiosk browser is "the panel" only because of its source address (section 2). Over the tailnet the
address changes from `<home-LAN-IP-of-panel>` to the panel's tailnet `100.x` address, and the code reacts like this:

| Check | Over the tailnet, unchanged code | Fix |
|---|---|---|
| `panel_principal` path 1 (device token, voice daemon) | works: the token travels in the header | none |
| `panel_principal` path 2 (registered address) | fails: `panels.ip_address` holds the LAN address | interim: register the tailnet address; target: below |
| `ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE=lan` | fails: `100.64.0.0/10` is shared address space and Python's `is_private` is False for it | do not use `lan` for a tailnet panel |
| `skybridge` display identity (`X-Real-IP == panels.ip_address`) | fails, as path 2 | as path 2 |
| push socket for a guest kiosk (`_panel_allows_guest_push`) | works: it keys on `panel_id` + `allow_guest`, not the address | none |
| `livekit-token` `livekit_available` | false (not a private address, no TURN) | none needed: the panel's voice is the daemon, not LiveKit |

**Interim (P1):** set `panels.ip_address` to the panel's tailnet address. That is no weaker than today for the two things the address grants
(a guest's household-list rights and the panel's display name): a tailnet source address is bound to the sending
node's WireGuard key, so another node cannot forge it (cell L4b tests this rather than assuming it), but it is only
as trustworthy as the control server that assigns addresses (section 4.6), which is why P3 moves identity to the
device token. Over Cloudflare TCP pass-through there is no usable address at all (section 3.3). Lab test L4 checks
that nginx's `X-Real-IP` really is the tailnet address (Docker's published port DNATs and keeps the source for
non-loopback traffic; if the Jetson's Docker uses the userland proxy for this path, every client would appear as the
bridge gateway and P1 stops until that is fixed).

**Target: a kiosk credential minted from the device token** (built flag-dark in #2082 as a single-use ticket the
loopback gate trades for an HttpOnly cookie; `docs/knowledge/panel-away-p1.md`). The design it implements:

1. A loopback helper on the panel holds the device token; the browser never sees it. The natural home is the voice
   daemon's own HTTP server (port 7777, which already holds `DEVICE_TOKEN` and serves `/activate` to the kiosk page),
   bound to loopback. (The separate panel agent on 8765 is deployed on the Pi but its source is not in this repo.)
2. At boot and on every 401, the kiosk page asks the helper for a session; the helper calls a new
   `POST /api/panels/{panel_id}/kiosk-session` with `X-Device-Token` and hands the browser only the returned
   session id. The helper applies the Mac `/activate` guard (loopback `Host`, `Origin` must be the Zoe origin) so no
   other page can mint one.
3. zoe-data mints a GUEST session and records it against the token's `panel_id` (a binding like the existing
   `ui_panel_sessions` rows). `get_current_user` then stamps `panel_id` on that guest, the same shape
   `_resolve_device_token_user` returns for an unbound device, so `panel_principal` path 1 covers the kiosk too and
   `skybridge` accepts "a session bound to this `panel_id`" as the device check.
4. Path 2 (address) stays as the fallback for panels without an agent; `_via_public_tunnel` stays.
5. Revoking the device token (section 4.6) kills the next kiosk-session mint, so a lost panel goes deaf and dumb
   within one guest-session lifetime (30 min) or at once if sessions for that `panel_id` are also revoked.

### 4.3 Voice over a higher-latency link

The voice path is unchanged code; only the network under it changes. What changes is cost per turn:

| Leg | LAN today | Over a WAN | Source |
|---|---|---|---|
| Upload (base64 WAV in JSON; 16 kHz mono 16-bit = 32 KB/s raw, ~43 KB/s as base64) | median 99 ms incl. decode | + one RTT + bytes / uplink. A 5 s turn is ~213 KB: ~0.2 s on a 10 Mbit/s uplink, ~0.9 s on 2 Mbit/s | `docs/knowledge/panel-ttfa-breakdown-2026-09-28.md`; `zoe_voice_daemon.py` |
| Reply stream (NDJSON, one base64 WAV per sentence) | negligible | + one RTT before the first sentence; later sentences overlap playback | daemon `_do_single_turn_stream` |
| Push socket text (`voice:transcript` etc.) | negligible | + one RTT; 30 s ping already survives idle NAT | `touch-ui-executor.js` `PUSH_PING_MS` |

Budget, stated honestly. Conversational guidance puts one-way delay under ~150 ms (research 3); Zoe's
time-to-first-audio is already ~2 s on the LAN. With today's full-WAV upload the added time is roughly
**one RTT + 0.34 s x (clip seconds / uplink Mbit/s) + the first sentence's download**, so the upload alone breaks a
+300 ms target on ordinary links (a 5 s clip at 5 Mbit/s is ~341 ms before any RTT). So there are two budgets:

| Stage | Budget (median added time-to-first-audio) | Worked numbers, 4 s clip |
|---|---|---|
| **Interim: travel after P1 + P2, full-WAV upload** | **<= +0.5 s** on >= 5 Mbit/s uplink; **<= +1 s** on 2 Mbit/s or when relayed | direct, 50 ms RTT, 5 Mbit/s: ~+0.33 s; relayed through the owner's server (~+100 ms RTT): ~+0.43 s; hotel 2 Mbit/s, relayed: ~+0.85 s |
| **Target: after P3's upload change** | **<= +300 ms** at 100 ms RTT and 5 Mbit/s | Opus at 24 kbit/s makes the 4 s clip ~12 KB (~20 ms at 5 Mbit/s), so the RTT dominates |

The owner travels before P3, so the interim budget is the accepted one: noticeably slower on a poor link, not
broken. The worked numbers are arithmetic, not measurements; P1 cell L6 and P3 cell L9 replace them with measured ones.

The upload change is P3, decided by measurement (cell L9). (a) The existing flag-dark under-speech upload
(`ZOE_STT_STREAM_UPLOAD`, 0.32 s batches while the person speaks) is **not** an upload saving on a WAN as built: the
full WAV is still sent with the turn as the fallback, so it adds bytes and saves only server STT time; it would need
a change to skip the WAV when the stream is complete. (b) Opus at ~24 kbit/s for the upload, about a tenth of raw
PCM (research 3), changes what Moonshine receives and needs the replay gate on the owner's real-voice corpus before
any use. Either is a voice-path change: replay-gated, flag-dark, and neither is cheap enough to pull ahead of travel.

### 4.4 "Can't reach Zoe" on the panel

Replace the boot loop (section 2) with a local status screen the kiosk shows whenever Zoe does not answer. **Built
flag-dark in #2082:** `scripts/setup/touchscreen/zoe-kiosk-gate.py`, bound to `127.0.0.1:7781`, renders with no
network and no Zoe, and also hands the browser its device-token credential (record:
`docs/knowledge/panel-away-p1.md`). P2 adds the hotspot QR to it; the spoken line is still to do.

- Served on loopback (no network needed). It shows three lights, in order: **Wi-Fi**
  (NetworkManager state), **link home** (`tailscale status --json`: `BackendState`, whether the Jetson peer is online),
  **Zoe** (`/health` over the tailnet).
- Each red light has a plain sentence and one action: "No Wi-Fi - scan this with your phone" (the hotspot QR);
  "Wi-Fi is on but it wants a sign-in page" (section 5.4); "Can't reach home - Zoe's computer may be off or home
  internet may be down. I'll keep trying." The daemon says the same sentence once with its local TTS
  (`espeak-ng`, already the fallback) and then stays quiet.
- When all three are green the kiosk loads the estate. The estate keeps its own banner for a push socket that
  has been down for more than one reconnect cycle.

### 4.5 Away mode: features that assume the panel is in the house

A panel that is not at home must not behave as a room of the house. The panel reports `location_mode = away`
when its current Wi-Fi is not one of the home networks NetworkManager has marked as home (decision D4). Away mode:

| Feature | Remote behaviour |
|---|---|
| AirPlay "Zoe Panel" speaker (shairport-sync, found by mDNS on the home LAN) | not offered; Music Assistant cannot discover it across the tailnet. Music on an away panel = the panel's own browser player only |
| Room presence, occupancy, sleep gate (`identity-presence-engine.md`: one panel = one room) | an away panel produces no home-presence evidence and is excluded from "is anyone home" |
| Home control ("turn off the lights") | asks "the lights at home?" before acting, or is off away (decision D6) |
| Announcements and the brief addressed to all panels | not delivered to an away panel unless the person there asked |
| Face ID, Bluetooth settings, screen wake | unchanged (local to the Pi) |
| Household lists, chat, memory, voice turns | unchanged |

### 4.6 Security

- **Fix the Jetson's bind drift first.** The HA MCP bridge (no inbound auth) and Postgres listen on every interface
  although the merged compose binds them to loopback (#1727; the containers were never recreated) [live]. Joining
  any tailnet before that is applied would put them one policy mistake away from the panel and the front-door
  server. Recreate both containers from the current compose, then join.
- **Policy before login.** A tailnet with no policy allows everything. The policy file is written first, then the
  boxes join. Headscale reads the same HuJSON grants syntax as Tailscale (research 2.2). Shape (names are
  placeholders):

  ```
  {
    "groups":    { "group:owner": ["<owner-user>@"] },
    "tagOwners": { "tag:zoe-server": ["group:owner"], "tag:zoe-panel": ["group:owner"],
                   "tag:front-door": ["group:owner"] },
    "grants": [
      // panel -> Zoe: HTTPS only. Never 8000 (zoe-data direct), 8007 (HA bridge), 8123, 5432, 22.
      { "src": ["tag:zoe-panel"],  "dst": ["tag:zoe-server"], "ip": ["tcp:443"] },
      // owner devices -> Zoe and panel: HTTPS + SSH for maintenance.
      { "src": ["group:owner"],    "dst": ["tag:zoe-server"], "ip": ["tcp:443", "tcp:22"] },
      { "src": ["group:owner"],    "dst": ["tag:zoe-panel"],  "ip": ["tcp:22"] }
      // later (D8): { "src": ["tag:front-door"], "dst": ["tag:zoe-server"], "ip": ["tcp:<connector-port>"] }
    ]
  }
  ```
  Nothing grants the panel anything but 443 on the Jetson; nothing grants the Jetson or the server access to the panel.
- **Joining.** Each box joins once with a single-use, pre-tagged, short-lived pre-auth key created on the control
  server (`tailscale up --login-server https://<control-name> --auth-key ...`). Tagged nodes do not expire (no
  silent logout of the panel); everything else follows `node.expiry` (research 2.2). Only the operator can create
  keys or register a node: that is the "device approval" step on Headscale (decision D3).
- **No Tailnet Lock on Headscale, so the control server is trusted.** A compromised control server could add a node
  or hand a rogue node the panel's tailnet address. Designed around, not ignored: (1) the Jetson's own nftables
  admit `tailscale0` traffic to 443 only, enforced locally, so a pushed policy cannot open 8000/8007/5432; (2) every
  zoe-data call still needs the device token or a session, so a rogue node reaches only what an anonymous guest
  reaches; (3) the address-based panel identity of P1 grants only a guest's household-list rights and the panel's
  display name, and P3's device-token kiosk session removes even that dependence; (4) the server is hardened as
  what it now is - key-only SSH, automatic security updates, Headscale's database backed up off the box.
- **Never `tailscale serve` or Funnel for Zoe** (Headscale does not support them anyway, but the rule is about any
  loopback proxy). `auth._is_internal_request` trusts any loopback TCP peer on zoe-data: a loopback proxy pointed at
  `127.0.0.1:8000` would make every tailnet caller an internal service that may assert any identity. Pointed at
  nginx it would hide the panel's address behind the Docker gateway. nginx terminates the tailnet TLS itself.
- **Panel host firewall (P1, before the panel travels), narrow on the tailnet too.** The tailnet is not a trusted
  network: without Tailnet Lock a compromised control server can add a node and push a policy that lets it reach the
  panel, and the panel's own rules are the one thing it cannot rewrite. nftables on the Pi: accept loopback,
  established/related traffic, DHCP and the onboarding hotspot while it is up; on `tailscale0` accept **only TCP 22
  from the owner devices' tailnet addresses** (pinned in the ruleset, not taken from the policy) and nothing else -
  the panel is a client, so the Jetson never opens a connection to it (announcements are polled, the push socket is
  outbound). Drop all other inbound. At home the AirPlay port needs the home LAN, so the home profile allows 7000 +
  mDNS + nqptp from the home subnet only (the same `location_mode` switch). SSH is key-only.
- **Local helpers bind to loopback.** The voice daemon's `/health` + `/activate` (7777) and the screen-wake agent
  (8765) are reached only by the kiosk browser on the same Pi, so they bind `127.0.0.1` (the kiosk gate already
  does, 7781), and `/activate` takes the Mac build's `Host`/`Origin` guard. Then even an over-wide rule exposes
  nothing that can start a recording. **#2082's merged firewall accepts all of `iifname "tailscale0"`**; narrowing it
  to the rule above is a ledger follow-up (2026-10-16).
- **Lost or stolen panel (runbook, P1):** delete the node on the control server (link gone at once), revoke its
  device token (`DELETE /api/panels/{id}/token/{token_id}`; the voice daemon gets 401 at once), set
  `panels.is_active = 0` (address path off). With P3, also revoke its kiosk sessions. The panel's disk holds a device
  token and a node key, both now dead; no member credential lives on it.
- **Key rotation:** device tokens get an `expires_at` (the API already supports it) and are re-issued by the pairing
  flow; node keys rotate by re-auth; pre-auth keys are single-use and expire; the private CA's leaf is short-lived.

## 5. The onboarding flow (P2)

### 5.1 Happy path

1. **Panel boots somewhere new.** NetworkManager tries every saved network; after ~60 s with none, the panel agent starts
   wifi-connect with a fresh random passphrase.
2. **Panel screen:** "I need Wi-Fi. Scan this with your phone's camera." A large QR (Wi-Fi join code for the
   panel's own hotspot `Zoe-Panel-XXXX`, fresh random WPA2 password) and, under it, the hotspot name + password in
   text for phones whose camera does not read Wi-Fi codes. The daemon says the first sentence once.
3. **Phone** joins the hotspot from the camera prompt. The phone's captive-portal check opens the setup page
   by itself; if it does not, a second QR on the panel opens the page's address.
4. **Phone page:** the networks the panel saw (scanned before the hotspot started), strongest first. The person taps
   one and types its password on the phone. Hidden network: "Other network" with name + password.
5. **Panel screen:** "Connecting to the new Wi-Fi..." The hotspot goes down (one radio), so the phone loses it: the
   phone page says so in advance ("your phone will drop off this network - watch the panel").
6. **Panel screen:** the three lights (section 4.4) go green one by one. "Connected. Reaching home... Done." The
   estate loads; Zoe greets the room.

### 5.2 Failure cases

| Case | What the person sees and hears |
|---|---|
| Wrong password | the panel says "That password didn't work"; the hotspot comes back with the same QR; the person re-scans |
| Network needs a sign-in page (hotel, cafe) | section 5.4 |
| WPA2-Enterprise (802.1X, work/uni) | not in P2: "This network needs a work or school login. Use your phone's hotspot instead" (P3 may add PEAP/MSCHAPv2 fields on the phone page; research 1.4) |
| Wi-Fi works, link home does not (Zoe's computer off, home internet down, UDP blocked) | the "can't reach home" light; Tailscale falls back to its DERP relays by itself, so a strict network usually still works, only slower (still end-to-end encrypted). With Headscale the relay is the owner's server or Tailscale's Sydney region (research 2.2) |
| No Wi-Fi at all | the phone's own hotspot is the supported fallback: the phone page offers "use my phone's hotspot" as the first tip |
| Nobody completes it | the hotspot stays up and the panel stays on the status screen; nothing times out into a blank screen |

### 5.3 Pairing is not repeated

Moving the panel does not re-pair it: its device token and its Tailscale node key are on its disk. The phone is
only needed for the Wi-Fi password. A brand-new panel still pairs once with the existing QR flow
(`/api/panels/provision/*`, member confirms on the phone), and joins the tailnet with a pre-tagged auth key the
operator sets up (decision D3 on who may approve).

### 5.4 Captive portals

A hotel or cafe sign-in page authorises the device that completes it (by its MAC address), so the person cannot
finish it for the panel on their phone. NetworkManager reports such a network as `portal` (connectivity check), and
the panel says so honestly. Supported answers, in order: the phone's hotspot; a travel router; P3 option: show the
portal page on the panel itself for a tap-only "accept" page (no typing; anything needing a room number is the
phone-hotspot answer).

## 6. Phases and lab tests

Every phase runs on a **lab node first** (the Mac virtual panel for the link and voice, a spare Pi or the panel's
SD-card clone for Wi-Fi onboarding), then the live panel in a planned window. Most cells carry a negative control
that must fail; where a cell can only be compared against a baseline, the column says "baseline" so it is not
mistaken for a falsifying control.

### P1 - the link (operator steps + small config/code)

Steps: recreate the HA bridge and Postgres containers from the current compose (section 4.6); install Headscale on
the owner's server with a grey-cloud DNS name, TCP 443 through the host nginx (upgrade headers per Headscale's
example) and UDP 3478 for its relay's STUN, then try it without nginx first as Headscale's docs advise; write the
policy; create the private CA and the leaf for the MagicDNS name; join the Jetson (`tag:zoe-server`) and a lab
node (`tag:zoe-panel`) with single-use pre-tagged keys; the Jetson's local nftables (443 only on `tailscale0`);
nginx SNI block; `ZOE_ALLOWED_WS_ORIGINS`; the panel firewall; then the live panel: tag, firewall, URLs, CA root,
`VERIFY_SSL=true`, `panels.ip_address` = its tailnet address.

| Cell | Test | Negative control |
|---|---|---|
| L1 | lab node reaches `https://<zoe-host>.<tailnet-domain>/health` with verification ON | the same node to `:8000`, `:8007`, `:8123`, `:5432`, `:22` times out (policy) |
| L1b | the Jetson's local nftables hold on their own | with the policy temporarily widened to allow all, `:8000` from the lab node still times out |
| L2 | an owner device reaches 443 and SSH | an untagged node registered to another user reaches nothing |
| L3 | the certificate validates for the MagicDNS name AND for `https://<home-LAN-IP>` (IP SAN) with only the private CA trusted | the same two `curl`s without the CA root fail; a name on no leaf gets the self-signed default and fails |
| L4 | nginx log `X-Real-IP` = the node's tailnet address; `is_panel_guest` true for the registered panel | the owner laptop on the tailnet is NOT the panel; a request with a `CF-Ray` header is still refused |
| L4b | spoofing: from a second lab node, send packets to the Jetson with the panel's tailnet address as source | they are dropped (none reach nginx); the same node's own address is accepted (the probe works) |
| L5 | push socket connects from the MagicDNS origin | without the `ZOE_ALLOWED_WS_ORIGINS` entry it is refused (proves the entry is load-bearing) |
| L6 | at home, panel over the tailnet: time-to-first-audio median within +20 ms of the LAN path on 20 live turns | baseline: the same 20 turns on the LAN path. Plus the replay gate (`voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock`) must show no regression vs its baseline |
| L7 | lost-panel drill: delete node + revoke token -> link gone, daemon 401 | before the drill the same calls succeed |
| L8 | from another device on the panel's Wi-Fi, ports 22/7000/7777/8765 are closed | the same scan before the firewall shows them open |
| L10 | control server down (stop Headscale): existing panel-Jetson link keeps working for the test window | a NEW node cannot join while it is down (proves the server was really down) |

### P2 - Wi-Fi onboarding (wifi-connect on the panel image)

**Prerequisite: the local status screen.** The hotspot QR has to appear when there is no network and no Zoe, so it
lives on the loopback kiosk gate (`scripts/setup/touchscreen/zoe-kiosk-gate.py`, `127.0.0.1:7781`, built in #2082),
not on any Zoe page. P2 starts only once the gate is the kiosk's start page on the lab image; W1 cannot pass without it.

| Cell | Test | Negative control |
|---|---|---|
| W1 | no known network: hotspot + QR on screen within 90 s; iPhone and Android cameras join from the QR | with a known network in range, no hotspot appears |
| W2 | captive page opens by itself on both phones after joining; network picked, password typed, panel connects and the lights go green | with the captive-portal answer disabled on the lab image, the page does NOT open by itself (proves the auto-open is ours); wrong password: panel says so, hotspot returns |
| W3 | hidden SSID via "Other network" | the same hidden SSID with a one-character name typo fails and the hotspot returns |
| W4 | captive-portal network detected and explained | a normal network is not flagged as a portal |
| W5 | hotspot password differs on each start | a capture of an old QR no longer joins |
| W6 | strict network (UDP blocked): link still comes up via the relay and the status shows "relayed" | the same lab network with UDP restored shows "direct" (proves the relay label is real) |

### P3 - remote polish (each a flag-dark PR)

- Status screen: built in #2082 (the P2 prerequisite above). P3 adds the spoken line and mid-session drops (today a
  drop after boot shows the estate's banner, then Chromium's error page on reload). Test: unplug the Jetson's network:
  the panel shows "can't reach home" within 30 s and recovers on its own. Negative: no message while Zoe is up.
- Panel listeners: 7777 and 8765 bound to loopback, `/activate` guarded. Negative: from a tailnet node allowed by a
  deliberately wide policy, `:7777/activate` and `:8765` time out.
- Kiosk session from the device token (section 4.2) + the loopback helper endpoint. Negative: a page from another
  origin calling the helper gets 403; a revoked device token cannot mint.
- Away mode (section 4.5). Negative: at home, nothing is gated.
- Voice WAN study, cell **L9**: `tc netem` on the lab node at RTT 50/100/200 ms, uplink 1/5/20 Mbit/s, 1% loss,
  20 turns each; record upload, first-audio and total. Decide (a) under-speech upload vs (b) Opus from this table;
  either one replay-gated before deploy. Baseline: 0 ms netem reproduces the LAN numbers. Negative control: netem adding
  500 ms each way (1 s RTT) must make the harness's budget check report a breach, since one extra RTT alone exceeds
  the +300 ms allowance (proves the check can fail).
- Cloudflare TCP pass-through as the fallback path (section 3.3), only if the owner wants a second path: the
  device-token kiosk session must land first. Negative: a service-token holder without a device token is not "the
  panel" and cannot reach `:8000` or `:8007`.

## 7. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | The control plane, under "no new account". **Deciding fact, now known:** the owner's existing cloud server is owner-controlled with root and host nginx, so Headscale is feasible there. Remaining questions: accept one grey-cloud DNS name (it reveals that server's public address, which the proxied apex hides today; a second public address on the server avoids that), UDP 3478 open, no Tailnet Lock (designed around in 4.6), and the operator owning Headscale upgrades and backups | **Headscale on the owner's server.** Second: Cloudflare TCP pass-through on the existing tunnel (no UDP, always relayed, no address identity). Reference if a new account becomes acceptable: hosted Tailscale (adds Tailnet Lock and `tailscale cert`; same clients) |
| D2 | Which devices join: Jetson + panel only, or also the owner's phone/laptop (needed for SSH and the owner's own remote browser over the tailnet) | Jetson + panel + owner devices; the front-door server only when D8 starts; no other household devices until asked |
| D3 | Who may create join keys and register nodes | owner only (the Headscale CLI on the server) |
| D4 | How the panel knows it is at home: a list of home Wi-Fi networks (kept on the panel, never in the repo) or "Zoe's LAN address answers directly" | the home-network list; the direct-answer check as a cross-check |
| D5 | The MagicDNS domain and node names | neutral names, no family name (with a private CA nothing is published; with hosted Tailscale the name lands in Certificate Transparency logs) |
| D6 | What an away panel may do: voice + chat + lists + memory (yes); home control (ask "at home?" / off); announcements and the brief (off unless asked); AirPlay (not possible) | as listed |
| D7 | Remote voice quality before P3: accept the interim budget (<= +0.5 s on >= 5 Mbit/s, <= +1 s on 2 Mbit/s or relayed; section 4.3) or hold travel until P3's upload change (target +300 ms) | travel after P1+P2 on the interim budget; P3 measured on real trips |
| D8 | The server as front door: webhooks and OAuth redirects reach the Jetson over the tailnet (server as `tag:front-door`, one narrow listener on the Jetson with its own request verification), or through the planned device relay | over the tailnet, when the first connector needs it; the device relay for devices that are not tailnet members. Do not build both paths for the same traffic |

## 8. What this does not decide, and the risks

- **The tailnet is a dependency for the panel at home too.** If `tailscaled` breaks, the panel loses Zoe at home.
  Mitigation: the status screen offers the LAN URL as a fallback when the panel is on a home network (D4) and the
  tailnet link is down; the LAN endpoint carries a private-CA certificate with the LAN address as an IP SAN, so
  the fallback verifies like the tailnet path (section 4.1). A control-server outage should leave
  established links up (cell L10 measures it rather than assuming it).
- **The owner's server becomes security-relevant.** It holds the tailnet's membership; without Tailnet Lock its
  compromise lets an attacker add nodes. Section 4.6 limits what a rogue node can reach; hardening the server is
  part of P1, not an afterthought.
- **Metadata** (device names, public addresses, connection times) now sits on the owner's server instead of a third
  party's. With hosted Tailscale it would sit at Tailscale, plus client connection logs (research 2.1).
- **A strict network can force relayed traffic,** adding latency the owner will hear. The status screen reports
  "relayed" so this is visible, not mysterious.
- **Headscale is pre-1.0** and changes its config and CLI between releases (its next release requires clients at
  1.84 or newer; both boxes run 1.104.1). Upgrades are read-the-changelog operator work.
- **The interim address check (P1) and the target device-session (P3)** must not both stay forever; P3 retires the
  address fallback for panels with a loopback helper.
- **This design does not cover the phone as a remote Zoe client** (W12.1 in the evolution plan); the tailnet built
  here is the same path that work can reuse.
