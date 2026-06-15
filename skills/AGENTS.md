# skills/ — Zoe runtime skills

## Purpose

User-facing skills for the running Zoe assistant. Each subfolder is one skill described by a `SKILL.md` file, discovered by the router at runtime.

## Ownership

One directory per skill (e.g. `calendar-events/`, `shopping-list/`, `smart-home/`, `touch-panel/`, `proactive-agent/`). The skill's `SKILL.md` is its definition; supporting assets live beside it.

## Local Contracts

- Adding a skill is a file drop (a new directory with `SKILL.md`), not a code edit in the router or kernel.
- These are NOT the operator-level Hermes engineering skills — those live outside the repo under `~/.hermes/skills` and must not be mixed in here.
- Skills may reference scopes (`personal` / `shared` / `ambient`) but must not assume household/family structure in kernel APIs.

## Work Guidance

Keep each `SKILL.md` self-contained: trigger conditions, behavior, and any tool usage explicit in the file.

## Verification

After adding or changing a skill, confirm the router discovers it via a live chat turn that should trigger the skill.

## Child DOX Index

No child AGENTS.md files; per-skill documentation is the skill's own `SKILL.md`.
