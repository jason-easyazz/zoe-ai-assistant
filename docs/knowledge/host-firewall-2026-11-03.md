---
type: Runbook
title: Host firewall and LAN exposure, round 2 (2026-11-03) - every listener's decision and the owner's sudo checklist
description: The Jetson still had no host firewall on 2026-11-03. This lists every listening port measured that day with a keep/close decision and the evidence, ships one reviewed nftables table (scripts/setup/jetson/firewall/zoe-host-fw.nft, kernel-checked on the box), and gives the owner's exact sudo steps with a dead-man rollback, plus the two container steps (Multica recreate, SMB drop retired) and the zoe-data unit cleanup.
tags: [security, lan, firewall, nftables, docker, runbook, owner-steps]
timestamp: 2026-11-03T21:00:00+08:00
---

# Host firewall and LAN exposure, round 2 (2026-11-03)

Supersedes steps 3a and 3b of [lan-exposure-hardening-2026-10-19.md](lan-exposure-hardening-2026-10-19.md); its panel steps
(3c-3g) still stand. **No agent applied anything here.** Every live change is an owner step with a verify and a rollback.
Host-specific values (addresses, the share, the device names) are in the owner's private copy of this checklist, not in this
public repo. `<box>` below is the Jetson's LAN address.

## 1. Every listener and its decision (measured with `ss -ltnup` and `docker ps`, read-only)

