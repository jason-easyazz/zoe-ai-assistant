---
type: Audit
title: Documentation system audit 2026-10-29
description: Measured health of the DOX AGENTS.md chain, the command-center docs, the OKF knowledge bundle, the generated and CI-pinned docs, and the tooling that reads them - counts, evidence, what was fixed in the same PR, and what needs an owner decision.
tags: [docs, dox, okf, audit, tooling]
timestamp: 2026-10-29T00:00:00Z
---

# Documentation system audit, 2026-10-29

Every number below comes from a command run against a fresh worktree of `origin/main` on 2026-10-29 (and, for "what runs",
from `scripts/maintenance/zoe_ground_truth.sh`). See [index](index.md). Fixes listed under each area shipped in the same PR;
items under "Owner decisions" were deliberately NOT changed because they alter a contract or install tooling.

## Verdicts

| area | verdict | key numbers |
|---|---|---|
| 1. DOX AGENTS.md chain | OK | 20 files; 0 unlisted in a parent index; 0 index entries to a missing file; 0 broken links |
| 2. Command-center docs | OK after fixes | 4/4 exist; 1 stale live claim fixed (speaker model) |
| 3. OKF bundle `docs/knowledge/` | OK after fixes | 121 pre-existing core records all with `type`; 10 not in `index.md` (fixed); 1 broken link (fixed); archive subtree is a documented exception |
| 4. Generated and CI-pinned docs | OK | `validate_structure.py` rc 0; flag inventory fresh; `validate_critical_files.py` rc 0 |
| 5. Tooling | issues | pre-commit config not armed; `.cursor/mcp.json` spawned private Serena (fixed); codebase-memory graph empty |
| 6. Retired systems in docs | OK after fixes | 5 stale instructions fixed; remaining mentions are historical |
| 7. Knowledge in private agent memory | 4 records moved | more remain (listed below) |

## 1. DOX hierarchy

- `find` over the tree (skipping `node_modules`/`.venv`): **20** `AGENTS.md` files. A script resolved each file's "Child DOX Index"
  links and compared with the nearest parent: **every child is listed in its nearest parent** and **no index entry points at a
  missing file**. A regex hit for `hammer|tok` in `services/zoe-data/AGENTS.md` is prose, not a link.
- Section order: every file has Purpose, Local Contracts/Work Guidance, Verification and a Child DOX Index. Subtrees that own an
  autonomous loop or agent have a Forbidden section: `docs/`, `scripts/`, `labs/`, `services/zoe-data/`, `services/zoe-vaultd/`,
  `services/omi-sync/`, `services/panel-bluetooth/`, `services/zoe-data/tests/samantha_live/`. Files without a Forbidden section
  (`config/`, `modules/`, `services/`, `services/zoe-ui/`, `services/zoe-auth/`, `services/homeassistant-mcp-bridge/`, `tests/`,
  `tools/`, `skills/`, `services/zoe-data/routers/`, `services/zoe-data/tests/`) own no autonomous loop. `services/zoe-data/tests/`
  has no Ownership section (minor).
- Two root `AGENTS.md` copies are in circulation: an agent started from an older worktree loads that worktree's copy. The
  session context of one such worktree still carried the fixed 01:50-04:55 blackout rule that `origin/main` replaced on 2026-10-19.
  The fix is to start agents from fresh worktrees; nothing to change in the file.

## 2. Command-center docs vs live reality

`docs/VISION.md` (72 lines), `CANONICAL.md` (189), `PLANS.md` (254), `IDEAS.md` (146) all exist with frontmatter.

Ground truth, read 2026-10-29: `zoe-data`, `llama-server` (:11434), `kokoro-tts`, `functiongemma-router` (:11436), the Flue 2.x brain
(:3579) and the Telegram sidecar are active and healthy; `ZOE_BRAIN_BACKEND=flue`, `ZOE_ROUTER_HEAD=active`; `hermes-agent` and
`openclaw-gateway` are inactive; the Multica dispatch kill switch is present. `ps` of the brain shows llama.cpp b11194, MTP draft,
`--ctx-size 8192`, q8_0 K/V, which matches CANONICAL.

- **Stale, fixed:** `PLANS.md` listed `ZOE_SPEAKER_MODEL=dual` as live (and "dual is LIVE as of 2026-10-18"); the running
  zoe-data process reads `resnet34`. `knowledge/speaker-resnet34-switch.md` still said "FLAG-DARK". Both now carry the re-read.
- Verified correct: every other flag in the PLANS "Live flag state" block matched the process environment (ON flags, the five
  `enforce` flags, the five unset `shadow` flags, `ZOE_PROACTIVE_SPOKEN=0`, `ZOE_NIGHT_MIND` unset). Values were compared, not
  printed.
