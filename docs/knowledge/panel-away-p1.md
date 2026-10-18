---
type: Runbook
title: Panel away P1 - kiosk credential, "can't reach Zoe" gate, panel inbound firewall
description: What PR feature/panel-away-p1 built for taking the touch panel away from home (design docs/architecture/panel-remote-access.md P1), every flag and file, the exact operator commands to install the gate, turn on the kiosk credential, apply and roll back the panel firewall, the recorded exposures (password SSH, the unauthenticated /activate), what is unverified until the lab cells run, and what is deferred (away mode).
tags: [panel, kiosk, tailscale, headscale, firewall, nftables, security, runbook]
timestamp: 2026-10-16T12:00:00+08:00
---

# Panel away P1

Design: `docs/architecture/panel-remote-access.md` (PR #2080, docs only, landing separately) sections 4.2, 4.4, 4.6. The link home
(Tailscale or Headscale) does not matter to anything here; joining a tailnet is the owner's step and nothing below does it.

## What shipped (all flag-dark or opt-in; the LAN panel is unchanged until an operator step below)

| Piece | Where | Default |
|---|---|---|
| **Kiosk credential.** The kiosk BROWSER proves it is the panel with an HttpOnly, Secure, SameSite=Strict, host-only cookie (`zoe_panel_kiosk`) minted from the panel's device token, instead of by its source address. Accepted by `panel_principal.is_panel_guest` (path 1b: household lists) and the skybridge display identity. Dies with the device token (revoke = dead on the next request) and with the panel row (`is_active`, `allow_guest`). Never over the Cloudflare tunnel. Confers "this is the panel" on a GUEST only. | `services/zoe-data/panel_kiosk_credential.py`, `routers/panel_kiosk.py` (`POST /api/panels/PANEL/kiosk-ticket`, `POST /api/panels/kiosk-enter`) | `ZOE_PANEL_KIOSK_CREDENTIAL` off: both routes 404 and a presented cookie is ignored |
| **Skybridge address check hardened.** The display identity's address check now uses `panel_principal.trusted_client_ip` (X-Real-IP only from our proxy) and refuses the Cloudflare tunnel. Before, a LAN device talking to :8000 directly could write `X-Real-IP` itself and get the panel's owner name. The kiosk via nginx is unaffected. | `routers/skybridge.py` | always on |
| **Status gate.** A loopback page the kiosk browser starts on: three lights (Wi-Fi, link home, Zoe), one plain sentence for whatever is red, retries every 3 s forever, opens Zoe when it answers. Does the credential hand-off when enabled. | `scripts/setup/touchscreen/zoe-kiosk-gate.py`, `systemd/zoe-kiosk-gate.service`, `install_kiosk_gate.sh` | not installed; `ZOE_KIOSK_CREDENTIAL` off |
| **Launcher on the gate.** With `gate_url` in `/opt/TouchKio/config.json`, `start-kiosk.sh` starts Chromium on the gate: no ping loop, no exit 1. Without it the legacy path is byte-for-byte as before. | `scripts/setup/touchscreen/start-kiosk.sh` | no `gate_url` |
| **Panel inbound firewall.** nftables table `inet zoe_panel_fw`, its own table (never `flush ruleset`). Admits loopback, replies, `tailscale0`, the home subnet captured at install, DHCP, IPv6 ND, UDP 41641. Drops everything else, so 22 / 7000 / 7777 / 8765 are closed on any other network. Outbound untouched. Dry run, a rollback timer, boot persistence only after `--confirm`. | `scripts/setup/touchscreen/firewall/` | not applied |

Signing key: `ZOE_PANEL_KIOSK_SECRET`, else `ZOE_INTERNAL_TOKEN` (set on the live box), else a per-process random (credentials
then die on a zoe-data restart and the gate re-mints on the kiosk's next start). The key is derived, so the other handoff
flows' tokens never verify as a kiosk credential. TTLs: `ZOE_PANEL_KIOSK_TICKET_TTL_S` (60), `ZOE_PANEL_KIOSK_CREDENTIAL_TTL_S`
(30 days; revocation is the device token, not the TTL).

## Operator commands

Run from a checkout of the MERGED commit (a worktree, never the live checkout). The panel alias is `zoe-pi`.

### Panel firewall (apply after review; rollback is automatic unless confirmed)

```bash
scp -r scripts/setup/touchscreen/firewall zoe-pi:zoe-panel-firewall
ssh zoe-pi 'sudo ~/zoe-panel-firewall/install_panel_firewall.sh --dry-run'      # prints the rules + the detected home subnet; changes nothing
ssh zoe-pi 'sudo ~/zoe-panel-firewall/install_panel_firewall.sh --apply --rollback-after 300'
# within 5 minutes, from a NEW session (proves new SSH from home still works) and from the Jetson:
ssh zoe-pi 'sudo nft list table inet zoe_panel_fw | head -3'
curl -s http://zoe-pi:7777/health                                            # the voice daemon still answers from home
ssh zoe-pi 'sudo ~/zoe-panel-firewall/install_panel_firewall.sh --confirm'     # cancels the timer, enables it at boot
```

Roll back at any time: `ssh zoe-pi 'sudo ~/zoe-panel-firewall/install_panel_firewall.sh --rollback'`. Without the script:
`ssh zoe-pi 'sudo nft delete table inet zoe_panel_fw; sudo systemctl disable zoe-panel-firewall'`. If SSH is lost, wait for the
timer (unconfirmed) or reboot the panel (an unconfirmed apply is not enabled at boot). `--status` shows the table, the unit and the
timer. Lab cell L8 (design section 6) - a port scan from ANOTHER network shows 22/7000/7777/8765 closed - needs the panel on a
non-home network; from home they stay open by design (the home subnet is admitted).

### Status gate

```bash
scp -r scripts/setup/touchscreen zoe-pi:zoe-touchscreen
ssh zoe-pi 'sudo ~/zoe-touchscreen/install_kiosk_gate.sh'           # dry run
ssh zoe-pi 'sudo ~/zoe-touchscreen/install_kiosk_gate.sh --apply'   # backs up launcher + config.json, enables the gate
ssh zoe-pi 'sudo systemctl restart zoe-kiosk'                        # the browser now starts on the gate
```

Rollback: `ssh zoe-pi 'sudo ~/zoe-touchscreen/install_kiosk_gate.sh --rollback && sudo systemctl restart zoe-kiosk'`. Note this
also installs the TRACKED `start-kiosk.sh`, which carries the README's hardening deltas (DevTools loopback-only).

### Kiosk credential (after the gate)

1. Issue the gate its own token (so revoking it never deafens the voice daemon): admin `POST /api/panels/zoe-touch-pi/token` with
   `{"name": "kiosk-gate", "role": "kiosk", "scopes": ["kiosk"]}`; put it in `/etc/zoe-kiosk-gate.env` as
   `ZOE_KIOSK_DEVICE_TOKEN=...` (root, mode 0600) with `ZOE_KIOSK_CREDENTIAL=1`. Without its own token the gate uses the voice
   daemon's `DEVICE_TOKEN`.
2. zoe-data: `ZOE_PANEL_KIOSK_CREDENTIAL=1` in `services/zoe-data/.env`, restart, poll `/health`.
3. `ssh zoe-pi 'sudo systemctl restart zoe-kiosk-gate zoe-kiosk'`. Verify: `~/.zoe-logs/` shows
   `panel_kiosk: kiosk credential set for panel zoe-touch-pi`; DevTools (`ssh -L 9222:127.0.0.1:9222 zoe-pi`) shows the cookie as
   HttpOnly. Negative control: with the zoe-data flag off the gate logs `kiosk ticket not issued (HTTP 404)` and the kiosk opens
   as today.

Lost panel: revoke the gate's token (`DELETE /api/panels/zoe-touch-pi/token/TOKEN_ID`) - the credential is dead on the next request.

## Recorded exposures (fixed by the firewall away from home; still open on the home LAN)

- **SSH password login is on** (`PasswordAuthentication` unset = yes, `sshd_config.d` empty) on `0.0.0.0:22`. Key login via `zoe-pi`
  works. Fix once the tailnet SSH path is proven: a drop-in `PasswordAuthentication no` + `sshd -t` + reload. Ledger:
  [open-problems.md](open-problems.md).
- **`/activate` on :7777** (fixed in code 2026-10-18, fix/voice-path-identity; live once the panel daemon is redeployed): the POSTs
  answer only a loopback peer with a loopback `Host` and the Zoe page's `Origin` (`_activate_request_allowed`); `GET /health` still
  answers the LAN. Before, any device that reached it started a recording the daemon sent to Zoe with the panel's device token.
- **:8765 (screen-wake agent)** answers on all interfaces with no auth; same firewall scope. Its source is not in this repo and the
  Jetson calls it over the LAN, so it cannot simply bind loopback. Ledger line 2026-10-18.

## Unverified until the lab cells run

- Chromium storing a SameSite=Strict cookie from the response to a cross-site top-level form POST (the gate page is
  `http://127.0.0.1`), and keeping a Secure cookie on the self-signed LAN origin under `--ignore-certificate-errors`. The spec
  says both work; check the cookie in DevTools after the first enable. If it is missing the kiosk is a plain guest kiosk, exactly
  today's behaviour.
- `nft -c` passed on the Jetson in a user namespace apart from the `ct`/`counter` expressions its kernel lacks; the panel's
  kernel has `CONFIG_NFT_CT=m` (read-only check). `--dry-run` on the panel is the real syntax check.

## Deferred (follow-ups on the ledger)

- **Away mode (D6)**: voice/chat/lists/memory unchanged; home control asks or refuses; AirPlay/presence off. Needs the panel to
  report `location_mode` (D4 home-network list on the panel) and the intent path to read it per turn.
- The gate's spoken line (espeak-ng once), the Wi-Fi onboarding QR (P2), and taking over the screen when Zoe drops MID-session
  (today only boot and every kiosk restart go through the gate; mid-session the estate's own banner applies).
