---
type: design
title: Panel remote access - the touch panel on another Wi-Fi, linked back to Zoe over a private mesh
date: 2026-10-16
status: design only; nothing built, nothing changed on the panel or the Jetson. Every phase is lab-proven on a lab node before the live panel, and each Zoe-side code change ships flag-dark.
evidence: docs/research/panel-remote-access-2026-10.md (field research with sources tagged verified/secondary, plus the code survey and the read-only live state)
builds_on: docs/knowledge/mac-virtual-panel.md (the one remote panel that exists - over the Cloudflare tunnel), services/zoe-data/panel_principal.py (#2068), routers/panel_auth.py + routers/panel_provision.py (device tokens, QR pairing), docs/architecture/identity-presence-engine.md (panel = room), docs/architecture/samantha-evolution-plan.md W12 (the direct link)
---

# Panel remote access

> Owner ask (2026-10-16): *"Is there a way we can connect the touch panel back to Zoe over the internet? We'd
> need the ability to connect the panel to a new Wi-Fi and something like Tailscale to connect over a different
> network."*
>
> Sources, tags and the code survey are in [panel-remote-access-2026-10.md](../research/panel-remote-access-2026-10.md)
> (cited below as "research N"). Addresses, network names and host names below are placeholders:
> `<zoe-host>.<tailnet>.ts.net` for Zoe's tailnet name, `<home-LAN-IP>` for today's address, `<panel-id>` for the
> registered panel.

## 0. Answer first

- **Yes, and half of it is already on the boxes.** Tailscale 1.104.1 is installed, enabled and running on both
  the Jetson and the panel (installed 2026-10-08), but neither has ever logged in (`BackendState: NeedsLogin`,
  no tailnet, no peers) [live, read-only, 2026-10-16]. So phase 1 is a policy file, two logins, one TLS name and
  a config switch, not an install.
- **Recommended stack: Tailscale (hosted control plane, free plan) for the link + balena wifi-connect for Wi-Fi
  onboarding.**
  Audio, transcripts and pages travel end-to-end WireGuard-encrypted between the panel and the Jetson; Tailscale's
  servers see device metadata, never content (research 2.1). Runner-up for the link: **Headscale** on a small
  VPS, if the owner does not want a third-party coordination server (it moves the same metadata to a box we run,
  and adds a box to patch). Runner-up for Wi-Fi: **Comitup**.
- **Not the Cloudflare tunnel for the panel.** It works for the Mac virtual panel today, but Cloudflare
  terminates TLS at its edge, so every voice clip, reply and transcript is plaintext on a third party's machine
  (research 2.4). That breaks "nothing leaves the box unless opted in" for the most private stream Zoe has. Keep
  the tunnel for what it does now (owner browser, Omnigent).
- **Zoe must change in four places:** the panel's URL and TLS name; how the kiosk BROWSER proves it is the panel
  (today only its source IP does, which a tunnel changes); a real "can't reach Zoe" screen instead of a boot loop;
  and an away mode that turns off the features that assume the panel is in the house (AirPlay speaker, room
  presence, "turn off the lights" meaning this house).
- **One security fix is needed before the panel ever joins a foreign network**, independent of everything else:
  the panel listens on every interface with no firewall (SSH with password login on, the voice daemon's
  `/activate` with no checks, the wake agent, AirPlay) [live]. On a friend's or hotel Wi-Fi, any device on that
  network could start a recording that the daemon then sends to Zoe with the panel's device token. Phase 1 closes
  this with a host firewall that admits only the tailnet and loopback.

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
| Jetson listeners | `0.0.0.0`: 80/443 (zoe-ui nginx in Docker), **8000 (zoe-data, host-native)**, 8123 (Home Assistant); llama and Serena are loopback | live `ss -ltn` |
| Voice daemon identity | `X-Device-Token` on every call; an unbound token resolves to guest with `panel_id` stamped | `routers/panel_auth.py:_resolve_device_token_user`; `auth.py:get_current_user` |
| Kiosk browser identity | a guest session only. It is "the panel" when its address equals the registered `panels.ip_address` (`panel_principal.is_panel_guest` path 2; `skybridge` display identity; the push socket's guest policy uses `panels.allow_guest`) | `services/zoe-data/panel_principal.py`, `routers/skybridge.py:58-62`, `main.py:_resolve_subscribable_panel` |
| Browser origins | the push socket and CORS admit `ALLOWED_ORIGINS` + this host's own LAN IP origin + `ZOE_ALLOWED_WS_ORIGINS`. A new host name for the kiosk page is a new origin and is refused until added (the 2026-07-03 dead-kiosk incident class) | `main.py:_allowed_browser_origins`, `_self_lan_origins` |
| Pairing | `/api/panels/provision/*` mints a kiosk device token after a signed-in member confirms a 6-character code on their phone (QR in `provision.html`) | `routers/panel_provision.py` |
| Legacy Wi-Fi portal | `provision-server.py --mode wifi-setup` (AP on 192.168.4.1, nmcli scan/connect, `portal.html`). Not installed on the live panel | `scripts/setup/touchscreen/README.md` |
| The one remote panel | the Mac virtual panel reaches zoe-data through the Cloudflare tunnel + an Access service token; `panel_principal` refuses any request carrying a Cloudflare header as "not the panel" | `docs/knowledge/mac-virtual-panel.md`; `panel_principal._via_public_tunnel` |

## 3. The recommended stack and why

### 3.1 The link: Tailscale (runner-up Headscale)

| Criterion (weight) | Tailscale (hosted) | Headscale (self-hosted control) | Plain WireGuard | Cloudflare Tunnel + Access | ZeroTier / Nebula / NetBird |
|---|---|---|---|---|---|
| Content privacy (must) | end-to-end WireGuard; relays (DERP) forward ciphertext | same data plane | same | **TLS ends at Cloudflare; content readable there** | end-to-end |
| What leaves the house | device names, public keys, public endpoints, who-talks-to-whom; each client also streams connection logs to Tailscale | the same, to our own VPS | nothing to a third party | every request, decrypted | coordination metadata (ZeroTier: to ZeroTier; Nebula/NetBird self-hosted: to our lighthouse/server) |
| Works behind NAT / CGNAT / hotel Wi-Fi | yes: hole punching, DERP over 443 as fallback | yes (embedded DERP or Tailscale's) | **no** without a port forward or a public relay; CGNAT at either end breaks it | yes (outbound only) | ZeroTier yes; Nebula needs a public lighthouse + relay; NetBird needs its server |
| Household setup burden | operator: one account, one policy file. Household: none | operator: a VPS, DNS, TLS, upgrades, backups | operator: keys, a VPS relay, firewall | operator: Access apps + service tokens (exists) | between Tailscale and Headscale |
| arm64 Debian/Ubuntu packages | official apt repo (installed on both boxes) | server is a Go binary; clients are the official Tailscale clients | in-kernel on the Pi; userspace on the Jetson | `cloudflared` arm64 (already running) | yes |
| HTTPS certificate for the panel | `tailscale cert` (Let's Encrypt for `<host>.<tailnet>.ts.net`) | no ts.net certs; bring our own name + ACME | our own | Cloudflare's | our own |
| Cost | free Personal plan: 6 users, unlimited user devices, 50 tagged devices, non-commercial (research 2.1) | a VPS (a few dollars a month) | a VPS if CGNAT | free plan | free tiers |

**Deciding reasons for Tailscale:** it is the only option that is content-private, needs nothing public at the
house, works from hotel/CGNAT networks, is already installed on both boxes, and gives the panel a real
certificate so TLS verification can finally be switched on (today both clients skip it). Its cost is metadata at
a third party, which the owner can remove later by pointing the same clients at Headscale (the clients are
identical; a re-login moves them).

**Headscale is the runner-up, not the pick,** because it needs a publicly reachable server. The house has none,
so it means renting a VPS: a new always-on machine to patch, back up and secure, outside the house, holding the
same metadata. Choose it if the owner decides no third-party coordination is acceptable (decision D1).

**Cloudflare stays where it is.** The owner's browser and Omnigent keep using it. The panel never does: content
privacy is the must-have row above, and the tunnel also has a 100-second idle cut and buffering caveats for
streamed NDJSON (mac-virtual-panel section 4).

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

## 4. What Zoe must change

### 4.1 The panel's address and TLS

- One name everywhere: `https://<zoe-host>.<tailnet>.ts.net` in the kiosk `config.json` `url` and the daemon's
  `ZOE_URL` (and Omi sync, if it runs on the panel). **The panel uses the tailnet name at home too.** One path means
  one identity (section 4.2) and one thing to test; on the home Wi-Fi Tailscale finds a direct LAN path, so the cost
  is WireGuard overhead on a LAN hop (measured in P1, section 6).
- Certificate: `tailscale cert <zoe-host>.<tailnet>.ts.net` on the Jetson, installed into the zoe-ui nginx as a
  second `server` block selected by SNI (the self-signed default block stays for `<home-LAN-IP>` and for anything
  else). Renewal: a systemd timer re-runs `tailscale cert` and reloads nginx (certs are 90-day Let's Encrypt).
  **Caveat (decision D5):** the name is published in Certificate Transparency logs (research 2.1); the tailnet name
  should not contain a family name.
- Then `VERIFY_SSL=true` on the daemon and drop `--ignore-certificate-errors` from the kiosk for that host - today's
  "trust any certificate" stops being needed.
- `ZOE_ALLOWED_WS_ORIGINS` gains `https://<zoe-host>.<tailnet>.ts.net`; without it the push socket refuses the
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

**Interim (P1):** set `panels.ip_address` to the panel's tailnet address. That is no weaker than today: a tailnet
source address is bound to the sending node's WireGuard key, so another device cannot forge it from outside the
tailnet, and the ACL (section 4.6) admits only the panel's tag to port 443 anyway (research 2.1). Lab test L4 checks
that nginx's `X-Real-IP` really is the tailnet address (Docker's published port DNATs and keeps the source for
non-loopback traffic; if the Jetson's Docker uses the userland proxy for this path, every client would appear as the
bridge gateway and P1 stops until that is fixed).

**Target (P3): a kiosk session minted from the device token.** The address stops mattering:

1. The panel agent (already on the Pi, loopback) holds the kiosk's device token. The browser never sees the token.
2. At boot and on every 401, the kiosk page asks the agent on loopback for a session; the agent calls a new
   `POST /api/panels/{panel_id}/kiosk-session` with `X-Device-Token` and hands the browser only the returned
   session id. The agent's endpoint applies the Mac `/activate` guard (loopback `Host`, `Origin` must be the Zoe
   origin) so no other page can mint one.
3. zoe-data issues a GUEST session stamped with `panel_id` (same shape `_resolve_device_token_user` returns for an
   unbound device). `get_current_user` returns it with `panel_id`, so `panel_principal` path 1 covers the kiosk too,
   and `skybridge` accepts "a session stamped with this `panel_id`" as the device check.
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

Budget: conversational guidance puts one-way delay under ~150 ms for interactive speech (research 3). Zoe's
time-to-first-audio is already ~2 s on the LAN, so the network budget we set is **at most +300 ms median
time-to-first-audio at 100 ms RTT and a 5 Mbit/s uplink**; beyond that the panel says it is on a slow link.

The upload is the leg that scales with the network. Two options, decided by measurement (P3, cell L9), not now:
(a) turn on the existing flag-dark under-speech upload (`ZOE_STT_STREAM_UPLOAD`, 0.32 s batches while the person
speaks), which hides most of the upload inside the speech itself; (b) Opus at ~24 kbit/s for the upload, about a
tenth of raw PCM (research 3). (a) is code we already have; (b) changes what Moonshine receives and needs the
replay gate on the owner's real-voice corpus before any use. Both are voice-path changes: replay-gated, flag-dark.

### 4.4 "Can't reach Zoe" on the panel

Replace the boot loop (section 2) with a local status screen the kiosk shows whenever Zoe does not answer:

- Served by the panel agent on loopback (no network needed). It shows three lights, in order: **Wi-Fi**
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

- **ACL before login.** A new tailnet allows everything by default. The policy file is written first, then the
  boxes join. Shape (tag names are placeholders; JSON-with-comments as Tailscale accepts):

  ```
  {
    "tagOwners": { "tag:zoe-server": ["autogroup:admin"], "tag:zoe-panel": ["autogroup:admin"] },
    "grants": [
      // panel -> Zoe: HTTPS only. Never 8000 (zoe-data direct), 8123 (Home Assistant), 22.
      { "src": ["tag:zoe-panel"],   "dst": ["tag:zoe-server"], "ip": ["tcp:443"] },
      // owner devices -> Zoe and panel: HTTPS + SSH for maintenance.
      { "src": ["autogroup:admin"], "dst": ["tag:zoe-server"], "ip": ["tcp:443", "tcp:22"] },
      { "src": ["autogroup:admin"], "dst": ["tag:zoe-panel"],  "ip": ["tcp:22"] }
    ]
  }
  ```
  Nothing grants the panel access to anything but 443 on the Jetson; nothing grants the Jetson access to the panel.
- **Tagged devices, device approval on.** Both boxes join with a one-time, pre-tagged auth key; tagged devices
  have key expiry off by default (no silent 180-day logout of the panel), and device approval (all plans) means a
  new device waits for the owner (research 2.1). Join keys expire in at most 90 days and are single-use. Tailnet
  Lock (needs at least two signing devices) is optional (decision D3).
- **Never `tailscale serve` or Funnel for Zoe.** `serve` proxies from loopback, and `auth._is_internal_request`
  trusts any loopback TCP peer on zoe-data: a `serve` rule pointed at `127.0.0.1:8000` would make every tailnet
  caller an internal service that may assert any identity. Pointed at nginx it would hide the panel's address
  behind the Docker gateway. nginx terminates the tailnet TLS itself (section 4.1).
- **Panel host firewall (P1, before the panel travels).** nftables on the Pi: accept loopback, established
  traffic, `tailscale0`, DHCP and the onboarding hotspot while it is up; drop all other inbound. That closes 22/7000/7777/8765
  to whatever network the panel is on. SSH then works only over the tailnet; turn off password login.
  At home the AirPlay port needs the home LAN, so the home profile allows 7000 + mDNS + nqptp from the home subnet
  only (the same `location_mode` switch).
- **Lost or stolen panel (runbook, P1):** remove the node in the Tailscale admin (link gone immediately), revoke
  its device token (`DELETE /api/panels/{id}/token/{token_id}`; the voice daemon gets 401 at once), set
  `panels.is_active = 0` (address path off). With P3, also revoke its kiosk sessions. The panel's disk holds a device
  token and Tailscale node key, both now dead; no member credential lives on it.
- **Key rotation:** device tokens get an `expires_at` (the API already supports it) and are re-issued by the pairing
  flow; Tailscale node keys rotate by re-auth; the auth key used to join is single-use and expires.

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
| Wi-Fi works, link home does not (Zoe's computer off, home internet down, UDP blocked) | the "can't reach home" light; Tailscale falls back to its DERP relays by itself, so a strict network usually still works, only slower (still end-to-end encrypted). If relayed latency hurts, the Jetson can be a Tailscale peer relay (research 2.1) |
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
SD-card clone for Wi-Fi onboarding), then the live panel in a planned window. Each test has a negative control that
must fail.

### P1 - the link (operator steps + small config/code)

Steps: write the ACL; enable HTTPS certificates and MagicDNS; join the Jetson (`tag:zoe-server`) and a lab node
(`tag:zoe-panel`) with pre-tagged single-use keys; `tailscale cert` + nginx SNI block + renew timer;
`ZOE_ALLOWED_WS_ORIGINS`; the panel firewall; then the live panel: tag, firewall, URLs, `VERIFY_SSL=true`,
`panels.ip_address` = its tailnet address.

| Cell | Test | Negative control |
|---|---|---|
| L1 | lab node reaches `https://<zoe-host>.<tailnet>.ts.net/health` with verification ON | same URL to `:8000`, `:8123`, `:22` from the panel tag times out (ACL) |
| L2 | an owner device reaches 443 and SSH | an untagged non-admin device reaches nothing |
| L3 | certificate validates for the ts.net name | `curl` to the LAN IP with verification ON still fails (self-signed default block unchanged) |
| L4 | nginx log `X-Real-IP` = the node's tailnet address; `is_panel_guest` true for the registered panel | the owner laptop on the tailnet is NOT the panel; a request with a `CF-Ray` header is still refused |
| L5 | push socket connects from the ts.net origin | without the `ZOE_ALLOWED_WS_ORIGINS` entry it is refused (proves the entry is load-bearing) |
| L6 | at home, panel over the tailnet: time-to-first-audio median within +20 ms of the LAN path on 20 live turns | the replay gate is unaffected (config only) and is run anyway: `voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock` |
| L7 | lost-panel drill: remove node + revoke token -> link gone, daemon 401 | before the drill the same calls succeed |
| L8 | from another device on the panel's Wi-Fi, ports 22/7000/7777/8765 are closed | the same scan before the firewall shows them open |

### P2 - Wi-Fi onboarding (wifi-connect on the panel image)

| Cell | Test | Negative control |
|---|---|---|
| W1 | no known network: hotspot + QR on screen within 90 s; iPhone and Android cameras join from the QR | with a known network in range, no hotspot appears |
| W2 | captive page opens by itself on both phones; network picked, password typed, panel connects and the lights go green | wrong password: panel says so, hotspot returns |
| W3 | hidden SSID via "Other network" | - |
| W4 | captive-portal network detected and explained | a normal network is not flagged as a portal |
| W5 | hotspot password differs on each start | a capture of an old QR no longer joins |
| W6 | strict network (UDP blocked): link still comes up via relay; L6 timing recorded as "relayed" | - |

### P3 - remote polish (each a flag-dark PR)

- Status screen + spoken line (section 4.4). Test: unplug the Jetson's network: the panel shows "can't reach home"
  within 30 s and recovers on its own. Negative: no message while Zoe is up.
- Kiosk session from the device token (section 4.2) + panel agent endpoint. Negative: a page from another origin
  calling the agent gets 403; a revoked device token cannot mint.
- Away mode (section 4.5). Negative: at home, nothing is gated.
- Voice WAN study, cell **L9**: `tc netem` on the lab node at RTT 50/100/200 ms, uplink 1/5/20 Mbit/s, 1% loss,
  20 turns each; record upload, first-audio and total. Decide (a) under-speech upload vs (b) Opus from this table;
  either one replay-gated before deploy. Negative: 0 ms netem reproduces the LAN numbers.

## 7. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | Tailscale (hosted, free; metadata at Tailscale) or Headscale (self-hosted on a rented VPS; metadata on our VPS; one more machine) | Tailscale now; Headscale stays possible later without touching Zoe |
| D2 | Which devices join: Jetson + panel only, or also the owner's phone/laptop (needed for SSH and the owner's own remote browser over the tailnet instead of Cloudflare) | Jetson + panel + owner devices; no other household devices until asked |
| D3 | Who may approve a new device / issue join keys | owner only (`autogroup:admin`); Tailnet Lock off for now (it adds a signing step on every new device) |
| D4 | How the panel knows it is at home: a list of home Wi-Fi networks (kept on the panel, never in the repo) or "Zoe's LAN address answers directly" | the home-network list; the direct-answer check as a cross-check |
| D5 | The tailnet name (published in certificate logs) | a neutral name, no family name |
| D6 | What an away panel may do: voice + chat + lists + memory (yes); home control (ask "at home?" / off); announcements and the brief (off unless asked); AirPlay (not possible) | as listed |
| D7 | Remote voice quality floor: accept the network budget (+300 ms at 100 ms RTT) or require P3's upload work before the panel travels | travel after P1+P2; P3 measured on real trips |

## 8. What this does not decide, and the risks

- **Tailscale is a dependency for the panel at home too.** If `tailscaled` breaks, the panel loses Zoe at home.
  Mitigation: the status screen offers the LAN URL as a fallback when the panel is on a home network (D4) and the
  tailnet link is down; the self-signed LAN block stays.
- **Metadata at Tailscale** (device names, public IPs, connection times, client connection logs) is the price of
  D1. Device names should be neutral. Whether client log streaming can be turned off was not confirmed (research 2.1).
- **A strict network can force relayed traffic,** adding latency the owner will hear. The status screen reports
  "relayed" so this is visible, not mysterious.
- **The interim address check (P1) and the target device-session (P3)** must not both stay forever; P3 retires the
  address fallback for panels with an agent.
- **This design does not cover the phone as a remote Zoe client** (W12.1 in the evolution plan); the tailnet built
  here is the same path that work can reuse.
