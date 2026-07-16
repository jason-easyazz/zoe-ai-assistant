# skills/ — Zoe skill definitions (NOT wired to runtime discovery)

## Purpose

Skill definitions for the Zoe assistant, one subfolder per skill, each described
by a `SKILL.md`.

**This directory is not read by any runtime code.** An earlier version of this
contract claimed each skill was "discovered by the router at runtime". That is
false. Runtime discovery (`services/zoe-data/skill_discovery.py`) parses exactly
two directories — `~/.openclaw/workspace/skills/` and `~/.hermes/skills/` — and
there is no sync, copy, symlink, or install step feeding this tree into either.
Adding a directory here has **no runtime effect**.

What this tree really is today: version-controlled documentation for humans and
agents. That is a genuine role — `autoresearch-engineer/SKILL.md` is the
reference for the `autoresearch_bridge.py` promotion contract — but it is not a
runtime surface. Whether to build a loader or move these out is an open operator
decision: see
[docs/architecture/EXTENSIBILITY.md](../docs/architecture/EXTENSIBILITY.md#open-decision-the-repo-skills-directory).

## Ownership

One directory per skill (e.g. `calendar-events/`, `shopping-list/`, `smart-home/`,
`touch-panel/`, `proactive-agent/`). The skill's `SKILL.md` is its definition;
supporting assets live beside it.

Note the drift hazard: `self-improvement/` and `touch-panel/` share a *name* with
directories under `~/.openclaw/workspace/skills/`, but those are separate,
independently edited copies. Editing here does not change those; do not assume
the two are in sync.

## Local Contracts

- Adding or editing a skill here is a documentation change, not a runtime change.
  To change what the running assistant can do, edit the skill in its discovery
  directory (`~/.openclaw/workspace/skills/` or `~/.hermes/skills/`).
- These are NOT the operator-level Hermes engineering skills — those live outside
  the repo under `~/.hermes/skills` and must not be mixed in here.
- Skills may reference scopes (`personal` / `shared` / `ambient`) but must not
  assume household/family structure in kernel APIs.
- Do not restate the loader/`api_only`/`allowed_endpoints`/`skills.lock` model in
  a `SKILL.md` — none of it exists. See
  [docs/governance/SECURITY_POLICY_SKILLS.md](../docs/governance/SECURITY_POLICY_SKILLS.md).

## Work Guidance

Keep each `SKILL.md` self-contained: trigger conditions, behavior, and any tool
usage explicit in the file. Where a skill documents a real code contract, name the
module and function so the link back to runtime is checkable.

Format reference (including the format the discovery parser actually accepts):
[docs/guides/CREATING_SKILLS.md](../docs/guides/CREATING_SKILLS.md).

## Verification

No runtime verification exists for this tree, because nothing loads it — a live
chat turn cannot confirm a skill here, and the previous instruction to do so was
unfollowable.

For a skill placed in a discovery directory, confirm it is parsed:
`curl /api/openclaw/skills` should list its `id` and description.

## Child DOX Index

No child AGENTS.md files; per-skill documentation is the skill's own `SKILL.md`.
