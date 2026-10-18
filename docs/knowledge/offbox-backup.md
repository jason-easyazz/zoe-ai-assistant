---
type: Runbook
title: Off-box encrypted backup to the411 (A5) and the B9 ops-hygiene owner steps
description: How the box's nightly backup leaves the box - restic into an append-only rest-server on the411, household words and secrets also age-encrypted to a key that never touches the box - what is in each tier and what is never shipped, retention reconciled with "forgotten means forever" (time-bounded 35 days for the household tier), the monthly restore drill, the DNS decision, the threat model, the owner's install steps on the411 and the box, the lab result, the owner steps for the B9 hygiene items (units, health check, container caps, .env reaping, file modes, the VAPID key, the chromadb finding), and (deep dive 2 S0-8) the host sources added to both copies, the weekly restore drill into a throwaway container, local encryption at rest, and their owner steps.
tags: [backup, restic, age, the411, forget, retention, drill, restore, systemd, docker, secrets, home-assistant, runbook]
timestamp: 2026-10-19T06:00:00+08:00
---

# Off-box encrypted backup (A5) and ops hygiene (B9)

Source: [architecture deep dive](../research/architecture-deep-dive-2026-10-18.md) sections 5 and 10 (A5, B9). Owner decisions
(2026-10-18): an encrypted nightly backup goes to the owner's the411 server; it needs a household account there, tied to the
relay enrolment ([panel-anywhere](../architecture/panel-anywhere.md) section 4.1); the backup key is held off the box
([credential vault](../architecture/credential-vault.md) decision O5). **Nothing here is installed live.** Everything is a
tracked script or template plus the owner steps below.

## 1. Design

```
 box (nightly 05:10)                                                   the411
 ~/.zoe-backups dumps ─┐                                               nginx :443 backup.the411.life (TLS, LE)
 ~/.zoe (filtered)     ├─ age -R <recipient> ─► stage (ciphertext) ─┐       │  proxy, limit_req
 voice corpus          │                                            ├─ restic ──►  rest-server --append-only --private-repos
 .env*/ssl/config ─────┘                                            │       │      /srv/zoe-backup/data/<hid>/
 router checkpoint + GGUF ──────────────────────────────────────────┘       │
                                                                     prune container (04:00 UTC): restic forget+prune
                                                                     with its OWN key, per household
```

- **restic** to a **rest-server in append-only mode**: the box adds snapshots and can never delete or rewrite one (lab N2:
  `forget --prune` exits 3, a raw `DELETE` is 403). A stolen or compromised box cannot destroy its own history.
- **Private repos, one per household**: the rest-server user is the household id `<hid>`, the same id the relay's session
  master assigns at enrolment (panel-anywhere 4.1). Until the relay exists the owner issues `<hid>` by hand (`h` + 8 hex,
  random, never derived from a name); once `POST /relay/enrol` exists, it returns the backup credential alongside the relay
  secret and rotates it through signed device calls. Another household's login cannot reach the repo (lab N3).
- **Retention runs on the411**, in its own container with its own restic key, because the box's credential cannot delete.

### 1.1 Keys: what is where

restic is symmetric: whoever WRITES needs a key that also reads. So "the backup key is off the box" is built from three keys:

| key | where | can | if it leaks |
|---|---|---|---|
| restic **master** password | the owner's vault only; typed once on the411 for `init` / `key add` | everything | revoke nothing needed: it is never stored on a machine |
| restic **box** key (`restic key add`) | the box, `~/.config/zoe-backup/restic.pass`, 0600 | read + append (rest-server forbids deletes) | revoke on the411 with `restic key remove`; it only decrypts the restic layer (below) |
| restic **prune** key | the411, `/srv/zoe-backup/prune-keys/<hid>.pass`, 0600 root | forget + prune | same: restic layer only |
| **age identity** (`AGE-SECRET-KEY-...`) | the owner's vault only (O5), printed copy as the last resort | decrypt household words and secrets | the box holds only the PUBLIC recipient; the backup script refuses to run if an identity is in its config dir (lab N6) |

The **household** and **secrets** tiers are age-encrypted to that recipient before restic sees them. So the restic layer that the
box key and the prune key open holds ciphertext for them: a stolen box key, a compromised the411, or both together, read no household
word and no secret from the off-box copy. A thief who has the box already has the live data; what they do NOT get is the history,
including words that were since forgotten.

### 1.2 Tiers, and what never leaves the box

