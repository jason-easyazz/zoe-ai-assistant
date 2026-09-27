# B3.1 — Bi-temporal supersession + keep-the-richer-fact reconciliation

**Status:** lab spike (draft PR #1692), flag-dark (`ZOE_BITEMPORAL_SUPERSEDE`, default off), nothing
prod-wired. Lab code + 50-pair fixture + regression net: `labs/b3-1-supersession/`
(the test lives inside the lab dir, hand-run — no CI lane collects `labs/`; see the
lab README). Program item B3.1 in
`beat-the-bar-2026-program.md`; review §7 #3 + register rows M7/M9 in
`docs/knowledge/state-of-zoe-review-2026-09-25.md`.

## 1. The bug, precisely

Two strict-xfail engine gaps in `services/zoe-data/tests/samantha_live/test_live_dedup.py`:

- **M7 correction not superseded** (`:97`) — `memory_quality._attribute_key` keys only
  possessive/copula assertions ("my dad's name is …"). Gemma distils "I work at Globex
  now, not Acme" to "user works at globex": key `None` → `classify_against_existing`
  falls back to text similarity (< `_SUPERSEDE_RATIO`) → **ADD**. Both employers stay
  approved.
- **M9 paraphrases accumulate** (`:145`) — "user's dad's name is neil" / "user's father
  is called neil" / "user has a father named neil" key to `father name` / `father` /
  `None`; each lands as a new row (observed father=3, globex=4 rows for 8 mentions).
- The weekly `memory_digest._resolve_contradictions` is a brain call per lexically
  overlapping pair (`_text_overlap ≥ 0.25`, `max_pairs=50`) with **newest-added wins**
  and no notion of *when a fact was true*: "worked at Acme 2018–21" and "works at Globex"
  contradict on text, so the history is retired as if it were wrong.

Today's store already has half the shape: Chroma rows carry `status` (`superseded` is in
`_BLOCKED_READ_STATUSES`), `supersedes_id` / `superseded_by_id`, `added_at`,
`expires_at`; `review(decision="edit")` writes new-row-first-then-retire (the safe order).
`person_relationships` already has `valid_from / valid_to / superseded_by` (migration
0015) with a partial unique index on the current edge. What is missing is **event time on
facts, an overlap rule, an attribute normaliser, and a richer-fact rule.**

## 2. Schema delta (plan — NOT applied)

Facts live in **Chroma metadata**, not a Postgres table, so the "columns" are flat scalar
metadata keys written by `MemoryService._build_metadata`; the Alembic migration covers
only `person_relationships`.

