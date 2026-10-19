# P0: Zoe's services stop being root-equivalent

**Status:** plan + lab proof, nothing applied (2026-10-18). **Owner decision:** "Fix the admin-rights problem (P0)
first: yes." This is phase P0 of the credential vault design (`credential-vault.md` section 11, PR #2100), gate G-P0.
**Deliverables (not installed by anything):** `scripts/setup/p0-deroot/` (sudoers allowlist, two root helpers, the
Docker proxy unit, three service drop-ins, a tmpfiles fragment), pinned by `tests/unit/test_p0_deroot_files.py`, proven in
`labs/p0-deroot/`.

## 1. Decision in one paragraph

Removing `NOPASSWD: ALL` and the `docker` group from `zoe` is **necessary but not sufficient**: two more root paths
survive it (section 2.6). P0 therefore has three moves, in this order: (1) every Docker caller goes through an
allowlisted socket proxy and every `sudo` caller through a fixed-function helper, while `zoe` still has its rights,
so nothing breaks when they go; (2) no root-run mount or privileged container may read a path `zoe` can write;
(3) `zoe` loses `sudo` and `docker`, and the owner gets a separate `zoe-admin` login. That is the gate G-P0 minimum
(**P0-3**). The full target (**P0-4**) then moves the LLM-exposed services to their own uids (`zoe-svc`, `zoe-flue`,
`zoe-model`) as hardened system units with per-service root-owned secrets, and the live checkout to a `zoe-deploy`
uid, so a compromised service or coding agent can neither become root nor read another's secrets.

## 2. Inventory (read-only, 2026-10-18)

### 2.1 Privileges today

- `zoe` (uid 1000): groups `sudo`, `docker`, `adm`, `video`, `render`, `audio`, `gpio`, `i2c`, ... `sudo -l`:
  `(ALL : ALL) ALL` (sudo group) and `(ALL) NOPASSWD: ALL` from `/etc/sudoers.d/zoe-temp` (28 bytes, dated
  2026-06-20: a temporary grant that became permanent).
- Every Zoe process runs as `zoe`: the user systemd manager (`user@1000`), the containers' host-side tooling, the
  Claude Code / Codex sessions, Omnigent (in-container uid 1000), Serena, and the GitHub runner.
- `/home/zoe` is `0750 zoe:zoe`; the checkout, `~/models`, `~/.zoe` are `0775 zoe:zoe`. Root `.env` is `0664`
  (**world-readable**); `services/zoe-data/.env` and the Flue `.env`s are `0600`.
- GPU nodes `/dev/nvmap`, `/dev/nvhost-*`, `/dev/nvgpu/igpu0/*` are `root:video 0660`: group `video` is the whole
  GPU requirement. polkit is 0.105 (no JS rules, so no per-unit polkit grants: sudoers is the allowlist tool).

### 2.2 Services

| unit (scope) | port | reads / writes | secrets | Docker | sudo | LLM-exposed |
|---|---|---|---|---|---|---|
| `zoe-data` (user) | 0.0.0.0:8000 | checkout (ro + `CAPABILITIES.md`, write tolerated to fail), `~/.zoe`, `~/.zoe-logs`, `~/.zoe-voice-samples`, `~/.mempalace`, `~/models`, `~/.worktrees`, `~/.ssh/zoe_pi_key` | `services/zoe-data/.env`, `~/.hermes/.env` | yes (2.3) | no | **yes**: chat, tools, web, Telegram, intents |
| `flue-zoe-brain-2x` (user) | *:3579 | its lab dir, `data/zoe-brain.db` | its `.env` | no | no | **yes**: the brain lane |
| `flue-zoe-telegram` (user) | *:3582 | its lab dir | bot token `.env` | no | no | **yes**: external messages |
| `llama-server` (user) | 127.0.0.1:11434 | `~/models`, `~/llama.cpp-b11194` | none | no | `brain_cold_start.sh` drop_caches | prompts only |
| `kokoro-tts` (user) | 127.0.0.1:10201 | checkout script, `~/.zoe/kokoro_cache`, venv | none | no | no | model text |
| `functiongemma-router` (user) | 127.0.0.1:11436 | `~/models`, `~/llama.cpp` | none | no | no | utterances |
| `github-runner` (user) | none | `~/actions-runner`, the live checkout, venvs, `~/.nvm` | runner token | compose | no | no (runs `main`) |
| `zoe-night-window` (user timer 02:50) | :11500 (12B) | `~/.zoe/night-window` | zoe-data `.env` | no | drop_caches, NvMap debugfs | household data |
| timers: backup, backup-verify, memory-export, health, ma-idle-reap, multica-heartbeat, crash-loop-watch, merge-queue, voice-regression, training | none | `~/.zoe`, backups | `.env`s | exec `zoe-database`, stop MA, `ps` | no | no |
| `serena-mcp` (user), `serena-bridge` (system, `DynamicUser`, code in `/usr/local/lib/zoe`) | 9121 | repo | none | no | no | agents |

