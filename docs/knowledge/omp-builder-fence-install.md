---
type: Runbook
title: omp builder-lane fence — install & drift check
description: How the three oh-my-pi builder-lane fence artifacts (env-scrubbing wrapper, ACP lifetime supervisor, PI_CONFIG_FILES settings overlay) get from the tracked copies in scripts/setup/omp-fence/ onto a host, what breaks if each one is missing, and the sha256 command that detects live-vs-tracked drift.
tags: [omp, oh-my-pi, omnigent, fence, install, security, drift]
timestamp: 2026-08-05T00:00:00Z
---

# omp builder-lane fence — install & drift check

The oh-my-pi (`omp`) builder lane runs inside `zoe-omnigent` behind a three-file fence. Until
2026-08-05 all three files existed **only on this box, untracked** — real protection on one host,
in no template, reproduced by no rebuild. That is the #1409 pattern exactly (`scripts/AGENTS.md`,
"Latency-critical user units must carry their cgroup memory guards IN THE TEMPLATE"), and it is why
they are tracked now.

**The LIVE files remain authoritative until an operator re-installs from the tracked copies.**
Tracking is a *backup and a review surface*; it is not a deploy. Nothing in CI copies these files.
Run the drift check below before assuming the two agree.

Design rationale for each fence condition lives in the file headers themselves (they are heavily
commented on purpose) and in the adoption note `docs/knowledge/omp-builder-adoption.md`, which
arrives via PR #1629 — including the kill-path diagnosis with `file:line` for the orphan-spend hole
the supervisor closes.

## The three artifacts

| tracked copy | installed path | mode | read by |
|---|---|---|---|
| `scripts/setup/omp-fence/omp-omnigent-fenced` | `/home/zoe/.local/bin/omp-omnigent-fenced` | `0755` | the container, first on `PATH` |
| `scripts/setup/omp-fence/omp-acp-supervisor` | `/home/zoe/.local/bin/omp-acp-supervisor` | `0755` | exec'd by the wrapper |
| `scripts/setup/omp-fence/omp-fence.yml` | `/home/zoe/.config/zoe/omp-fence.yml` | `0444` | `omp`, via `PI_CONFIG_FILES` |

Both destination directories are already bind-mounted **read-only** into `zoe-omnigent` at the
identical path by `modules/omnigent/docker-compose.module.yml`
(`- /home/zoe/.local/bin:/home/zoe/.local/bin:ro`, `- /home/zoe/.config/zoe:/root/.config/zoe:ro`),
so nothing inside the container — `omp` included — can rewrite the fence. No compose change is
needed to install; the mounts predate this work.

**No secret is in any tracked file.** The dedicated capped OpenRouter key lives in
`/home/zoe/.config/zoe/openrouter-omp.env`, which defines exactly one variable
(`OPENROUTER_API_KEY_OMP`) and **must never be tracked**. The wrapper references it by *path* only
and promotes it to `OPENROUTER_API_KEY` for its own process. Verified before commit: the key value
appears in none of the three tracked files, and the overlay contains no credential-shaped token at
all.

## Install

Run on the host, as `zoe`, from a checkout of this repo:

```sh
install -Dm0755 scripts/setup/omp-fence/omp-omnigent-fenced /home/zoe/.local/bin/omp-omnigent-fenced
install -Dm0755 scripts/setup/omp-fence/omp-acp-supervisor  /home/zoe/.local/bin/omp-acp-supervisor
install -Dm0444 scripts/setup/omp-fence/omp-fence.yml       /home/zoe/.config/zoe/omp-fence.yml
```

Modes are load-bearing, not cosmetic:

- **`0755` on both `bin` files.** The wrapper is resolved off `PATH` and executed directly; a
  non-executable wrapper is simply not found and `omp` runs unfenced under whatever else matches.
  The supervisor is exec'd as `python3 <supervisor>` so it only needs to be *readable*, but the
  wrapper's precondition tests `[ -r "$SUPERVISOR" ]` and 0755 keeps it runnable standalone for
  debugging.
- **`0444` on the overlay.** It is the authoritative half of the fence and is meant to be
  unwritable even by its owner; `install -m0444` also means a re-install needs an explicit
  overwrite, which is the desired friction.

Re-installing an updated overlay over a `0444` file:

```sh
install -Dm0444 -b scripts/setup/omp-fence/omp-fence.yml /home/zoe/.config/zoe/omp-fence.yml
```

`install` replaces the inode, so the read-only mode does not block it. If a tool ever refuses,
`rm` the target first — never `chmod +w` and edit in place, which drifts the live copy from the
tracked one silently.

There is **no restart step for the fence itself**: the wrapper is exec'd fresh per ACP dispatch. A
running `omp` child keeps the environment it started with, so an install takes effect on the next
dispatch, not the current one.

## What breaks if each file is missing

Each failure is deliberately different, and only one of the three is loud on its own:

