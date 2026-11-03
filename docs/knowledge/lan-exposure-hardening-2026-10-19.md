---
type: Runbook
title: LAN exposure hardening 2026-10-19 - Jetson ports, panel agent, panel host
description: Who legitimately calls zoe-data :8000, the Flue brain :3579 and the Telegram bot :3582 from off the box (measured), what the repo now ships (loopback drop-ins for the two Flue sidecars, the panel agent on :8765 brought into the repo with an access guard), and the exact owner steps for everything that is a system security setting - the Jetson nftables INPUT rules, DOCKER-USER rules, the panel's sshd, token file modes, sudo, TLS and kiosk flags.
tags: [security, lan, firewall, nftables, docker, panel, sshd, runbook]
timestamp: 2026-10-19T02:00:00+08:00
---

# LAN exposure hardening (2026-10-19)

Master to-do items 6 and 8 ("panel-side leftovers", "zoe-data / brain / Telegram reachable on the LAN past nginx"). Nothing
here was applied to a live host by an agent: every live change below is an **owner step** with its own verify and rollback.

## 1. Who calls what from off the box (measured 2026-10-19)

| port | service | bind today | off-box callers found | verdict |
|---|---|---|---|---|
| 8000 | zoe-data (host uvicorn) | `0.0.0.0` | none from the LAN: two days of `~/.zoe-logs` access lines show only `127.0.0.1` and docker addresses (`172.18.0.x`: nginx, Home Assistant, Multica). The panel (`VOICE_ROUTE_MODE=direct`, `ZOE_URL=https://192.168.1.218`), omi-sync and the panel agent all go through nginx on 443. | must stay reachable from the docker bridges (see 1a), so: host firewall |
| 3579 | Flue brain (`flue-zoe-brain-2x`) | `*` | none: zoe-data uses `http://127.0.0.1:3579`; no compose service names it | loopback (drop-in, section 2) |
| 3582 | Flue Telegram bot | `*` | none: zoe-data's notify uses `http://127.0.0.1:3582`; the bot long-polls Telegram outbound | loopback (drop-in, section 2) |

**1a. Why zoe-data is NOT moved to `--host 127.0.0.1`.** nginx (the `zoe-ui` container) reaches zoe-data as
`host.docker.internal:8000`, which is `host-gateway` = `172.17.0.1`, arriving on a docker bridge - as do Home Assistant
(`ZOE_HA_VOICE_INGRESS_URL`) and the Multica board webhook. A loopback bind would cut all three, and uvicorn binds exactly one
address. The firewall rule in 3a keeps `:8000` to loopback + docker instead, with no restart and no voice-path change.

**1b. Also noted.** The panel's `.env.voice` names `HA_BRIDGE_URL=http://192.168.1.218:8007`; the bridge is published on
`127.0.0.1:8007` only, so that URL is dead - harmless while `VOICE_ROUTE_MODE=direct`, broken if anyone sets `ha_bridge`.

## 2. What the repo ships (PR fix/security-cf-token-panel-lan)

- **`scripts/lib/node_listen_host.mjs`** + drop-ins **`scripts/setup/systemd/flue-zoe-brain-2x.service.d/zoe-loopback.conf`** and
  **`flue-zoe-telegram.service.d/zoe-loopback.conf`**. Flue 2.1.1's generated entry passes no hostname and reads no `HOST`
  variable, so the bind cannot be configured; the preload (`NODE_OPTIONS=--import=...`, `ZOE_LISTEN_HOST=127.0.0.1`) rewrites
  the TCP listen. Pinned by `tests/unit/test_node_listen_host.py` (real Node, with a no-preload negative control).
- **`scripts/setup/touchscreen/zoe-panel-agent.py`** (+ `systemd/zoe-panel-agent.service`): the panel's screen agent on `:8765`,
  previously only on the Pi, now in the repo with an access guard - the kiosk on loopback gets every route, the Zoe server
  only `/wake` `/reload` `/brightness` (the calls zoe-data makes), anyone else `GET /health`. Pinned by
  `tests/unit/test_panel_agent_access.py`.

