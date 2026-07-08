---
type: Runbook
title: Relationship-Memory Flag-Enable Runbook
description: Operator procedure to turn on the three relationship-memory features (temporal edges, graph traversal, person-merge) in prod — the migrate-first order, the incremental replay-gated flag flips, live verification, and the flag-off (not schema-downgrade) rollback. All three ship OFF; this is how they go live safely. Also covers the independent recall-dossier flags (ZOE_MEMORY_COMPOSE_ENABLED + ZOE_PERSON_DOSSIER_ENABLED) that render a compact per-person line into the brain's recall packet.
tags: [relationships, memory, flags, deploy, migration, runbook, voice-replay]
timestamp: 2026-07-05T00:00:00Z
---

# Relationship-Memory Flag-Enable Runbook

The relationship-memory roadmap (temporal edges, recursive-CTE graph traversal, person-merge
/ entity resolution) is **merged but dark**: all three features default **OFF** and ship behind
env flags. This is the operator procedure to turn them on in prod without regressing the live
voice write-path or losing relationship history. It also covers the independent **recall
dossier** flags (`ZOE_MEMORY_COMPOSE_ENABLED` + `ZOE_PERSON_DOSSIER_ENABLED`, last section) that
control how a person is rendered into the brain's recall packet.

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

## Recall dossier — compact per-person line (independent add-on)

Separate from the three flags above (no migration, no write-path change): this controls how a
person is *rendered into the brain's recall packet*. OFF, a person is a thin `Name (rel) — notes`
line; ON, it becomes a compact cited dossier sourced from fields already in the DB:

```
- Alex Example (brother · family, score 82) — likes chocolate, fruit loops · enjoys travelling · alex@example.com, 555-0100, b.Jan 1 [people]
```

