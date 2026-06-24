# Claude Code — repo entrypoint

This file exists so Claude Code loads the repo's command-center hub (Claude Code
only auto-reads `CLAUDE.md`, not `AGENTS.md`).

**Read first:** `docs/VISION.md` — Zoe's north star, direction & core principles.

The full rules hub — graphify/codebase-memory, opensrc, the Greptile PR loop,
Hermes-first delegation, and the Child DOX Index — lives in `AGENTS.md`:

@AGENTS.md

> **Override:** the graphify section inherited from `AGENTS.md` is **superseded** —
> graphify is retired. Use the **codebase-memory** MCP for who-calls-what / architecture
> and **Serena** for symbol read + symbolic edits.

Use the **Serena** and **codebase-memory** MCP servers (wired in `.mcp.json`) for
code work — symbol read/edit and who-calls-what — over hand-grep/Read/Edit.
