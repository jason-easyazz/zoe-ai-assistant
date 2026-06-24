---
type: audit
title: Agent setup + tool adoption audit (Omnigent / Claude Code / Codex / Cursor)
status: draft
date: 2026-06-24
author: agent-setup-audit
scope: read-only audit — recommendations only, no config changed in this PR
---

# Agent setup + tool adoption audit

Jason drives Zoe development remotely through **Omnigent + Claude Code + Codex** and
reports that the repo's rules and code-intel tools keep getting *skipped*. The tools
exist; this audit verifies, agent by agent, whether each one actually **loads the repo
rules (`AGENTS.md` / DOX)** and **wires the MCP tools** (Serena, codebase-memory),
plus `opensrc` and the Greptile loop.

**Bottom line:** the tools are real and present on the host, but the *wiring that makes
agents pick them up is largely missing or invisible to a fresh checkout.* The single
biggest cause is that the one file that wires the MCP servers — `.mcp.json` — is **not
committed** (it is git-ignored), and there is **no `CLAUDE.md`** at all, so Claude Code
never reads `AGENTS.md`.

---

## What exists (ground truth)

| Artifact | Location | Status |
|---|---|---|
| Root `AGENTS.md` (DOX hub) | `AGENTS.md` (14 KB) | **Tracked.** Rich hub: graphify, opensrc, Greptile loop, Hermes-first delegation, Child DOX Index. |
| Nested `AGENTS.md` (DOX) | 16 files (`config/`, `modules/`, `services/`, `services/zoe-*/…`, `docs/`, `scripts/`, `tests/`, `tools/`, `skills/`) | **Tracked.** Mature per-dir DOX system. |
| Root `CLAUDE.md` | — | **MISSING** (none anywhere in repo or live tree). |
| Root `.mcp.json` (wires Serena + codebase-memory) | live tree `/home/zoe/assistant/.mcp.json` | **EXISTS but UNTRACKED + git-ignored** (`*.json` rule, `.gitignore:142`). Not in any clean checkout / worktree. |
| `.cursor/mcp.json` | tracked (whitelisted `!.cursor/mcp.json`) | **Tracked.** Wires `graphify` + `zoe-tools` — **NOT** Serena/codebase-memory. |
| `.cursor/rules/*.mdc` (7 rules) | tracked | **Tracked, `alwaysApply: true`.** Reference codebase-memory, opensrc, Greptile, Hermes. Do **not** reference `AGENTS.md`. |
| `.cursorrules` | tracked | **Deprecated** header; points to `.cursor/rules/*.mdc`. |
| `.greptile/{config.json,files.json,rules.md}` | tracked | **Tracked.** Review rules; says "graphify is retired, use codebase-memory MCP". |
| Serena binary | `/home/zoe/.local/bin/serena` → uv tool | **Present (host only).** |
| codebase-memory binary | `/home/zoe/.local/bin/codebase-memory-mcp` (267 MB) | **Present (host only).** |
| `opensrc` CLI | `/home/zoe/.local/bin/opensrc` + cache `~/.opensrc/` | **Present (host only).** |
| `github-greptile-loop` Hermes skill | `~/.hermes/skills/github-greptile-loop` (+ profiles) | **Present (host only).** |
| `.claude/settings.local.json` | tracked? (no) — permissions only | Local permissions allowlist; **no MCP wiring**. |

### The smoking gun

`.mcp.json` in the live tree wires exactly the two servers the operator expects:

```json
{ "mcpServers": {
    "serena": { "command": "/home/zoe/.local/bin/serena",
      "args": ["start-mcp-server","--context","claude-code","--project","/home/zoe/assistant",
               "--transport","stdio","--enable-web-dashboard","false"] },
    "codebase-memory": { "command": "/home/zoe/.local/bin/codebase-memory-mcp" } } }
```

But `git check-ignore .mcp.json` → `.gitignore:142: *.json`. It is **ignored by a blanket
`*.json` rule** and never committed. Compare `.cursor/mcp.json`, which IS tracked only
because `.gitignore` has an explicit un-ignore: `!.cursor/mcp.json` (line 266). No such
exception exists for `.mcp.json`. So:

- Any fresh worktree, container, or new machine has **no `.mcp.json`** → Claude Code/Codex
  start with **zero MCP tools**.
