# Zoe Review Rules

Greptile should review Zoe as a local-first, multi-user assistant with a host-run `services/zoe-data` API and a chat/voice first product surface.

## Priorities

- Find correctness, security, data-loss, privacy, and user-facing regressions before style nits.
- Call out missing tests when the change touches routing, memory, database writes, auth, agent delegation, voice, or AG-UI behavior.
- Prefer small, surgical fixes that match existing Zoe patterns.
- Do not suggest parallel `_new`, `_old`, `_fixed`, or backup files.
- Treat hardcoded absolute paths outside approved Zoe roots as portability risks unless they are documented local operator paths.
- Zoe uses a PR-first workflow with protected `main`; do not recommend direct pushes, admin bypasses, or force pushes as normal fixes.

## Architecture

- Production chat API is `services/zoe-data/routers/chat.py`; do not fork it.
- Natural-language understanding belongs in `intent_router.py`, Zoe Agent, Hermes, or OpenClaw, not broad ad hoc branches in `chat.py`.
- `services/zoe-core/` is retired and should not receive new production features.
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

## Graphify And Context

Use `graphify-out/GRAPH_REPORT.md` as map context when fresh, but treat it as stale if its "Built from commit" differs from `git rev-parse HEAD`. Do not ask contributors to run `graphify update .` or install Graphify hooks; this repo uses the safe full extract and `cluster-only . --no-viz` report-refresh commands documented in `.cursor/rules/graphify.mdc`.
