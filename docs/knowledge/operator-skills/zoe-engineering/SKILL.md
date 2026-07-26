---
name: zoe-engineering
description: Use for all Zoe engineering work. Makes Hermes the default agent for Zoe planning, implementation strategy, code review, Greptile loops, Graphify-first codebase navigation, and repair work.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [zoe, engineering, cursor, graphify, greptile, multica]
    related_skills: [plan, writing-plans, subagent-driven-development, requesting-code-review, github-pr-workflow, github-greptile-loop, code-structure-cleanup, source-code-context]
---

# Zoe Engineering

Hermes is Zoe's default development agent. This is Zoe's local umbrella equivalent of the upstream `agentic-engineering-workflow` skill: small work units, source-backed context, minimal implementation first, cleanup pass, review loop, and explicit verification.

Use this skill for planning, implementation advice, code review, debugging, PR repair, Greptile loops, Multica board repair, Graphify-guided architecture work, and agentic engineering workflow tasks.

OpenClaw is fallback only when the task needs browser automation, persistent login/session state, Home Assistant UI flows, or a workflow Hermes cannot execute.

## Harness Operating Model

Zoe engineering tasks run inside the **Multica→Hermes autonomous harness**. Read this before any git/test/patch/PR command.

- **Phase pipeline.** Each Multica issue assigned to Hermes moves through `scout → implement → verify → review → closeout → retro`. You are running one phase of one task; do that phase's job and hand off cleanly.
- **Stay in your worktree — NEVER the live checkout.** Every task runs in an isolated git worktree. Its path is your `workspace_path` (shown on the Kanban task). Run ALL commands — `git status`, `git worktree list`, tests, patches, file reads, `gh pr create` — from inside `<workspace_path>`. **NEVER `cd` into, read from, write to, or reference `/home/zoe/assistant`** (the live checkout) for any command. Touching `/home/zoe/assistant` trips the runtime guard `WORKTREE_PATH_VIOLATION` and aborts your task. Even orientation commands must target the worktree (`git -C <workspace_path> status`).
- **Terminal protocol.** Every run MUST end by calling `kanban_complete` (success) or `kanban_block` (blocked/needs human). Never exit silently — a silent exit strands the task.
- **One small PR per implement task.** When your diff is finished, from inside `<workspace_path>` run `git push -u origin HEAD` then `gh pr create --base main`. Report `PR_URL`, `TESTS` run, and evidence. Ship exactly ONE small reviewable PR per implement task — do not bundle multiple concerns.
- **Capture-on-exit safety net.** If you run out of turns with a finished-but-unpushed diff, the harness auto-salvages it into a PR (kanban_adapter `_maybe_recover_unshipped_diff`). Do NOT rely on this — push and open the PR yourself. The salvage is a backstop, not the plan.
- **Reference docs:** `docs/guides/MULTICA_HERMES_PR_LOOP.md` and `docs/guides/ENGINEERING_HARNESS_LOOP.md` (read them at `<workspace_path>/docs/guides/...`).

## Mission Alignment (read first)

At the start of any Zoe engineering task, read the charter section relevant to your change (`<workspace_path>/docs/governance/ZOE_DESIGN_PRINCIPLES.md`) and the graphify map (`<workspace_path>/graphify-out/GRAPH_REPORT.md` or the `graphify_search` tool). Your `ZOE_WHY` block in SOUL.md is the condensed version. Honor the hard rules: per-user/scope memory and credentials, no `home`/`family`/`household` in the kernel, allow-list tools, proposal path for world-changing actions, harness-first (improve routing/prompts/memory/tools before reaching for a bigger model). Zoe is the AI layer of **the411.life** — favor the411-aligned designs; surface charter open questions instead of deciding them silently.

## Repo

You work in your task's isolated worktree, **not** the live checkout. The worktree path is your `workspace_path` (shown on the Kanban task); it is a full Zoe checkout pinned to its own branch.

```text
<workspace_path>   # e.g. ~/.worktrees/<task_id> — your isolated checkout
```

Never `cd /home/zoe/assistant` (the live checkout) — that trips `WORKTREE_PATH_VIOLATION`. Run everything from `<workspace_path>`.

Before changing Zoe files, from inside your worktree:

```bash
cd <workspace_path>
python3 tools/audit/validate_structure.py
```

