# docs/ — project documentation

## Purpose

All project documentation, organized by category. The root of the repository holds at most 10 markdown files; everything else lives here.

## Ownership

- `governance/` — `ZOE_DESIGN_PRINCIPLES.md` (the design charter — NORMATIVE for large design changes), cleanup safety, critical-files list, manifest system.
- `architecture/`, `api/`, `guides/`, `developer/`, `deployment/`, `adr/` — technical reference by audience.
- `strategy/`, `research/`, `reviews/`, `post-mortems/`, `implementation/`, `performance/` — working documents.

## Local Contracts

- New documentation goes in `docs/{category}/`, NEVER in the repository root.
- The charter in `governance/` is normative: read it before large design changes; the enforceable subset is mirrored in `.cursorrules` and `.cursor/rules/`.
- Retired documents are removed from the working tree; git history keeps the old bytes. Do not create or restore `docs/archive/`.
- `knowledge/` is an OKF knowledge bundle (markdown + YAML frontmatter, agent-curatable records — see root "Knowledge vs. Records (OKF)"). It holds descriptive facts, NOT contracts; binding rules stay in `AGENTS.md`.

## Forbidden

The autonomous knowledge/memory loop curates `knowledge/` only. It must NEVER:
- edit any `AGENTS.md` contract (contracts change only via the DOX pass + human review)
- create, modify, or delete documentation outside `knowledge/`
- alter `governance/` (the normative charter) or create/restore `docs/archive/`
- add files to the repository root

## Work Guidance

Several loose top-level .md files predate the category structure; place new material in categories rather than adding more loose files.

## Verification

`python3 tools/audit/validate_structure.py` (enforces the root .md cap and manifest coverage).

## Child DOX Index

No child AGENTS.md files yet.

OKF knowledge bundles (records, not contracts — see root "Knowledge vs. Records (OKF)"):
- [knowledge/](knowledge/index.md) — curated Zoe reference knowledge (tool stack, topology); agent-curatable.
- [knowledge/autopilots/](knowledge/autopilots/index.md) — Loop-Engineering contracts for the live Multica autopilots (Job/Inputs/Allowed/Forbidden/Output/Evaluation); agent-curatable.
- [audits/](audits/) — point-in-time capability + gap audits against live systems.
  Dated snapshots, NOT contracts: each records what was measured on that day, so a
  stale one is history rather than a wrong rule. Re-measure before acting on an old
  audit.