### 2.3 Docker callers (who needs the daemon, and for what)

| caller | calls | P0 route |
|---|---|---|
| zoe-data `ma_ondemand.py`, `routers/voice_livekit.py`; `ma_idle_reap.py` | inspect/start/stop `zoe-music-assistant`, `livekit` | proxy (allowlisted) |
| backup, backup-verify, multica heartbeat, health check | inspect + `exec` in `zoe-database` (`pg_dump`, `pg_restore -l` / `-f -` for the dump manifest, `psql` SELECTs) | proxy (allowlisted; named volume only, see 2.6) |
| weekly restore drill (`zoe_restore_drill.py`, deep dive 2 S0-8) | `run` / `exec` / `rm` of ONE throwaway `zoe-restore-drill` container + its `--internal` network | container create = root: root helper `zoe-restore-drill-pg` (`up`, `down`, `restore <db>`, `psql <db>`) (pinned image, no binds, capped; exact argv in `60-zoe-deroot`) |
| host backup (`zoe_host_snapshot.py`) | Home Assistant's root-owned `.storage` / `.cloud` (NOT `docker exec homeassistant`: a zoe-writable bind) | root helper `zoe-ha-config-export storage` (O_NOFOLLOW from `/` down, regular single-link files only, capped); at P0-2 its `HA_CONFIG` line moves to `/srv/zoe/homeassistant` |
| zoe-data `omnigent_issue_executor.py` (Multica, `ZOE_MULTICA=true`) | `exec` in `zoe-omnigent` | **not** the proxy: a root helper that pins `-u 1000:1000` (P0-2) |
| zoe-data `main.py` + `music_discovery_batch.py` (`ZOE_MUSIC_DISCOVERY=on`) | `run` digarr (`:latest`, unpinned), `rm -f` | container create = root: moves to a zoe-tier timer + helper, or stays off (P0-2) |
| zoe-data `system_updates.py` (admin endpoint) | inspect, `compose pull/up --build`, `exec zoe-ui nginx -s reload` | retired from zoe-data; owner path (P0-2) |
| `deploy.yml` (runner) | `compose up [--build] zoe-auth`, `compose up zoe-ui`, `docker restart zoe-ui`, `docker run nginx -t` | root helper `zoe-compose-deploy` (P0-1, see 4.4) |
| coding agents, labs (`labs/relay`) | build/run anything | owner-approved, or rootless Docker (P0-5) |

### 2.4 sudo callers

`night_window.py` (drop_caches + compact before the 12B, `cat` of two NvMap debugfs files), `bakeoff.py`,
`brain_cold_start.sh` (llama-server `ExecStartPre`), `mtp_vocab_trim_window.sh`, and agents by hand (apt upgrades,
container recreation, `npm -g` installs, system unit installs, drop_caches for 12B windows). Nothing in zoe-data calls
sudo. Pi-only templates (`omi-sync`, `zoe-bluetooth`: `hcitool`) are out of scope (the panel Pi). All of these already
degrade without sudo (`sudo -n true` probe first): the cold start logs one line and starts; the night window refuses a
12B load on fragmented RAM; the bake-off falls back to a throwaway container, which the proxy will refuse. So the
helpers are about keeping the night window working, not about anything breaking loudly.