- **Not a doc bug but worth knowing:** the nightly voice gate on main reads `fail` (STT 732 ms vs baseline 467 ms), available
  memory was 865 MiB, and unit-template drift shows 9 differences. These are operational signals, recorded in
  [unit-drift-2026-10-25.md](unit-drift-2026-10-25.md) and the ledger rather than here.

## 3. OKF knowledge bundles

- `docs/knowledge/` had 121 core markdown records (excluding `operator-skills/`): **0** without frontmatter or `type`; every
  directory with markdown has an `index.md` except the archive subtree below.
- **10 records were not mentioned in `knowledge/index.md`** (`manner-block`, `person-half-guards`, `person-half-enforce-pack-2026-10-09`,
  `samantha-person`, `mac-virtual-panel`, `brain-kv-cache-tuning`, `forgotten-text-physical-erase`, `dependency-alerts-2026-10-04`,
  `log-review-units-2026-10-04`, `unit-drift-2026-10-25`): added.
- **1 broken relative link** in `offbox-backup.md` (a forward reference to a research file that is still on an unmerged branch):
  replaced by plain text.
- Whole-repo markdown link check (459 files, skipping the archive and vendored trees): 5 broken links in total, 4 more fixed
  (`docs/implementation/README.md`, three in `docs/guides/iOS-SHORTCUTS-INTEGRATION.md`).
- **Exception, not fixed:** `docs/knowledge/operator-skills/` is a verbatim retirement archive of imported skill files:
  **70** of its 71 markdown files have no frontmatter or no `type`, and about 60 of its subdirectories have no `index.md`. Adding
  frontmatter would alter preserved source. See the owner-decision list.
- Registration: `docs/AGENTS.md` lists the three bundles (`knowledge/`, `knowledge/autopilots/`, `knowledge/operator-skills/`).
- Stale-content fixes: `zoe-tool-stack.md` described Hermes as the active default, OpenClaw as a live fallback and the brain as
  E2B; corrected (Hermes inactive, OpenClaw retired, brain E4B). `night-window.md` restated the retired fixed blackout as the
  binding rule; rewritten to the measured pause (and notes that several lab/perf scripts still hard-code the old span, which is
  safe because it is stricter).
- `docs/agent-setup-audit.md` (2026-06-24, status draft) claimed there is no `CLAUDE.md` and `.mcp.json` is not committed;
  both exist. Marked superseded.

## 4. Generated and CI-pinned docs

- `python3 tools/audit/validate_structure.py` (under the shared test-lane lock): **rc 0**, 3,460 approved files, 0 orphans,
  root markdown 9/12.
- `python3 tools/audit/validate_critical_files.py`: **rc 0**.
- `python3 tools/audit/flag_inventory.py --repo . --check-only`: **rc 0** (785 prod flags, 735 not in `.env.example`; that gap
  is informational, not a gate).
- `docs/knowledge/live-state.json` is present (keys: flags, kind, note, prod_flag_count, source).
- `test_canonical_invariants.py`, the research-index test and the rest of the repo-root `ci_safe` lane were run for this PR; the
  result is in the PR body.

## 5. Tooling that reads and writes the docs

- `CLAUDE.md` includes `AGENTS.md` (`@AGENTS.md`): OK.
- `.mcp.json`: Serena is `http://127.0.0.1:9121/mcp`, codebase-memory goes through `codebase_memory_capped.sh`: OK.
  `.codex/config.toml`: same, `url =` for Serena, wrapper for codebase-memory: OK.
- `pgrep -af "serena start-mcp-server"`: **1** real server (the second line of a naive count is the shell running the pgrep).
  `scripts/maintenance/serena_mcp_health.sh`: `HEALTHY`, rc 0.
- **`.cursor/mcp.json` was the one config still stdio-spawning a private Serena** (context `ide-assistant`) and the raw
  codebase-memory binary; both were moved to the shared URL and the capped wrapper in this PR. Not covered by
  `tests/unit/test_agent_mcp_memory_bounds.py` (see recommendations).
- **pre-commit is NOT armed.** `core.hooksPath` points at the live checkout's `.git/hooks`, shared by all 352 registered
  worktrees, and the installed `pre-commit` there is an older hand-written script (65 lines) that does not call the
  `pre-commit` framework, so `.pre-commit-config.yaml` (structure validator, flag-inventory auto-fix, hygiene hooks) never runs
  locally. `~/.local/bin/pre-commit` is installed. The CI `validate` job is the backstop. Arming it means replacing a shared
  hook file: an owner decision.
- **codebase-memory index is empty** (`list_projects` returned no projects). `index_repository(/home/zoe/assistant, moderate)`,
  the documented remedy, dropped the MCP connection (the per-spawn memory cap is 768 MB and the box had about 1.3 GB
  available); not retried. Agents that follow the checklist will fall back to grep until it is indexed, so this needs an
  operator-run index (or a CLI index outside the cap).
