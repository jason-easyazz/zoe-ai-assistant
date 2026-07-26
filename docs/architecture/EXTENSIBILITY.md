# Extensibility Architecture

> **Corrected.** An earlier version of this document described a skills
> enforcement layer (`api_only` rejection at load time, an `allowed_endpoints`
> executor whitelist, `skills.lock` integrity checking, a `~/.zoe/skills/` →
> `modules/*/skills/` → `skills/` precedence chain). **None of it exists in
> code.** Everything below is verified against the runtime.

Zoe has two extensibility surfaces, and they are less symmetrical than the old
"two-layer model" framing suggested: **modules** ship real code; **skills** ship
descriptions.

## Layer 1: Modules (real infrastructure)

Modules are add-on services served under `/modules/`. They ship actual code —
Docker services, intents, widgets, MCP tools. See [modules/AGENTS.md](../../modules/AGENTS.md).

Live modules today: `omnigent`, `zoe-music`.

**Directory structure (as actually used):**
```
modules/{module-name}/
├── docker-compose.module.yml   # Docker services
├── main.py                     # Module entry point / bridge
├── intents/                    # HassIL intent YAML + handlers
│   ├── {name}.yaml
│   └── handlers.py
├── widget/                     # Optional dashboard widgets
│   ├── manifest.json
│   └── index.html
└── README.md
```

There is **no `modules/{name}/skills/` convention**. No module ships skills, and
no code would auto-load them if one did.

**When to create a module:**
- Needs its own Docker service (API, database, etc.)
- Adds hardware integration (sensors, cameras)
- Requires significant compute (ML model, browser automation)
- Has complex state management

## Layer 2: Skills (descriptions advertised to the model)

A skill is a Markdown file whose **description** is parsed and handed to the
brain so it knows a capability exists. That is all a skill is.

`openclaw_manager.list_skills()` parses `SKILL.md` files from
`~/.openclaw/workspace/skills/`, and `zoe_agent.py` surfaces that list via the
`list_openclaw_skills` tool.

> **Corrected 2026-07-20.** This section previously credited
> `skill_discovery.py` (plus `skills_watcher.py` for cache invalidation) with
> feeding the `list_openclaw_skills` tool. It never did — those modules produced
> an A2A agent card with **zero callers** and a `FEDERATION_SKILLS.md` with
> **zero readers**, dispatched nothing, and have been deleted. The tool always
> used `openclaw_manager`'s own independent parser.
>
> Caveat worth carrying: the surfaced list is **not** evidence a skill can run.
> OpenClaw resolves its workspace-skills root from `agents.list[0].workspace`,
> which points at a directory with no `skills/` — so as of this writing none of
> the 31 Zoe-authored workspace skills has ever been loaded, while
> `list_openclaw_skills` still reports the builders as installed.

**Zoe does not load, sandbox, or execute skills.** There is no executor, so there
is nothing to whitelist. When a skill's capability is actually invoked, it is
invoked by the peer agent (OpenClaw / Hermes) that owns it, under that agent's
own privileges.

**There is no precedence chain.** `~/.zoe/skills/` is not read by anything.
`modules/*/skills/` is not read by anything. The repo's own `skills/` directory
is **not read by anything** — see below.

**When to write a skill:**
- You want the brain to know a capability exists and mention it when relevant
- The capability is implemented by OpenClaw or Hermes, not by zoe-data

Format and the real endpoints: [../guides/CREATING_SKILLS.md](../guides/CREATING_SKILLS.md).

## Open decision: the repo `skills/` directory

The repo's `skills/` tree is **disconnected from runtime discovery**. Nothing
reads it; no sync, copy, symlink, or install step feeds it into
`~/.openclaw/workspace/skills/` or `~/.hermes/skills/`. 9 of its 11 skills do not
exist in the discovery directories at all; `self-improvement` and `touch-panel`
share only a *name* with independently maintained copies under `~/.openclaw`.

Today the tree functions as documentation for humans and agents — a real role
(`skills/autoresearch-engineer/SKILL.md` documents the `autoresearch_bridge.py`
promotion contract), just not a runtime one.

Two coherent resolutions, **both requiring an operator decision**:

1. **Build the loader** — make repo `skills/` a genuine discovery source (or
   install it into the discovery dirs at deploy). This makes the docs' original
   promise true and puts skills under version control and review.
2. **Move them out** — relocate the tree to `~/.openclaw/workspace/skills/` (or
   accept it as docs-only and say so in its `AGENTS.md`), leaving one home for
   skills instead of two that silently drift.

Until that is decided, the honest position is the one documented here: repo
`skills/` is not wired to runtime.

## Security

The old version of this section listed five enforcement guarantees. **None are
implemented.** No code rejects a skill for missing `api_only`, no executor
enforces `allowed_endpoints`, and no `skills.lock` file is written, read, or
verified anywhere in the repo. Relying on those guarantees would be unsafe.

The real security posture:

- **Skills are untrusted input, not sandboxed code.** A skill file influences
  what the model believes it can do. Treat a malicious skill as a
  prompt-injection and capability-confusion vector.
- **Execution happens in the peer agent** (OpenClaw / Hermes), with that agent's
  privileges. Zoe's process does not sandbox it.
- **Scan before installing.** Root `AGENTS.md` → "Skill & extension safety":
  `skillspector scan <dir|file|git-url>`, and record the outcome or a deliberate
  waiver.
- ~~**Cache reload is admin-gated**~~ — **removed 2026-07-20** with
  `skill_discovery.py`; there is no skill cache to reload.

See [../governance/SECURITY_POLICY_SKILLS.md](../governance/SECURITY_POLICY_SKILLS.md).
