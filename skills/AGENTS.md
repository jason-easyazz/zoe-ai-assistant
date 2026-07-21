# skills/ — Zoe skill definitions (NOT wired to runtime discovery)

## Purpose

Skill definitions for the Zoe assistant, one subfolder per skill, each described
by a `SKILL.md`.

**Most of this directory is not read by any runtime code — but `openclaw/` is.**
Runtime discovery (`services/zoe-data/skill_discovery.py`) parses exactly two
directories: `~/.openclaw/workspace/skills/` and `~/.hermes/skills/`. Adding a
directory *here* generally has **no runtime effect**.

**`skills/openclaw/` is symlinked into the discovery dir — and still does not
load. Verified 2026-07-20 by running OpenClaw's own shipped loader.**
`~/.openclaw/workspace/skills/` contains four symlinks pointing back here:

| Symlink in the discovery dir | Target here | Loads? |
|---|---|---|
| `zoe-capability-extender` | `openclaw/zoe-capability-extender` | **no** — symlink-escape |
| `zoe-page-builder` | `openclaw/zoe-page-builder` | **no** — symlink-escape |
| `zoe-widget-builder` | `openclaw/zoe-widget-builder` | **no** — symlink-escape |
| `zoe-verify` | `openclaw/zoe-verify` | **no** — target does not exist |

Three independent reasons, any one sufficient:

1. OpenClaw resolves `workspaceDir` from `agents.list[0].workspace`, which wins
   over `agents.defaults.workspace`. It points at `~/.openclaw/agents/main`, and
   `~/.openclaw/agents/main/skills` **does not exist** — so the whole workspace
   skills root is empty regardless of symlinks.
2. Even from the correct root, the loader rejects these as
   `reason=symlink-escape` unless `skills.load.allowSymlinkTargets` lists the
   target. The live config has **no `skills.load` key at all**.
3. `zoe-verify`'s target was deleted from the repo.

Consequence: editing `skills/openclaw/*` changes **nothing** at runtime today.
Do not treat it as a live surface — but do not treat the symlinks as harmless
either, because they make the wiring *look* connected. `mcp_server.py`
(`list_openclaw_skills`) scans the workspace dir directly and reports
`builder_skills_installed`, so Zoe claims these are installed while the agent
cannot load one.

Two prior versions of this contract were wrong in opposite directions: first
"there is no sync, copy, symlink, or install step feeding this tree" (false —
the symlinks exist), then "those skills ARE live" (also false — they are
rejected). Restore neither.

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
