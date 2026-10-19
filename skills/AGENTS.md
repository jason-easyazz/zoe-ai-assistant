# skills/ — Zoe skill definitions (NOT wired to runtime discovery)

## Purpose

Skill definitions for the Zoe assistant, one subfolder per skill, each described
by a `SKILL.md`.

**No runtime code reads this directory.** OpenClaw and Hermes are retired, and the
discovery directories they used (`~/.openclaw/workspace/skills/`, `~/.hermes/skills/`)
were deleted 2026-10-18; nothing is wired to load a `SKILL.md`. Adding a directory
*here* has **no runtime effect**. (`skills/openclaw/` holds the retired OpenClaw
builder-skill definitions - documentation only, deletion candidates.)

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

## Local Contracts

- Adding or editing a skill here is a documentation change, not a runtime change.
  What the running assistant can do is defined by the Zoe MCP tools
  (`services/zoe-data/mcp_server.py`) and the brain sidecar's own tool registry.
- Do not restore a discovery directory for these; a loader, if ever built, is an
  operator decision (see the EXTENSIBILITY link above).
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

## Child DOX Index

No child AGENTS.md files; per-skill documentation is the skill's own `SKILL.md`.
