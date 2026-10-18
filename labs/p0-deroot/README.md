# P0 de-root lab

Hermetic proof of the P0 permission model in `docs/architecture/p0-deroot.md`. Record, not a contract.

**What runs:** one Docker container (no network, 512 MB cap, not `--privileged`) booting systemd 249 (Ubuntu 22.04,
the Jetson's own version) as PID 1. `lab/provision.sh` builds the post-P0 box in miniature and installs
`scripts/setup/p0-deroot/` **verbatim**: the sudoers allowlist, the two root helpers, the Docker proxy unit (the real
`wollomatic/socket-proxy` 1.13.1 binary, digest-pinned), and the three service drop-ins on top of stand-in base units
that keep the voice guards (`MemorySwapMax=0`, `MemoryLow`, `LimitMEMLOCK`, zoe-data's stdout capture). A fake
dockerd (`lab/fake_dockerd.py`) owns `/var/run/docker.sock` and logs what reaches it. Each stand-in
(`lab/standin.py`) answers `/probe` from inside its own sandbox; `lab/cells.py` scores the cells, prints them on the
console and powers off. Synthetic secrets only; the host's users, sudoers, Docker daemon and services are untouched.

**Run:** `labs/p0-deroot/run.sh [--keep-image]`. It refuses 01:35-04:55, waits for `MemAvailable >= 1500 MB`
(`P0LAB_WAIT_MEM_S`, default 3600) before taking `flock -x /tmp/zoe-test-lane.lock`, runs niced, removes the
container (and the image unless `--keep-image`) and asserts the container is gone. Exit 0 = every cell passed.
The last result is written to `last-result.json` and the boot console to `last-console.log` (both git-ignored).

**Cells:** S (each service, from inside its sandbox), Z (the `zoe` login uid after P0-3), C (negative controls
proving the instruments see power when it exists). The list is in section 6 of the design doc.

**Not provable here, verified live first (design doc phase P0-0):** the Jetson GPU as `zoe-model` (a CUDA smoke),
the real Docker CLI through the proxy including the `exec -i` stream hijack, and the real units under the sandbox.

## Result: lab pending (memory)

**Not yet run to completion.** On 2026-10-18 the box sat at 0.8-1.2 GB `MemAvailable` all day, below the 1500 MB
gate (it frees up when the owner restarts the Omnigent container). One attempt got through the gate briefly at
about 10:00: the image built and the container booted and exited, but it printed **no CELL lines**. The cause is not
diagnosed. It may be systemd failing to start under the non-privileged flags (a read-only cgroup mount), or the
console capture. `run.sh` now saves the full boot console to `last-console.log` (git-ignored scratch, not
committed) for the next run. Treat every lab claim in the design doc as **unproven until a run reports all cells
PASS**. If systemd will not boot without `--privileged`, the documented fallback is a privileged lab container. That
is still hermetic for the host's users, but say so in this section.

**What IS verified (static, on the host, read-only, 2026-10-18):**
- `visudo -c -f sudoers.d/60-zoe-deroot`: parsed OK. A deliberately broken copy is rejected.
- `systemd-analyze verify` (systemd 249) of `zoe-docker-proxy.service` and of three stand-in units carrying the
  real `50-p0-deroot.conf` drop-ins: no diagnostics from our files (only pre-existing host-unit noise). Negative
  control: a misspelt directive in a drop-in beside them is reported, so the drop-ins are really loaded and checked.
- `tests/unit/test_p0_deroot_files.py` (ci_safe): 44 passed. It covers the sudoers linter and its negative
  controls, the proxy allow/deny table, the drop-in sandbox, the no-`/home`-bind-source and GPU-group rules, and
  the helper argument refusal.