| port | what | bound | decision | evidence |
|---|---|---|---|---|
| tcp 22 | sshd | all | **keep** | owner admin from the LAN |
| tcp 80, 443 | nginx (`zoe-ui`, docker) | all | **keep** | the household's door: browsers, the panel, phones |
| tcp 8123 | Home Assistant (docker) | all | **keep** | the companion app on phones talks to it directly |
| udp 5353 (+ avahi's ephemeral udp) | avahi mDNS | all | **keep** | discovery for Home Assistant and AirPlay |
| Music Assistant 8095 / 8097 / AirPlay ports | MA, host network (when running) | all | **keep** | speakers fetch streams from the box; AirPlay ports are not fixed. Closing 8095 alone is an owner option (nginx already serves MA's UI behind the sign-in gate) |
| tcp 8000 | zoe-data (host uvicorn) | `0.0.0.0` | **close to the LAN; keep the bind** | a week of access lines: every caller is `127.0.0.1` or a `zoe-network` container. See section 4 |
| tcp 3579, 3582 | Flue brain, Flue Telegram bot | `*` | **close** | callers are loopback only; the loopback drop-ins (2026-10-19 runbook 3c) are still not installed |
| tcp 8002 | zoe-auth (docker) | all | **close** | nginx reaches it as `zoe-auth:8002`; zoe-auth's own log shows no LAN client in 7 days |
| tcp 8080, 3000 | Multica board API / web (docker) | all | **close**, and recreate | compose already publishes them on `127.0.0.1` (#2130); the running containers predate that. #2134 puts the board behind Zoe's sign-in |
| tcp 139, 445 | SMB drop (`zoe-smb-drop`, hand-run `docker run`, no compose file) | all | **close, then retire the container** | it holds one file copied in months ago; nothing in the repo or the household uses it |
| tcp 111, udp 111 | rpcbind | all | **close** | nothing in Zoe uses NFS/RPC |
| tcp 631 | CUPS | all | **close** | no printing through this box (re-open if the household starts) |
| tcp 9121 | shared Serena MCP | loopback + a docker bridge address | **close to the LAN** | agents reach it on loopback / the bridge |
| 5432, 8007, 4416, 6767, 9443, 10201, 11434, 11436, 53 | Postgres, HA bridge, potoken, Omnigent, nginx admin, router, llama-servers, resolver | `127.0.0.1` | nothing to do | already loopback-only |

IPv6 matters: both LAN interfaces hold global IPv6 addresses, and docker's userland proxy answers the published ports on
`[::]` from the host itself, so those connections arrive on INPUT, not FORWARD. The ruleset closes every published port on
both hooks.

## 2. The ruleset

`scripts/setup/jetson/firewall/zoe-host-fw.nft` - its own table `inet zoe_host_fw`, never `flush ruleset`:

- `input` (policy accept): accept loopback, `docker0` and `br-*`; drop the closed tcp/udp sets.
- `forward` (policy accept): accept from `docker0` / `br-*`; drop the closed docker-published ports (IPv4 DNAT).
- No `counter`, no `ct`: this Tegra kernel rejects both. The file passed `sudo nft -c -f` on the box (check mode only).
- Why not policy drop: Music Assistant's AirPlay and stream ports are not fixed; a default-drop would silently cut household
  audio. Every listener instead has the written decision above.

Pinned by `tests/unit/test_host_firewall_ruleset.py` (household ports never closed, accepts before drops, policy accept, no
`counter`/`ct`, with negative controls).

## 3. Owner checklist (exact commands)

Do it at a quiet time. Keep a second SSH session open the whole way.

**3.1 Load with a 10-minute dead-man rollback**

```sh
sudo install -d -m 0755 /etc/zoe-host-fw
sudo install -m 0644 ~/assistant/scripts/setup/jetson/firewall/zoe-host-fw.nft /etc/zoe-host-fw/ruleset.nft
sudo nft -c -f /etc/zoe-host-fw/ruleset.nft
sudo systemd-run --unit=zoe-host-fw-rollback --on-active=600 /usr/sbin/nft delete table inet zoe_host_fw
sudo nft -f /etc/zoe-host-fw/ruleset.nft
sudo nft list table inet zoe_host_fw
```

**3.2 Verify (inside the 10 minutes)**

- On the box: `curl -sf http://127.0.0.1:8000/health` and `curl -sk https://127.0.0.1/health` both answer.
- From another LAN device: `https://<box>/` loads and you can sign in; `curl -m3 http://<box>:8000/health`,
  `curl -m3 http://<box>:8002/`, `curl -m3 http://<box>:8080/health` all time out.
- The panel: "Hey Zoe, what time is it?" answers, and a light toggles.
- A phone: the Home Assistant app connects; music plays to a speaker.

**3.3 Keep it** (cancel the rollback): `sudo systemctl stop zoe-host-fw-rollback.timer`

**3.4 After a day of normal use, load it at every boot**

```sh
sudo install -m 0644 ~/assistant/scripts/setup/jetson/firewall/zoe-host-fw.service /etc/systemd/system/zoe-host-fw.service
sudo systemctl daemon-reload && sudo systemctl enable zoe-host-fw
```

**Rollback, any time:** `sudo nft delete table inet zoe_host_fw` (plus `sudo systemctl disable --now zoe-host-fw` once
enabled). Nothing else on the box is changed by this table.

**3.5 Multica on loopback** (the compose change from #2130 reaches the containers only when they are recreated):

```sh
cd ~/assistant
docker compose -f docker-compose.modules.yml up -d --no-deps --force-recreate multica-backend
docker compose -f docker-compose.modules.yml up -d --no-deps multica-web
ss -ltn '( sport = :8080 or sport = :3000 )'     # 127.0.0.1 only
```

Full context and the `MULTICA_DEV_CODE` requirement: [multica-loopback.md](multica-loopback.md).

**3.6 Retire the SMB drop** (hand-run, no compose file, so there is nothing for the repo to change):

```sh
docker stop zoe-smb-drop && docker update --restart=no zoe-smb-drop
# after a week with nobody missing it:
docker rm zoe-smb-drop
```

Rollback: `docker update --restart=unless-stopped zoe-smb-drop && docker start zoe-smb-drop`. The table above closes 139/445
to the LAN either way.

**3.7 Optional:** `sudo systemctl disable --now rpcbind rpcbind.socket` (and CUPS if installed) removes the services the
table closes.

## 4. zoe-data stays on `0.0.0.0:8000` (decided, not deferred)

Callers of `:8000` over a week of access lines: loopback (the host's own agents, scripts, the Flue sidecars) and
`zoe-network` containers (nginx, zoe-auth, the Multica web app, and one that has since been recreated) arriving over the docker
bridge as `host.docker.internal` = the host gateway; Home Assistant's voice ingress uses the same path. A loopback bind would cut every container caller, and uvicorn binds one
address. The panel, omi-sync and phones all use nginx on 443. So the bind stays and the firewall closes `:8000` to the LAN.

## 5. zoe-data's unit must not load the retired agent's env file

The live (host-local) unit carried `EnvironmentFile=-/home/zoe/.hermes/.env`. The tracked template never had it. That file
held a revoked Telegram token which overrode the valid one in every zoe-data alert for a week (fixed in code too: the ops
alert now prefers the bot's own token file). What zoe-data gets from that file today, by name: the same values its own
`.env` already sets (`POSTGRES_URL`, `MULTICA_*`, `API_SERVER_KEY`, `HERMES_API_KEY`), four cloud LLM API keys the code only
strips from subprocess environments (`pi_executor` reads the OpenRouter key from that file directly when it needs it), and
`ZOE_CHEAP_PR_AGENT_*` (only the greploop cheap-runner MCP tool), plus `ZOE_DEVICE_TOKEN`, which zoe-data does not read (the
voice-regression probe does, from its own unit). Nothing on zoe-data's live path needs it. The full retirement is the
2026-10-18 "Hermes env retirement" owner steps in [open-problems.md](open-problems.md) (`migrate_hermes_env.py`, which moves
the names other units still need, then removes the line from three units). The zoe-data line alone, safe today:

```sh
sed -i '\#^EnvironmentFile=-/home/zoe/.hermes/.env$#d' ~/.config/systemd/user/zoe-data.service
systemctl --user daemon-reload
systemctl --user restart zoe-data      # a quiet moment; then poll http://127.0.0.1:8000/health
```

Pinned: `tests/unit/test_host_firewall_ruleset.py` fails if a tracked zoe-data unit file ever loads `.hermes`.
