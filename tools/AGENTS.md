# tools/ — repo audit and maintenance utilities

## Purpose

Tooling that enforces repository structure and safety: validators, cleanup, generators, and verification helpers.

## Ownership

- `audit/` — `validate_structure.py` (manifest/orphan check, root .md cap), `validate_critical_files.py`, `find_file_references.sh`, `sw_precache_digest.py` (records a digest of the service worker's precached files next to `SW_VERSION`; `tests/unit/test_sw_precache_digest.py` fails on drift — bump `SW_VERSION`, then `--write`).
- `audit/flag_inventory.py` — the `ZOE_*` flag inventory GENERATED from the flag registry (`services/zoe-data/flag_registry.py`) joined with an AST/TS scan; also the conflicting-default check, the legacy direct-read ratchet (`docs/knowledge/flag-ratchet.json`, `--tighten-ratchet`) and `--sync-registry`. `tests/unit/test_flag_registry.py` + `test_flag_inventory.py` enforce it; record `docs/knowledge/flag-registry.md`.
- `audit/memory_writer_map.py` — the memory writer map GENERATED from code (`docs/knowledge/memory-writer-map.md`; role lines in `docs/knowledge/memory-writer-notes.json`); `tests/unit/test_memory_writer_map.py` fails on a stale map or a new writer module with no note.
- `cleanup/` — `safe_cleanup.py` (dry-run first, `--execute` only after operator approval).
- `validate_module.py` — module structure + the `modules/AGENTS.md` security contract (service-token gate on state-changing `/tools/*`, fail-closed 503, loopback-only ports). Security checks are AST/parsed-compose, never text search; keep them that way, and keep the documented templates passing.
- `generators/`, `intent/`, `docker/`, `reports/`, `verification/`, `utilities/` — supporting tool groups.

## Local Contracts

- `python3 tools/audit/validate_structure.py` and `python3 tools/audit/validate_critical_files.py` must pass before every commit; the pre-commit hook runs them and must never be bypassed with `--no-verify`.
- New file locations must be reflected in `.zoe/manifest.json` `approved_patterns` or validation fails with orphan files.
- Cleanup is staged: dry-run, operator review, execute, validate. Never bulk-delete without this process.

## Work Guidance

Loose scripts in the `tools/` root predate the subfolder structure; place new tools in the appropriate subfolder, not the root.

## Verification

Run both validators; they exit non-zero on failure and print the offending paths.

## Child DOX Index

No child AGENTS.md files yet.