| tier | contents | encryption | retention |
|---|---|---|---|
| `household` | newest on-box Postgres dumps (`zoe`, `multica`; biometric table data already excluded by `postgres-nightly-backup.sh`) and their manifests, `pg_dumpall --globals-only`, newest MemPalace tarball, `~/.zoe` filtered, `~/.zoe-voice-samples`; **host sources (section 7)**: HA config (minus `.storage`, logs, recorder, tts, deps) + the newest `.storage`/`.cloud` helper export, `services/zoe-data/data/` (minus the router shadow log, the VAPID key and the dead `zoe.db`), the models lock | age + restic | one per day for 7 days, one per week for 34 days; **never older than 35 days**, no monthly |
| `secrets` | the live checkout's `.env`, `.env.*`, `services|labs|modules/*/.env*` (never `*.example`, `*.bak*`), `ssl/`, `config/`, `~/.zoe/env`, `~/.zoe/secrets`; **host config (section 7)**: HA `secrets.yaml`, `~/.config/systemd/user` (units + drop-ins), `/etc/systemd/system/*.d`, `~/bin`, the crontab | age + restic | 7 daily, 4 weekly, 6 monthly |
| `artefacts` | `labs/functiongemma-finetune/runs/gen1/merged` (the router lineage checkpoint: NEVER delete) and `~/models/functiongemma-router` | restic | 7 daily, 4 weekly, 6 monthly |