(relationship · circle · `health_score`→score, top-3 recent likes from `person_activities` with
same-verb grouping, notes, and email/phone/birthday; clipped so a chatty contact can't crowd the
prompt. PRs #1169 + #1170.)

| Flag (env, in `services/zoe-data/.env`) | Turns on | Reader |
|---|---|---|
| `ZOE_MEMORY_COMPOSE_ENABLED` | The whole relational recall block (increment 2b). **Gates everything below** — the dossier does nothing without it. **Already `=1` in prod** (2026-07-08). | `zoe_memory_compose.py:43` |
| `ZOE_PERSON_DOSSIER_ENABLED` | Swaps the thin person line for the dossier + adds one bounded batch read of recent likes. Default OFF. | `zoe_memory_compose.py` (`person_dossier_enabled`) |

**Enable:** set `ZOE_PERSON_DOSSIER_ENABLED=1` (compose is already on) → `systemctl --user restart
zoe-data.service`. No migration — reads existing `people` + `person_activities` columns.

**Independent of the three relationship flags:** the dossier reads people + activities, not the
temporal/graph/merge machinery, so it works with those flags on *or* off. It is, however, on the
voice **recall** path (compose runs when `needs_relational` fires), so treat a flip as
voice-path-adjacent — run the `~/.zoe-voice-samples` replay at deploy when there's RAM headroom.

**Rollback:** unset `ZOE_PERSON_DOSSIER_ENABLED` → restart. Instant, lossless (render-only; the thin
line returns). OFF is a byte-for-byte no-op — the dossier columns and the likes read only run when
the flag is on.

**Lab proof:** `services/zoe-data/tests/test_person_dossier_compose.py` (flag OFF no-op + ON
assembly + `_group_facts`/`_fmt_score` helpers).

### Ready-to-run flip (verified 2026-07-08)

Run on the host as `zoe`. The restart is **operator-authorized** (the turn classifier blocks it for
agents). State at verification: dossier code live at `ec41cb2a`, `ZOE_MEMORY_COMPOSE_ENABLED=1`,
`ZOE_PERSON_DOSSIER_ENABLED` unset — so this is a one-flag flip. Re-check step 0 before flipping in
case the tree has moved.

The whole block is safe to paste and safe to re-run: the flip is **guarded** on the dossier code
being live (won't flip a tree that lacks it) and the `.env` write is **idempotent**.

```bash
ENV=/home/zoe/assistant/services/zoe-data/.env
health() { for _ in 1 2 3 4 5 6 7 8; do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)
  [ "$code" = 200 ] && { echo "health 200"; return 0; }; sleep 2
done; echo "health $code (not up after ~16s)"; return 1; }

# 0. Pre-flight — expect all three OK
git -C /home/zoe/assistant merge-base --is-ancestor ec41cb2a HEAD \
  && echo "OK: dossier code live" || echo "STOP: deploy main first"
grep -E 'ZOE_MEMORY_COMPOSE_ENABLED|ZOE_PERSON_DOSSIER_ENABLED' "$ENV"  # expect COMPOSE=1, no DOSSIER
health

# 1. (optional, if RAM ≥ ~1.5 GB free) baseline the voice replay — compose is on the recall path
free -m | awk '/Mem:/{print "avail="$7"MB"}'
flock /tmp/zoe-voice-harness.lock \
  python /home/zoe/assistant/scripts/maintenance/voice_regression_probe.py

# 2. Flip + restart — GUARDED on the code being live, IDEMPOTENT write (safe to re-run)
if git -C /home/zoe/assistant merge-base --is-ancestor ec41cb2a HEAD; then
  grep -qxF 'ZOE_PERSON_DOSSIER_ENABLED=1' "$ENV" || echo 'ZOE_PERSON_DOSSIER_ENABLED=1' >> "$ENV"
  systemctl --user restart zoe-data.service
  health
else
  echo "STOP: dossier code not live in the checkout — deploy main first; did NOT flip"
fi

# 3. Verify — a RELATIONAL message for a real contact; look in "packet" for a dossier-shaped
#    [people] line: Name (rel · circle, score N) — likes … · contact
curl -s 'http://127.0.0.1:8000/api/memories/for-prompt' --get \
  --data-urlencode 'user_id=<your-user-id>' \
  --data-urlencode 'message=tell me about my brother' | python3 -m json.tool

# 4. Rollback (instant, lossless — thin line returns)
sed -i '/^ZOE_PERSON_DOSSIER_ENABLED=/d' "$ENV"
systemctl --user restart zoe-data.service && health
```

Notes: the dossier `[people]` line only renders when the message trips the relational gate (a
relationship word or "tell me about X") *and* the contact has data. If `/health` returns `000` on the
host (service "active"), that is the accept-queue-hang signature ([incident-runbook.md](incident-runbook.md)) — a
restart clears it.

## Contacts from known people (propose / backfill / promote)

Turns people Zoe knows only *narratively* (portrait / MemPalace facts) into structured, editable
contacts — via **user-confirmed suggestions**, never silent writes. Design SSOT:
[`docs/adr/ADR-contacts-from-known-people.md`](../adr/ADR-contacts-from-known-people.md). All merged
**dark** (Phases 1/2a/2b/3, PRs #1177/#1181/#1182/#1180). No migration — reads existing
`people` + `person_activities` columns.

| Flag (env) | Turns on | Reader |
|---|---|---|
| `ZOE_PERSON_SUGGEST_ENABLED` | Propose-on-mention (a mentioned person → "add to contacts?" card) **and** the `person_create` accept-executor (creates a full, **private-by-default** contact) **and** promote-on-confirm (stub→full) | `pending_suggestions.person_suggestions_enabled` |
| `ZOE_CONTACT_BACKFILL_ENABLED` | The one-shot backfill admin pass (`POST /api/memories/backfill-contacts`) that proposes contacts for people already known | `contact_backfill.contact_backfill_enabled` |
| `ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED` | A birthday mentioned for a not-yet-contact creates the person so the date lands (voice write-path; replay-gate at enable) | `person_extractor.birthday_capture_enabled` |

### Enable — propose-on-mention (works end-to-end today)
```bash
ENV=/home/zoe/assistant/services/zoe-data/.env
grep -qxF 'ZOE_PERSON_SUGGEST_ENABLED=1' "$ENV" || echo 'ZOE_PERSON_SUGGEST_ENABLED=1' >> "$ENV"
systemctl --user restart zoe-data.service
```
Now, when the user mentions a person who isn't a contact, Zoe surfaces an "Add X to contacts?" card
in that live chat; accepting creates a full editable contact (and promotes a matching stub). This
path stores the proposal under the **live** session, so it surfaces correctly.

### Backfill — ⚠️ has a delivery gap (do NOT rely on it live yet)
`ZOE_CONTACT_BACKFILL_ENABLED=1` + `POST /api/memories/backfill-contacts?user_id=<u>&session_id=<s>`
generates correct `person_create` proposals from known people — **but** suggestion retrieval
(`list_active` / `load_for_prompt`) filters `WHERE user_id=$1 AND session_id=$2`. A live chat/panel
uses a **per-conversation** session id, so proposals stored under the default `'backfill'` session
(or any static session) **never surface in the user's UI**. The endpoint docstring says as much.
Until a **non-session-scoped delivery** for backfill proposals lands (a "suggested contacts" review
view, or routing onto the user's next active session), running the backfill live is a **no-op for
the user** — it just accumulates orphan rows. Treat backfill as **blocked on that follow-up**.

### Rollback
Unset the flag(s) → restart. Reversible; created contacts (from accepted suggestions) persist as
normal editable people rows — delete via the contacts UI if unwanted.
