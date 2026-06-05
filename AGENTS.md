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
- Prefer `opensrc path pypi:<package>` or `opensrc path owner/repo` to locate already-fetched source.
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