Read `<workspace_path>/.zoe/AI_ASSISTANT_CHECKLIST.md` and respect `<workspace_path>/.zoe/manifest.json`.

## Non-Negotiables

- Production web/chat API is `services/zoe-data/`.
- Keep one production chat router: `services/zoe-data/routers/chat.py`.
- `services/zoe-core/` is retired reference code.
- Do not create `_v2`, `_new`, `_fixed`, `_backup`, or duplicate router files.
- Never hardcode secrets or print tokens.
- Never use destructive git commands, force push, or discard user changes.
- Use PostgreSQL as Zoe's primary data store; old SQLite migration docs are archive context only.

## Development Loop

1. Use Graphify before broad searches for architecture or cross-module work.
2. Use `opensrc` for third-party library source instead of guessing APIs.
3. Plan first for risky work or changes touching more than a couple of files.
4. Split large plans into small PR-sized units before implementation.
5. Build the minimal working feature first; do not mix broad refactors into the feature pass.
6. After the feature works, run a structure cleanup pass for duplicated runtime mechanics.
7. Use Greptile on PRs and fix real findings through `github-greptile-loop`.
8. Run focused tests and Zoe validators before reporting done.

Bias toward shipping a small usable increment instead of hiding behind one more private feature. A clean small PR with real feedback is better than a perfect local branch that never reaches review.

## Starter Prompt

```text
We are building this with Zoe's agentic engineering workflow.

Rules:
1. Keep the change small and reviewable.
2. Search existing Zoe code before creating new abstractions.
3. If using a package or framework, check local source with opensrc or upstream source before guessing APIs.
4. Build the minimal working version first.
5. After it works, run a code-structure cleanup pass.
6. Run focused tests, Zoe validators, and any live smoke checks that match the change.
7. Summarize what changed, what was tested, and what still needs human judgment.

Task:
<describe the feature or fix>
```

## Source Context

When integrating third-party packages or fast-moving tools:

- Prefer `opensrc path pypi:<package>` or `opensrc path owner/repo` to locate local source.
- Search the package source before coding against undocumented or uncertain APIs.
- Keep external source caches outside Zoe, under `~/.opensrc/repos/`.
- Do not vendor reference repos into the Zoe checkout (your worktree or the live tree).
- Avoid adding dependencies younger than 14 days unless the operator explicitly approves the risk.
- Report the package files, examples, or tests that informed the implementation.
- When a package breach trends, inspect Zoe for the package and affected versions before changing code.

## Cleanup Pass

Use `code-structure-cleanup` after a feature works, especially when Hermes created or touched similar mechanics in multiple files.

Keep Zoe domain policy in callers:

- Routes, intents, and actions own auth, user/scope policy, status transitions, and user-facing decisions.
- Service modules own reusable mechanics such as provider calls, parsing, validation, command execution, and payload transforms.
- Do not extract one-off logic just to make the code look abstract.
- Do not create duplicate routers, `_new`, `_fixed`, or parallel feature files.

## Verification

Minimum checks for most Zoe engineering changes (run from inside your worktree, never the live checkout):

```bash
cd <workspace_path>
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 -m py_compile services/zoe-data/agent_sync.py services/zoe-data/zoe_agent.py services/zoe-data/routers/chat.py
```

Adjust tests to the touched files. For live system changes, also check:

```bash
curl -sf http://127.0.0.1:8000/health
curl -sf http://127.0.0.1:8000/api/system/status
```

## Skill Capture (self-improvement)

After any Zoe engineering task of 5+ tool calls, or where you hit and solved a dead end, use `skill_manage` to capture the working procedure as a `SKILL.md` (prefer `patch` over a full rewrite). Keep domain policy in routes/intents and reusable mechanics in skills. The skill library is the durable asset — a captured procedure makes the next run cheaper and more reliable. Pinned Zoe-critical skills (`zoe-engineering`, `github-greptile-loop`, `zoe-graphify`, `agentic-engineering-workflow`, `zoe-board`) may be patched but are never auto-archived.

## Security And Human Judgment

- Never paste secrets into prompts, screenshots, logs, or PR comments.
- Never print tokens from local config, environment files, remotes, or MCP output.
- Prefer authenticator-app 2FA and a password manager for operator accounts; do not rely on SMS 2FA where stronger options exist.
- Explicitly call out security-sensitive changes, skipped tests, and decisions that need operator judgment.
