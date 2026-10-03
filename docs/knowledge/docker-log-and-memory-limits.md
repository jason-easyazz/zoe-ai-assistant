---
type: Runbook
title: Docker log rotation and container memory limits (A8 + A9)
description: Operator recipes for the two root-level Docker items from the 2026-10-03 infrastructure audit — bounding json-file logs (daemon.json log-opts + per-container recreate) and giving every long-running container a memory/swap backstop sized from measured RSS, swap and VmHWM. Includes the measurements, the sizing rule, exact apply/verify/rollback commands, the two exemptions (Postgres, omnigent) and the traps (log-opts are not reloadable and only reach NEW containers; live-restore is off, so restarting dockerd bounces every container).
tags: [docker, logging, memory, cgroup, operations, runbook, infra-audit]
timestamp: 2026-10-03T23:00:00+08:00
---

# Docker log rotation and container memory limits (A8 + A9)

Source: the infrastructure audit, `docs/research/infra-data-config-2026-10-03.md` §2 D4/D5 and
§3 A8/A9 (branch `docs/research-infra-config`), and the 2026-10-03 memory profile
(`docs/knowledge/memory-pressure-profile-2026-10-03.md`, branch `docs/memory-profile-2026-10-03`). Nothing here has been
applied. Each step is a root or docker-group action for the operator. None of them touches the
voice-path units.

## Ground truth (read-only, 2026-10-03 22:5x AWST)

- Docker 29.1.3, cgroup v2, default log driver `json-file`.
- `/etc/docker/daemon.json` holds only `dns`, `runtimes` and `default-runtime`. There are no
  `log-opts`.
- `docker info`: `live-restore=false`.
- Every running container has `LogConfig = json-file map[]`, `Memory=0` and `MemorySwap=0`,
  i.e. unbounded logs and no limit.
- Container JSON logs total 946 MB (audit). The largest are `zoe-multica-backend` 621 MB after
  7 weeks, `homeassistant-mcp-bridge` 122 MB, `zoe-ui` 93 MB and `zoe-music-assistant` 68 MB.
  Disk is 23% used, so this is slow growth plus `docker logs` latency, not an emergency.
- `homeassistant`, `zoe-multica-web` and `zoe-smb-drop` carry **no compose labels**. They were
  created by `docker run`, so a compose-file change never reaches them. Only `daemon.json`
  defaults or `docker update` do.

## A8 — bound Docker logs

Two facts decide the procedure:

- **`log-opts` apply only to containers created after the change.** Docker documents that
  json-file settings do not apply to existing containers.
- **`log-opts` are not a reloadable daemon option.** `systemctl reload docker` does not pick them
  up, so `dockerd` has to restart. With `live-restore=false`, a dockerd restart stops every
  container, including Postgres, HA and MA, and `unless-stopped` brings them back. Do **not**
  restart docker just for this. MA restarts carry the YouTube Music re-auth risk described in
  [music-ytdlp-js-runtime.md](music-ytdlp-js-runtime.md).

So the change lands in two halves.

### 1. Set the daemon default now; it takes effect at the next natural dockerd restart

```bash
sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.bak-2026-10-03
sudo python3 - <<'EOF'
import json; p = "/etc/docker/daemon.json"
cfg = json.load(open(p))
cfg["log-driver"] = "json-file"
cfg["log-opts"] = {"max-size": "10m", "max-file": "3"}
json.dump(cfg, open(p, "w"), indent=2)
EOF
sudo dockerd --validate --config-file /etc/docker/daemon.json   # must print "configuration OK"
```

The next reboot or docker package upgrade restarts dockerd and arms the default. From then on,
every container created gets at most 3 × 10 MB of logs.

### 2. Existing containers pick it up when they are re-created

Batch this with a re-create that is already planned: the B0.12/A3 Postgres + HA-bridge recreate,
or the next MA re-create. Do not re-create containers only for logs.

- For a compose service, `docker compose -f <file> up -d --force-recreate <service>`. Once the
  daemon default is armed, it applies the default. Until then, the compose-level setting below
  does.
- For the three non-compose containers, the setting arrives only with the daemon default, on
  their next re-create.

Repo follow-up (a separate PR, so that it reaches containers independently of the daemon): add a
shared anchor to `docker-compose.yml`, `docker-compose.modules.yml` and
`modules/omnigent/docker-compose.module.yml`:

```yaml
x-logging: &default-logging
  driver: json-file
  options: {max-size: "10m", max-file: "3"}
# then, per service:
#   logging: *default-logging
```

**Verify:** `docker inspect -f '{{.HostConfig.LogConfig}}' <container>` →
`{json-file map[max-file:3 max-size:10m]}`, and
`sudo du -ch /var/lib/docker/containers/*/*-json.log | tail -1` stays bounded.

**Rollback:** `sudo cp -a /etc/docker/daemon.json.bak-2026-10-03 /etc/docker/daemon.json`, then
validate it. Like the change itself, the rollback takes effect at the next dockerd restart. A
container that was already re-created keeps its LogConfig until it is re-created again.

## A9 — memory backstops for containers

