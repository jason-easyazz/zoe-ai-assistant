# tools/ — repo audit and maintenance utilities

## Purpose

Tooling that enforces repository structure and safety: validators, cleanup, generators, and verification helpers.

## Ownership

- `audit/` — `validate_structure.py` (manifest/orphan check, root .md cap), `validate_critical_files.py`, `find_file_references.sh`, `live_state.py` (derives `docs/knowledge/live-state.json`, the flag DEFAULTS the code ships, from the flag inventory plus the reader shape at each call site, so a 1/0 default is `on`/`off` only for a flag read as a bool and stays numeric for an int flag; `--check` exits 1 when stale), `sw_precache_digest.py` (records a digest of the service worker's precached files next to `SW_VERSION`; `tests/unit/test_sw_precache_digest.py` fails on drift — bump `SW_VERSION`, then `--write`). `privacy_canary.py` (the household-data canary for this PUBLIC repo: `--scan` fails on a tracked file carrying a household name, personal email, home coordinate or phone number; the repo holds only keyed HMAC digests in `privacy_canary_digests.json` — the key (`ZOE_PRIVACY_CANARY_KEY` / `~/.zoe/privacy-canary/key`) and the plaintext list (`~/.zoe/privacy-canary/canaries.txt`) never enter the repo; `--rebuild` after adding a canary; `tests/unit/test_privacy_canary.py`).
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