- **Wrapper missing** — the worst case, and the SILENT one. Omnigent's `acp:` config block has no
  per-agent `env` field and `acp_executor.py` does `env = os.environ.copy()`, so the child inherits
  the full runner environment — including the **shared** OpenRouter key passed through for the `pi`
  review worker. Without the wrapper `omp` runs completely unfenced on that shared key: no env
  scrub, no `HOME` pin (so it inherits `/root/.claude.json`, `/root/.codex`, `/root/.cursor` and
  Omnigent's whole MCP fleet), no `PI_CONFIG_FILES`, autoqa on, plugin auto-update reachable,
  no model pin. Nothing errors. This is the state the fence exists to prevent, and it is exactly
  what a rebuilt or second host had before these files were tracked.
- **Supervisor missing** — the wrapper **fail-closes with exit 78** ("lifetime supervisor missing")
  and the dispatch fails as a harness error. That is intentional: the supervisor is part of the
  fence, not an optional extra. Without it a metered `omp` child outlives Omnigent's kill switch —
  measured in trial-002, a child kept working 49s past its "kill", burned two more turns, and sat
  orphaned for 8 minutes on a dead ACP channel. `python3` missing on `PATH` fail-closes the same
  way, also exit 78.
- **Overlay missing** — two layers fail. The wrapper's precondition `[ -r "$OVERLAY_FILE" ]` exits
  78 first; and if that check were ever removed, `PI_CONFIG_FILES` would point at nothing and
  `omp`'s `#loadOverlayYaml` is a **strict** loader, so it hard-errors rather than falling back to
  unfenced defaults. Bypass both and `omp` is unpinned (no `enabledModels` — trial-001 resolved
  `openai/gpt-5.5` on its own and spent ~$2–4 on a kata), telemetry-on (`dev.autoqa` defaults
  **true** and posts model-authored free text plus a persistent install UUID), plugin auto-update
  reachable, project-scoped MCP config re-enabled, and `web_search` back on.
- **Key file missing** — exit 78 as well ("dedicated key file missing"), before any scrub-and-run.

## The key file is PARSED, never sourced (2026-08-06)

`/home/zoe/.config/zoe/openrouter-omp.env` must define `OPENROUTER_API_KEY_OMP` and **nothing else**,
and the wrapper now enforces that rather than trusting it. Until 2026-08-06 it did `. "$KEY_FILE"` —
the file was *executed as shell*, **after** the credential scrub and **after** every precondition, so
any extra line landed inside the fence with nothing left to catch it. Measured against the pre-fix
wrapper with a crafted file: a pasted `export ANTHROPIC_API_KEY=…` reached the `omp` child, and a
`SUPERVISOR=/tmp/evil` line re-pointed the wrapper's final `exec` at an arbitrary path. Both now exit
78.

The reader tolerates comments, blank lines, an `export ` prefix and quoted or unquoted values, and
fail-closes on anything else: a second variable, a duplicate assignment, a command, an empty value, an
absent one, or a value carrying characters outside the key alphabet (which is how `KEY=x; export
OTHER=y` gets caught — one assignment to a naive parser). **Diagnostics name the file and a line
NUMBER, never the line text**, because a rejected line may itself be the key and stderr goes to
omnigent's logs.

This is defence in depth, not a live hole: the key file is host-owned under `/home/zoe/.config/zoe`,
which the container sees `:ro`, so nothing inside the fence can append to it. The exposure it closes
is a mistake by whoever edits that file **on the host**.

## The supervisor binds to its parent BEFORE arming (2026-08-06)

`main()` used to call `_set_pdeathsig()` and *then* read `os.getppid()`. If the Omnigent runner died
inside that window the supervisor was reparented to init, `original_ppid` recorded **1**, and the
`getppid` poll then compared 1 against 1 forever — nothing reparents an already-init-owned process
again — while `Popen` still launched the metered child. Both parent-death detectors defeated at once,
in the one case with no backstop: the orphan-spend hole the supervisor exists to close, reopened by a
startup ordering bug. Reproduced against the pre-fix copy by double-forking to ppid 1; the child
launched, silently.

Now: capture `original_ppid` first, **refuse outright (exit 78) if it is already `1`**, arm
`PR_SET_PDEATHSIG`, re-check `getppid()` before `Popen`, and re-check once more immediately after so
a loss in the last narrow window becomes an immediate group kill instead of a `POLL_S` wait. A
refused dispatch costs one harness error; an unkillable metered child costs money until someone
notices.

## Drift check (run this before trusting the tracked copies)

The tracked copies were committed **byte-identical** to the live files, so the correct check is a
plain checksum comparison. From a checkout:

```sh
sha256sum \
  /home/zoe/.local/bin/omp-omnigent-fenced \
  /home/zoe/.local/bin/omp-acp-supervisor \
  /home/zoe/.config/zoe/omp-fence.yml \
  | awk '{print $1}' > /tmp/omp-fence.live

sha256sum \
  scripts/setup/omp-fence/omp-omnigent-fenced \
  scripts/setup/omp-fence/omp-acp-supervisor \
  scripts/setup/omp-fence/omp-fence.yml \
  | awk '{print $1}' > /tmp/omp-fence.tracked

diff -u /tmp/omp-fence.tracked /tmp/omp-fence.live \
  && echo "omp-fence: live matches tracked" \
  || echo "omp-fence: DRIFT — reconcile before trusting either copy"
```

Per-file, if you want to see *which* one drifted:

```sh
for f in omp-omnigent-fenced omp-acp-supervisor; do
  cmp -s "scripts/setup/omp-fence/$f" "/home/zoe/.local/bin/$f" \
    && echo "ok   $f" || echo "DRIFT $f"
done
cmp -s scripts/setup/omp-fence/omp-fence.yml /home/zoe/.config/zoe/omp-fence.yml \
  && echo "ok   omp-fence.yml" || echo "DRIFT omp-fence.yml"
```

Baseline at the tracking commit (2026-08-05) — all three matched live:

```
b447295fa3da2c9db5f47392e0b14c80e184f82a9d507ea725143084a9b7d873  omp-omnigent-fenced
beecd9977a40d6716285169a39b5a00feca29c070a69438e32ee7e98f62b2037  omp-acp-supervisor
2884620cdae2c8c040e0d84d19c237001edb9711d2f3e9770c32ab6c454a966a  omp-fence.yml
```

**Superseded 2026-08-06 by the P2 hardening pass** (key file parsed instead of sourced; supervisor
captures its expected parent before arming `PR_SET_PDEATHSIG`). New **tracked** hashes:

```
f0b95dcc7f5e0d05aa18dac50cb26f5eb89b6c44197a4971588d80e0a402fbf9  omp-omnigent-fenced
84dd02450c2ac2488b10de12de002326875b5ea7dfc5206b414df50c8393633a  omp-acp-supervisor
2884620cdae2c8c040e0d84d19c237001edb9711d2f3e9770c32ab6c454a966a  omp-fence.yml   (unchanged)
```

**Between that merge and the operator re-install, tracked ≠ live is EXPECTED**: the drift check will
correctly report a difference for the two `bin` files, and it means "the repo carries a fix the host
has not taken yet" — not tampering. The `LIVE file wins by default` rule below is suspended for
exactly that window; here the tracked copy is the newer one. Run the install block above, then
re-run the drift check: all three must match, and the live hashes must equal the tracked ones
recorded here. If they do not, do not guess — reconcile before the next builder-lane dispatch.

**On drift, the LIVE file wins by default** — it is what actually fenced the last dispatch. Copy it
back into `scripts/setup/omp-fence/` and open a PR, rather than overwriting the host from a tracked
copy that may be older than the fix someone made in place.

Two consequences of the byte-parity contract, both deliberate:

- **Any edit to a tracked copy breaks checksum parity with live until an operator re-installs — so
  change the file and re-install in the same pass, or leave it.** That includes cosmetics: the two
  `bin` files carried a stale "STAGED, NOT INSTALLED. APPLY.md copies this to …" header until
  2026-08-06, and it was corrected only because that pass was already changing and re-installing
  them. Never tidy wording on its own.
- The tracked overlay is committed `0644`, not `0444`. Git records only the executable bit, so the
  read-only mode is a property of the **install command**, not of the tracked file.

## Doctrine pinned by test

`tests/unit/test_omp_fence_doctrine.py` (`ci_safe`, offline) pins the properties that make these
files a fence rather than three scripts:

- the wrapper **execs the supervisor, never `omp` directly**, and its preconditions are fail-closed
  (exit 78) for a missing supervisor, overlay, key file, or `python3`;
- the wrapper **parses the key file, never sources it**, and exits 78 on anything that is not a
  single `OPENROUTER_API_KEY_OMP` assignment (see below);
- the wrapper arms `OMP_SUPERVISOR_WATCH_STDIN=1` **only** for the `acp` subcommand (a one-shot
  `omp config get` must stay byte-transparent on stdin — relaying it broke the fence read-back);
- the supervisor puts the child in a **new session / process group** (`start_new_session=True`) and
  kills the whole **group** (`killpg`), not the single PID, and it **captures its expected parent
  before arming `PR_SET_PDEATHSIG`**, refusing to spawn at all if that parent is already gone;
- the overlay pins `enabledModels` and disables `dev.autoqa` and `marketplace.autoUpdate`, with
  `autoUpdate: "off"` **quoted** (bare `off` is a YAML 1.1 boolean and the setting is a string enum).

Each assertion was negative-controlled at authoring time: breaking the property turns the test red,
restoring it turns it green.

## Related

- `docs/knowledge/omp-builder-adoption.md` — the adoption record and the full kill-path diagnosis
  (arrives via PR #1629; not on `main` at the time of writing).
- `scripts/AGENTS.md` — the `scripts/setup/` ownership contract and the tracked-template doctrine
  this follows.
- `modules/omnigent/docker-compose.module.yml` — the two read-only bind mounts the fence depends on.
