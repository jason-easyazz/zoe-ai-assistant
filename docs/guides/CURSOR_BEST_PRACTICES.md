# Cursor And AI Assistant Best Practices For Zoe

**Purpose**: Keep agent-assisted work small, reviewable, and aligned with Zoe's current architecture.

This guide replaces the old recent-changes workflow. Use the current context sources below instead of retired snapshot files or briefing scripts.

## Current Context Sources

Use these sources before broad or risky work:

- The **codebase-memory** and **Serena** MCP servers (see `.mcp.json`) for a map of
  Zoe's codebase — codebase-memory for who-calls-what / architecture / seams,
  Serena for symbol read + symbolic edits.
- `AGENTS.md` for repo-level agent instructions.
- `.cursor/rules/*.mdc` for Cursor-specific rules.
- `.zoe/AI_ASSISTANT_CHECKLIST.md` and `.zoe/manifest.json` before file changes.
- `git status --short --branch` and recent commits for branch state.
- Focused source reads and `rg` searches for the actual files involved.

For architecture or cross-module questions, start with codebase-memory. If the graph looks stale, treat it as a rough map and verify against source.

> graphify is **retired** — there is no committed `graphify-out/` graph and no
> `graphify query`/`path`/`explain` workflow. Do not resurrect it. See `AGENTS.md`.

## Development Loop

1. Understand the request and current branch state.
2. Search existing code before creating new abstractions.
3. For third-party packages, use `opensrc` or upstream source before guessing APIs.
4. Plan first for broad, risky, or multi-file changes.
5. Build the minimal working feature first.
6. Run a cleanup pass for duplicated runtime mechanics after the feature works.
7. Verify with focused tests and Zoe validators.
8. Use a small PR and Greptile review loop for mergeable work.

Keep domain policy in routes, actions, intents, and UI handlers. Move only reusable mechanics into service-layer helpers. Service helpers should use explicit parameters, structured returns, consistent failure semantics, and avoid hidden global state or unrelated database mutation.

## Source Context

Reference source code instead of stale docs when integrating packages or tools:

```bash
opensrc path pypi:<package>
opensrc path owner/repo
```

Keep external source caches outside the repo under `~/.opensrc/repos/`. Do not vendor reference repos into `/home/zoe/assistant`.

Avoid adding dependencies younger than 14 days unless the operator explicitly approves the risk.

When source context informs an implementation, name the package files, examples, or tests used in the final summary.

## Hermes And Skills

Hermes is the default escalation agent for Zoe engineering, planning, review, repair, Greptile loops, codebase-memory/Serena-guided codebase work, and board repair.

Useful local Hermes skills live under `~/.hermes/skills`:

- `zoe-engineering`
- `source-code-context`
- `code-structure-cleanup`
- `github-greptile-loop`
- `zoe-status-refresh`

These are operator-level Hermes skills. Do not copy them into Zoe runtime `skills/` unless the goal is to expose them as user-facing Zoe capabilities.

OpenClaw remains a manual fallback only when the operator explicitly asks for OpenClaw or Hermes lacks a needed workflow.

## Review Workflow

For reviewable changes:

1. Work on a feature branch.
2. Keep the diff small enough to review.
3. Push a focused PR against `main`.
4. Let Greptile review independently.
5. Use Greptile MCP for status/comment reads only; trigger or re-trigger reviews through `scripts/maintenance/run_greploop_guard.sh --pr N --once` so the shared Greploop state prevents duplicate agent work.
6. For Zoe engineering tasks, run `python3 scripts/maintenance/greploop_guard.py --task-id <id> --packet-only` before broad expensive-agent repair.
7. Read the PR diff first; if it is too large or bundled, split before running the loop.
8. Fix real correctness, security, data-loss, behavior, or test findings.
9. Re-run focused tests and use the guarded loop to request any needed Greptile re-review.

Do not push directly to `main`, bypass branch protection, or force-push protected branches.

## Verification

Run these for most docs/rules or light backend changes:

```bash
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
```

For backend Python changes, add focused tests and syntax checks:

```bash
python3 -m pytest services/zoe-data/tests/<relevant_test>.py -q
python3 -m py_compile services/zoe-data/<changed_file>.py
```

For live system changes, also check:

```bash
curl -sf http://127.0.0.1:8000/health
curl -sf http://127.0.0.1:8000/api/system/status
```

## Anti-Patterns

- Starting from memory when codebase-memory/Serena or source files should be checked.
- Creating `*_new`, `*_fixed`, `_v2`, backup, or duplicate router files.
- Adding broad hardcoded language branches to `services/zoe-data/routers/chat.py`.
- Treating Greptile as a replacement for local verification.
- Copying external source repos or Hermes operator skills into Zoe runtime folders.
- Refactoring the whole app as part of a feature pass.
