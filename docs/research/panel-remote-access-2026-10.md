---
type: research
title: Panel remote access - headless Wi-Fi onboarding and a private link home, field research and Zoe survey (2026-10-16)
date: 2026-10-16
status: research-only; nothing built. Design is docs/architecture/panel-remote-access.md.
description: Research record behind the panel-remote-access design. Covers headless Wi-Fi onboarding for a keyboard-less Debian 12 / NetworkManager kiosk (balena wifi-connect, Comitup, RaspAP, a plain nmcli hotspot with captive-portal detection, Improv Wi-Fi over BLE, WPS, Raspberry Pi Connect, the Wi-Fi QR format); private links back to a home server (Tailscale, Headscale, plain WireGuard, Cloudflare Tunnel + Access, Nebula, ZeroTier, NetBird) compared on privacy, what leaves the house, NAT behaviour, packages, cost and latency; the voice latency budget (ITU-T G.114) and Opus bitrates; and the read-only survey of Zoe's code and the live panel and Jetson.
---

# Panel remote access: research (2026-10-16)

Owner ask: *"Is there a way we can connect the touch panel back to Zoe over the internet? We'd need the ability
to connect the panel to a new Wi-Fi and something like Tailscale to connect over a different network."*

The design built on this record is [panel-remote-access.md](../architecture/panel-remote-access.md). Nothing on the
panel or the Jetson was changed to write it; the live reads were read-only (`ss`, `nmcli`, `tailscale status`,
`sshd -T`, config files). No household addresses, network names or people appear here.

**Evidence tags.** **[verified]** = read from the primary page (official docs, repository, release API, standard),
or from the code or the live box, in this session. **[secondary]** = a search snippet, blog or forum only.
**[unverified]** = not confirmed; reasoning or memory. GitHub release dates below are 2026 unless stated.

## 1. Headless Wi-Fi onboarding

Target: a Pi 5 kiosk, Debian 12 bookworm, NetworkManager active, Chromium in kiosk mode, no keyboard. The
person must finish on their phone.

### 1.1 balena wifi-connect

- https://github.com/balena-os/wifi-connect - Apache-2.0, Rust [verified].
- Latest release v4.11.90, 2026-10-02; releases are frequent (looks automated); not archived; 213 open issues;
  last push 2026-10-02 [verified: releases page, GitHub API].
- Requires NetworkManager as the active manager and saves the result as a NetworkManager connection [verified: README].
- Flow: starts an access point (default SSID "WiFi Connect", **optional WPA2 passphrase**) with a captive portal;
  the user picks a network and types its passphrase; if the join fails the AP comes back up [verified: README].
- The README's install script is written for Raspbian/Debian Stretch and balenaOS; Bookworm is not mentioned
  [verified: README]. No Bookworm confirmation found.
- No QR code built in; SSID and passphrase are launch options, so the host can render the standard Wi-Fi QR for
  its own AP [verified: README].
