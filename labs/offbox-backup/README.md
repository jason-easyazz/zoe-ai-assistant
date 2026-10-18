# Off-box backup lab (A5)

Hermetic proof of `scripts/maintenance/zoe_offbox.py` + `scripts/setup/offbox-backup/the411/` against a real
append-only rest-server. Synthetic data only; nothing touches the box's backups, the411 or the network beyond loopback.

    labs/offbox-backup/run.sh

- **Gates:** refuses 01:35-04:55 and MemAvailable < 1500 MB; takes `flock /tmp/zoe-test-lane.lock`.
- **Pins:** the rest-server and restic images by index digest, read from `the411/compose.yaml` (the lab tests the shipped
  file); restic 0.19.1 and age v1.3.2 installed into `.bin/` by `scripts/setup/offbox-backup/install_tools.sh` (sha256).
- **Cells:** B1-B5 (backup, tier policy, ciphertext-only stage, drill checksum, dedup, forget propagation), N1-N7
  (negative controls), R1-R3 + R1b (the 35-day household bound, the411's prune, and the count-based policy that fails it),
  teardown. Each prints PASS/FAIL; exit 1 on any FAIL.

Record (2026-10-18, Jetson, restic 0.19.1, rest-server 0.14.0, age v1.3.2): all cells PASS; details in
`docs/knowledge/offbox-backup.md` section 5.
