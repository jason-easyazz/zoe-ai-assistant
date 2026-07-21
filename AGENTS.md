# Command Center — READ THESE FIRST (every agent: Claude Code, Codex, Omnigent, Hermes)

- **[docs/VISION.md](docs/VISION.md)** — Zoe's north star, direction & core principles (the *why*). Read this first; every decision aligns to it (the rocks; local/private/fast; lab-prove-before-prod; build-to-stick; capture-don't-lose).
- **[docs/CANONICAL.md](docs/CANONICAL.md)** — the **locked-in truth**: what's actually live, and what's settled (the rocks: Gemma 4 E4B-QAT+MTP brain, Moonshine STT, Kokoro TTS). If a system isn't listed canonical there, it's **not load-bearing** — don't extend or resurrect it. Swapping a rock fails CI (`test_canonical_invariants.py`) by design.
- **[docs/PLANS.md](docs/PLANS.md)** — what we're building + status. Before starting work, check it; after finishing a step, update it.
- **[docs/IDEAS.md](docs/IDEAS.md)** — the pin-it-so-we-don't-lose-it board. When Jason says *"pin this / put a pin in it / remember this idea"*, add a one-line entry there. Never drop an idea on the floor.

**Tool discipline (these get skipped — don't skip them):** use **Serena** + **codebase-memory** (MCP, see `.mcp.json`) for code navigation/edits over raw grep; **opensrc** (`opensrc path …`) for third-party source before guessing; the **Greptile loop** to drive PRs to merge; follow the **DOX** doc conventions below. Detail for each is in the sections that follow.

<!-- start-of-task-checklist -->
## Start-of-task checklist (non-optional)

Before any code task, you MUST — do not fall back to raw grep/guessing:

- **Read LIVE reality before trusting any doc about what is live — run `scripts/maintenance/zoe_ground_truth.sh` (read-only, ~5s).** codebase-memory/Serena/grep answer *what EXISTS*; they cannot tell you *what RUNS* — which flag is set in the running process, whether a scheduled job actually registered, whether a "paused" service got restarted for a test. A static doc physically cannot track that. Every wrong conclusion in the 2026-07-20 session lived in that gap (a paused-doc Hermes that was live; a board assumed "inside Hermes" that is its own product; a flag read from its default; a job that never registered). The probe prints all of it. **What exists ≠ what runs — verify execution, not presence** (`[[feedback_verify_your_instruments]]`).
- **Navigate + edit code via the MCP bus, not raw grep/Read/Edit.** Use **codebase-memory** for who-calls-what / architecture / seams and **Serena** for symbol read + symbolic edits. Reach for `grep`/`Read` only when the bus genuinely can't answer. If codebase-memory `list_projects` comes back empty, run `index_repository(repo_path=/home/zoe/assistant, mode="moderate")` before proceeding — an empty graph silently unmeets this mandate and agents fall back to grep.
- **Use `opensrc` for any third-party API before guessing** — `opensrc path pypi:<pkg>@<version>` (pin the installed version). Never invent library behaviour from memory.
- **Drive every PR to merge with the Greptile loop** — reply to + RESOLVE each Greptile thread, follow up until it actually merges; squash only, never `--admin`/`--force`.
- **Follow the DOX `AGENTS.md` chain** — read the root plus every nested `AGENTS.md` on the path to files you touch, and do the closeout DOX pass after editing (see *DOX framework* below).
- **Honour the rocks.** Gemma 4 E4B+MTP (brain) and Moonshine v2 Medium (STT) are fixed — optimise *around* them, never propose swapping (see `docs/VISION.md` principle 1). **Retire by removing** (git keeps history); don't hoard `_old`/`_v2`/archive copies.
- **Replay-gate every voice change (MANDATORY).** Any change to the voice path (STT / brain / TTS — `services/zoe-data/routers/voice_tts.py`, `zoe_core_client.py`, `fast_tiers.py`, the brain/Kokoro config) MUST be replay-gated against Jason's real-voice corpus `~/.zoe-voice-samples` before merge/deploy: said-vs-did must not regress (a previously-working command that now fails = a bug) and per-stage speed must not regress. Use the voice regression + speed harness — `scripts/maintenance/voice_regression_probe.py` (baseline-compared) and the underlying `scripts/perf/measure_voice.py` / `measure_tts.py` — always under `flock /tmp/zoe-voice-harness.lock` (two Kokoro loads ~2.3 GB each will OOM the box). The harness is warm + stops before TTS, so its numbers are RELATIVE (drift vs baseline), not live performance. Full tool doc: `docs/knowledge/voice-pipeline.md`.

---

## Codebase navigation — codebase-memory + Serena (graphify retired)

graphify is **retired**: there is no committed `graphify-out/` graph and no `graphify query`/`path`/`explain` workflow. Do not resurrect it.

- **codebase-memory** (MCP) — who-calls-what, architecture, seams, cross-module "how does X relate to Y".
- **Serena** (MCP) — symbol read + symbolic edits.

Reach for raw `grep`/`Read` only when the MCP bus genuinely can't answer.

### Serena is ONE shared server — read via Serena, EDIT in your own worktree

`.mcp.json` points every agent at a **single long-lived** Serena server
(`http://127.0.0.1:9121/mcp`, unit `scripts/setup/systemd/serena-mcp.service`).
It is not spawned per agent. Two consequences are load-bearing:

- **It is pinned to `--project /home/zoe/assistant` — the LIVE checkout.** So
  **navigation/read is correct** (agents branch off fresh `origin/main`, so the
  live checkout on `main` *is* their baseline), but **symbolic EDITS would
  target the live checkout, not your worktree.** Do your Read/Edit **in your own
  worktree** with the normal file tools. Two agents hit this on 2026-07-16 via
  absolute paths. This pinning is not new — the old stdio config had the same
  `--project` — but one shared server makes it fleet-wide instead of incidental.
- **Serena serialises the fleet.** All tool calls run through one
  `SerenaAgent` TaskExecutor queue (one worker, strict FIFO), so concurrent
  calls queue rather than interleave, and every agent waits for the whole queue
  to drain. Measured: 6 concurrent cold calls = 6.15s wall for 4.53s of work.
  That tax is deliberate — 6 separate servers (up to 2G each) do not fit in
  15.6G and OOMed the live voice brain on 2026-07-16. Keep Serena calls
  purposeful; it is a shared single-lane resource, not free parallelism.

If code-intel tools vanish, the server is down — Claude Code does **not**
auto-start a URL-based MCP server. Check
`scripts/maintenance/serena_mcp_health.sh`.

## opensrc

Use `opensrc` for third-party library source before guessing API behavior.

Rules:
- Keep the global source cache outside the repo at `~/.opensrc/repos/`.
- Prefer `opensrc path pypi:<package>@<version>` or `opensrc path owner/repo` to locate already-fetched source.
- Pin the version to the one Zoe actually installs (for example `opensrc path pypi:chromadb@0.6.3`). A bare package name resolves to the latest published version (e.g. bare `chromadb` is 1.5.9), which can be a major-version mismatch from the running stack and will mislead you.
- Search package source directly when debugging integrations, for example:
  `rg "class FastAPI" "$(opensrc path pypi:fastapi)"`
- When source context informs an implementation, report the package files, examples, or tests that were used.
- Do not vendor opensrc cache contents into Zoe.
- Be cautious with brand-new dependencies; avoid adopting packages less than about 14 days old unless the user explicitly accepts the risk.

Currently useful cached sources include FastAPI, ChromaDB, LiveKit, faster-whisper, MCP/FastMCP, APScheduler, pyannote-audio, and AG-UI.

## code structure cleanup

Build the smallest working feature first, then run a cleanup pass before review.

Use the cleanup pass to remove duplicated runtime mechanics: repeated provider calls, parsing, validation, command execution, payload transforms, or business logic. Keep product/domain policy in routes, actions, intents, and UI handlers. Move only reusable mechanics into service-layer helpers.

Service helpers should be small capability blocks with explicit parameters, structured returns, consistent failure semantics, and no hidden global state or unrelated database mutation.

Do not refactor the whole app as cleanup. Do not create `_new`, `_fixed`, `_v2`, `_old`, backup, or duplicate router files.

## Greptile PR loop

For reviewable development work:
- Work from feature branches and open pull requests; `main` is protected.
- Do not bypass branch protection or use administrator merges unless the operator explicitly asks for that emergency path.
- Keep PRs small; use `/split-to-prs` when a branch grows too large.
- Let Greptile review every PR independently.
- For Zoe engineering tasks, prefer `scripts/maintenance/greploop_guard.py --packet-only` or `--once` before broad expensive-agent repair.
- Cheap models must receive one generated fix packet for one finding or CI failure; never hand them the whole PR.
- Use Cursor's Greptile MCP to fetch review status/comments.
- Use the `github-greptile-loop` Hermes skill to delegate heavier fix/re-review loops.
- Do not treat Greptile as a replacement for local Zoe verification; run focused tests and live health checks before marking work merge-ready.

Merge mechanics & gotchas — canonical record: **[docs/knowledge/merge-and-deploy.md](docs/knowledge/merge-and-deploy.md)** (read it before driving any PR to merge). The load-bearing rules:
- A green `Greptile Review` **check ≠ resolved threads**. `required_conversation_resolution` is the gate that enforces "5/5, every comment sorted" — mark every thread resolved (GraphQL `resolveReviewThread`), don't just reply.
- **Arm auto-merge** (`gh pr merge <n> --squash --auto`) instead of merging by hand. `strict` drains a batch **serially** — nudge one PR per merge, and expect each branch-update to re-trigger a fresh Greptile review that may post new threads (the re-review treadmill).
- **New tests reach CI by marker, not enumeration, on the main lanes.** `services/zoe-data/tests` + repo-root `tests/unit` are marker-based (co-located `pytestmark = pytest.mark.ci_safe`, registered in `pytest.ini`) and `services/zoe-auth/tests` runs full-directory — all three are enumeration-free, and hand-listing files there silently drops new tests (the failure this rule used to cause). Do NOT edit `validate.yml` for these lanes. Only the remaining explicitly-enumerated lanes need a YAML entry — confirm the file actually runs in its CI job. SSOT: [tests/AGENTS.md](tests/AGENTS.md) + [docs/knowledge/merge-and-deploy.md](docs/knowledge/merge-and-deploy.md).
- GitGuardian scans **branch history**: a leaked/test cred in an intermediate commit fails even with a clean head tree. Scrub via a clean re-branch (squash to one commit on a new branch, replacement PR) — force-push is blocked by design.
- Never `--admin`/`--force`; squash-only; the **human merges** (or armed auto-merge does) — agents never bypass the gate.

Local pre-commit — a tracked `.pre-commit-config.yaml` at repo root runs the repo's own `tools/audit/validate_structure.py` + `validate_critical_files.py` plus standard hygiene hooks. Run `pre-commit install` once per clone to arm it. `validate_structure.py` treats any root file not in `.zoe/manifest.json` `approved_root_files` as an orphan and fails — register new root files there.

## Cursor MCP

The tracked Cursor MCP config intentionally includes only non-secret local servers. `zoe-tools` launches the operator-local helper at `/home/zoe/bin/zoe-tools-mcp.py` through `uv run --with fastmcp --with httpx`; provision that helper on Zoe hosts before relying on the repo-local MCP entry. Keep token-backed servers such as Greptile in user-global Cursor config or environment-backed local config, never in tracked repo files.

## Hermes-First Delegation

Hermes is Zoe's default engineering and browser agent. Use it for planning, code review, implementation repair, architecture analysis, Greptile loops, codebase-memory/Serena-guided codebase work, Multica board repair, generated knowledge refresh, and browser work through Zoe's CloakBrowser tools.

Local Zoe Hermes engineering skills live under `~/.hermes/skills`, including `zoe-engineering`, `agentic-engineering-workflow`, `source-code-context`, `code-structure-cleanup`, `github-greptile-loop`, `grep-loop-review-workflow`, and `zoe-status-refresh`. They are operator-level Hermes skills, not user-facing Zoe runtime skills under `skills/`.

The `agentic-engineering-workflow` and `grep-loop-review-workflow` names are kept as compatibility entrypoints for the Micky-style workflow pack, but they map onto Zoe's Hermes-first codebase-memory/opensrc/Greptile process rather than introducing a second parallel system.

OpenClaw remains installed and available as a future/manual fallback. Do not route ordinary coding, planning, review, board work, browser work, or background work to OpenClaw by default. Zoe's Multica-first engineering driver owns workflow state and phase advancement; Hermes executes the one ready phase unless the user explicitly asks to use OpenClaw.

## Branching policy

Trunk-based development off protected `main`. No permanent `develop` or `staging` branch.

- One branch per issue or Multica task, created from fresh `origin/main`.
- Naming: `codex/<slug>` (agent work), `feature/<slug>`, `fix/<slug>`, `verify/<slug>` (throwaway validation).
- Use a dedicated git worktree under `~/.worktrees/<slug>` for development; do not switch the live checkout (`/home/zoe/assistant`) to feature branches for agent work.
- Branches die at merge: remote branches auto-delete (`delete_branch_on_merge`); local task worktrees are reclaimed automatically — see below.
- Automatic cleanup (Multica owns its worktrees):
  - On chain completion, the harness removes each task's worktree + `wt/<id>` branch once merged (`worktree_bootstrap.remove_task_worktree`, called from `kanban_adapter`).
  - A daily safety-net sweep in the Multica poll loop reclaims orphaned merged worktrees (`worktree_bootstrap.prune_merged_worktrees`). Interval via `ZOE_WORKTREE_PRUNE_INTERVAL_S` (default 86400).
  - Both detect **squash-merged** branches via `gh pr view` (not just git-ancestor merges) and never touch dirty, locked, unmerged, or the live checkout.
- Manual prune still available: `scripts/maintenance/prune_worktrees.sh` (dry-run first, `--execute` after operator review).

## Skill & extension safety

Third-party skills and extensions run with the agent's privileges. Treat them as untrusted code.

- Before installing any third-party skill, Pi/OpenClaw extension, or code-bearing MCP server into a Zoe agent runtime — and before promoting a self-authored skill from the lab to a live agent — scan it: `skillspector scan <dir|file|git-url>` (installed at `~/.local/bin/skillspector`).
- The static stage needs no credentials but is deliberately conservative: it flags legitimately powerful, process-spawning extensions as HIGH/CRITICAL. Do not treat the raw static score as a verdict — use the optional LLM stage (`SKILLSPECTOR_PROVIDER=...`) plus human judgement for promotion decisions.
- Do not egress internal Zoe skill content to an external LLM provider for scanning without operator consent; prefer static scans, or a local/NV provider, for internal skills.
- Record the scan outcome (or a deliberate waiver) when adopting a new skill or extension.

# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Knowledge vs. Records (OKF)

DOX governs two kinds of document; do not conflate them.

- **Contracts** — `AGENTS.md` files. Prescriptive, binding, prose. They change only through the deliberate DOX pass below — never by an autonomous loop.
- **Records / knowledge** — curated facts, schemas, learned insights, and durable reference (e.g. memory exports, tool/topology notes). Write these as **Open Knowledge Format (OKF)** bundles: a directory of markdown files with YAML frontmatter (required `type`), an `index.md` per directory, and cross-links via relative markdown links.
- Register every OKF bundle in the nearest owning AGENTS.md's Child DOX Index so the DOX walk discovers it. An OKF bundle stays inside DOX governance; it is not a parallel system.
- The autonomous memory/knowledge loop may freely create, update, and lint OKF records. It must never edit an AGENTS.md contract — contract changes go through the DOX pass and human review.

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists
- A subtree that owns an autonomous loop or agent MUST state a Forbidden list (what the agent must never do, e.g. paths/actions out of scope). It is the most load-bearing part of the contract; omit it only when nothing is autonomous in the subtree

Default section order:
- Purpose
- Ownership
- Local Contracts
- Forbidden
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

## Child DOX Index

- [services/AGENTS.md](services/AGENTS.md) — runtime services: zoe-data production web/chat API, zoe-ui static UI + nginx, zoe-auth, MCP bridges, LiveKit
- [skills/AGENTS.md](skills/AGENTS.md) — Zoe skill definitions (SKILL.md dirs); documentation only — NOT wired to runtime discovery, which reads `~/.openclaw/workspace/skills` + `~/.hermes/skills`
- [tools/AGENTS.md](tools/AGENTS.md) — audit, cleanup, validation, and generator utilities (structure/critical-file validators live here)
- [scripts/AGENTS.md](scripts/AGENTS.md) — setup, maintenance, deployment, and utility scripts, including systemd unit templates
- [tests/AGENTS.md](tests/AGENTS.md) — unit, integration, performance, e2e, and voice test suites
- [docs/AGENTS.md](docs/AGENTS.md) — categorized documentation; governance charter is normative
- [modules/AGENTS.md](modules/AGENTS.md) — optional add-on modules served under /modules/
- [config/AGENTS.md](config/AGENTS.md) — deployment configuration and key material locations (values never documented)
- [labs/AGENTS.md](labs/AGENTS.md) — lab-only experiments & spikes, isolated from the runtime (e.g. the Flue harness substrate spike)

Not indexed (runtime/data/generated, no durable editing contracts): `backups/`, `checkpoints/`, `data/`, `models/`, `ssl/`, `homeassistant/` (live Home Assistant runtime), `demos/`.
