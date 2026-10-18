# Restore-drill lab (deep dive 2 S0-8)

Hermetic proof of the weekly restore drill and the host backup sources (`docs/knowledge/offbox-backup.md` section 7):
a synthetic source database in its own throwaway container, a synthetic palace built with the real chromadb 1.5.9, a
synthetic box, and the REAL scripts end to end - `postgres-nightly-backup.sh` + `pg_dump_manifest.py`,
`mempalace-nightly-backup.sh`, `zoe_host_snapshot.py` + `zoe-ha-config-export`, `zoe_restore_drill.py` +
`zoe-restore-drill-pg` (the repo copies, exactly what the owner installs). Synthetic data only; nothing touches the live
database, the live palace or the live backups (T1 checks the live database's database list is unchanged).

    labs/restore-drill/run.sh

- **Gates:** refuses while `~/.zoe/agent-tools/night_busy.sh test` says pause, inside 01:35-04:55, or with MemAvailable
  < 2000 MB; takes the shared test-lane lock. Containers: the source DB 384 MB and the drill sandbox 512 MB, never at once.
- **Cells:** S1-S4 (the nightly script's manifest counted from the dump, the palace tarball, the host archive = exactly
  the new sources, the HA helper export), D1 (the drill green on good backups), N1-N8 (negative controls: a corrupted
  dump, a dump corrupted before its manifest, a dump missing a table, a restore image with another pgvector, a palace
  without its HNSW files, a tampered host-archive file, the HA helper never following a planted symlink, the memory
  floor, the helper's argument set), T1 (teardown). Each prints PASS/FAIL; exit 1 on any FAIL.

Record: see `docs/knowledge/offbox-backup.md` section 7.5.