- Even where the file exists (the operator's live host), Claude Code's global
  `~/.claude.json` shows the `/home/zoe/assistant` project with `mcpServers: {}`,
  `enabledMcpjsonServers: []`, and `hasTrustDialogAccepted: false` — i.e. the project's
  `.mcp.json` servers were **never trusted/enabled**, so they don't load even when present.

---

## Per-agent audit

| Agent | Loads `AGENTS.md`? | MCP tools wired? | `opensrc`? | Greptile? | Gaps | Concrete fix |
|---|---|---|---|---|---|---|
| **Claude Code** | **No.** No `CLAUDE.md` exists; Claude Code only auto-reads `CLAUDE.md`, not `AGENTS.md`. AGENTS.md is effectively orphaned for this agent. | **No.** Root `.mcp.json` is git-ignored (absent from clean checkouts); on the live host the project shows `mcpServers:{}`, `enabledMcpjsonServers:[]`, `hasTrustDialogAccepted:false` in `~/.claude.json`. Serena + codebase-memory binaries exist but are never connected. | Indirect only (binary on host PATH); nothing tells the agent to use it. | Rules exist in `AGENTS.md` (## Greptile PR loop) but agent never reads that file. | (1) AGENTS.md never seen. (2) MCP never loaded — neither committed nor trusted. | Add a tracked **`CLAUDE.md`** that `@AGENTS.md`-includes the hub. **Un-ignore `.mcp.json`** (add `!.mcp.json` to `.gitignore`) and commit it, OR move Serena/codebase-memory into a tracked `.mcp.json`. Then accept the project trust dialog / set `enableAllProjectMcpServers` once so the servers actually load. |
| **Codex** | **Partial / native.** Codex natively reads `AGENTS.md` from the cwd up the tree, and the DOX system (root + nested) is well-formed, so when Codex runs with cwd inside the repo it WILL pick up `AGENTS.md`. But there is **no host `~/.codex/` and no `config.toml`**, and inside Omnigent `/root/.codex/` has runtime DBs but **no `config.toml`** either. | **No.** No Codex MCP config anywhere (`~/.codex/config.toml` absent on host and in container). Codex has no Serena/codebase-memory wiring; it relies on AGENTS.md prose. | Indirect only (host PATH); not in container PATH. | Via AGENTS.md prose only; no MCP/tool wiring. | No `config.toml` → no MCP servers, no model/tool policy. Relies entirely on AGENTS.md being in cwd. | Add a **repo-local Codex config** (`.codex/config.toml` at the repo root — version-controlled and present on a clean checkout) registering the Serena + codebase-memory MCP servers, so Codex gets code-intel beyond prose; optionally also a host `~/.codex/config.toml` (can't be committed). Confirm Codex is always launched with cwd = repo root so AGENTS.md resolves. |
| **Omnigent** (`zoe-omnigent` container) | **Repo mounted, but agents inside not wired.** Repo bind-mounted `rw` at `/workspace`; `/workspace/AGENTS.md` is visible. Codex-in-container will read it from cwd; Claude-in-container will NOT (no `CLAUDE.md`, and `/root/.claude.json` doesn't even exist → never initialized). | **No.** In-container `codex`/`claude`/`cursor-agent` are installed, but **Serena, codebase-memory, and opensrc are entirely absent from the container** (not on PATH, not on disk). The host `.mcp.json` points at `/home/zoe/.local/bin/...` which **does not exist inside the container**, so even if copied in it would be broken. `/root/.claude/settings.json` is just `{"theme":"auto"}`. | **No.** `opensrc` not in container at all. | **No.** Greptile binaries/Hermes skills live under `~/.hermes` on the host, not mounted into the container. | Container has the code but none of the code-intel/tooling; Claude-in-container never initialized; host-path MCP config won't resolve inside the container. | Bake Serena + codebase-memory + opensrc into the Omnigent image (or mount `/home/zoe/.local/bin` + `~/.opensrc` read-only) and ship a **container-relative `.mcp.json`** using in-container paths. Add `CLAUDE.md` (see Claude fix) so Claude-in-container reads AGENTS.md. Optionally mount `~/.hermes/skills` for the Greptile loop. |
| **Cursor** (cursor-agent) | **No direct ref.** `.cursor/rules/*.mdc` are `alwaysApply:true` and thorough, but **none reference `AGENTS.md`** — Cursor gets the curated rules, not the DOX hub. | **Partial / wrong set.** `.cursor/mcp.json` wires `graphify` + `zoe-tools`, but the rules say "graphify is RETIRED, use codebase-memory MCP." So the tracked MCP config and the tracked rules **contradict each other**, and the recommended `codebase-memory`/`serena` servers are **not** in Cursor's MCP config. | **Yes (prose).** `agentic-workflow.mdc` instructs `opensrc` use; binary on host PATH. | **Yes (prose).** Greptile loop + Hermes `github-greptile-loop` documented in rules. | MCP config lists a retired tool (graphify) and omits the recommended ones; rules don't link AGENTS.md. | Update `.cursor/mcp.json` to add `codebase-memory` (+ `serena`) and drop/disable `graphify`. Add an `AGENTS.md` pointer in a Cursor rule so the DOX hub is reachable. |

---

## DOX adoption (Q7): is `AGENTS.md` referenced by each entrypoint, or orphaned?

`AGENTS.md` is a **genuine, well-structured hub** (graphify, opensrc, code cleanup,
Greptile loop, Cursor MCP note, Hermes-first delegation, and a Child DOX Index linking all
16 nested files). DOX as a *document system* is healthy. But as an *entrypoint that agents
actually load*, adoption is uneven:

- **Codex:** reads it natively from cwd → **adopted** (the one agent that does).
- **Claude Code:** **orphaned** — no `CLAUDE.md`, so AGENTS.md is never loaded.
- **Cursor:** **orphaned** — `.cursor/rules/*.mdc` re-state policy but never link AGENTS.md.
- **Omnigent:** mounts the repo so AGENTS.md is on disk, but only the Codex sub-agent will
  actually read it; Claude-in-container won't.

> **Note / discrepancy for the operator:** the task brief described an AGENTS.md
> "Command Center — READ THESE FIRST" header pointing to `docs/PLANS.md` + `docs/IDEAS.md`.
> The current `AGENTS.md` has **no such header** (it opens with `## graphify`), and
> **`docs/PLANS.md` and `docs/IDEAS.md` do not exist** in the tree. Either that header was
> planned but not landed, or it lives on another branch. Flagging so it isn't assumed
> present. (No files were created or modified for this audit.)
>
> Separately, `AGENTS.md` still leads with **graphify**, which `.greptile/rules.md` and
> `.cursor/rules/agentic-workflow.mdc` both declare **retired** in favor of codebase-memory.
> The hub's headline tool contradicts the rest of the rule set.

---

## Prioritized fix list

1. **Make Serena + codebase-memory load for Claude Code & Codex.** Add `!.mcp.json` to
   `.gitignore` and commit the existing `.mcp.json` (or move those two servers into a tracked
   config). Today it's git-ignored by `*.json`, so no clean checkout ever sees it. *(Highest
   leverage — fixes the "tools get skipped" complaint at the root.)*
2. **Add a tracked root `CLAUDE.md` that `@AGENTS.md`-includes the hub.** Without it Claude
   Code never reads the repo rules. One line: `@AGENTS.md` (plus a short "use the Serena +
   codebase-memory MCP" reminder).
3. **Trust/enable project MCP on the live host.** Even with `.mcp.json` present,
   `~/.claude.json` shows the project never accepted the trust dialog
   (`hasTrustDialogAccepted:false`, `enabledMcpjsonServers:[]`). Accept it once (or set
   `enableAllProjectMcpServers`).
4. **Wire the Omnigent container.** Bake/mount Serena + codebase-memory + opensrc into the
   image with container-relative paths and ship a container `.mcp.json`; add `CLAUDE.md` so
   Claude-in-container reads AGENTS.md. Host-path `/home/zoe/.local/bin/...` will not resolve
   inside the container.
5. **Fix Cursor's MCP config.** Add `codebase-memory` (and `serena`) to `.cursor/mcp.json`
   and retire `graphify` there, so the MCP config matches the rules that call graphify retired.
6. **Give Codex an MCP config.** Add a repo-local `.codex/config.toml` (version-controlled,
   present on a clean checkout) registering the two code-intel servers — optionally also a host
   `~/.codex/config.toml` (not committable) — so Codex gets more than AGENTS.md prose.
7. **Reconcile AGENTS.md with "graphify is retired."** Demote the graphify section and lead
   with codebase-memory, so the hub agrees with `.greptile/rules.md` and the Cursor rules.
   (Optionally land the "Command Center" header + `docs/PLANS.md`/`IDEAS.md` if that was the
   intent.)

*This audit changed no agent configuration. All fixes above are recommendations for a
follow-up PR.*