### 2.5 The self-hosted runner

`github-runner.service` is a **user unit as `zoe`** (`~/actions-runner`). Jobs on it: `deploy.yml` (every push to
`main`: fetch + `reset --hard`, pip, alembic, `systemctl --user restart`, `npm ci && build` for the Flue sidecars,
compose for zoe-auth / zoe-ui when changed), `voice-gate.yml` evidence (base-sha code, PR as data),
`self-hosted-tests.yml` (nightly, `main`). It needs: write to the live checkout and venvs, DB credentials for
migrations, restart of the service units, and the zoe-auth / zoe-ui container ops. No `sudo` today.

### 2.6 Root paths that survive removing `sudo` and `docker`

1. **Privileged Home Assistant reads a zoe-writable config.** `homeassistant` runs `Privileged=true` with
   `/home/zoe/assistant/homeassistant` (`0775 zoe`) as `/config`. HA loads Python from `/config/custom_components`,
   so anything that can write as `zoe` can run code as root in a privileged container: host root. (Reasoned from the
   mounts and HA's loader; deliberately not exercised.)
2. **Bind sources are re-resolved, as root, at every container start.** `zoe` owns every component of
   `/home/zoe/...`, so it can rename a bind source and leave a symlink to `/`; the next start (a reboot, or a proxied
   `start`) mounts the host root into a root-in-container process. Combined with any exec-as-root, that is host root.
   Affected: zoe-auth (`./data`), zoe-ui (`dist` rw, `ssl`, nginx conf), cloudflared, the HA MCP bridge (its code),
   Music Assistant (`~/.zoe/music-assistant`), smb-drop (`~/smb-drop` rw), Omnigent (worktrees, gh config).
3. **The runner executes zoe-writable code.** Harmless while the runner is `zoe`; fatal the day it is given rights
   and still runs the checkout's venv, `~/.nvm` node or `.git/hooks` that `zoe` can edit.

**Principle (pinned by the drop-in test):** no root-run mount, privileged container or privileged runner may read a
path any component of which an LLM-exposed uid can write.

### 2.7 Who legitimately needs root-ish power

| actor | need | P0 answer |
|---|---|---|
| deploy (runner) | recreate zoe-auth / zoe-ui, restart services, migrate | `zoe-compose-deploy` helper (reviewed `main` only), sudoers restarts |
| night window | drop_caches + compact, NvMap reads, stop/start the model units | `zoe-mem-compact`, `zoe-nvmap-read`, exact `systemctl` lines |
| brain cold start | drop the page cache | `zoe-mem-compact 1` |
| zoe-data | on-demand MA / LiveKit, DB exec for nothing (backups are timers) | proxy |
| coding agents | labs (Docker), apt, recreating containers, installs | `zoe-ops` request + owner approval; rootless Docker for labs |
| owner | everything | `zoe-admin` (password sudo, SSH key) |

## 3. Options evaluated

| option | removes root from LLM-exposed processes? | cost | verdict |
|---|---|---|---|
| A. dedicated service users only, `zoe` keeps its rights | services yes; agents stay root; HA path stays | large (paths, units) | not alone |
| B. strip `zoe` (sudo allowlist + proxy) only | yes, for everything as `zoe`, **if** 2.6 is fixed too | small-medium | **P0-3** (gate G-P0) |
| C. rootless Docker for everything | yes | Jetson GPU containers, compose, HA privileged all rework | no; rootless only for agents' labs (P0-5) |
| D. tecnativa docker-socket-proxy | sections only (`POST=1` allows create) | small | no: cannot say "start MA, never create" |
| E. **wollomatic/socket-proxy 1.13.1** (MIT, per-method anchored path regex, unix socket out) | yes for the allowlisted calls | tiny | **adopted** (no home-grown proxy) |
| F. polkit per-unit rules | n/a | polkit 0.105 has no JS rules | no; sudoers exact argv |
| G. B, then A + systemd sandbox + per-service secrets | yes, and services isolated from agents and each other | medium-large, phased | **P0-4 target** |

## 4. Target design

### 4.1 Identities

| uid | runs | sudo | Docker | owns |
|---|---|---|---|---|
| `zoe` (1000) | owner shell, coding agents, Omnigent, Serena, worktrees, labs, the night window | allowlist (`60-zoe-deroot`) | proxy group only | `/home/zoe` |
| `zoe-admin` (new) | the owner's privileged ops | full, **password** | via sudo | none |
| `zoe-svc` | zoe-data | none (`NoNewPrivileges`) | proxy group | `/var/lib/zoe-svc`, `/var/log/zoe-data` |
| `zoe-flue` | flue-zoe-brain-2x, flue-zoe-telegram | none | none | `/var/lib/zoe-flue` |
| `zoe-model` | llama-server, kokoro-tts, functiongemma-router | none | none | nothing (`video`, `render`) |
| `zoe-deploy` (P0-4) | the GitHub runner | exact restarts | via `zoe-compose-deploy` | `/srv/zoe/assistant`, `/srv/zoe/venvs`, `/srv/zoe/node` |
| `zoe-sockproxy` | the Docker proxy | none | `docker` group (the one member) | `/run/zoe-docker-proxy` |

### 4.2 Docker

`zoe-docker-proxy.service` (system) serves `/run/zoe-docker-proxy/docker.sock` (`0660`, group `zoe-dockerproxy`).
Allowed: `_ping`, `version`, `ps`; inspect `zoe-music-assistant` / `livekit` / `zoe-database`; start/stop of the
first two; exec in `zoe-database` (named volume only, so exec-as-root reaches nothing on the host); exec start/inspect.
Everything else is 403/405, including create, build, pull, restart, delete, and exec in any container with a
zoe-writable bind (HA, Omnigent, zoe-ui). Clients set `DOCKER_HOST=unix:///run/zoe-docker-proxy/docker.sock`. The proxy matches the URL path only
(`r.URL.Path` in its `handlehttprequest.go`), so query strings such as `?all=1&filters=...` do not change a verdict.
The binary is copied out of `wollomatic/socket-proxy:1.13.1@sha256:3935b709...689002` into `/usr/local/bin`.

### 4.3 sudo

`/etc/sudoers.d/zoe-temp` is deleted and `zoe` leaves the `sudo` group. `60-zoe-deroot` grants `zoe` exact argv
only: `zoe-mem-compact 0|1|3` (compact only / drop page cache / drop all + compact), `zoe-nvmap-read free|clients`, the backup helpers (`zoe-ha-config-export storage`; `zoe-restore-drill-pg` with
`up`, `down`, `restore zoe|multica`, `psql zoe|multica|drill`; docs/knowledge/offbox-backup.md section 7), `systemctl start|stop|restart` of the three model units,
`systemctl restart` of the three service units (the last two groups match nothing until P0-4 makes them system units).
`zoe-deploy` gets the service restarts. Helpers live root-owned in `/usr/local/libexec/zoe-priv/` and validate their
one argument. **Owner path:** `zoe-admin` (SSH key + password sudo). **Agent path:** `zoe-ops request "<why>" -- <cmd>`
writes a request to `/var/spool/zoe-ops` (group-writable, sticky); the owner reviews and runs
`sudo zoe-ops approve <id>` as `zoe-admin`, which runs the command verbatim and logs it. (A follow-up PR; until it
lands, agents hand privileged steps to the owner as an operator pack, as today's classifier rules already require.)

### 4.4 Deploy

P0-1 adds `zoe-compose-deploy <zoe-auth|zoe-ui> <sha> [--build]` (root helper, follow-up PR): it fetches `main` from
the **public** GitHub URL into a root-owned mirror, refuses unless the sha is on `main`, exports that tree root-owned,
and runs `docker compose` from it with `--env-file /etc/zoe/compose.env` (root `0600`) and bind sources under
`/srv/zoe/` (moved in P0-2). The runner can name a reviewed commit and a service, nothing else. `nginx -t` runs inside
the helper. At P0-4 the runner moves to `zoe-deploy`, which owns `/srv/zoe/assistant` (the live checkout;
`/home/zoe/assistant` becomes a symlink for humans and agents), `/srv/zoe/venvs` and a root-installed node.

### 4.5 systemd (P0-4)

Each service becomes a **system** unit (`systemctl --user cat` → one file, `%h` → `/home/zoe`, `WantedBy=multi-user`)
plus its `50-p0-deroot.conf`. System scope is required: a `--user` unit silently drops IP firewalls and `Nice=-N` (both measured
on this box), and its mount sandboxing needs unprivileged user namespaces; the lab proves the system-scope form. The drop-ins keep `HOME=/home/zoe` and rebuild the
home view with `ProtectHome=tmpfs` + `BindPaths`/`BindReadOnlyPaths` from `/srv/zoe` and `/var/lib/<svc>`, so no code
path changes. Every voice-path guard stays in the ported unit (`MemorySwapMax=0`, `MemoryLow`, `LimitMEMLOCK=infinity`,
no `MemoryMax` on the brain); the GPU tier has `video` and no `PrivateDevices`/`DevicePolicy`. System scope also makes
`MemoryLow` effective again if `system.slice` gets a floor (the user-slice ancestors are why it is dead today).

### 4.6 Secrets (vault O10 / S2)

Phase 1: `/etc/zoe/env/<unit>.env`, `root:root 0600`, read by systemd before it drops to the service uid. No app
change; another uid cannot read it (lab S5, Z3). Phase 2: `LoadCredential=` per secret.

## 5. Phases (each reversible; voice is never down longer than one stack restart)

| phase | what | voice downtime | rollback | verify |
|---|---|---|---|---|
| **P0-0** prep | create `zoe-admin` (SSH key + password) **and prove `sudo -v` with it before anything else**; groups/users; install helpers, `60-zoe-deroot`, the proxy; `chmod 600 .env`; `/etc/zoe/compose.env`; **CUDA smoke as `zoe-model`** (it cannot enter `/home/zoe`): copy the brain build to `/srv/zoe/llama.cpp-b11194`, then `sudo -u zoe-model env LD_LIBRARY_PATH=<the unit's> /srv/zoe/llama.cpp-b11194/build-jetson/bin/llama-server --list-devices` must list the CUDA device (MemAvailable >= 1500, outside 01:50-04:55) | none | delete the added files/users | `sudo -n -l` as `zoe`; `visudo -c`; proxy `docker -H unix:///run/zoe-docker-proxy/docker.sock ps`, `inspect zoe-database`, `exec zoe-database true`, `exec -i` pipe (the real CLI incl. the exec hijack, which the lab cannot run) |
| **P0-1** route callers | `DOCKER_HOST` for the user manager (`environment.d`), shells and the runner `.env`; scripts call the helpers instead of `sudo sh -c`; deploy.yml calls `zoe-compose-deploy`; run 48 h and read the proxy's 403/405 log | one zoe-data restart (about 60 s) | unset `DOCKER_HOST`, revert the PR | `/health`; voice replay probe; MA on-demand play; a backup; a night window; a deploy touching zoe-auth |
| **P0-2** close 2.6 | move HA config, zoe-auth data, zoe-ui ssl/dist, cloudflared config, MA data, smb-drop, the MCP bridge code to `/srv/zoe/<name>` (root or the container's uid); recreate each container; Omnigent kick via a `-u 1000:1000` helper; digarr behind a helper or off; `system_updates` install and router self-train out of zoe-data | seconds per container; none on the voice path | old bind paths are untouched until a week later; recreate with the old compose | HA automations, login, panel, tunnel, music |
| **P0-3** de-root `zoe` (**G-P0**) | `rm /etc/sudoers.d/zoe-temp`; `gpasswd -d zoe sudo`; `gpasswd -d zoe docker`; reboot (the user manager keeps old groups until restarted) | one reboot, about 3-5 min | as `zoe-admin`: re-add the groups, restore `zoe-temp` from `/root/zoe-temp.bak`, reboot | `sudo -n true` fails as `zoe`; `/var/run/docker.sock` EACCES; `/health`; voice probe; night window (next 02:50, or `--dry-run`); a deploy |
| **P0-4** isolate | per service, smallest first: model tier, then Flue, then zoe-data: port the unit to `/etc/systemd/system`, add the drop-in + `/etc/zoe/env` (zoe-data also the `tmpfiles.d` fragment, applied with `systemd-tmpfiles --create`), `systemctl --user disable --now X && systemctl enable --now X`; runner to `zoe-deploy`, checkout to `/srv/zoe/assistant` | about 15-60 s per service (brain 11 s, stack 50-60 s measured) | `systemctl disable --now X; systemctl --user enable --now X` | per service: `/health`, voice probe (said-vs-did + per-stage speed), `cat /proc/<pid>/status` uid, swap 0, night window, deploy |
| **P0-5** agents' labs | rootless Docker for `zoe` (`uidmap`, `rootlesskit`, `slirp4netns`; subuids exist) | none | uninstall | `labs/relay/run.sh lab` |
| **P0-6** HA de-privilege | replace `privileged` with explicit `devices` | HA restart | re-add `privileged` | HA integrations |

**Owner approvals needed:** P0-0 (create users, install sudoers/helpers/proxy, the CUDA smoke); P0-1 (the
deploy/helper PR merge and the `DOCKER_HOST` switch); P0-2 (each container move, the zoe-data duty moves);
**P0-3 (the de-root itself, with a scheduled reboot)**; P0-4 (each service move, the runner + checkout move);
P0-5/P0-6 independently. Nothing above is run by an agent.

## 6. Lab proof (`labs/p0-deroot/`)

One container, no network, systemd 249 as PID 1 (Ubuntu 22.04, the Jetson's own), not `--privileged`. It does share
the host cgroup namespace with a read-write `/sys/fs/cgroup`, because systemd does not boot otherwise (lab README).
The deliverables are installed **verbatim**; three stand-in services carry the real drop-ins on top of base units that
keep the voice guards. Each stand-in answers `/probe` from **inside its own sandbox**. **Result: 47/47 cells pass
(2026-10-18 20:14).** The lab also caught one real defect: `StandardOutput=append:` is opened before the namespace
and before `LogsDirectory=`, so the drop-in moves zoe-data's capture to `/var/log/zoe-data/` and
`tmpfiles.d/zoe-p0-deroot.conf` pre-creates it.

- **S cells (per service):** its uid, not in `sudo`/`docker`; `sudo -n` fails; raw socket EACCES; zoe-data gets the
  proxy allowlist (MA start 200, create 403, HA exec 403), the others cannot reach the proxy; cannot read the other
  service's secret; the real `/home` is hidden; checkout readable, not writable; own secret present; its job works
  (input read, state written); `/etc` read-only; voice guards kept (`MemorySwapMax=0`); the model tier is in `video`;
  zoe-data's stdout capture works through the bound `~/.zoe-logs`.
- **Z cells (`zoe` after P0-3):** no sudo; the allowlist allows exactly its entries and refuses shells, other args,
  `systemctl edit`, other units; no service secrets; raw socket EACCES; 20 proxy cases (traversal, `%2F` and query strings included);
  helpers refuse bad args; `zoe-admin` needs a password.
- **C cells (instruments):** a `zoe-pre` user with today's rights CAN sudo and reach the socket (so the probes see
  power); the fake daemon saw only the allowed calls; `visudo -c` passes and catches a broken file;
  `systemd-analyze verify` is clean.

**Not labbed, verified live first:** the GPU as `zoe-model` (P0-0 CUDA smoke), the real Docker CLI through the proxy
including `exec -i` stream hijack (P0-0), and the real units' start under the sandbox (P0-4, one service at a time).

## 7. Residuals and out of scope

- `zoe-deploy` and `zoe-compose-deploy` make "who merges `main`" the root boundary; `main` is protected and reviewed.
- Exec in `zoe-database` gives DB superuser, which zoe-data's credentials already are.
- Until P0-4 all `zoe` processes share one uid: no root, but an agent can read zoe-data's secrets (vault A6 waits).
- The panel Pi (`pi` user), the Omnigent container's own token mounts, and HA's own tokens are separate work.