- WPA2-Enterprise: not in the portal; a balena staff reply implies it is not supported
  [secondary: https://forums.balena.io/t/wi-fi-wpa-enterprise-with-eap-tls/290630].

### 1.2 Comitup

- https://github.com/davesteele/comitup - GPL-2.0 [verified: GitHub API].
- Latest tag v1.47.1 (no GitHub Releases); last push 2026-08-18 - active, slower cadence [verified: tags API].
- Debian package (`apt install comitup`), Python web service, needs NetworkManager + systemd. On Bookworm it needs
  an updated `python3-networkmanager` from the Comitup repository [verified: https://davesteele.github.io/comitup/].
- Flow: on boot tries known networks; if none connect, starts an AP `comitup-<nnn>`; the comitup-web page at
  `10.41.0.1` lists networks; the device is also `comitup-<nnn>.local`. Remembers several upstream networks and
  falls back to the hotspot only when none succeed [verified: README and docs page].
- A "Locate" button blinks the board LED (useless on a kiosk). No QR built in. The AP's security mode is not
  documented on the pages read; WPA-Enterprise not covered [verified, absence only].

### 1.3 RaspAP

- https://github.com/RaspAP/raspap-webgui - GPL-3.0; 3.5.8 on 2026-10-02; very active [verified].
- PHP + hostapd + dnsmasq + lighttpd; Raspberry Pi OS on Debian 11/12/13 [verified: README].
- A full wireless-router admin UI (routed/bridged AP, WireGuard, OpenVPN, Tailscale, ad blocking); "captive portal
  integration" listed without detail; NetworkManager not mentioned [verified: README]. Built around hostapd/dnsmasq,
  so it overlaps with NetworkManager on a Bookworm kiosk [unverified: inference]. Too heavy and the wrong shape.

### 1.4 A plain nmcli hotspot with a captive page

- `nmcli device wifi hotspot [ifname] [con-name] [ssid] [band] [channel] [password]`; with no password nmcli
  generates one; WPA if the driver supports it [verified: https://networkmanager.dev/docs/api/latest/nmcli.html].
- Shared mode (`ipv4.method shared`) turns on forwarding + masquerade and starts dnsmasq for DHCP/DNS on
  `10.42.0.1/24`; extra options in `/etc/NetworkManager/dnsmasq-shared.d/`
  [secondary: https://fedoramagazine.org/internet-connection-sharing-networkmanager/].
- Captive-portal detection: Android/ChromeOS fetch `connectivitycheck.gstatic.com/generate_204` and expect 204;
  Apple fetches `captive.apple.com/hotspot-detect.html` and expects "Success"; anything else opens the sign-in
  sheet [secondary: https://isc.sans.edu/diary/Captive%20Portal%20Detection/33172]. RFC 8910 lets DHCP option 114
  announce the portal URI [verified: https://www.rfc-editor.org/rfc/rfc8910.html]; iOS 16+ honours it
  [secondary]. The wildcard-DNS + port-80 redirect recipe is [unverified] and would need a lab test.
- This is what wifi-connect and Comitup already package, so a hand-rolled version only makes sense if both fail.
- 802.1X (PEAP/MSCHAPv2) profiles can be created with nmcli; on Bookworm the password sits in plaintext in the
  `.nmconnection` file (mode 600) [secondary: https://www.jeffgeerling.com/blog/2023/nmcli-wifi-on-raspberry-pi-os-12-bookworm/].

### 1.5 BLE provisioning: Improv Wi-Fi

- Open standard over BLE or serial: the client sends credentials, the device joins and returns a URL
  [verified: https://www.improv-wifi.com/]. The site's browser demos work in Chrome/Edge **but not on iOS**
  [verified: improv-wifi.com].
- Sending credentials needs an "authorized" state (usually a physical button) and times out after ~1 minute; the
  spec defines no BLE pairing or link encryption, so credentials are not protected by the transport
  [verified: https://www.improv-wifi.com/ble/].
- SDKs (Apache-2.0): C++, BLE-JS, serial-JS, Android, iOS; no Python and no Linux device-side SDK in the org
  [verified: https://github.com/improv-wifi]. A Rust crate `improv-wifi` 0.1.3 (GPL-3.0-or-later, BlueZ over D-Bus,
  pluggable Wi-Fi backend - a NetworkManager backend would have to be written) [verified: https://docs.rs/improv-wifi].
- For a panel with a screen, camera-QR + captive portal works on any phone with no app; BLE needs an app on iOS.

### 1.6 The Wi-Fi QR code

- Format `WIFI:T:WPA;S:<ssid>;P:<password>;;` (fields T, S, P, H hidden, and EAP fields E/A/I/PH2); escape
  `\ ; , : "` [verified: https://github.com/zxing/zxing/wiki/Barcode-Contents].
- The iPhone Camera shows a "Join network" banner (Scan QR Codes is on by default)
  [secondary: https://www.idownloadblog.com/2017/06/09/ios-11-router-qr-code/]; Android Camera / Lens shows
  "Connect to network" [secondary: https://www.digitaltrends.com/phones/how-to-share-wi-fi-settings-in-android/].
- Joining the panel's AP drops the phone's internet; a captive-portal response makes the phone open the setup page.

### 1.7 WPS

- NetworkManager has `802-11-wireless-security.wps-method` (since 1.10: auto, PBC, PIN)
  [secondary: https://networkmanager.dev/docs/api/1.40/settings-802-11-wireless-security.html].
- Needs the venue's router button (not realistic away from home). PIN mode is brute-forceable in ~11,000 tries and
  Pixie Dust recovers it offline on weak chipsets [secondary: https://thehacker.recipes/radio/wi-fi/wps/]. Not a fit.

### 1.8 Raspberry Pi Connect

- Remote screen share + shell for the Pi itself, direct with relay fallback; Bookworm+; screen share needs
  Wayland; bound to the board serial; relays keep "the metadata required to operate Connect"
  [verified: https://www.raspberrypi.com/documentation/services/connect.html]. Free for individuals [secondary].
- Remote admin of the Pi, not a link for app traffic to the home server and not Wi-Fi onboarding.

## 2. The link back home

### 2.1 Tailscale

- Client BSD-3-Clause; coordination server closed [verified: https://github.com/tailscale/tailscale; server:
  secondary]. Latest v1.104.1, 2026-10-07 [verified: release API] - the version on both boxes (section 4).
- Packages: apt repositories for Debian/Ubuntu and Raspbian bookworm/trixie, arm64 static tarballs
  [verified: https://pkgs.tailscale.com/stable/].
- **Personal plan:** $0, up to 6 users, unlimited user devices, 50 tagged resources included, ACLs, MagicDNS,
  peer-to-peer; non-commercial use only [verified: https://tailscale.com/pricing].
- **Node key expiry** default 180 days (1-180); **tagged devices have key expiry disabled by default**
  [verified: https://tailscale.com/kb/1028/key-expiry].
- **Device approval** on all plans: a new device cannot send or receive until an admin approves it; a pre-approved
  auth key skips that [verified: https://tailscale.com/kb/1099/device-approval].
- **Auth keys:** one-off, reusable, ephemeral, pre-approved, tagged; expiry 1-90 days (default 90); stolen
  reusable keys are dangerous [verified: https://tailscale.com/kb/1085/auth-keys].
- **Tailnet Lock** (Personal and Enterprise) stops a compromised coordination server adding nodes; needs 2-20
  signing nodes [verified: https://tailscale.com/kb/1226/tailnet-lock].
- **Policy file:** HuJSON with `grants` (preferred) and `acls`, `tagOwners`, `groups`, `hosts`
  [verified: https://tailscale.com/kb/1337/policy-syntax].
- **HTTPS certificates:** `tailscale cert` issues Let's Encrypt certificates for `<machine>.<tailnet>.ts.net` once
  MagicDNS and HTTPS are enabled; **names are published in Certificate Transparency logs**; keys stay local;
  90-day certificates, renewed by the operator unless a tool does it [verified: https://tailscale.com/kb/1153/enabling-https].
- **`tailscale serve`** shares a local service in the tailnet with tailscaled terminating HTTPS; Funnel is the
  separate public option [verified: https://tailscale.com/kb/1242/tailscale-serve].
- **Connection types:** every connection starts relayed via DERP and upgrades to direct, then to a peer relay,
  staying on DERP only if both fail; all three are end-to-end WireGuard; DERP cannot decrypt
  [verified: https://tailscale.com/kb/1257/connection-types, https://tailscale.com/kb/1232/derp-servers]. Hard NAT
  on both sides usually prevents direct paths [verified]; CGNAT/blocked UDP likewise [secondary]. Relayed is slower;
  `tailscale ping` / `tailscale status` show `direct` / `relay` / `peer-relay` [verified].
- **Peer relays** (1.86+): a tailnet device with an open UDP port relays for others, generally lower latency than
  DERP [verified: https://tailscale.com/kb/1591/peer-relays]; plan availability not stated [unverified].
- **What Tailscale sees:** public keys and connection metadata (which nodes talk, public IPs), never content
  [secondary: https://tailscale.com/security]; each client streams logs (including connection open/close) to
  `log.tailscale.com` [verified: https://tailscale.com/kb/1011/log-in-to-tailscale]; whether that can be disabled
  was not confirmed [unverified].
- **Source addresses are bound to keys:** a tailnet peer's traffic is accepted only from the tailnet IP assigned to
  its node key, so another device cannot forge the panel's tailnet address [unverified: standard WireGuard
  cryptokey routing, not re-read for this record; the design's lab cell L4 checks the address Zoe actually sees].

### 2.2 Headscale

- https://github.com/juanfont/headscale - BSD-3-Clause; v0.29.5, 2026-10-13; pre-1.0; unaffiliated, single tailnet;
  maintainers do not support running it behind reverse proxies or in containers [verified: README, release API].
- Features: ACLs and grants, MagicDNS, embedded DERP, pre-auth keys, tags, OIDC, subnet routers, Serve
  [verified: https://headscale.net/stable/about/features/]. Not listed: `tailscale cert`, Tailnet Lock, key expiry
  [verified, absence only]. `tailscale cert` relies on Tailscale publishing DNS records under `ts.net`, which a
  Headscale server cannot do [secondary].
- Needs a publicly reachable server (VPS or forwarded port) with a domain and TLS; the official clients log in to it
  with `--login-server` [unverified: widely documented, not fetched]. It moves the metadata to our VPS; it does not
  remove it.

### 2.3 Plain WireGuard

- No NAT traversal; `PersistentKeepalive` (25 s suggested) keeps a mapping open; one side needs a reachable
  endpoint [verified: https://www.wireguard.com/quickstart/]. A house behind CGNAT therefore needs a VPS hub that
  relays all traffic (encrypted) [unverified: reasoning]. The Jetson's kernel has no in-kernel WireGuard (section 4).

### 2.4 Cloudflare Tunnel + Access

- `cloudflared` makes outbound-only connections; no inbound port or public IP [verified:
  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/].
- **Cloudflare terminates TLS at its edge for HTTP(S), so URLs, headers, cookies and bodies are readable there**
  [secondary]. For the panel that means voice clips, replies and transcripts.
- WebSockets on all plans; idle connections are closed after an unstated period, so heartbeat; edge restarts end
  sockets [verified: https://developers.cloudflare.com/network/websockets/]. Zoe's own notes record a 100 s idle cut
  (HTTP 524) and SSE drops cured by turning QUIC off [verified: docs/knowledge/mac-virtual-panel.md section 4].
- Service tokens: `CF-Access-Client-Id` / `CF-Access-Client-Secret` with a Service Auth policy, expiring and
  rotatable [verified: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/];
  50 service tokens per account [verified: https://developers.cloudflare.com/cloudflare-one/account-limits/].
- Always relayed through the nearest Cloudflare data centre, never peer-to-peer [verified: connect-networks page].
  The WARP client + private networks option exists but would put the panel's traffic through Cloudflare as well
  [verified: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/private-net/cloudflared/].

### 2.5 Nebula, ZeroTier, NetBird

- **Nebula** (https://github.com/slackhq/nebula): MIT; v1.11.2, 2026-09-22; needs at least one lighthouse with a
  routable fixed IP on UDP 4242; certificates from your own CA [verified: README, https://nebula.defined.net/docs/].
- **ZeroTier** (https://github.com/zerotier/ZeroTierOne): agent MPL-2.0; controller and `nonfree/` under the
  ZeroTier Source-Available License 1.0 (personal non-commercial / evaluation) [verified: repo + nonfree/LICENSE.md];
  1.16.2, 2026-05-28; free plan 10 devices, 1 network [verified: https://www.zerotier.com/pricing/].
- **NetBird** (https://github.com/netbirdio/netbird): BSD-3-Clause with AGPLv3 management/signal/relay; v0.80.0,
  2026-10-01; WireGuard mesh with ICE/STUN and a relay fallback; self-hosting needs a public VM with 80/443 TCP and
  3478 UDP [verified: README].
- None beats Tailscale here: Nebula and self-hosted NetBird need a public box (as Headscale does), ZeroTier's
  controller licence is source-available and its relays are slow on the free tier [verified: README].

### 2.6 Summary

| Option | Direct P2P | Needs a public server | Provider can read content | Household cost |
|---|---|---|---|---|
| Tailscale | yes, DERP fallback | no | no (metadata only) | free (Personal) |
| Headscale | yes | yes (VPS) | no (metadata on our VPS) | a VPS |
| WireGuard | only if one side reachable | yes behind CGNAT | no | a VPS behind CGNAT |
| Cloudflare Tunnel | no, via edge | no | **yes** | free |
| Nebula | yes | lighthouse | no | a lighthouse |
| ZeroTier | yes | no | no | free, 10 devices |
| NetBird | yes, relay fallback | self-host VM (or hosted) | no | a VM or hosted |

## 3. The voice budget

- ITU-T G.114 (05/2003): below 150 ms mouth-to-ear, most applications see "essentially transparent
  interactivity"; 400 ms one-way is the upper limit for general network planning
  [verified: T-REC-G.114-200305 PDF text]. A voice assistant is not a two-way call, so this is a guide.
- A far DERP region showed ~282 ms in a Tailscale example [secondary].
- Opus (RFC 6716): 6-510 kbit/s, 2.5-60 ms frames, 5-65.2 ms algorithmic delay, DTX and in-band FEC
  [verified: https://www.rfc-editor.org/rfc/rfc6716.html]; 10-24 kbit/s suggested for VoIP, wideband speech ~10
  kbit/s minimum [verified: https://wiki.xiph.org/index.php?title=Opus_Recommended_Settings]; high speech quality
  around 32 kbit/s [verified: https://wiki.xiph.org/OpusFAQ].
- Raw 16 kHz 16-bit mono PCM = 256 kbit/s; Zoe sends it base64-encoded in JSON (~341 kbit/s on the wire). Opus at
  ~24 kbit/s is about a tenth [verified: arithmetic]. Moonshine would then receive decoded audio, which is a
  voice-path change for the replay gate.

## 4. Zoe survey (code at origin/main d8b957d3e, live boxes read-only 2026-10-16)

**Live, panel** [verified]: Debian 12 bookworm, aarch64; NetworkManager active, connected on `wlan0`; Tailscale
1.104.1 from the vendor apt repo, `tailscaled` active and enabled, `BackendState: NeedsLogin`, 0 peers;
`tailscale0` present (unmanaged by NetworkManager); `dnsmasq-base` and BlueZ installed; no hostapd. Listening on all
interfaces: 22 (`PasswordAuthentication yes`), 7000 (AirPlay), 7777 (voice daemon), 8765 (screen-wake agent), UDP
5353/319/320/41641; DevTools 9222 bound to loopback despite the live launcher's
`--remote-debugging-address=0.0.0.0` / `--remote-allow-origins=*` flags. No nftables ruleset, no ufw. The kiosk
URL and the daemon's `ZOE_URL` are `https://` + a private LAN address; `VERIFY_SSL=false`. Units: `zoe-kiosk`,
`zoe-voice`, `zoe-panel-agent`, `zoe-airplay`, `nqptp`, `omi-sync`.

**Live, Jetson** [verified]: Tailscale 1.104.1 (installed 2026-10-08), `tailscaled` active and enabled,
`NeedsLogin`. `CONFIG_TUN=y`, `/dev/net/tun` present, `CONFIG_WIREGUARD` not set. Listening on all interfaces:
80/443 (zoe-ui nginx in Docker), 8000 (zoe-data, host-native), 8123 (Home Assistant); loopback-only: llama 11434,
Serena 9121. Docker uses the iptables backend. An earlier record (`agent-sessions-off-box-2026-10-04.md`) saw no
Tailscale on 2026-10-04; it was installed after that.

**Code** [verified]:

- `scripts/setup/zoe_voice_daemon.py`: `ZOE_URL` (default `https://zoe.local`), `DEVICE_TOKEN` sent as
  `X-Device-Token` on every call, `VERIFY_SSL`, Cloudflare Access headers attached only for a public https host
  (`_zoe_url_may_carry_access_secret`), turn upload = base64 WAV in JSON, flag-dark under-speech upload
  (`ZOE_STT_STREAM_UPLOAD`, 0.32 s batches), streaming reply over `/api/voice/turn_stream`.
- `scripts/setup/touchscreen/start-kiosk.sh`: `--ignore-certificate-errors`; pings the Zoe host 60 times then
  exits 1 for systemd to retry (no on-screen message); provision mode only when `provision-server.py` exists.
- `scripts/setup/touchscreen/provision-server.py`: legacy `wifi-setup` (AP at 192.168.4.1, nmcli scan/connect) and
  `provision` modes; README says the live panel does not use them.
- `services/zoe-data/panel_principal.py` (#2068): a guest is "the panel" by a device token with `panel_id`, or by
  `X-Real-IP` (trusted only from a proxy in `ZOE_TRUSTED_PROXY_NETS`, default loopback + 172.16/12) equal to an
  active `panels.ip_address` with `allow_guest`; any Cloudflare header = not the panel; `lan` mode uses Python
  `is_private`, which is False for the tailnet range `100.64.0.0/10` (checked on the box's Python 3.10).
- `routers/panel_auth.py`: device tokens (hashed, cached, `expires_at`, revocation endpoint); an unbound token
  resolves to guest with `panel_id`.
- `routers/panel_provision.py`: QR pairing; `/confirm` needs a real member session; poll secret per attempt;
  `ZOE_BASE_URL` builds the phone link.
- `routers/skybridge.py:58-62`: display identity only when `X-Real-IP` equals the registered panel address.
- `main.py`: `/ws/push` origin guard and CORS share `_allowed_browser_origins` = `ALLOWED_ORIGINS` + this host's
  LAN-IP origin + `ZOE_ALLOWED_WS_ORIGINS`; panel sockets accept a device token or resolve a session/guest policy.
- `auth.py:_is_internal_request`: any loopback TCP peer on zoe-data is an internal caller that may assert identity
  - the reason a `tailscale serve` proxy in front of zoe-data would be unsafe.
- `routers/voice_tts.py` (`livekit-token`): `livekit_available` needs a private client address or a TURN server.
- `docs/knowledge/panel-ttfa-breakdown-2026-09-28.md`: LAN upload median 99 ms; time-to-first-audio ~2 s.
- `docs/knowledge/mac-virtual-panel.md`: the one remote panel today, over the Cloudflare tunnel with an Access
  service token.
