---
type: index
title: Operator Hermes skills — backup copies (NOT installed)
description: Version-controlled backup of the ten Zoe-authored operator skills that live at ~/.hermes/skills. Preservation only — nothing here is read by any runtime, and restoring is a deliberate manual step.
---

# Operator Hermes skills — backup copies

## Why this bundle exists

`~/.hermes/skills` is **not a git repository and has no remote**. The ten skills
copied here carry `author: Zoe` in their frontmatter, which means they exist
nowhere upstream — they are not part of `NousResearch/Hermes-Agent`, and a
`hermes` reinstall would not bring them back. Before this bundle they existed in
exactly one place: that directory, on that box.

The other 58 skills under `~/.hermes/skills` are third-party (`Hermes Agent`,
`community`, `Orchestra Research`, `Nous Research`, and assorted ports). Those
ship with the upstream project and are re-pullable, so they are deliberately
**not** backed up here.

## This is a backup, not an installation

`services/zoe-data/skill_discovery.py` parses exactly two directories —
`~/.openclaw/workspace/skills/` and `~/.hermes/skills/`. This tree is read by
**nothing**. Copying a skill here does not install it, enable it, or change what
Zoe can do.

Worth knowing before you trust that module's name: **`skill_discovery.py` is a
dead-end catalogue.** Its only two outputs are `GET /api/agent/peers/{name}/card`
(zero callers — verified across Python and JS including `dist/`) and
`~/.openclaw/workspace/FEDERATION_SKILLS.md` (zero readers). It dispatches
nothing. Four separate implementations parse these same directories, and the most
sophisticated one is the only one feeding nothing.

This bundle also sits outside `skills/` on purpose: `skills/AGENTS.md` states
that operator-level Hermes skills "must not be mixed in here". That contract is
respected — these are records under `docs/knowledge/`, not skill definitions
under `skills/`.

All ten `SKILL.md` files here are byte-identical to their source (verified with
`cmp`). One file was deliberately **not** copied: a stray
`zoe-engineering/SKILL.md.bak.20260615-200903` in the source directory, excluded
per the root `AGENTS.md` rule against hoarding backup copies in the repo. It
still exists at `~/.hermes/skills/zoe-engineering/` and is worth deleting there.

## Restoring

Only restore if a skill is found **missing** from `~/.hermes/skills`. Restoring
is manual and deliberate:

```bash
cp -r docs/knowledge/operator-skills/<skill-name> ~/.hermes/skills/
```

`skills_watcher.py` watches `~/.hermes/skills` and invalidates the discovery
cache on change, so a restored skill is picked up without a zoe-data restart.

## Contents

Eight live skills — six of which `AGENTS.md` names as binding process:

| Skill | Notes |
|---|---|
| `zoe-engineering` | Default agent contract for Zoe engineering work |
| `github-greptile-loop` | Named in `AGENTS.md`; wraps `scripts/maintenance/greploop_guard.py` |
| `source-code-context` | opensrc-first third-party source lookup |
| `code-structure-cleanup` | Cleanup-pass contract |
| `agentic-engineering-workflow` | Micky-style workflow pack entrypoint |
| `grep-loop-review-workflow` | Micky-style workflow pack entrypoint |
| `zoe-board` | Multica board repair |
| `zoe-cloakbrowser` | Browser work via the CloakBrowser broker |
| `zoe-status-refresh` | Generated knowledge refresh |

Two stale, retained only so the retirement decision is recorded rather than
silently lost:

| Skill | Status |
|---|---|
| `zoe-graphify` | **Stale.** graphify is retired (`CLAUDE.md`); do not restore. |
| `zoe-engineering` | Description still claims "Graphify-first codebase navigation" — inaccurate; navigation is codebase-memory + Serena. Fix on restore. |

## OpenClaw skills — [`openclaw/`](openclaw/)

27 skills from `~/.openclaw/workspace/skills/` (a git repo with **no remote**).
Unlike Hermes, these carry no `author:` metadata, so provenance was established by
diffing all 34 against the stock `skills/` shipped with `openclaw@2026.5.12`:

| Class | Count | Backed up? |
|---|---|---|
| Zoe-only (absent from stock) | 20 | yes |
| Stock but locally modified | 11 | yes, minus the 4 symlinks below |
| Stock, untouched | 3 | no — re-pullable from the npm package |

**31 of 34 OpenClaw skills are ours, versus 10 of 68 for Hermes** —
`briefing`, `family-data`, `grocery-meal`, `ha-patterns`, `home-assistant`,
`journal`, `memory-consolidation`, `proactive`, `touch-panel`, `transactions`,
`weather`, `zoe-ui`, `dynamic-widgets`. So this is not a vendor pack.

**But none of them has ever run.** Verified 2026-07-20 by two independent
methods: session-corpus forensics (18,433 tool calls, 17,042 `read`s, **17,040 of
them `HEARTBEAT.md`, zero of any skill path**), and executing OpenClaw's own
shipped loader against the live config — it builds a 14-skill catalog containing
**no `zoe-*` entries at all**. Corroborated behaviourally: `journal_entries`,
`transactions`, `open_loops`, `background_tasks` and `dashboard_layouts` all hold
**0 rows**.

The cause is a **config bug, not a design gap.** The loader *does* scan
`workspaceDir/skills`, merged last at highest precedence — but `workspaceDir`
resolves from `agents.list[0].workspace` (which beats `agents.defaults.workspace`)
to `~/.openclaw/agents/main`, and `~/.openclaw/agents/main/skills` does not exist.
Repoint it and 12 load immediately. `agents/main` is also where `HEARTBEAT.md`
lives, which is why the corpus is 17,040 heartbeat reads.

These skills are therefore **unproven intent, not lost capability**. They are
worth reading before rebuilding the same ideas on Flue — which supports this exact
`SKILL.md` format natively — and worth nothing as a restore target.

**Two traps that made this look wired-up:**

- **Silent shadowing.** 12 workspace skills collide by name with bundled ones
  (`github`, `healthcheck`, `taskflow`, `weather`, …). The agent loads the STOCK
  version, so the catalog shows the skill "present" while the customization is inert.
- **`list_openclaw_skills` reports them as installed.** `mcp_server.py` scans the
  workspace directory directly and returns `builder_skills_installed`, so Zoe
  claims the builders are installed while the agent cannot load one.

**Four are symlinks into the repo and are NOT duplicated here** —
`zoe-capability-extender`, `zoe-page-builder`, `zoe-widget-builder` point at
`skills/openclaw/*` (already version-controlled), and `zoe-verify` dangles. All
four are rejected at load time as `symlink-escape` regardless, because the config
has no `skills.load.allowSymlinkTargets`. See
[`../../../skills/AGENTS.md`](../../../skills/AGENTS.md). Also broken:
`memory-consolidation` has a lowercase `skill.md` and could never load.

## Related

- [../../architecture/zoe-flue-integration.md](../../architecture/zoe-flue-integration.md) §8.2 — the Hermes retirement inventory these skills belong to
- [../merge-and-deploy.md](../merge-and-deploy.md) — the Greptile loop `github-greptile-loop` drives