## 3. Owner steps

### 3a. Jetson host firewall (nftables INPUT) - covers :8000, :3579, :3582 at once

Its own table; never `flush ruleset` (Docker and tailscale keep theirs). Policy stays `accept`: it drops only the
host-native service ports from anything that is not loopback or a docker bridge, so SSH, nginx (docker-published, which goes
through FORWARD, not INPUT) and everything else are untouched.

```sh
sudo mkdir -p /etc/zoe-host-fw
sudo tee /etc/zoe-host-fw/ruleset.nft >/dev/null <<'EOF'
table inet zoe_host_fw {
  chain input {
    type filter hook input priority filter; policy accept;
    iif "lo" accept
    iifname "docker0" accept
    iifname "br-*" accept
    tcp dport { 8000, 3579, 3582, 111, 631 } drop comment "host-native services: loopback + docker only"
    udp dport { 111, 631 } drop
  }
}
EOF
sudo nft -c -f /etc/zoe-host-fw/ruleset.nft && sudo nft -f /etc/zoe-host-fw/ruleset.nft
```

(Syntax-checked on the Jetson with `nft -c` on 2026-10-19. No `counter`: this Tegra kernel rejects it -
`Could not process rule: No such file or directory`.)

Verify: from another LAN machine `curl -m3 http://192.168.1.218:8000/health` times out, while `curl -sk https://192.168.1.218/health`
(nginx) and the panel ("Hey Zoe, what time is it?") still work; on the box `curl -sf http://127.0.0.1:8000/health`. Persist only
after a day of normal use (a oneshot unit running `nft -f` at boot, like the panel's `zoe-panel-firewall.service`).
Rollback: `sudo nft delete table inet zoe_host_fw`. (111 = rpcbind, 631 = CUPS: nothing in Zoe uses either; drop them from the
sets if the household prints from the LAN through this box.)

### 3b. Jetson DOCKER-USER (docker-published ports)

Published ports bypass INPUT. Keep 80/443 (nginx), decide the rest. Recommended - zoe-auth `:8002` is reached through nginx, and
the Multica board (`:8080` API, `:3000` web) is an operator tool:

```sh
for ifc in enP8p1s0 wlP7p1s0; do for p in 8002 8080 3000; do
  sudo iptables -I DOCKER-USER -i "$ifc" -p tcp -m conntrack --ctorigdstport "$p" --ctdir ORIGINAL -j DROP
done; done
```

Leave 8123 (Home Assistant: the companion app), 139/445 (the SMB drop) as they are unless you decide otherwise. Verify from a
LAN machine: `curl -m3 http://192.168.1.218:8002/` times out, `https://192.168.1.218/` still loads. Rollback: the same loop with
`-D` instead of `-I`. Docker does not persist DOCKER-USER rules; re-apply at boot with a oneshot unit ordered `After=docker.service`.

### 3c. Flue sidecars on loopback (the drop-ins in section 2)

Brain (restarts the live brain: a quiet window, then the replay gate):

```sh
cp ~/assistant/scripts/setup/systemd/flue-zoe-brain-2x.service.d/zoe-loopback.conf ~/.config/systemd/user/flue-zoe-brain-2x.service.d/
cp ~/assistant/scripts/setup/systemd/flue-zoe-telegram.service.d/zoe-loopback.conf ~/.config/systemd/user/flue-zoe-telegram.service.d/
systemctl --user daemon-reload
systemctl --user restart flue-zoe-telegram flue-zoe-brain-2x
ss -ltn '( sport = :3579 or sport = :3582 )'     # both 127.0.0.1 only
curl -sf http://127.0.0.1:3579/health && curl -sf http://127.0.0.1:3582/health
```

Rollback: remove the two files, `daemon-reload`, restart. (3a already closes both ports to the LAN; this is defence in depth.)

### 3d. Panel agent (`:8765`) - install the guarded copy on the Pi

The live file is the pre-guard source (sha256 `a7534b08...`). After merge:

```sh
scp ~/assistant/scripts/setup/touchscreen/zoe-panel-agent.py zoe-pi:/tmp/zoe-panel-agent.py
ssh zoe-pi 'cp ~/.zoe-panel-agent/zoe-panel-agent.py ~/.zoe-panel-agent/zoe-panel-agent.py.bak-$(date +%Y%m%d) &&
            install -m 0755 /tmp/zoe-panel-agent.py ~/.zoe-panel-agent/zoe-panel-agent.py &&
            systemctl --user restart zoe-panel-agent && sleep 2 && journalctl --user -u zoe-panel-agent -n 5 --no-pager'
```

Verify: the log names the Zoe server address under "access:"; from the Jetson `curl -s -X POST http://192.168.1.61:8765/wake`
answers `{"ok": true...}`; from any OTHER LAN machine the same call answers 403; display settings still apply within a second.
Rollback: restore the `.bak` copy and restart, or set `Environment=AGENT_GUARD=off` in the unit.

### 3e. Panel SSH password login (system setting)

Effective config today: `passwordauthentication yes` (unset = yes; `sshd_config` `Include`s `sshd_config.d/*.conf` at line 12,
so a drop-in there wins). Keep the current SSH session open while doing this:

```sh
ssh zoe-pi 'echo "PasswordAuthentication no" | sudo tee /etc/ssh/sshd_config.d/10-zoe-no-password.conf &&
            sudo sshd -t && sudo sshd -T | grep -E "^passwordauthentication" && sudo systemctl reload ssh'
```

Test from a second terminal: `ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password pi@192.168.1.61` must fail
with `Permission denied (publickey)`, and `ssh zoe-pi true` must still work. Rollback: delete the drop-in, reload.

### 3f. Panel host hygiene (recorded; each is the owner's call)

- `~/.zoe-voice/.env.voice` (holds `DEVICE_TOKEN`) is mode 644 with five older copies (`.env.voice.bak*`) that may hold
  earlier tokens: `chmod 600 ~/.zoe-voice/.env.voice` and delete the `.bak*` copies (the home directory is 700, which limits
  this to `pi` and root today). If an old copy holds a token that is still issued, revoke it.
- `pi` has `NOPASSWD: ALL` (`/etc/sudoers.d`): anything running as `pi` (the voice daemon, the kiosk) is root. Narrow it to the
  commands the deploy scripts need, or require a password.
- The voice daemon runs with `VERIFY_SSL=false` (the box uses a self-signed certificate): pin the certificate instead
  (`VERIFY_SSL=/path/to/zoe.crt`) so a LAN impostor cannot collect the device token.
- The kiosk (`/opt/TouchKio/start-kiosk.sh`) runs Chromium with `--ignore-certificate-errors --use-fake-ui-for-media-stream
  --no-sandbox`: the first has the same certificate fix; the second auto-grants the microphone/camera to any page the kiosk
  opens; the third removes the renderer sandbox. The debug port 9222 is loopback-only (verified).
- The panel firewall (`zoe_panel_fw`) admits the whole home `/24` on every port: narrowing it to the Jetson (and the owner's
  machines) for 22/7777/8765 is the next step after 3d.

### 3g. Device tokens never expire

All 9 `device_tokens` rows have `expires_at` NULL (new tokens get 365 days since 2026-10-18; nothing on the panel calls
`POST /api/panels/device-token/renew` yet). Owner: revoke every row no panel uses (`panel_id` + `name` in the settings page),
then rotate the live panel's token (it was echoed into transcripts on 2026-10-17). Backfill an expiry on the survivor only
once the voice daemon renews it (agent follow-up, replay-gated; open problems ledger).
