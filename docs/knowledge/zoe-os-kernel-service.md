---
type: Runbook
title: Optional Zoe OS kernel service
description: Draft owner installation, deploy, backup, health and rollback procedures.
---

# Optional Zoe OS kernel service (DRAFT, not installed)

These are OWNER steps, never agent actions. Run outside the brain-window quiet hours,
with the coordinator's memory clearance. This draft has not been tested or applied.
The acceptance number is zero lost acknowledged adds across restart and restore;
read the authority kill-point tests and the WAL snapshot test results before enabling.

The code remains in `labs/zoe-os/`. The only source-location setting is
`KERNEL_SOURCE_REL` in `scripts/maintenance/zoe_os_kernel.conf`; launch and deploy
change detection use it. The same file defines the database setting
`ZOE_OS_KERNEL_DB` (default `~/.zoe/zoe-os/zoe-os.db`) and state directory
`OS_KERNEL_STATE` (default `~/.zoe/os-shadow`), holding `people.json`, `creds.json`
and `k.sock`. The socket and credentials are reissued on start, mode 0600.
Keep the client on the same paths. Overrides belong in unit drop-ins; writable
path overrides must also replace `ReadWritePaths`. The default backup and drill
assume the default database location; a custom installation must adapt the drill's
presence check before use.

Prerequisite: land and validate the authority client integration first. This branch
has neither `services/zoe-data/os_authority.py` nor `os_shadow.py`, nor their flag
registrations. The lab's documented names are `ZOE_OS_LIST_SHADOW` (off/shadow) and
`ZOE_OS_LIST_ADD_AUTHORITY` (legacy/kernel). Their production defaults, registration,
timeout fallback and credential refresh must be verified on that integration branch.
No flags or migrations are introduced here. Do not enable authority until its
real-voice replay gate passes. The infrastructure never changes either flag.

## One-time installation and database move (OWNER ONLY)

Review the files before copying. Back up the existing installed unit and its
owner drop-ins outside the checkout if replacing an earlier installation. Preserve
those drop-ins; do not overwrite other service templates.

```bash
cd "$HOME/assistant"
umask 077
mkdir -p "$HOME/.config/systemd/user" "$HOME/.zoe/os-shadow" "$HOME/.zoe/zoe-os"
chmod 700 "$HOME/.zoe/os-shadow" "$HOME/.zoe/zoe-os"
systemctl --user disable --now zoe-os-shadow-kernel.service
systemctl --user stop zoe-os-authority-kernel.service
```

An absent old unit may report “not loaded”; verify no kernel process remains before
continuing. Keep an existing roster. If absent, the owner creates one from member
IDs without printing them (requires the client's agreed roster contract):

```bash
docker exec zoe-database psql -U zoe -d zoe -tAc 'SELECT id FROM users ORDER BY id' |
  python3 -c 'import json,sys; from pathlib import Path; p=Path.home()/".zoe/os-shadow/people.json"; people=sys.stdin.read().split(); assert people and not p.exists(); p.write_text(json.dumps({"people":people}))'
```

Move the old store only with both daemons stopped. The online snapshot checks
integrity and the kernel marker before deletion. Never overwrite a destination:

```bash
set -e
old="$HOME/.zoe/os-shadow/kernel/zoe-os.db"
new="$HOME/.zoe/zoe-os/zoe-os.db"
if [ -f "$old" ]; then
  test ! -e "$new"
  python3 -B scripts/maintenance/zoe_os_kernel_ops.py snapshot "$old" "$new"
  chmod 600 "$new"
  rm -f -- "$old" "$old-wal" "$old-shm"
fi
cp scripts/setup/systemd/zoe-os-authority-kernel.service "$HOME/.config/systemd/user/"
mkdir -p "$HOME/.config/systemd/user/zoe-backup.service.d"
cp scripts/setup/systemd/zoe-backup.service.d/50-os-kernel.conf "$HOME/.config/systemd/user/zoe-backup.service.d/"
systemctl --user daemon-reload
systemctl --user enable --now zoe-os-authority-kernel.service
bash scripts/maintenance/zoe_os_kernel.sh health
bash scripts/maintenance/zoe_os_kernel.sh report
```

Check the unit's effective memory and sandbox settings with `systemctl --user show
zoe-os-authority-kernel.service -p MemoryHigh -p MemoryMax -p Result`.
Verify user-manager sandbox support before enabling product authority. The unit
sets 64M high / 96M max against the supplied ~20 MB resident observation, low
CPU/I/O weights and OOM score +500. It takes none of the voice stack's protections (no swap lock, no memory reserve);
a kernel leak is bounded and expendable. This is not proof against global OOM.
Restart waits 10s and stops retrying after five starts in 300s; after correction,
the owner uses `systemctl --user reset-failed zoe-os-authority-kernel.service`.

## Deployment and monitoring

CD runs the optional step after the tree reset and main-service restart. There is
no hard dependency: the main service must boot and use its legacy path without it.
Only installed, enabled authority units are touched. Change detection uses the
last successful deploy, not the pre-reset tree; unhealthy units and pending failed
updates retry even on an unchanged tree. A night marker defers the restart.
A restart failure or failed write-health probe warns in the log and job summary,
leaves `~/.zoe/deploy/kernel-update-pending`, and does not block other services.
Unexpected helper failures are tolerated by the workflow and visibly fail that
optional step. No unit is copied or enabled by CD; reviewed unit changes are owner
reinstallation steps. There is no automatic kernel database rollback.

Ground truth adds `kernel: not installed` alone when unit, socket and database
are absent. Otherwise it reports unit state, bounded read-only wire health, database
presence/bytes/age, and only set/unset/unreadable for the two flags in zoe-data's
running process. It never prints credentials, configured paths or flag contents.
Read-only health checks liveness/schema/database access; deploy health additionally
proves a write transaction succeeds. This is on-demand monitoring, not a new pager.

The backup drop-in adds a final step to the existing nightly job, preserving its
schedule and earlier steps. No database means silent success and no new directory.
A present database is online-snapshotted including committed WAL, integrity and
kernel-marker checked, compressed and atomically published under
`~/.zoe-backups/zoe-os/`; only then are older copies pruned (seven, maximum 35 days).
The weekly drill restores to scratch and checks integrity/marker/freshness; missing
kernel backups fail only when the default database exists. Existing archives remain
checked even if the service was removed. Credentials/socket are never backed up.

## Rollback and restore (OWNER ONLY)

Immediate rollback: `systemctl --user disable --now zoe-os-authority-kernel.service`.
The validated client must fall back. Do not enable the shadow unit against the
moved authority store. Keep the database so member erasure can still reach it.
For a code rollback, use a reviewed revert through the normal deploy gates.
Remove only `~/.config/systemd/user/zoe-backup.service.d/50-os-kernel.conf` and run
`systemctl --user daemon-reload` to undo backup enrollment.

To restore, stop the authority first, choose a verified archive, decompress it to a
scratch file, run `zoe_os_kernel_ops.check_database` on that copy, then replace the
stopped database and remove its stale `-wal`/`-shm`. Set mode 0600, reapply every
forget newer than the archive, restart and run `health` plus the integration's
reconcile probe. Never restore an older record into a running kernel, and never
silently discard an acknowledged write by restoring a backup as code rollback.