**Why:** no container has a limit. A leaking HA or MA grows until the *global* OOM killer picks a
victim on a box where MemAvailable is about 0.5 GB. A cgroup limit confines that kill to the
leaking container, and `unless-stopped` restarts it. These limits are **leak backstops, not
working limits**, which is the same doctrine as the user units in
`tests/unit/test_systemd_memory_protection.py`.

**Sizing rule (applied to the numbers below):**

- `--memory = max(3 × (anon + swap now), 1.5 × summed VmHWM, 256M)`, rounded up to a 256 MB step.
- `--memory-swap = 1.5 × --memory`. The swap allowance is half the limit, and every row's
  allowance is above that container's current swap.
- The 3× footprint term is the units' 3× ceiling rule. The 1.5 × VmHWM term keeps a container that
  has *already peaked* high (MA: 833 MB) clear of its own observed peak.

Measured from per-container cgroups (`memory.stat` `anon`, `memory.swap.current`), plus summed
`VmHWM` over each container's processes. All values are MB.

| container | anon | swap | Σ VmHWM | 3×(anon+swap) | 1.5×HWM | `--memory` | `--memory-swap` |
|---|---|---|---|---|---|---|---|
| homeassistant | 50 | 404 | 453 | 1,362 | 680 | **1536m** | **2304m** |
| zoe-music-assistant | 38 | 325 | 833 | 1,089 | 1,250 | **1280m** | **1920m** |
| zoe-ytmusic-potoken | 9 | 97 | 213 | 318 | 320 | **512m** | **768m** |
| zoe-auth | 9 | 42 | 83 | 153 | 125 | 256m | 384m |
| homeassistant-mcp-bridge | 21 | 26 | 51 | 141 | 77 | 256m | 384m |
| zoe-multica-web | 0 | 33 | 94 | 99 | 141 | 256m | 384m |
| zoe-multica-backend | 6 | 7 | 26 | 39 | 39 | 256m | 384m |
| zoe-cloudflared | 15 | 14 | 39 | 87 | 59 | 256m | 384m |
| zoe-ui | 3 | 8 | 47 | 33 | 71 | 256m | 384m |
| zoe-smb-drop | 0 | 2 | 25 | 6 | 38 | 256m | 384m |
| zoe-database | — | — | — | — | — | **none** (exempt) | — |
| zoe-omnigent | 24 | 214 | 256 (idle) | — | — | **measure first** | — |

**Exemptions:**

- **`zoe-database` (Postgres): no limit.** Its working set is about 150 MB against 128 MB
  `shared_buffers`. An OOM kill would be an outage of every data path plus crash recovery, which
  is the same blast-radius argument that exempts `zoe-data` from `MemoryMax`. Revisit it with A3's
  `pg_stat_statements` data.
- **`zoe-omnigent`: measure before capping.** The snapshot is idle, but this container runs agent
  sessions. Its `.mcp.json` launches **codebase-memory as the raw binary**, because the host
  wrapper `codebase_memory_capped.sh` needs a systemd user bus the container lacks. One host-side
  spawn of that binary was measured at about 1.3 GB (anon plus swap) on this repo. A cap sized
  from the idle 256 MB would kill sessions. Sample during one real session, then set
  `--memory` to 1.5 × the peak:
  `id=$(docker inspect -f '{{.Id}}' zoe-omnigent); while sleep 5; do cat /sys/fs/cgroup/system.slice/docker-$id.scope/memory.current; done`.
  Expect 2–3 GB. That limit is also the only bound on in-container codebase-memory.

**Apply** (`docker update` is live: no restart, and the change persists across container restarts
but **not** across a re-create). Pass both flags every time:

```bash
docker update --memory 1536m --memory-swap 2304m homeassistant
docker update --memory 1280m --memory-swap 1920m zoe-music-assistant
docker update --memory 512m  --memory-swap 768m  zoe-ytmusic-potoken
for c in zoe-auth homeassistant-mcp-bridge zoe-multica-web zoe-multica-backend \
         zoe-cloudflared zoe-ui zoe-smb-drop; do
  docker update --memory 256m --memory-swap 384m "$c"
done
```

To make the limits survive a re-create, the compose services need `mem_limit:` and
`memswap_limit:` with the same values. That belongs in the same repo follow-up as the logging
anchor. The three non-compose containers keep their `docker update` values until someone
re-creates them by hand.

**Verify:**

```bash
for c in $(docker ps --format '{{.Names}}'); do
  id=$(docker inspect -f '{{.Id}}' "$c"); d=/sys/fs/cgroup/system.slice/docker-$id.scope
  printf '%-26s max=%s swap.max=%s %s\n' "$c" "$(cat $d/memory.max)" "$(cat $d/memory.swap.max)" \
    "$(grep oom_kill $d/memory.events)"
done
```

**Watch for one week.** An `oom_kill` above 0 on any row means its limit is inside normal use:
raise that row and record why here. HA's first start after an upgrade is the known spike. If HA
is OOM-killed after an upgrade, raise it to 2048m/3072m.

**Rollback** (per container, live): `docker update --memory 8g --memory-swap -1 <container>`.
A limit above what the box can supply is functionally unlimited. Clearing the field back to 0
requires re-creating the container without the flag.
