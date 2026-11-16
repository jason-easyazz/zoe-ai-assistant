# P0-2 root-path lab

Hermetic proof of phase P0-2 in `docs/architecture/p0-deroot.md` (section 8). Record, not a contract.

**Why a sibling of `labs/p0-deroot/`, not new cells in it:** that lab boots systemd to prove the P0-0/P0-3/P0-4
end state (47 cells, its own record); P0-2 is about containers and mounts on the box BETWEEN P0-3 and P0-4. A
container runtime cannot be modelled inside the systemd lab without Docker-in-Docker (privileged), and mixing the
two would rewrite the other lab's record. This lab needs no systemd, so it runs with less: no host cgroup tree.

**What runs:** one Docker container from the digest-pinned `python:3.12-slim-bookworm` image already on the box
(no package install, no network at build or run), NOT `--privileged`, no host mount of any kind, private cgroup
namespace, default seccomp, 256 MB, and one added capability, `SYS_ADMIN`, so the runtime stand-in can mount and
unshare INSIDE it. `lab/runtime.py` stands in for the container runtime: a bind source is `mount --bind`-ed as
root at every start (following symlinks, as the daemon does), a restart policy restarts on a crash, and Docker's
default capability set, `user:`, `cap_drop: [ALL]` and `no-new-privileges` are applied with `setpriv`. A
"privileged" container is uid 0 with every capability and the filesystem writable; an unprivileged one sees the
rest of the filesystem read-only and writes only through its binds. `lab/svc.py` holds the stand-in container
processes, `lab/fake_docker.py` logs what the exec helper would pass to Docker, and `lab/cells.py` scores. The
deliverables in `scripts/setup/p0-deroot/p0-2/` and `libexec/zoe-omnigent-exec` are used VERBATIM. Proof of a
root route is always harmless: a marker file owned by uid 0 in `/root` (0700 root), or a synthetic root-only
canary read back. Synthetic names, users and contents only.

**Run:** `labs/p0-2-root-paths/run.sh [--keep-image]`. It refuses 01:00-05:00, waits (up to
`P02LAB_WAIT_MEM_S`, default 1800 s) for no voice probe running and `MemAvailable >= 2000` MiB, then takes
`flock -x -w 14000 /tmp/zoe-test-lane.lock`, runs niced, re-checks, removes the container (and the image unless
`--keep-image`) and asserts it is gone. Exit 0 = every cell passed; 5 = no result (read `last-console.log`).
`last-result.json` and `last-console.log` are git-ignored.

**Not provable here, still to be checked in the live rehearsal (not done yet):** Docker's own resolution of a bind source, compose
`!override` merging, the real images' needs under `cap_drop`, and the real exec through sudo.

## Cells

- **C (instruments):** the service user cannot write `/root` directly and has no sudo or Docker group, so every
  marker below came through the route under test.
- **R1 config-load (2.6 #1):** a privileged container runs a plugin the service user dropped into its config dir
  (a); after the move the same write is refused and nothing runs, while still privileged (b); the source dir owned
  by the service user again reopens it (b-nc); the hub loads its plugin and writes its state (c).
- **R5 dormant definition (new):** a stopped privileged definition still binds the OLD path (a); removed, `inspect`
  passes and nothing runs (b); back again, it reopens (b-nc); the moved hub runs (c).
- **R2 bind swap (2.6 #2) and R2f file bind (the realtime-media config):** the service user crashes the running
  service through a bad file, the restart policy re-resolves a source it swapped for a symlink to `/root` (a); a
  root-owned chain refuses the rename (b); one parent component owned by the service user reopens it (b-nc); the
  service works (c). The file case reads a root-only canary through a swapped config file.
- **R3 set-uid plant (new):** an unprivileged root container plants a set-uid `cp` in a traversable bind (a); each
  of three layers closes it alone, each with its control: source not traversable, non-root container without
  capabilities, `/srv/zoe` `nosuid,nodev` from `srv-zoe.mount` (b-1..3); the service works (c).
- **R4 root unit (2.6 #3, and a live instance):** a root unit runs a file the service user owns (a); root-owned it
  is refused (b); the file back to the service user, or a root file in a service-user directory, reopens it and
  `verify` catches the second (nc1, nc2); the unit runs (c).
- **O (B4 exec helper):** an allowed exec reaches Docker as `-u 1000:1000` with the arguments untouched; another
  container, another user, `--privileged`, `-e`, a `..` work dir, `bash` and an absolute program are refused
  before Docker is called.
- **X (the shipped manifest and tool):** the public manifest applies and verifies on synthetic copies of the
  design's paths (X1); verify catches a writable parent (X2); apply is idempotent (X3); revert renames and keeps
  the old tree (X4); apply refuses a symlinked old path (X5); the copy carries a planted file, so the rehearsal
  reviews first (X7); `srv-zoe.mount`'s options are `nosuid,nodev` (X8).

## Result: 48/49 cells pass (2026-11-14 23:01, MemAvailable >= 2000 MiB, lane lock held, teardown asserted)

- Every "route exists" cell reproduced its route; every fix closed it; every negative control reopened it;
  every "still works" cell passed. Classes: R1, R2, R2f, R3 (all three layers), R4 (both controls), R5.
- O cells 10/10; X cells 7/7 (the public manifest applies and verifies verbatim).
- **FAIL R5b-fix-remove-dormant-definition-closes:** the route itself closed (no root marker after the dormant
  definition was removed), but `inspect` still exits 1, because the REMAINING hub is modelled privileged (cell R1
  keeps it privileged to show ownership alone closes the config-load route) and `inspect` fails every privileged
  container by design. Not patched. It says the fix list is incomplete for any container that stays privileged:
  `inspect` passes only once it is de-privileged (P0-6). On the live box the main hub is already unprivileged.
