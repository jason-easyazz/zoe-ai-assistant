# Zoe Review Rules

Greptile should review Zoe as a local-first, multi-user assistant with a host-run `services/zoe-data` API and a chat/voice first product surface.

## Priorities

- Find correctness, security, data-loss, privacy, and user-facing regressions before style nits.
- Call out missing tests when the change touches routing, memory, database writes, auth, agent delegation, voice, or AG-UI behavior.
- Prefer small, surgical fixes that match existing Zoe patterns.
- Flag PRs that are too large or bundle unrelated product decisions; recommend splitting before deep review.
- Do not suggest parallel `_new`, `_old`, `_fixed`, or backup files.
- Treat hardcoded absolute paths outside approved Zoe roots as portability risks unless they are documented local operator paths.
- Zoe uses a PR-first workflow with protected `main`; do not recommend direct pushes, admin bypasses, or force pushes as normal fixes.

## Architecture

- Production chat API is `services/zoe-data/routers/chat.py`; do not fork it.
- Natural-language understanding belongs in `intent_router.py`, Zoe Agent, Hermes, or OpenClaw, not broad ad hoc branches in `chat.py`.
- `services/zoe-core/` is Zoe's Pi-agent core (the reasoning/orchestration brain on Gemma 4) — new core features belong here. It orchestrates and delegates; it must not absorb `zoe-data`/`zoe-auth`/`zoe-database` code or become a monolith. The OLD `zoe-core` Docker monolith (replaced by `zoe-data`) is retired and archived under `docs/archive/retired-services/zoe-core/`; do not revive that legacy tree.
- Host `llama-server`, Hermes, OpenClaw, and Multica are local services; avoid Docker-only assumptions in core code.

## Data Safety

- Memory writes should go through `MemoryService` unless maintaining the memory service or MCP bridge.
- PostgreSQL is the live relational store. Do not reintroduce SQLite as a production path.
- Use parameterized SQL and transactions. Destructive database changes require explicit approval and verified backups.
- No credentials, API keys, or personal data should be hardcoded or logged.

## Chat And Voice First

For new user-facing features, check for:

- A chat/voice intent or tool path.
- Rich inline status or AG-UI response, not just a URL.
- `/api/<feature>/status` for integrations.
- Settings or feature-page controls where users can configure the feature without leaving Zoe.

## Code Intelligence And Context

Code intelligence is provided by the codebase-memory MCP (re-indexed on demand); graphify is retired. Use the codebase-memory MCP for architecture and code-graph context instead of any `graphify-out/` artifact — the old `graphify-out/GRAPH_REPORT.md` map is no longer committed or maintained.

For SDK, framework, MCP, or package integrations, expect the implementation to be checked against `opensrc` or upstream source before relying on uncertain docs. Call out guessed APIs, unexplained replacement dependencies, or new packages less than 14 days old without explicit operator approval.

## Code Structure Cleanup

After feature work, flag duplicated runtime mechanics that should live in a service-layer helper: repeated provider calls, command execution, parsing, validation, payload transforms, or readiness/retry mechanics. Keep domain policy in routes, actions, intents, or UI handlers.

Service helpers should use explicit parameters, structured returns, consistent error semantics, and avoid hidden global state or unrelated database mutation.

## Python And Test Hygiene

- Reset or override module-level globals (cached singletons, `_ENV_BOOTSTRAPPED`, `_ENV_FILES`, and similar) with `monkeypatch.setattr`, never direct attribute assignment. Direct assignment is not restored on teardown and leaks stale state into later tests. Apply this consistently across every test in a file, not just newly added ones.
- For module-level env-derived constants, patch the module variable after import; do not set `os.environ` after importing the module and expect the already-loaded constant to change.
- When code under test writes `os.environ` directly (bypassing monkeypatch), guarantee cleanup in a `try/finally` that pops the key. `monkeypatch.delenv` only restores keys monkeypatch itself set.
- Keep test changes scoped to the behavior under test; do not refactor unrelated fixtures in the same PR.

## Pre-PR Self-Review Checklist

This is the standard every PR is graded against — by Greptile and by the harness review phase. Before opening or finalizing a PR, review your own diff against it and fix violations first, so the first review lands clean (target: Greptile 5/5, zero comments):

1. **Scope** — smallest change that satisfies the acceptance criteria; no unrelated churn, no scope creep, one concern.
2. **Tests** — touched behavior has a focused test; test hygiene above is satisfied (monkeypatch.setattr for globals, no env leaks, consistent across the file).
3. **Patterns** — matches existing Zoe patterns and the architecture/data-safety rules above; reuses service-layer helpers instead of duplicating mechanics.
4. **No junk** — no `_new`/`_old`/`_fixed`/backup/tmp files; no commented-out code; no debug prints.
5. **Secrets** — no credentials, API keys, tokens, or personal data added or logged.
6. **Commit** — conventional, imperative subject under 72 chars; body explains why, not what.
