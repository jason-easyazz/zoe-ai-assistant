---
type: Runbook
title: The Hindsight bake-off is one command (owner's page)
description: How to run the pre-registered memory bake-off (Z0, Z0-off, H0, H1, H2) with one command inside a brain-stop window, what the window does to the box, how long it takes, how to read the verdict (G0-G3 per arm and the winner clause), how to abort, and what is still unverified. The embeddings shim, the Hindsight adapter and the runner are built and tested against fakes; the real Hindsight server has not yet been in the loop.
tags: [memory, bake-off, hindsight, zmb, runbook, brain-window, owner]
timestamp: 2026-10-06T07:00:00Z
---

# The Hindsight bake-off: one command

Decision record: `docs/research/memory-system-decision-2026-10-05.md` section 6 (arms, cells, the G0-G3 rule fixed in advance).
Bench: `docs/knowledge/zoe-memory-bench.md`. Install record: `/home/zoe/.zoe/bakeoff-2026-10/G0-install-report.md`.

## The command

```bash
cd <a worktree of the repo>                      # the draft report lands in its docs/research/ (or pass --docs-dir)
scripts/perf/zmb/bakeoff_window.sh --dry-run     # FIRST: prints every step, the generated Gemma clone command, the schedule. Changes nothing.
scripts/perf/zmb/bakeoff_window.sh               # the window: about 85 minutes, hard cap 90, ends with the live brain restored
```

Everything else (`--arms H1,H2`, `--cap-min`, `--docs-dir`) is optional. Run it from a worktree: it only writes an untracked markdown draft, but the
usual rule stands (no work in the live checkout).

## What it does to the box

The voice stack is **down for the whole window** (the live `llama-server.service` is stopped; a clone of the same model on `:11500` takes
its RAM). Nothing else is touched: no `zoe-data` restart, no live Postgres, no palace, no backups, no Kokoro.

| Step | What | Undone by |
|---|---|---|
| preflight | refuses if `/tmp/zoe-brain-window.lock` is held; waits until the panel has been quiet 10 min (the `land_voice_pr.sh` check) and no landing / samantha bar runs (anchored `pgrep`); refuses if MemAvailable < 1.2 GB, the router's embedding model is not on disk, the live brain is not active, or the window would overlap 01:45-03:15 / 04:18-04:52 | nothing was started |
| 1 | scratch Postgres `zoe-bakeoff-pg` (compose, 256 MB cap, loopback `:55432`) | `docker compose down` |
| 2 | loopback embeddings shim `zoe-bakeoff-embed` (`:11501`, the router's bge-small ONNX, about 135 MB) | `systemctl --user stop` |
| 3 | **stop `llama-server.service`** | `systemctl --user start` + health poll |
| 4 | Gemma clone `zoe-bakeoff-gemma` (`:11500`): command GENERATED from `systemctl --user cat llama-server.service` and its drop-ins, same model and flags, `--parallel 1`, same MemorySwapMax / MemoryLow | `systemctl --user stop` |
| 5 | `hindsight-api` `zoe-bakeoff-hindsight` (`:18888`, loopback env from `hindsight.env.example`, in-process egress audit hook on) | `systemctl --user stop` |
| measure | see below | |
| restore | stops the three units and the container, starts the live brain, polls `:11434/health`, releases the lock. Runs on EVERY exit path (refusal after start, memory floor, hard cap, any exception, Ctrl-C); a marker file `WINDOW_OPEN` makes the shell wrapper re-run it if the driver is killed hard | |

Guards: MemAvailable below 1.2 GB at any time aborts and restores; 90 minutes is a hard cap (7 minutes are always kept for restore and report).

## What it measures, and how long (planned minutes; `--dry-run` prints the live numbers)

1. Z0 and Z0-off in the lab on three seeds (1): the control and the negative control. Seeds: `zmb-v1` (baseline) plus two held-out.
2. Forgetting probes start for each Hindsight arm (1): forget an invented friend, check at t+0. The **real t+6 min** check (wait, replay the transcript
   through the arm's own nightly pass and a late model writer, check again) runs later, between cells.
3. Adapter negative controls on the real server (1): with a Zoe-layer protection OFF the cell that claims it must go red.
4. Seed 1 of H1 (9), H2 (11), H0 (6): the store-tier cells, interleaved across axes, time-boxed. A cell the box did not reach is a SKIP, never a pass.
5. Recall latency n=50 per arm (4.5), extraction JSON validity over >= 100 retain calls per mode (9), brain-slot seconds per retained turn (3).
6. Seeds 2 and 3 of each arm in whatever time is left (up to 31). Fewer than three seeds makes an arm INCOMPLETE, which is not adoptable.
7. Per arm: PSS of every candidate PID every 2 s (steady = median, burst = max, plus the scratch Postgres container), MemAvailable floor, and non-loopback
   connects (the egress hook log plus `ss` polling). A hook that wrote nothing is "not measured", never "zero".

## Reading the verdict

Output: `~/.zoe/bakeoff-2026-10/run-<stamp>.log`, `run-<stamp>.json`, and a **draft** `docs/research/bakeoff-run-<stamp>.md`.
The first lines of the markdown are the verdict by the pre-registered rule (`bakeoff_gates.py`; a test pins every threshold):

* **`KEEP_Z0`** - no arm passes every built gate and beats Z0 beyond the Wilson interval on two of B/C/D/E (ties go to Z0). The rule says: keep Z0, finish audit P1-P3.
* **`ADOPT_CANDIDATE <arm>`** - passes every built gate and beats Z0 on two axes. Advisory: every axis the rule names now exists in the spec (C = `temporal`, D = `recall`, A3 / A8 under `authority`, `poisoning` a hard axis);
  known-failing targets count against an arm and the owner decides. The report prints a one-line advisory note under the verdict.
* Per arm: `NOT_ADOPTABLE` (a gate item failed), `INCOMPLETE` (something was not measured or fewer than three seeds ran; not a pass),
  `PASSES_BUILT_GATES`. If H1 and H2 both pass the rule chooses H1. H0 is the measurement of what Hindsight does with no Zoe layer; it cannot win.
* Gate tables list each item as threshold / measured / `PASS | FAIL | NA`. The axes table shows pass / n with Wilson 95% for Z0, Z0-off, H0, H1, H2.
  Z0-off must be red on the authority cells (the negative control) and every run records `controls red x/y`.
* "Not verified" at the bottom lists what the run could not establish.

## How to abort

Ctrl-C in the terminal (the wrapper's trap restores), or `kill <pid of bakeoff_window.sh>`. If the terminal is gone:

```bash
scripts/perf/zmb/bakeoff_window.sh --restore-only      # idempotent; stops only the zoe-bakeoff-* units and the scratch container
# by hand, the same thing:
systemctl --user stop zoe-bakeoff-hindsight zoe-bakeoff-gemma zoe-bakeoff-embed
docker compose -f /home/zoe/.zoe/bakeoff-2026-10/scratch-postgres.compose.yml down
systemctl --user start llama-server.service && curl -sf http://127.0.0.1:11434/health
```

Exit codes: 0 done, 2 refused before anything was started, 3 aborted (restored), 4 RESTORE FAILED (the live brain is not healthy: look at
`systemctl --user status llama-server.service` first).

## What is still unverified (be honest when you quote the result)

* **The real Hindsight server has never been in the loop with this adapter.** The adapter, the Zoe layer and the runner are proven red-before-green
  against `arms/fake_hindsight.py`, a test double of the documented API shapes; the request and response fields were read from the installed 0.10.2
  source, and the embeddings shim was exercised with the openai SDK exactly as Hindsight calls it. The first real window is also the first real
  contact: expect it to find adapter bugs (a field name, a timeout, an observation that behaves differently from the double). A `SKIP` with
  "cannot reach Hindsight" or an `ERROR` is information, not a result.
* Extraction quality on the B cells under real Gemma (the double splits sentences; it says nothing about the model).
* Net RSS: the Chroma / ONNX that adoption frees inside `zoe-data` is not subtracted, so the figure is gross (conservative).
* G3 "delete >= 5,000 of 17,923 lines" is an estimate from a file list, not a proof that those files are deletable.
* The affect line of G2 predates the owner's 2026-10-05 policy (households incl. children recorded, guests never); the cells follow the policy.
* A forgotten name can survive inside an INVALIDATED observation (Hindsight keeps invalidated rows); the cells count retained rows only
  (approved / pending / disputed), and no cell checks physical erasure of Hindsight's own tables.
* The embedding model is whatever `embed_shim.py --find` found (bge-small from `/tmp/fastembed_cache`, which a reboot clears; the fallback is Chroma's
  MiniLM under `~/.cache/chroma`, a different model). The run record names it.

## Files

`scripts/perf/zmb/`: `bakeoff_window.sh` (the command), `bakeoff.py` (preflight, window, restore), `bakeoff_measure.py` (phases, sampler, report),
`bakeoff_gates.py` (the rule), `embed_shim.py` (OpenAI-compatible `/v1/embeddings` on 127.0.0.1:11501, `--selftest`), `arms/hindsight.py` (H0/H1/H2 and the
Zoe layer, `zoe_layer_lines()` for G3), `arms/fake_hindsight.py` (the test double). Tests: `tests/unit/test_zmb_bakeoff.py`, `test_zmb_hindsight_arm.py`,
`test_zmb_embed_shim.py`. Runtime files: `/home/zoe/.zoe/bakeoff-2026-10/` (`hindsight.env.example`, `scratch-postgres.compose.yml`, the venv, the logs).