| key | type | meaning | backfill |
|---|---|---|---|
| `valid_from` | ISO-8601 or `""` | when the fact became TRUE (event time). Extractor may set it from the utterance ("since 2024", "used to"); else `added_at` | `added_at` |
| `valid_until` | ISO-8601 or `""` | when it stopped being true; empty = still true | `""` (`superseded` rows: the successor's `valid_from`) |
| `expired_at` | ISO-8601 or `""` | when the ROW was retired (transaction time) | `""`; `superseded` rows: their `reviewed_at` |
| `superseded_by` | id | forward link (already `superseded_by_id` — alias, keep the old key) | existing |
| `attribute_key` | `subject/attribute` string | the normalised key (§4), computed at write; `""` when none | lazy, at first idle pass |

Chroma metadata is scalar-only, so empty string stands for NULL (existing convention:
`concept_tags`, `related_ids`). Read paths are unchanged: `superseded` status stays the
visibility switch; `valid_until` non-empty + `status=approved` is a **historical fact that
recall may still serve when asked about the past** — a new capability, off by default.

Alembic `0029_bitemporal_person_relationships` (plan): `ALTER TABLE person_relationships
ADD COLUMN IF NOT EXISTS expired_at TEXT`, `ADD COLUMN IF NOT EXISTS attribute_key TEXT`;
backfill `expired_at = updated_at WHERE valid_to IS NOT NULL`. `valid_to` keeps its name
(it is `valid_until` semantically; renaming a live column buys nothing). **No downgrade
that drops columns** — rollback is the flag (§7).

## 3. The overlap rule (Graphiti `edge_operations.resolve_edge_contradictions`)

Four timestamps per fact: `valid_from` / `valid_until` (event time), `created_at` /
`expired_at` (transaction time). Half-open intervals; an open end is ±∞.

> A stored fact can contradict a new one **only if their validity intervals overlap**:
> `old.valid_from < new.valid_until AND new.valid_from < old.valid_until`.
> An old fact with `valid_until <= new.valid_from` is *history*, never a contradiction.

On contradiction, **never delete**: `old.valid_until = new.valid_from`,
`old.expired_at = now`, `old.superseded_by = new.id`, `old.status = superseded`. The
text is untouched, so "where did Person A work in 2019?" remains answerable.
`labs/b3-1-supersession/bitemporal.py::intervals_overlap / invalidate`.

## 4. Attribute-key normalisation (the M7 fix)

`attribute_key(text) → "subject/attribute" | None`. Subject ∈ {`user`, `person a`, …}
(first person → `user`); attribute from a framing table where every phrasing of one
attribute maps to one token: `works at | employed by | job is at | works for | employer is
| joined` → `employer`; `lives in | moved to | based in | home is` → `residence`; `father's
name is | father is called | has a father named | dad …` → `father name`; likewise
`mother name`, `dog name`, `cat name`, `car`, `favourite colour`, `birthday`, `phone`,
`allergy`, `school`. The value is the remainder; `same_value` is `memory_quality`'s
subset rule (rephrase = equal set, richer = superset, correction = leftover on each side).

Prod landing: fold the table into `memory_quality._attribute_key` (one function, all
writers already route through `reconcile_for_ingest`) and persist the key on the row so
the idle pass can group by it instead of re-parsing. Unrecognised text keys `None` and is
handed to the judge (§5), which is what closes M9 for framings the table does not know.

## 5. Reconciliation controller contract

**Input:** the new fact (text, `valid_from?`, `valid_until?`) + its top-k semantic
neighbours (k=10, mem0) shown with **small integer ids 1..k** (`[3] person a works at
acme`). **Output:** one of

| event | meaning | write |
|---|---|---|
| `ADD` | distinct fact, or history with a disjoint interval | new row |
| `UPDATE` | same fact, new phrasing is **richer** | replace text on the existing id (no new row) |
| `SUPERSEDE` | same attribute, different value, intervals overlap | new row first, then invalidate target (§3) |
| `NONE` | same fact, existing at least as rich | nothing |

Deterministic order (`bitemporal.reconcile`): (1) transition split — "switched from X to
Y" becomes the open `…Y` fact **and** an already-closed `…X` row if absent (mem0's rule:
both recorded); (2) drop non-live neighbours; (3) near-exact text dup (≥0.92) → value
check → richer rule; (4) same `attribute_key`: same value → richer rule; different value
→ `SUPERSEDE` if overlap else `ADD`; known key, no neighbour shares it → `ADD`; (5) only
if a key is unknown on one side → the **judge**.

**Judge** (`judge(new_text, [(id, text)…]) → {"event", "id"}`) is the single brain call,
mem0-prompt shaped. The controller **validates** it: an event outside the four, an id
not in the shown set, an exception, or no judge → `ADD` (a fact is never lost to the LLM
step); a judge `SUPERSEDE` is still subject to the overlap rule; a judge "same fact" is
still decided by the richer rule. The judge cannot mint ids and cannot delete.

**Richer rule** (mem0 "keep the fact which has the most information"): information =
salient characters of the **value**, not the sentence ("is employed by Globex" and
"works at Globex" both carry exactly `globex`); the candidate must be a **superset** of
the existing value (replacing "Neil, spelled N-E-I-L" with "Neil the fisherman" loses
the spelling — that is different, not richer); a keyed fact is never replaced by unkeyed
chatter; ties keep the stored row.

## 6. How it fixes M7 / M9

- M7 (`:97`): "user works at globex" and "user works at acme corporation" both key
  `user/employer`; values differ; both open intervals overlap → `SUPERSEDE`. Acme is
  closed with `valid_until = globex.valid_from`, still readable as history. Fixture:
  10/10 corrections, 7/7 transitions.
