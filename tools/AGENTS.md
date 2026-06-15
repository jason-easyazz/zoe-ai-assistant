# tools/ — repo audit and maintenance utilities

## Purpose

Tooling that enforces repository structure and safety: validators, cleanup, generators, and verification helpers.

## Ownership

- `audit/` — `validate_structure.py` (manifest/orphan check, root .md cap), `validate_critical_files.py`, `find_file_references.sh`.
- `cleanup/` — `safe_cleanup.py` (dry-run first, `--execute` only after operator approval).
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
