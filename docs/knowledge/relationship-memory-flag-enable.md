---
type: Runbook
title: Relationship-Memory Flag-Enable Runbook
description: Operator procedure to turn on the three relationship-memory features (temporal edges, graph traversal, person-merge) in prod — the migrate-first order, the incremental replay-gated flag flips, live verification, and the flag-off (not schema-downgrade) rollback. All three ship OFF; this is how they go live safely.
tags: [relationships, memory, flags, deploy, migration, runbook, voice-replay]
timestamp: 2026-07-05T00:00:00Z
---

# Relationship-Memory Flag-Enable Runbook

The relationship-memory roadmap (temporal edges, recursive-CTE graph traversal, person-merge
/ entity resolution) is **merged but dark**: all three features default **OFF** and ship behind
env flags. This is the operator procedure to turn them on in prod without regressing the live
voice write-path or losing relationship history.

Design SSOT: [`docs/adr/ADR-relationship-memory.md`](../adr/ADR-relationship-memory.md).
Deploy discipline this depends on: [merge-and-deploy.md](merge-and-deploy.md) (merged ≠ live),
[voice-pipeline.md](voice-pipeline.md) (the replay gate).

## The three flags

| Flag (env, in `services/zoe-data/.env`) | Turns on | Touches | Reader |
|---|---|---|---|
| `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED` | Supersession on relationship change (close old edge, `valid_to`/`superseded_by`, one current row per pair) | **Live voice/chat write-path** — `person_extractor._write_relationship` on every turn | `person_extractor.py:39` |
| `ZOE_RELATIONSHIP_GRAPH_ENABLED` | The `GET /people/{id}/graph` neighbours endpoint (bounded, owner-scoped BFS) | Read-only API; **now only traverses current edges** (`valid_to IS NULL`) | `relationship_graph.py:52` |
| `ZOE_PERSON_MERGE_ENABLED` | `merge_person` — fold a duplicate/stub contact into a real one (re-points satellites + edges, soft-deletes stub) | Explicit admin op | `person_merge.py:57` |

Flags are read from `os.environ` lazily on each call, so a **service restart** (which reloads
`.env`) is what makes a change take effect. Truthy = `1/true/yes/on`.

## Hard precondition — migration 0015 FIRST

All three flags require the temporal columns from **migration `0015`**
(`valid_from` / `valid_to` / `superseded_by` + the partial unique index
`person_relationships_pair_active WHERE valid_to IS NULL`):

- temporal writes `valid_to`/`superseded_by`;
- graph traversal filters `AND pr.valid_to IS NULL` (coupled deliberately as of PR #1044);
- person-merge re-points edges under the partial current-edge index.

**Enable a flag on a schema without 0015 and it will error at runtime.** So: migrate, *then* flags.
Migration is a **separate deploy step** — it does **not** run at service startup
(`database.py:79`).

## Procedure

### 0. Pre-flight
- Confirm the merged relationship work is actually present in the live checkout — an **ancestry**
  check, not just "what's at HEAD" (later commits can sit on top of it):
  ```
  git -C /home/zoe/assistant merge-base --is-ancestor 58e67241 HEAD \
    && echo "OK — relationship work present" \
    || echo "MISSING #1024–#1044 — do NOT proceed"
  ```
  (`58e67241` = the #1044 integration-test squash; if it isn't an ancestor of HEAD the temporal/
  graph/merge code may be absent and the flags will error.)
- Confirm flags are currently OFF: `grep -E 'ZOE_(TEMPORAL_RELATIONSHIPS|RELATIONSHIP_GRAPH|PERSON_MERGE)_ENABLED' services/zoe-data/.env` → expect none/`0`.
- **Baseline the voice replay corpus** so every later flip has something to diff against:
  ```
  flock /tmp/zoe-voice-harness.lock \
    python scripts/maintenance/voice_regression_probe.py   # records baseline
  ```
  (Corpus `~/.zoe-voice-samples`; numbers are RELATIVE — warm harness. See voice-pipeline.md.)

### 1. Apply migration 0015 (schema only — behaviour unchanged while flags OFF)
```
bash scripts/deploy/migrate.sh          # runs `alembic upgrade head` in ZOE_DATA_DIR
```
Verify the columns + index exist:
```
psql "$POSTGRES_URL" -c "\d+ person_relationships" | grep -E 'valid_from|valid_to|superseded_by'
psql "$POSTGRES_URL" -c "\di person_relationships_pair_active"
```
Nothing changes behaviourally here: flags are still OFF, existing rows get `valid_from = created_at`
and `valid_to = NULL` (all current). This step is safe to run and sit on.

### 2. Flip flags incrementally — one at a time, restart + verify between each
Do them in dependency order (write-path first, then the read/admin features built on it).
After each `.env` edit: `systemctl --user restart zoe-data.service` (operator-authorized — the
turn classifier blocks this as a "prod deploy", so it is a deliberate manual step; the
merge→checkout auto-sync does **not** reliably restart).

**2a. `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED=1`** — the only flag on the hot voice write-path, so
**replay-gate it**:
```
# after restart:
flock /tmp/zoe-voice-harness.lock \
  python scripts/maintenance/voice_regression_probe.py   # compare vs step-0 baseline
```
Gate: **said-vs-did must not regress** (a command that used to work and now fails = a bug) **and
per-stage speed must not regress**. If either regresses → unset the flag, restart, investigate.

**2b. `ZOE_RELATIONSHIP_GRAPH_ENABLED=1`** — read-only endpoint, not on the hot path, but restart
is still required. Smoke it against a **demo** contact:
```
curl -s "$ZOE_BASE/people/<demo_person_id>/graph?max_depth=2" | jq '.nodes | length'
```
Expect `200` + nodes (it returns `403` while the flag is off).

**2c. `ZOE_PERSON_MERGE_ENABLED=1`** — explicit admin op; exercise once on **two demo stubs**
(never real contacts blindly) and confirm satellites re-point + the source soft-deletes.

### 3. Live verification (demo user, never Jason's real data)
- **Temporal:** change a demo person's relationship type, then
  `SELECT rel_type, valid_to, superseded_by FROM person_relationships WHERE person_a_id=<demo>` →
  exactly one current row (`valid_to IS NULL`) + one closed row pointing to it.
- **Graph:** the `/graph` endpoint returns current-edge neighbours, owner-scoped, and does **not**
  surface a superseded edge.
- **Merge:** merge two demo stubs → satellite rows re-pointed, edges deduped under the partial
  index, source `deleted=1`.

## Rollback

**Preferred — flag off, keep the schema.** Unset the flag(s) in `.env`, `systemctl --user restart
zoe-data.service`. Instant, lossless: existing superseded rows simply become dormant history; with
the graph flag off the endpoint 403s again; with temporal off no new supersession is written.

**Do NOT `alembic downgrade` once temporal has produced superseded rows.** The `0015` downgrade
recreates the full (non-partial) unique pair index and will hit a duplicate-pair violation for any
pair that has both a current and a superseded row (documented in the migration). Temporal history
is intentionally lossy to reverse — roll back with the flags, not the schema.

## Notes / gotchas
- The migration and the graph flag are **coupled**: never enable `ZOE_RELATIONSHIP_GRAPH_ENABLED`
  on a schema missing 0015.
- Lab proof for the composed behaviour lives in
  `services/zoe-data/tests/test_relationship_features_integration.py` (all three flags on, isolated
  SQLite) — run it before a prod flip if the code has moved since #1044.
- Only `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED` is truly on the voice hot path; it is the flag the
  replay gate exists for. The other two still need a restart-and-smoke.