- M9 (`:145`): the three father phrasings key `user/father name` with value `{neil}`
  → `NONE` (or `UPDATE` when one adds the spelling); the eighth chatty mention keys
  `None` → judge → same fact → richer rule keeps the keyed row. Fixture: 10/10
  paraphrase, 8/8 richer-vs-distilled — including the four where the *existing* row is
  richer, which the pre-rule "newest wins" behaviour loses (control: 4/8, 0/10).
- The consolidation prod-enable hold (M9 register row) was on exactly the
  "distilled restatement replaces the richer fact" failure; §5's rule is its precondition.

Measured on the 50-pair synthetic fixture with the fake judge: accuracy 1.00, macro
P/R 1.00; **no overlap check** → historical 0/10 (every one wrongly `SUPERSEDE`d),
ADD recall 0.35; **no richer rule** → paraphrase 0/10, richer 4/8. Each control asserts
the specific wrong outcome, not just a lower score.

## 7. Prod wiring plan (idle-time only, flag-dark)

- **Where:** one new idle pass in `memory_digest.run_dreaming_cycle`, gated by
  `ZOE_BITEMPORAL_SUPERSEDE` (default off) **and** B3.2's dream gates (idle ≥ M min,
  cancel on speech, per-window brain-call budget). Not the write path: writers keep
  `reconcile_for_ingest` unchanged until the idle pass has a month of clean logs.
- **Per window:** select approved rows added since the last pass (cursor in
  `memory_consolidation_state`, migration 0014); for each, `svc.search(limit=10)` →
  `reconcile`. Brain calls = only the judge path (key unknown on one side) — on the
  fixture 2 of 50; on the live dedup corpus expect ≤ 20 % of new facts, i.e. **≤ 1 brain
  call per 5 new facts, hard-capped at 20 per window** (B3.2 budget). Each call is
  ~300 prompt tokens, `max_tokens 60`, `temperature 0`, JSON-only, ≤ 15 s timeout —
  the `_is_contradiction` shape already in `memory_digest`.
- **RAM:** zero new residents. The judge reuses the live brain's llama-server slot
  (`:11434`), serial, `/slots`-gated like `router-selftrain`; embeddings for `search`
  are the resident bge-small. Nothing loads. Skip the window entirely below 2 GB
  `MemAvailable` (the voice-stack guard).
- **Replaces** `_resolve_contradictions`' newest-wins loop once the flag is on; until
  then both are off/unchanged (the weekly pass stays as is).
- **Writes:** `review(edit)` order (new first, retire second); `superseded` status
  unchanged; new metadata keys per §2; every decision logged with event + reason + ids
  (no fact text), counted per event on the status endpoint.

## 8. Rollback and gates

**Rollback = flag off.** Rows already written keep `valid_from/valid_until/expired_at`
(inert metadata; every reader ignores unknown keys). **Never a schema downgrade** — the
0029 columns stay; superseded rows are already invisible under today's status rule and
history rows are approved-but-closed, which today's readers treat as approved (so a
rollback after history rows exist needs `valid_until != ""` added to the read filter
first — do that in the same PR as the flag reader, before any write).

Gates before the flag reader ships (all deterministic, locally runnable):
1. `labs/b3-1-supersession/test_supersession_lab.py` green, hand-run against the PR
   head (no CI lane collects `labs/`). When the controller moves to a non-lab package
   for prod wiring, its tests move with it into `tests/unit` and become `ci_safe`.
2. `test_live_dedup.py:97` and `:145` flipped from strict-xfail to pass on the Jetson
   lane, against the real Gemma extractor — the actual M7/M9 evidence.
3. A real-brain judge run on the 50-pair fixture ≥ 0.9 P/R (hand-run, `/slots`-gated,
   ≥ 2 GB free) — proves the prompt, not just the controller.
4. Shadow mode first: the idle pass logs decisions for a week without writing;
   operator reads the `SUPERSEDE` list before writes are enabled.
5. Voice replay gate unaffected (no voice-path file touched) — asserted by the PR's
   changed-file list.
