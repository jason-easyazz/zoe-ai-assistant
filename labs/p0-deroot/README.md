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
The last result is written to `last-result.json`.

**Cells:** S (each service, from inside its sandbox), Z (the `zoe` login uid after P0-3), C (negative controls
proving the instruments see power when it exists). The list is in section 6 of the design doc.

**Not provable here, verified live first (design doc phase P0-0):** the Jetson GPU as `zoe-model` (a CUDA smoke),
the real Docker CLI through the proxy including the `exec -i` stream hijack, and the real units under the sandbox.

## Result

(filled in from the first passing run)
