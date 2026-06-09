# docs/ — project documentation

## Purpose

All project documentation, organized by category. The root of the repository holds at most 10 markdown files; everything else lives here.

## Ownership

- `governance/` — `ZOE_DESIGN_PRINCIPLES.md` (the design charter — NORMATIVE for large design changes), cleanup safety, critical-files list, manifest system.
- `architecture/`, `api/`, `guides/`, `developer/`, `deployment/`, `adr/` — technical reference by audience.
- `strategy/`, `research/`, `reviews/`, `post-mortems/`, `implementation/`, `performance/` — working documents.
- `archive/` — retired documents. Move here instead of deleting; excluded from the knowledge graph.

## Local Contracts

- New documentation goes in `docs/{category}/`, NEVER in the repository root.
- The charter in `governance/` is normative: read it before large design changes; the enforceable subset is mirrored in `.cursorrules` and `.cursor/rules/`.
- When in doubt about deleting a doc, move it to `archive/`.

## Work Guidance

Several loose top-level .md files predate the category structure; place new material in categories rather than adding more loose files.

## Verification

`python3 tools/audit/validate_structure.py` (enforces the root .md cap and manifest coverage).

## Child DOX Index

No child AGENTS.md files yet.