- opensrc cache: `~/.opensrc/repos` is 9.8 GB, 89 owners under `github.com`, with `sources.json`. Not size-audited further.

## 6. Retired systems presented as live

Classification of `grep` hits (markdown, excluding the operator-skills archive):

- graphify: 82 hits in 24 files. Instructions in the root and scripts `AGENTS.md`, `.greptile/rules.md`, `CURSOR_BEST_PRACTICES.md`
  say it is retired (fine). **Stale instructions fixed:** `docs/guides/AGENT_SPIKE_PLAYBOOK.md` told agents to record Graphify
  use; `knowledge/index.md` listed graphify as installed tooling. The rest are dated plan/inventory/audit records (historical).
- OpenClaw / Hermes: the root contract still names Hermes the "default engineering agent" while `hermes-agent` is inactive; see
  owner decisions. OpenClaw is called retired in `AGENTS.md` and was still "explicit fallback" in `zoe-tool-stack.md` (fixed).
- Bugbot: root `AGENTS.md` says disabled 2026-07-28 (fine); `.cursor/BUGBOT.md` is retained as the local `/review` guide.
- Greptile-as-required: only historical sentences ("was required until 2026-07-31"): fine.
- Fixed 01:50-04:55 blackout: the contracts (`scripts/AGENTS.md`, root, `merge-and-deploy.md`) are updated. Remaining mentions in
  `labs/relay`, `labs/p0-deroot`, `scripts/perf/measure_wake.py`, `scripts/perf/omi_voice_calibrate.py`, `labs/AGENTS.md` and
  `knowledge/samantha-bar.md` are accurate descriptions of those scripts' own conservative refusal. Unverified: whether the
  samantha-bar harness still enforces its own copy (no hard-coded span was found in Python outside the files above).

## 7. Knowledge stranded in private agent memory

The private memory directory holds 136 files. Durable operational facts that were missing from `docs/knowledge/` and are now
records: [node-happy-eyeballs-timeout.md](node-happy-eyeballs-timeout.md), [chroma-hnsw-corruption-recovery.md](chroma-hnsw-corruption-recovery.md),
[music-provider-reconnect.md](music-provider-reconnect.md), [omnigent-handoff-mechanics.md](omnigent-handoff-mechanics.md).

Still stranded (candidates for a follow-up PR, none personal): the Pi RPC stream event schema read by `zoe_core_client`
(core lane); the panel voice daemon deploy recipe (remote unit, deploy from a worktree); the panel AirPlay-2 speaker setup and
its AirPlay-1 trap; the UI review instruments recipe (tunnelled browser ports, gates); the Intent-dispatch token gate and the
logging-location trap (zoe-data logs to a file, not the journal); the "sync the live checkout with `merge --ff-only`, never a
stash pop" recipe. Already covered in the repo and therefore not moved: the voice-stack swap guards
([memory-pressure-profile.md](memory-pressure-profile.md)), the marker-based CI lane (`tests/AGENTS.md`), the accept-queue
hang signature ([incident-runbook.md](incident-runbook.md)). Owner preferences and household facts were left in private memory
on purpose.

## Owner decisions and recommendations (contract or tooling changes, not made here)

1. **Arm pre-commit.** Replace the shared `.git/hooks/pre-commit` with `pre-commit install` output (or have the old script call
   `pre-commit run`), so the structure validator and flag-inventory auto-fix run locally as `AGENTS.md` claims.
2. **Hermes is described as the default engineering agent** in the root contract while `hermes-agent` is inactive; decide whether
   to re-enable it or amend the Hermes-First section.
3. **`operator-skills/` archive:** accept it as an OKF exception (document it in `docs/AGENTS.md`) or add frontmatter and
   per-directory `index.md` files (about 130 files).
4. **Index codebase-memory** from an operator shell where the memory cap does not apply, then re-check `list_projects`.
5. **Extend `tests/unit/test_agent_mcp_memory_bounds.py`** to cover `.cursor/mcp.json` so a `command =` Serena cannot return.
6. **Worktree registry:** 352 registered worktrees; run `scripts/maintenance/prune_worktrees.sh` (dry run first).
7. Add a doc-link check (the one used here is ten lines) to `validate` so broken relative links in markdown fail CI.
8. The scripts that hard-code the old 01:50-04:55 span could consult `night_busy.sh` instead; low value, listed for completeness.

## Could not check

- codebase-memory index status beyond "no projects" (the index call killed the connection).
- Whether `samantha_bar` enforces its own copy of the night span.
- The bodies of the 30+ architecture plans' status lines: only the flag block of `PLANS.md` was compared with live state.
