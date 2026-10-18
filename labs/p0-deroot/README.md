# P0 de-root lab

Hermetic proof of the P0 permission model in `docs/architecture/p0-deroot.md`. Record, not a contract.

**What runs:** one Docker container (no network, 512 MB cap, **not** `--privileged`) booting systemd 249 (Ubuntu
22.04, the Jetson's own version) as PID 1. `lab/provision.sh` builds the post-P0 box in miniature and installs
`scripts/setup/p0-deroot/` **verbatim**: the sudoers allowlist, the two root helpers, the tmpfiles fragment, the
Docker proxy unit (the real `wollomatic/socket-proxy` 1.13.1 binary, digest-pinned), and the three service drop-ins on
top of stand-in base units that keep the voice guards (`MemorySwapMax=0`, `MemoryLow`, `LimitMEMLOCK`, zoe-data's
live `~/.zoe-logs` stdout capture). A fake dockerd (`lab/fake_dockerd.py`) owns `/var/run/docker.sock` and logs what
reaches it. Each stand-in (`lab/standin.py`) answers `/probe` from inside its own sandbox; `lab/cells.py` scores the
cells into `/run/p0lab-cells.txt`, which `run.sh` reads with `docker exec`. Synthetic users and secrets only.

**Isolation, stated plainly:** systemd does not boot here under `--cgroupns=private` (Docker mounts the cgroup tree
read-only; measured: no output at all). The lab therefore runs with the **host cgroup namespace and a read-write
`/sys/fs/cgroup`**, plus `SYS_ADMIN` and unconfined seccomp/apparmor so the units' mount sandboxing works inside the
container. That container can see the host's cgroup tree. It has no network and cannot see the host's users, sudoers,
Docker socket or services. It is a throwaway, removed at the end of every run.

**Run:** `labs/p0-deroot/run.sh [--keep-image]`. It refuses 01:35-04:55, waits for `MemAvailable >= 1500 MB`
(`P0LAB_WAIT_MEM_S`, default 3600) before taking `flock -x /tmp/zoe-test-lane.lock`, runs niced, removes the
container (and the image unless `--keep-image`) and asserts the container is gone. Exit 0 = every cell passed;
exit 5 = no result (read `last-console.log`: docker logs, `systemctl --failed` and the warning journal, captured
whether or not the boot worked). `last-result.json` and `last-console.log` are git-ignored.

**Not provable here, verified live first (design doc phase P0-0):** the Jetson GPU as `zoe-model` (a CUDA smoke),
the real Docker CLI through the proxy including the `exec -i` stream hijack, and the real units under the sandbox.

## Result: 47/47 cells pass (2026-10-18 20:14, MemAvailable 1.7 GB, lane lock held)

- **S (per service, from inside its own sandbox), 3 x 11 + 2:**
  - Each service runs as its own uid: `zoe-svc` 999, `zoe-flue` 998, `zoe-model` 997. None is in `sudo` or `docker`.
  - `sudo -n true` fails (rc 1), and the raw Docker socket gives `PermissionError`.
  - zoe-data gets the proxy allowlist (MA start 200, create 403, HA exec 403). Flue and the model tier cannot reach the proxy at all.
  - No service can read another's secret (`PermissionError`). The real `/home/zoe/.ssh` does not exist in any view.
  - zoe-data and Flue can read the checkout but not write it (`OSError`). The model tier cannot see the checkout at all.
  - Each service has its own secret, except the model tier, which has none. Each reads its job input and writes its state. `/etc` is read-only.
  - `MemorySwapMax=0`, `MemoryLow` and `LimitMEMLOCK=infinity` survive the drop-ins.
  - The model tier is in `video` (gid 44).
  - zoe-data's stdout capture works, at `/var/log/zoe-data`.
- **Z (`zoe` after P0-3), 7 cells:**
  - `sudo -n true` fails.
  - The allowlist allows exactly its 4 probed entries and refuses all 9 escapes: shells, other arguments, `systemctl edit`, another unit, a trailing extra arg, visudo, docker.
  - `zoe` cannot read the service secrets, and the raw socket is refused.
  - The proxy gives the expected verdict in **20 of 20** cases: traversal, `%2F`, query strings, create, build, pull, exec into HA or Omnigent, a zoe-ui restart, delete, and PUT archive are all refused.
  - The helpers reject bad arguments (exit 2).
  - `zoe-admin` needs a password.
- **C (instrument controls), 5 cells:**
  - A `zoe-pre` user with **today's** rights CAN `sudo` and CAN reach the socket.
  - The fake daemon saw only allowed calls (8 distinct, 0 leaked).
  - `visudo -c` passes and catches a broken file.
  - `systemd-analyze verify` is clean on all four units.

**Found by the lab and fixed in the deliverables:** systemd opens `StandardOutput=append:` files **before** it builds
the namespace and before `LogsDirectory=` exists. So the live `20-capture-output.conf` path (`~/.zoe-logs`) and a
not-yet-created `/var/log/zoe-data` both fail with `Failed at step STDOUT`. The zoe-data drop-in now points the
capture at `/var/log/zoe-data/`, and `tmpfiles.d/zoe-p0-deroot.conf` creates that directory at boot.
