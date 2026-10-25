---
type: Record
title: Systemd unit drift - classification and host steps (2026-10-25)
description: Every difference between the installed user units and the tracked templates on 2026-10-25, classified (a) capture in the repo, (b) stale install to refresh, (c) host-only value to keep; the retired-unit and .bak cleanup done on the host; the owner commands for the class-b refresh.
tags: [systemd, unit-drift, hygiene, retired-units, operator-steps]
timestamp: 2026-10-25T12:00:00+08:00
---

# Systemd unit drift - 2026-10-25

Tools (read-only): `python3 scripts/maintenance/unit_inventory.py`, `python3 scripts/maintenance/unit_drift_check.py`.
Host-side cleanup: `scripts/maintenance/unit_drift_cleanup.sh` (dry-run by default, `--execute` to apply).
Values below are directive NAMES only; the drift tool hashes values on purpose.

## Counts

| | before | after |
|---|---|---|
| installed units | 52 = 41 tracked + 11 retired | 43 = 41 tracked + 2 retired (`zoe-dreaming.{service,timer}`) |
| `.bak` / `.disabled` cruft | 20 | 0 (moved to `~/.zoe/unit-backups/2026-10-25/`, none deleted) |
| drift findings | 2 DIFFERS, 11 LIVE_ONLY, 8 MISSING (5 units) | unchanged until the class-b refresh below |

## Classification of the drifted units

| unit | difference | class | action |
|---|---|---|---|
| `serena-mcp` | live `no-swap.conf` sets `MemorySwapMax=0` (template: 2G) | **a** intentional (swap forbidden, #1409 lesson) | captured as `serena-mcp.service.d/no-swap.conf`; the unit keeps 2G on purpose (`test_agent_mcp_memory_bounds.py` pins the member cap) |
| `zoe-data` | live drop-in `40-memory-tuning.conf` (`MALLOC_ARENA_MAX`, `MALLOC_TRIM_THRESHOLD_`) | **a** | captured as `zoe-data.service.d/40-memory-tuning.conf` |
| `zoe-data` | live `After=network.target` | **a** | added to the template |
| `zoe-data` | live `memory.conf` / `priority.conf` (`MemorySwapMax=0`, `MemoryLow=2G`, `CPUWeight`, `IOWeight`) | intentional, equal to the template's inline values | keep (voice-stack protection); no change |
| `zoe-data` | live `Environment=PATH`, `ZOE_DATA_DB` | **c** host values | stay host-only |
| `zoe-data` | live `HERMES_FAST_PATH`, `OPENCLAW_AGENT_TIMEOUT_S`, `PI_AGENT_LLM_TIMEOUT` | **b** stale knobs of retired lanes | drop on the next zoe-data edit/restart |
| `zoe-data` | live `10-sync-zoe-self.conf` (`ExecStartPre=-sync_zoe_self.sh`, syncs the Hermes SOUL + OpenClaw workspace) | **b** stale hook for retired agents | remove drop-in on the next restart; script and `/api/system/agent-sync` untouched |
| `zoe-data` | template lists `EnvironmentFile=%h/assistant/.env` first, live omits it (file exists) | **b** | refresh deliberately: loading it changes the live env; owner decision at a deploy |
| `kokoro-tts` | `TimeoutStartSec` 120 vs 300; `After=` lacks `llama-server.service` | **b** (no runtime effect: Type=simple) | install `70-start-timeout.conf` (daemon-reload only); template copy for `After` |
| `kokoro-tts` | live `ZOE_KOKORO_BACKEND=pytorch` (`backend.conf`) | **b** dead switch: the sidecar has no backend switch any more | delete `backend.conf` when next restarting |
| `kokoro-tts` | live `PYTHONPATH` (system site-packages of py3.10) | **c** keep until the venv is proven not to need it | leave; known harmless residue |
| `flue-zoe-brain-2x`, `flue-zoe-telegram` | missing `SuccessExitStatus=143`, `ZOE_LISTEN_HOST`, `NODE_OPTIONS --import` | **b** template changes never applied (`50-exit-143.conf`, `zoe-loopback.conf`) | install both drop-ins + template, restart under the brain-window rule; `happy-eyeballs.conf` on telegram is superseded by `zoe-loopback.conf` (sorts later, carries the same flags) and can be removed after |

## Retired units (11) - verified, then 9 removed

All 11 are `inactive`, `disabled` or `static` (a service started only by its own timer), linked from no
`*.wants`, and named by no other unit's `Requires/Wants/After/...` line (continuations joined). Each was copied
to `~/.zoe/unit-backups/2026-10-25/` (with its `.d` dir) before removal, then `daemon-reload`.

Removed: `bonsai-server`, `flue-zoe-brain` (+ `.d`), `llama-server-e2b-fallback`, `zoe-12b-postboot`,
`zoe-brain-boot-check`, `zoe-keepwarm.{service,timer}`, `zoe-purge-presence.{service,timer}`.

**Kept on purpose:** `zoe-dreaming.{service,timer}` - inactive and disabled, but `scripts/night/install_night_window.sh --uninstall`
re-enables `zoe-dreaming.timer` as the night window's rollback. Remove them together with that rollback path.
`hermes-agent` and `openclaw-gateway` are still tracked (not in the 11); inactive and disabled.

## `.bak` / `.disabled` files (20), archived

Dates are file mtimes. `functiongemma-router.service.bak-20260925` (2026-09-25), `zoe-backup.service.bak-20260926`
(2026-09-26), `.zoe-voice-regression.service.bak-20260727` (2026-07-27), `llama-server.service.bak-*` x10
(2026-04-04 to 2026-07-08), `llama-server-{12b,e2b,e4b}.service.bak` and `llama-server-12b-deepbrain.service.disabled`
(2026-06-20), `hermes-agent.service.bak-routing-cleanup-20260526_140650` (2026-05-26),
`openclaw-gateway.service.bak-*` x2 (2026-05-17, 2026-05-26).

## Owner steps (not done by this PR; each restarts or edits a live unit)

```bash
cd ~/assistant   # after the PR has merged and the checkout is fast-forwarded
T=scripts/setup/systemd; U=~/.config/systemd/user
# kokoro: consistency only, no restart needed
cp $T/kokoro-tts.service.d/70-start-timeout.conf $U/kokoro-tts.service.d/ && systemctl --user daemon-reload
# flue sidecars: exit-143 + loopback bind; restart OUTSIDE a landing probe and the night window
for s in flue-zoe-brain-2x flue-zoe-telegram; do
  mkdir -p $U/$s.service.d; cp $T/$s.service.d/*.conf $U/$s.service.d/; done
systemctl --user daemon-reload     # then restart one at a time and poll :3579 / the Telegram watchdog
# zoe-data stale bits (next deploy restart): rm $U/zoe-data.service.d/10-sync-zoe-self.conf, drop the three
# retired Environment= lines from the main unit, daemon-reload, restart, poll /readyz
python3 scripts/maintenance/unit_drift_check.py    # expect no MISSING/DIFFERS
```
