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

Runtime skill discovery (`services/zoe-data/skill_discovery.py`) parses exactly
two directories — `~/.openclaw/workspace/skills/` and `~/.hermes/skills/`.
This tree is read by **nothing**. Copying a skill here does not install it,
enable it, or change what Zoe can do.

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

## Related

- [../../architecture/zoe-flue-integration.md](../../architecture/zoe-flue-integration.md) §8.2 — the Hermes retirement inventory these skills belong to
- [../merge-and-deploy.md](../merge-and-deploy.md) — the Greptile loop `github-greptile-loop` drives
