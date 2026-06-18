## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- Read graphify-out/GRAPH_REPORT.md before broad source searches when it is fresh. If its "Built from commit" does not match `git rev-parse HEAD`, treat it as a rough map only and prefer `graphify query`, `graphify path`, or `graphify explain` against `graphify-out/graph.json`.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- To rebuild the graph after significant code or doc changes, run from /home/zoe/assistant:
  `OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" .env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai`
- To refresh only GRAPH_REPORT.md from the committed graph, run:
  `/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz`
  Do NOT use `graphify update .` or `graphify hook install` — both have inflated the graph in this repo.

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

## Cursor MCP

The tracked Cursor MCP config intentionally includes only non-secret local servers. `zoe-tools` launches the operator-local helper at `/home/zoe/bin/zoe-tools-mcp.py` through `uv run --with fastmcp --with httpx`; provision that helper on Zoe hosts before relying on the repo-local MCP entry. Keep token-backed servers such as Greptile in user-global Cursor config or environment-backed local config, never in tracked repo files.

## Hermes-First Delegation

Hermes is Zoe's default engineering and browser agent. Use it for planning, code review, implementation repair, architecture analysis, Greptile loops, Graphify-guided codebase work, Multica board repair, generated knowledge refresh, and browser work through Zoe's CloakBrowser tools.

Local Zoe Hermes engineering skills live under `~/.hermes/skills`, including `zoe-engineering`, `agentic-engineering-workflow`, `source-code-context`, `code-structure-cleanup`, `github-greptile-loop`, `grep-loop-review-workflow`, `zoe-graphify`, and `zoe-status-refresh`. They are operator-level Hermes skills, not user-facing Zoe runtime skills under `skills/`.

The `agentic-engineering-workflow` and `grep-loop-review-workflow` names are kept as compatibility entrypoints for the Micky-style workflow pack, but they map onto Zoe's Hermes-first Graphify/opensrc/Greptile process rather than introducing a second parallel system.

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

# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

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

Default section order:
- Purpose
- Ownership
- Local Contracts
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
- [skills/AGENTS.md](skills/AGENTS.md) — user-facing Zoe runtime skills (SKILL.md dirs discovered by the router)
- [tools/AGENTS.md](tools/AGENTS.md) — audit, cleanup, validation, and generator utilities (structure/critical-file validators live here)
- [scripts/AGENTS.md](scripts/AGENTS.md) — setup, maintenance, deployment, and utility scripts, including systemd unit templates
- [tests/AGENTS.md](tests/AGENTS.md) — unit, integration, performance, e2e, and voice test suites
- [docs/AGENTS.md](docs/AGENTS.md) — categorized documentation; governance charter is normative
- [modules/AGENTS.md](modules/AGENTS.md) — optional add-on modules served under /modules/
- [config/AGENTS.md](config/AGENTS.md) — deployment configuration and key material locations (values never documented)

Not indexed (runtime/data/generated, no durable editing contracts): `backups/`, `checkpoints/`, `data/`, `models/`, `ssl/`, `graphify-out/`, `homeassistant/` (live Home Assistant runtime), `demos/`.
