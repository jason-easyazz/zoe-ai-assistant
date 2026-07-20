# Creating Skills

> **Read this first.** This document describes what the code actually does today.
> An earlier version of this guide documented a skills loader, a
> `POST /api/skills/reload` endpoint, an `api_only` / `allowed_endpoints`
> enforcement layer and a `skills.lock` integrity check. **None of those exist.**
> They were never built. Everything below is verified against the runtime.

## What actually exists

Zoe has **skill discovery**, not a skill loader.

`openclaw_manager.list_skills()` parses each `SKILL.md` under
`~/.openclaw/workspace/skills/` for a **name and description only**, and
`zoe_agent.py` exposes that list through the `list_openclaw_skills` tool so the
brain can surface a capability gap ("I can't yet — the Discord skill would
enable it").

> **Corrected 2026-07-20.** This guide previously credited
> `skill_discovery.py` (`parse_openclaw_skills()` / `parse_hermes_skills()`).
> That module produced an agent card with zero callers and a markdown file with
> zero readers, fed no tool, and has been deleted. `~/.hermes/skills/` has no
> parser at all now — Hermes skills were never surfaced to Zoe's brain.
>
> **A description being listed does not mean the skill can run.** OpenClaw
> resolves its workspace-skills root from `agents.list[0].workspace`, which
> points at a directory containing no `skills/`, so workspace skills are not in
> the agent's catalog at all. Symlinked skills are additionally rejected
> (`symlink-escape`) unless `skills.load.allowSymlinkTargets` is configured.

That is the whole mechanism. A skill is a **description advertised to the model**.
It is not code that Zoe loads, sandboxes, or executes.

## The repo `skills/` directory is NOT wired to runtime discovery

This is the single most important thing to know, and the old guide got it wrong.

The `skills/` directory in this repository is **not read by any runtime code**.
Discovery only ever looks at `~/.openclaw/workspace/skills/` and `~/.hermes/skills/`.
There is no sync, copy, symlink, or install step that puts repo skills into either
location — dropping a `SKILL.md` into `skills/` has **no runtime effect whatsoever**.

Today 9 of the 11 skills under `skills/` do not exist in the discovery directories
at all. Two (`self-improvement`, `touch-panel`) share a *name* with a directory
under `~/.openclaw/workspace/skills/`, but those are **separate, independently
edited copies** — not the repo's files. Treat any resemblance as drift, not wiring.

Repo `skills/` is therefore documentation-for-humans-and-agents. It is real and
useful in that role — e.g. `skills/autoresearch-engineer/SKILL.md` documents the
`autoresearch_bridge.py` promotion contract — but it is not a runtime surface.

> Whether to build the loader or move `skills/` out of the repo is an open
> architectural decision. See [EXTENSIBILITY.md](../architecture/EXTENSIBILITY.md).

## Adding a skill the runtime will actually see

There is exactly **one** location Zoe reads:

```
~/.openclaw/workspace/skills/{skill-name}/SKILL.md
```

**Do not use `~/.hermes/skills/`.** It had a parser until 2026-07-20
(`skill_discovery.py`), that parser fed nothing, and it is now gone — so Zoe has
no reader for that directory at all. A skill installed there is invisible to her.
It remains meaningful to the *Hermes agent*, which reads it for its own purposes,
but that is a different consumer with a different catalogue.

**And placement alone is not sufficient.** OpenClaw resolves its workspace-skills
root from `agents.list[0].workspace` in `~/.openclaw/openclaw.json`, which
currently points at a directory with no `skills/` — so as of this writing the
agent's catalog contains **no workspace skills at all**. Symlinked skills are
additionally rejected (`symlink-escape`) unless `skills.load.allowSymlinkTargets`
names the target. Verify with the endpoint below rather than assuming the file
landing in the right directory is enough.

There is **no discovery cache and no watcher** — `skills_watcher.py` was deleted
with `skill_discovery.py` on 2026-07-20. `openclaw_manager.list_skills()` reads
the directory on each call, so a new skill is picked up with no restart and no
flush step.

`POST /api/agent/peers/{name}/skills/reload` has been removed; it existed only to
flush the deleted cache. `POST /api/skills/reload` never existed.

Before installing any third-party skill, scan it — see the root `AGENTS.md`
"Skill & extension safety" section (`skillspector scan <dir|file|git-url>`).

## SKILL.md format

Discovery is deliberately forgiving — it only needs to extract a description.

**OpenClaw skills** (`~/.openclaw/workspace/skills/`) — the parser tries, in order:

1. `<!-- metadata.when: ... -->` HTML comment
2. a `## When to Use` section (first 3 bullet/prose lines)
3. a `## Trigger conditions` section (first 3 lines)
4. fallback: the first non-heading line of the file (truncated to 150 chars)

The skill **id** is the directory name; the **name** is the directory name
title-cased. `SKILL.md` is preferred, `skill.md` is accepted.

```markdown
<!-- metadata.when: the user asks to send a Discord notification -->
# Discord

## When to Use
- The user wants a message posted to Discord.
```

**Hermes skills** (`~/.hermes/skills/`) use YAML frontmatter and support
categories and sub-skills:

```markdown
---
name: my-skill
description: What it does — this is the string surfaced to the model.
---
# My Skill
```

- Direct skill: `<skill>/SKILL.md` with `name:` / `description:`
- Category: `<category>/DESCRIPTION.md` with `description:`
- Sub-skill: `<category>/<skill>/SKILL.md` with `name:` / `description:`

Fields the old guide called mandatory — `version`, `author`, `api_only`,
`triggers`, `allowed_endpoints`, `tags`, `priority` — are **not parsed by
anything**. Including them is harmless but has no effect.

## Real skill endpoints

The OpenClaw router (`services/zoe-data/routers/openclaw.py`,
prefix `/api/openclaw`) is the actual skills API:

| Endpoint | Purpose |
|---|---|
| `GET /api/openclaw/skills` | List discovered skills |
| `GET /api/openclaw/skills/search` | Search skills |
| `GET /api/openclaw/skills/{name}/preview` | Preview a skill before install |
| `POST /api/openclaw/skills/{name}/install` | Install a skill |
| `POST /api/openclaw/skills/{name}/update` | Update a skill |
| `DELETE /api/openclaw/skills/{name}` | Remove a skill |

`GET /api/skills`, `GET /api/skills/audit/calls`, and
`POST /api/agent/peers/{name}/skills/reload` do not exist.

## Testing your skill

1. Confirm it is discovered: `curl /api/openclaw/skills` — your skill's `id`
   (its directory name) should appear with the description you expect.
2. Confirm the description reads well: it is the only text the model sees, so it
   is what decides whether the skill gets surfaced.
3. Send a chat turn that should surface the capability and check the model
   reaches for `list_openclaw_skills`.

Step 1 is the real gate. If the skill is not in a discovery directory, it will
not appear — no amount of frontmatter will change that.