Never shipped from `~/.zoe` (each is an fnmatch on any path component, `ZOE_TREE_EXCLUDES`): anything `*backup*` (backups of
backups, the ad-hoc owner-script backups that hold raw text or secrets), caches and venvs, `bakeoff-*` and the chroma migration
rehearsal (lab artefacts), logs, **`omi-recordings`** (pendant audio: claim-time retention and third parties' voices), compaction
salvage files, and `env` / `secrets` (they go in the secrets tier instead). The router corpus itself is tracked in git
(`labs/functiongemma-finetune/data/`), so GitHub already holds it.

**The age mirror.** age output is randomised, so re-encrypting everything nightly would defeat restic's de-duplication. The box
keeps one ciphertext file per source file in `~/.cache/zoe-offbox/stage/<tier>/` and re-encrypts only what changed (size + mtime);
an unchanged corpus costs nothing (lab B4: 0 re-encrypted on the second run) and a deleted source is absent from the next snapshot
(lab B5). File names are keyed HMACs (no path or name reaches the411, even inside restic's layer); the path map is itself
age-encrypted (`index.json.age`). The stage holds ciphertext only (lab B2b: no canary in any stage file). Disk cost on the box: the
ciphertext size of the household + secrets tiers, about 0.4 GB today.

### 1.3 Retention and "forgotten means forever"

The owner's general rule was 7 daily / 4 weekly / 6 monthly. It is kept for `secrets` and `artefacts`, which hold no household words.
The `household` tier is bounded at **35 days** instead, the same bound [forget-reach](forget-reach.md) (#2110) sets for on-box backups (owner
rule 2026-10-06: forgotten means forever). A forget cannot rewrite a snapshot, so forgotten words leave the off-box copy only by
ageing out, and six monthly snapshots would keep them for half a year.

The bound is **time-based** (`--keep-within-daily 7d --keep-within-weekly 34d`: a 34-day window plus at most one day until the next daily prune = never more than 35 days), measured to matter: restic's count-based
`--keep-weekly 4` keeps the last 4 weeks *that have snapshots*, so after a gap it kept a 40-day-old household snapshot (lab cell R2
failed with it). The `within` windows count back from the **newest** snapshot, which is the right behaviour for disaster recovery:
if the box dies, its last 34 days stay on the411 as the restore point instead of ageing out under the owner. While the box backs
up nightly, nothing holding household words is older than 35 days.

After a restore from a backup older than a forget, **re-issue the forget** (forget-reach: the `memory_forgotten` ledger comes back
with the data it protects). When #2110's inventory lands, add an `OTHER` store `backup_offbox` (`out_of_scope`, note: "household
tier time-bounded at 35 days on the411; restore => re-issue the forget") so the inventory test covers this copy too.

### 1.4 The restore drill and alerts

- **Weekly, on the box** (Sunday 05:30): the restore drill of section 7.2 - the on-box dumps restored into a throwaway
  container, the palace opened and queried, the host archive checked. It runs first in `zoe-backup-verify`.
- **Monthly** (the weekly `zoe-backup-verify` runs it once per 28 days, Sunday 05:30): for each tier, restore the newest snapshot
  from the411 into a scratch dir (`~/.cache/zoe-offbox/drill-*`, removed after), compare every file's sha256 with the manifest the
  box recorded when it SENT that snapshot, read one twelfth of the repo's data (`restic check --read-data-subset=<month>/12`, the
  whole repo once a year), and check retention: a household snapshot more than 35 days older than the newest (no slack) means the411's prune
  stopped; a newest snapshot older than 50 h means backups stopped landing. The artefacts tier restores only the GGUF (not the 1 GB
  checkpoint) monthly.
- **Alerts** go over Telegram through the crash-loop watcher's sender (a process separate from zoe-data): a backup run with problems
  (a stale on-box dump, a missing source, unreadable files), any failure, a low-memory refusal, a failed drill, and on-box verify
  failures. A refusal inside the night window does not alert (a `Persistent=` catch-up after a night boot lands there); the drill's
  "not landing" check covers a backup that really stopped.
- **The owner's decrypt drill** (quarterly, with the identity, NOT on the box): `zoe_offbox.py restore --identity <file>
  --tier household --target <dir>` decrypts every file and checks its plaintext sha256 against the encrypted index; then
  `pg_restore --list` the dump. The box cannot run this one by design: it never holds the identity.

## 2. Decision: the DNS name and port

| option | for | against |
|---|---|---|
| **A. dedicated `backup.the411.life`** on the411's PRIMARY address, as one more host-nginx site (TLS by certbot HTTP-01), proxying to the loopback rest-server | independent of the relay rollout (R1 is not live; backups should not wait for it); the relay's Reserved IP stays frps-only (panel-anywhere decision D2); bulk uploads never compete with the relay's per-IP / per-household limits; standard tooling the411 already runs | one more public name in Certificate Transparency (it reveals that the411 serves a backup endpoint, nothing more); one more vhost to maintain |
| B. behind the relay domain (e.g. `backup.relay.the411.life`) through the relay's SNI stream front | one public front for everything Zoe; no new name on the411's own zone | couples backups to the relay's front and its availability; mixes high-volume uploads into the relay's abuse limits; needs an SNI route and a cert in the relay stack that does not exist yet; the relay domain's naming is reserved for `<hid>` names and the Public Suffix List plan (D4) |

**Recommendation: A.** Port 443 only (nothing else opened; rest-server listens on `127.0.0.1:8730`). The household tie is the
**credential** (`<hid>` from enrolment), not the transport.

## 3. Owner steps on the411

All as root over SSH. Placeholders as in `labs/relay/RUNBOOK-R1.md` (this repo is public: no real addresses here).

1. **Recon** (read-only): `docker compose version`, free disk under `/srv` (plan 3x the box's first upload, about 5 GB to start),
   `ss -ltnp 'sport = :8730'` (must be empty), `nginx -t`.
2. **Directories**: `install -d -m 700 /srv/zoe-backup/data /srv/zoe-backup/prune-keys /opt/zoe-backup`; copy
   `scripts/setup/offbox-backup/the411/{compose.yaml,prune.sh}` into `/opt/zoe-backup/` (`chmod 755 prune.sh`).
3. **Start rest-server**: `cd /opt/zoe-backup && docker compose up -d rest-server`; `curl -s -o /dev/null -w '%{http_code}\n'
   http://127.0.0.1:8730/` prints `401`.
4. **Household user** (one per household): pick `<hid>` (or take it from the relay enrolment), generate a long random password in
   the owner's vault, then `docker exec -it zoe-backup-rest-server create_user <hid>` and paste it (bcrypt in `/data/.htpasswd`).
5. **Repo + keys** (the master password is typed, never stored):
   ```sh
   R="docker run --rm -it -v /srv/zoe-backup/data:/data -v /srv/zoe-backup/prune-keys:/keys restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510 -r /data/<hid>"
   $R init                                   # type the MASTER password (from the vault)
   $R key add                                # master, then the BOX key (generate in the vault; it goes to the box in step B3)
   head -c 32 /dev/urandom | base64 > /srv/zoe-backup/prune-keys/<hid>.pass && chmod 600 /srv/zoe-backup/prune-keys/<hid>.pass
   $R key add --new-password-file /keys/<hid>.pass   # master again
   $R key list                               # three keys
   ```
6. **TLS front**: install `the411/nginx-backup.conf` as `/etc/nginx/sites-available/backup.the411.life`, link it, add the DNS
   A/AAAA record `backup.the411.life -> <PRIMARY_IP>`, `certbot --nginx -d backup.the411.life`, `nginx -t && systemctl reload nginx`.
7. **Prune**: `docker compose up -d prune`; next day `docker logs zoe-backup-prune` shows one `prune: <hid>` block.
8. **Verify from outside**: `curl -s -o /dev/null -w '%{http_code}\n' https://backup.the411.life/<hid>/config` is `401` without
   credentials.

## 4. Owner steps on the box

1. `scripts/setup/offbox-backup/install_tools.sh` (restic 0.19.1 + age v1.3.2 into `~/.local/bin`, sha256-checked).
2. **age identity OFF the box**: on the owner's laptop or phone, `age-keygen -o zoe-backup.age` -> store the file in the vault
   (and print it); `age-keygen -y zoe-backup.age` prints the recipient `age1...`. Only that line goes to the box.
3. `install -d -m 700 ~/.config/zoe-backup`; copy `scripts/setup/offbox-backup/offbox.env.example` to
   `~/.config/zoe-backup/offbox.env` and fill `<hid>` + the rest-server password; put the BOX key in `restic.pass` and the
   recipient in `age-recipients.txt`; `chmod 600 ~/.config/zoe-backup/*`.
4. `python3 scripts/maintenance/zoe_offbox.py check` (no network) -> sources and tools listed.
5. First run by hand, outside 01:35-04:55: `python3 scripts/maintenance/zoe_offbox.py backup` (the first upload is the
   whole corpus at the 2 MiB/s default cap; later nights send only changes), then `... drill --force`.
6. Install the units: `cp scripts/setup/systemd/zoe-{backup,backup-verify,offbox-backup}.{service,timer} ~/.config/systemd/user/`,
   `systemctl --user daemon-reload`, `systemctl --user enable --now zoe-offbox-backup.timer` (the two on-box timers are already
   enabled; the copies move the MemPalace step and the verify script into the repo, add the host archive step, and turn the
   verify into the section 7.2 restore drill at Sunday 05:30).
7. `systemctl --user list-timers 'zoe-*backup*'`; after the first night, `~/.zoe-logs/offbox-backup.log` ends with three
   `snapshot` lines and `~/.local/state/zoe-offbox/last_backup.json` has `"problems": []`.

**Disaster recovery** (new box): install the tools, recreate `offbox.env` + the box key (or `key add` a new one on the411), then on
the machine holding the identity: `zoe_offbox.py restore --identity <file> --tier secrets --target /tmp/r` and `--tier
household`; restore Postgres from `plain/pg/*.dump.gz`, the palace from `plain/palace/mempalace.tar.gz`, `~/.zoe` from `plain/zoe/`;
**re-issue every forget newer than the snapshot**; then shred the plaintext. `restore` refuses on a host where the
`zoe-offbox-backup` unit is installed (the identity never touches a running box) and refuses an identity inside
`~/.config/zoe-backup`; on a REPLACEMENT box before its units are installed, pass `--allow-on-box` and delete the identity
afterwards. Every path in the decrypted index is checked to stay under the restore dir (the index is writable by anyone holding
the public recipient, i.e. the box).

## 5. The lab

`labs/offbox-backup/run.sh` (hand-run; the test-lane lock, refuses 01:35-04:55 and MemAvailable < 1500 MB): the rest-server image and
OPTIONS parsed from `the411/compose.yaml`, the pinned restic and age, a synthetic box with canary values, the real backup script
and the real `prune.sh`. **2026-10-18: 19/19 cells** - B1 three tagged snapshots; B2 household and secrets tiers exactly the policy,
plaintext byte-identical after an owner restore with the off-box identity; B2b no canary in the stage; B3 drill restore + checksum;
B4 second run re-encrypts nothing; B5 a deleted file is gone from the next snapshot; N1 wrong repo password refused; N2 append-only
refuses `forget --prune` (exit 3) and a raw `DELETE` (403) with the box's valid credentials, snapshot count unchanged; N3 another
household's login gets 401; N4 wrong rest-server password 401; N5 the drill goes red when a restored file differs from what was
sent; N6 an age identity in the config dir refuses the run; N7 a wrong identity cannot decrypt; R1 the drill goes red on a 40-day
household snapshot; R1b (control) the owner's literal count-based 7 daily + 4 weekly would KEEP that snapshot; R2 the411's prune ages it out and keeps the 40-day secrets snapshot (monthly); R3 the drill is green again;
teardown leaves nothing.

## 6. B9 owner steps (each small, each independent)

1. **Units.** `python3 scripts/maintenance/unit_inventory.py` lists every installed unit as tracked / retired / UNTRACKED;
   `zoe_ground_truth.sh` prints the one-line summary with the template drift count. Newly tracked templates (install = copy +
   `daemon-reload`, no restart needed unless noted): `zoe-backup`, `zoe-backup-verify`, `zoe-merge-queue`, `zoe-opensrc-refresh`
   (now runs `scripts/maintenance/refresh_opensrc.sh` against the zoe-data venv's versions), `github-runner`, and `zoe-health`
   (below). Retired, removal pending (`unit_inventory.RETIRED`): the #2110 families (`cleanup_dead_units.py`) plus
   `zoe-purge-presence`, `llama-server-e2b-fallback` (binds 0.0.0.0:11434), `zoe-12b-postboot`, `zoe-brain-boot-check`: disable and
   move them out the same way (`systemctl --user disable <unit>`, `mv` to `~/.zoe/backups/dead-units-<ts>/`, `daemon-reload`).
   The 20 `.bak` / `.disabled` files in `~/.config/systemd/user` can go with them.
2. **One health check.** Install `zoe-health.{service,timer}` over the untracked pair, then `crontab -e` and delete
   `*/5 * * * * /home/zoe/bin/zoe-watchdog.sh`. Retire `~/bin/zoe-health-check.sh` and `~/bin/zoe-watchdog.sh`; the former holds a
   literal database password (rotate it if the file was ever copied off the box). Dry run first:
   `python3 scripts/maintenance/zoe_health.py --dry-run` (2026-10-18: spine all 200; it flags `zoe-multica-backend` restarting,
   exit 1 x220, not OOM, and the old `zoe-health.service` failed).
3. **Container memory caps.** In compose now (`mem_limit` / `memswap_limit`, leak backstops; table below). A compose change reaches a
   container only when it is re-created, so batch it with a planned re-create, or apply live without a restart:
   `docker update --memory <mem> --memory-swap <swap> <container>`; `zoe-multica-web` and `zoe-smb-drop` were made with `docker run`
   and only ever get it that way. Log caps: every compose service already carries the 10m x 3 anchor; the five live containers
   without it (`zoe-cloudflared`, `zoe-ytmusic-potoken`, `zoe-multica-backend`, `zoe-multica-web`, `zoe-smb-drop`) predate it and
   pick it up on re-create (the daemon default covers the two `docker run` ones).
4. **`.env` reaping.** `python3 scripts/maintenance/reap_env_copies.py` (dry run; 2026-10-18: 105 files, 54 stale, 52 of them
   holding OLD values), then `--apply`. It never touches the live files.
5. **File modes.** `python3 scripts/maintenance/fix_secret_perms.py` then `--apply`: the live `.env` 0664 -> 0600, the VAPID key,
   `~/.zoe/env` 0775 -> 0700, `ssl/` 0755 -> 0750. "ssl 777" in the review was the `zoe.crt -> cert.pem` symlink, whose mode means
   nothing (the key and cert are already 0600). NOT changed: the cloudflared tunnel credentials (`config/<uuid>.json`, 0644) - the
   container runs as uid 65532, so 0600 would take the tunnel down; the fix there is `chown 65532` (owner decision).
6. **VAPID key out of the checkout.** `routers/push.py` reads `ZOE_VAPID_KEY_PATH` (default `~/.zoe/secrets/vapid_keys.json`), then
   the legacy `services/zoe-data/data/vapid_keys.json`; it only generates when neither exists (a new key would orphan every push
   subscription). After the deploy: `python3 scripts/maintenance/migrate_vapid_keys.py` then `--apply` (moves the same key, 0600,
   no restart needed).
7. **The mempalace pins and the host chromadb 0.6.3 - verified, NOT dropped here.** No live process loads the 3.10 pair (every
   python process's `/proc/<pid>/maps`, 2026-10-18: only zoe-data, on the 3.12 venv's chromadb 1.5.9), and the 3.10 format guard
   refuses the 1.x palace anyway. But `mempalace` (3.10.0, 3.12 venv) is imported by the bake-off harness (`scripts/perf/zmb`), so
   that pin stays; and the 3.10 pair is still named by `services/zoe-data/requirements.txt` (a `VOICE_PATH_PATTERNS` entry, so
   changing it needs the replay gate), `scripts/setup/jetson/install-jetson-agent.sh` (which INSTALLS `mempalace==3.3.1`, whose
   `_fix_blob_seq_ids` is unsafe on a 1.x palace) and three tests. Dropping it is a separate replay-gated PR; the live
   `pip uninstall --user chromadb mempalace` on 3.10 is safe at runtime once that lands.

### Container memory caps (2026-10-18)

Peak = the higher of summed per-process VmHWM on 2026-10-03 and 2026-10-18 (this kernel's cgroup v2 has no `memory.peak`).
Every cap is at least 1.3 x peak (`tests/unit/test_ops_hygiene_b9.py`); the values follow the existing sizing rule in
[docker-log-and-memory-limits](docker-log-and-memory-limits.md).

| container | peak MB | mem_limit | memswap_limit |
|---|---|---|---|
| homeassistant | 453 | 1536m | 2304m |
| zoe-music-assistant | 833 | 1280m | 1920m |
| zoe-ytmusic-potoken | 213 | 512m | 768m |
| zoe-omnigent | 273 idle; a session adds codebase-memory ~1.3 GB | 4096m | 6144m |
| zoe-multica-web | 98 | 256m | 384m |
| zoe-auth | 83 | 256m | 384m |
| zoe-ui | 71 | 256m | 384m |
| homeassistant-mcp-bridge | 53 | 256m | 384m |
| zoe-cloudflared | 41 | 256m | 384m |
| zoe-multica-backend | 26 | 256m | 384m |
| zoe-smb-drop (`docker run`) | 26 | `docker update` 256m | 384m |
| zoe-database | 217 | none (exempt: an OOM kill is an outage of every data path) | - |
| livekit | 19 idle | 512m in #2108 (A10) | 512m |

## 7. Host sources, the weekly restore drill, local encryption (deep dive 2 S0-8, 2026-10-19)

Source: [deep dive 2](../research/architecture-deep-dive-2-2026-10-19.md) section 10 (S0-8) and lane J section 6.3 (what a
dead disk lost even with this backup installed). Nothing here is installed live: scripts, templates and owner steps.

### 7.1 Sources added, to BOTH copies

One list, in `zoe_offbox.py` (`host_household_sources`, `host_secret_sources`, `crontab_source`). The on-box copy is
`zoe_host_snapshot.py`, the third step of `zoe-backup.service`: `~/.zoe-backups/host/host-<ts>.tar.gz` (0600 in a 0700 dir,
7 kept) with `MANIFEST.json` inside (every file's sha256, every source label, and which were missing). The off-box copy
mirrors the same sources file by file (so restic de-duplicates them).

| source | tier | how it is read | left out, and why |
|---|---|---|---|
| HA config (`homeassistant/`: YAML, blueprints, `custom_components`) | household | walked as `zoe` | `.storage`/`.cloud` (next row), the recorder DB (state history; a live root-owned SQLite HA rebuilds), `tts`, `deps`, `.cache`, logs |
| HA `.storage` + `.cloud` (integrations, auth, device/area registries; several files `0600 root`) | household | **root helper** `zoe-ha-config-export storage` through `sudo -n` (7.4) -> `~/.zoe-backups/host/ha-storage-<ts>.tar.gz` | until the owner installs the helper: not captured, reported (backup log line; weekly drill finding) |
| HA `secrets.yaml` (`zoe`-owned) | secrets | walked | |
| `services/zoe-data/data/` (`music_discovery/`, `music_prefs.json`, anything new) | household | walked | `router_head_shadow.jsonl*` (raw utterance text; #2110 strips it at 30 days and it regenerates), `vapid_keys.json` (already in the secrets tier), `zoe.db*` (the dead pre-Postgres SQLite, deep dive 2 D-2) |
| `~/.config/systemd/user` (units + the ~25 drop-ins) and `/etc/systemd/system/*.d` | secrets | walked (drop-ins carry env lines) | symlinks (`*.wants`) |
| `~/bin` | secrets | walked (one script holds a DB password) | |
| crontab | secrets | `crontab -l` at run time | |
| `~/.zoe/backups/panel/` (panel config + `hey_zoe.onnx`, written by the panel-as-code work, V-3) | on-box host archive only; off the box it rides in the `artefacts` tier (#2142), not twice | walked (the `~/.zoe` tree excludes `*backup*`, so it is its own source) | |
| models lock (`~/assistant/models.lock`, `~/models/models.lock` or `~/.zoe/models.lock`; `ZOE_MODELS_LOCK` names another) | household | the first that exists | the 80 GB of weights: the lock (paths + sha256) is the restore recipe. The panel-as-code PR (#2142) adds it at the repo root (so git holds it too); until it lands the source is reported missing |
| Postgres dump manifests (7.2) | household | beside each dump | |

**Pendant transcripts (owner decision OD3, 2026-10-19): kept.** They live in Postgres (`omi_recordings`, `omi_segments`, the
day-review tables), which every dump carries in full; only the biometric tables' DATA is excluded
(`learned_voiceprints`, `learned_voiceprint_segments`, `omi_segment_embeddings`, `omi_voice_clusters`). How long a transcript
lasts in backups after it is deleted live: on-box up to ~5 weeks (7 daily dumps + 4 Sunday weeklies), off-box at most 35 days.
Pendant **audio** (`~/.zoe/omi-recordings`) stays excluded from both. Pinned by `test_od3_pendant_transcripts_are_kept_audio_is_not`.

Not added (and why): the Flue brain session store (447 MB, append-only, no forget hook: shipping it would copy what the forget
path cannot reach; decide with D-1), Docker named volumes other than the database (`multica-uploads`: lab-grade), the ONNX MiniLM
cache and HF weights (re-downloadable; the lock covers the brain), the Pi itself (the panel's own backup is V-3's).

### 7.2 The weekly restore drill (`zoe_restore_drill.py`, Sunday 05:30)

The old weekly check restored the newest dumps **into the live Postgres instance**, counted two tables, never loaded the palace's
HNSW index, and ran at 03:30 inside the night window. Now:

1. **Manifest at backup time.** `postgres-nightly-backup.sh` runs `pg_dump_manifest.py` after each dump: the table list from the
   dump's TOC (`pg_restore -l`) and each table's row count from its COPY block (`pg_restore --data-only -f -`, SQL to stdout, no
   connection), so the counts are exactly what the dump holds; plus, from the live catalog (SELECTs only), the table set, the
   extension versions and the Alembic revision. A live table missing from the dump is flagged on the spot.
2. **Throwaway sandbox.** `zoe-restore-drill-pg up` starts `zoe-restore-drill`: the same image digest as the live `zoe-database`
   (test-pinned to compose), its own `--internal` network, no volume, no published port, 512 MB memory+swap, 256 pids, caps
   dropped, trust auth inside that network only. `down` removes container + network (also the unit's `ExecStopPost`).
3. **Checks** (each a finding on failure): the dump's sha256 vs its manifest and a full gunzip; `pg_restore --exit-on-error`
   into the sandbox; **every table's `count(*) FROM ONLY`** equal to the manifest, no table missing or extra, the dump's table
   set equal to the live catalog's at backup time; the **pgvector extension version** equal to the dump's; the Alembic revision;
   every HNSW index in the restore valid and USED by a nearest-neighbour query that returns the probed row; and always an HNSW
   index **built** in the sandbox (2,000 synthetic vectors) and queried with `enable_seqscan=off` (no live table has one today,
   so this is what proves the image's pgvector builds and uses one); the newest **palace** tarball extracted to a temp dir and
   opened with the zoe-data py3.12 venv's chromadb (`palace_drill_check.py`: the `palace_client` format guard, collection counts
   vs the SQLite rows, and per collection a stored vector queried back to itself, which loads HNSW from the segment files); the
   **host archive**: every file against its manifest sha256, every source label present, the HA export opens and holds
   `core.config_entries`; freshness (newest dump, tarball and archive under 26 h); the drill image vs the live container's.
4. **Guards.** Refuses (quietly) inside 01:35-04:55 or while `zoe-night-window` runs; waits up to 45 min for `zoe-backup` /
   `zoe-offbox-backup`; refuses **with an alert** below 2000 MB MemAvailable (the voice stack is resident). Findings alert over
   Telegram through the crash-loop watcher's sender; a drill that has not passed for 15 days says so. State:
   `~/.local/state/zoe-offbox/last_restore_drill.json`.

Never touched: the live database (the drill only reads its image name), the live palace (`palace_drill_check.py` refuses
`~/.mempalace`), the live backups (read only).

### 7.3 Encryption at rest for the local backups: recommended for the host archive only

- **Host archive: yes, once the age recipient exists** (section 4 step 2). It holds credentials (drop-in env lines, `~/bin`, HA's
  secrets and auth), and old copies of rotated secrets are what a stolen disk or a copied backup dir leaks. Set
  `Environment=ZOE_LOCAL_BACKUP_ENCRYPT=1` in a `zoe-backup.service` drop-in: the archive becomes `host-<ts>.tar.gz.age`, to the
  SAME public recipient as the off-box tiers (`~/.config/zoe-backup/age-recipients.txt`). Cost: the box can no longer read it
  (the identity never lives here), so the weekly drill only checks the age header, and a local restore of host config needs the
  owner's identity.
- **Postgres dumps and the palace tarball: no.** Their job is a fast local restore (a bad migration, a corrupted palace) without
  the owner's vault, and the drill must be able to restore them. Whoever can read `~/.zoe-backups` (0700) can already read the live
  database. The protection that actually covers a stolen box is full-disk encryption (LUKS on the NVMe), an owner decision with
  a boot-unlock cost on a headless Jetson.

### 7.4 Owner steps

1. **HA helper** (any time; needed before HA `.storage` is backed up):
   `sudo install -m 0755 -o root -g root scripts/setup/p0-deroot/libexec/zoe-ha-config-export /usr/local/libexec/zoe-priv/`,
   then `sudo -n /usr/local/libexec/zoe-priv/zoe-ha-config-export storage | tar -tzf -` lists `.storage/...`. Until P0-3 the
   `zoe-temp` grant lets `sudo -n` run it; from P0-0 the `ZOE_BACKUP` alias in `60-zoe-deroot` does. **At P0-2** (HA config to
   `/srv/zoe/homeassistant`), change its `HA_CONFIG` line.
2. **Drill helper** (optional before P0; required at P0-3, when `zoe` can no longer create containers):
   `sudo install -m 0755 -o root -g root scripts/setup/p0-deroot/libexec/zoe-restore-drill-pg /usr/local/libexec/zoe-priv/`.
   Once installed the drill uses it through `sudo -n`; before that it runs the repo copy as `zoe` (docker group). When the
   `zoe-database` image is bumped, bump `PINNED_IMAGE` in it too (a test fails until both match) and re-install.
3. **Units**: `cp scripts/setup/systemd/zoe-backup{,-verify}.{service,timer} ~/.config/systemd/user/ && systemctl --user
   daemon-reload` (both timers are already enabled live; the verify moves from the live 03:30 to Sunday 05:30). The first
   night writes `~/.zoe-backups/host/` and the first dump manifests; run `python3 scripts/maintenance/zoe_restore_drill.py` by
   hand once after that (outside 01:35-04:55, MemAvailable >= 2000 MB) and read its `ok:` lines.
4. **Optional**: local encryption of the host archive (7.3), after section 4 step 3 exists.

### 7.5 The lab (`labs/restore-drill/run.sh`)

A synthetic source database in its own capped `--network none` container (the pinned image), a synthetic palace built with the
real chromadb 1.5.9, a synthetic box, and the REAL scripts end to end: `postgres-nightly-backup.sh` + `pg_dump_manifest.py`,
`mempalace-nightly-backup.sh`, `zoe_host_snapshot.py` + `zoe-ha-config-export`, `zoe_restore_drill.py` + `zoe-restore-drill-pg`.
Gates: `night_busy.sh test`, 01:35-04:55, MemAvailable >= 2000 MB, the test-lane lock.

**2026-10-19: 15/15 cells** (Jetson, pgvector 0.8.6-pg17 pinned digest, chromadb 1.5.9). S1 the real nightly script's manifest:
1,234 multi-line rows counted exactly, a quoted table name, a partitioned parent (0) and its partition (40), the biometric
table listed as data-excluded, pgvector 0.8.6, Alembic 0057, live table set equal; S2 the real palace tarball; S3 the host
archive holds exactly the new sources and none of the exclusions (shadow log, VAPID key, dead `zoe.db`, HA log, recorder,
tts); S4 the HA helper export (`.storage` + `.cloud`, 0600); **D1 the drill green**: every table count, pgvector, Alembic, the
dump's own HNSW index valid + used + returning the probed row, a synthetic HNSW built and used, the palace opened with
chromadb and queried, 14 host files checksum-verified. Negative controls, each red for its own reason only: **N1** bytes
flipped in a dump (sha256 + gzip CRC); **N1b** a dump corrupted inside its zlib blocks with a matching sha256 (`pg_restore`
fails); **N2** a dump missing `chat_messages` (table missing after restore); **N3** the box's older `pgvector/pgvector:pg17`
image as the sandbox (pgvector 0.8.2 vs the dump's 0.8.6); **N4** a palace tarball without its HNSW segment dirs (chromadb
`Error finding id`); **N5** one host file changed (differs from the manifest); **N6** the HA helper refuses a symlinked
`.storage`, skips a symlinked file inside it and refuses a symlinked parent; **N7** a memory floor above MemAvailable
refuses (exit 3) before any container exists; **N8** the drill helper refuses five out-of-set argument lists (exit 2);
**T1** teardown leaves no container, network or scratch dir, and the live database's database list is unchanged.
The A5 lab (`labs/offbox-backup`) was re-run with the host sources in its synthetic box: 19/19 (B2 ships exactly the
new sources to each tier; B4 an unchanged crontab re-encrypts nothing).
