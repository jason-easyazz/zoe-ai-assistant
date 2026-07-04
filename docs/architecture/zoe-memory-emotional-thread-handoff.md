---
type: handoff
status: open
owner: Flue brain team (soul-side signal) + Zoe memory (store-side wiring)
opened: 2026-07-04
related:
  - zoe-memory-samantha-buildplan.md
  - zoe-flue-integration.md
  - zoe-memory-admission-gates.md
---

# Handoff — Samantha criterion #2 (emotional-thread continuity)

## TL;DR

Fact recall (#1) and evolving understanding (#4) are **delivered + live-verified**.
Emotional-thread continuity (#2) is **blocked on a capture signal that only the Flue
brain can emit** — it is not a memory-store change we can prove out alone. This note is
the ask to the Flue team, with the exact contract the memory side will honour once the
signal exists.

## Evidence (why this is a capture gap, not a wiring gap)

Queried jason's full store (`mempalace_drawers`, user_id=jason), 2026-07-04:

| type | count | | type | count |
|---|---|---|---|---|
| person | 38 | | goal | 6 |
| fact | 33 | | preference | 3 |
| insight | 7 *(all archived junk)* | | identity | 2 |
| relationship | 1 | | habit | 1 |

- **Zero `emotional_moment` rows.** The extractor exists in `services/zoe-data/memory_digest.py`
  but has never produced one for a real user.
- The 7 `insight` rows are meta-commentary garbage ("The concept of a 'gift' is being used
  as a placeholder…"), all already **archived** — correctly rejected by the quality gate
  (`memory_quality.is_storable_fact`) and not reaching recall.
- Emotional queries return nothing emotional: *"how have I been feeling lately"* → generic
  top-12 dump; *"how was my day"* → "My mum's birthday is the 17th…".

There is **no substrate** to wire a packet against. Building emotional recall now would ship
flag-off plumbing with nothing real to prove it — the exact trap that sank prior increments.

## The ask (Flue brain / soul-side)

Emit an explicit **emotional-significance signal** when a turn carries durable emotional
weight (stress, grief, joy, a milestone, a worry the user keeps returning to). The brain is
the only component that can judge this; the memory store cannot infer it from raw text.

Deliver it by calling the existing writer with an explicit type, e.g.:

```
POST /api/memories   (or the internal writer path Flue already uses)
  user_id:      <uid>
  text:         "<the durable emotional fact, first-person-normalised>"
  memory_type:  "emotional_moment"
  valence:      pos | neg | mixed          # optional but wanted for tone-matching
  intensity:    0.0–1.0                     # optional; feeds importance scoring (3b)
```

Constraints so it doesn't reintroduce junk:
- Store the **durable fact**, not the transcript line. "Jason has been anxious about the
  house settlement" — not "User: I'm so stressed about the house". The quality gate
  (`memory_quality.is_storable_fact`) will reject speaker-echo / transcript-echo shapes.
- Only emit on genuine significance — do not tag every turn. Sparse + high-signal.

## The contract (memory side — what we do once the signal lands)

Once real `emotional_moment` rows exist for a user, the memory side will, evidence-first:

1. **Recall routing** — extend `memory_gate.message_needs_memory` so emotional queries
   ("how have I been", "was I stressed", "how am I doing") fire search, and rank
   `emotional_moment` rows into the for-prompt packet (`routers/memories.py
   _build_memory_prompt_packet`). Ships flag-OFF, lab-proven on the real rows first.
2. **Importance boost (3b)** — wire `intensity` into the dormant importance arm of hybrid
   retrieval (2a) so weightier emotional moments surface first.
3. **Live-verify** the same way the portrait was: real user, real query, before prod flag flip.

## Success signal (what "done" looks like — the monitored check)

Run `scripts/maintenance/check_emotional_thread.py` (added with this note):

- **Substrate:** ≥1 non-archived `emotional_moment` row appears for a real user.
- **Recall:** an emotional query returns that row at/near the top of the for-prompt packet.

When substrate first appears, the memory-side wiring above is unblocked and I pick it up.

## Status log

- 2026-07-04 — opened. Blocked on Flue-side signal. Memory side (#1, #4) delivered + live.
- 2026-07-04 — **Flue capture signal DELIVERED** (PR #1003, `feat/emotional-moment-capture`).
  Both halves land: zoe-data's `memory_store` intent now carries `memory_type` /
  `valence` / `intensity` (valence/intensity via ingest metadata →
  `candidate_valence` / `candidate_intensity`), and the Flue brain has a new
  `remember_emotional_moment` tool + a sparse, silent capture doctrine. **Substrate
  now lands:** live-verified on a test sidecar + worktree zoe-data against a
  dedicated `emo-test-user` — emotional turns produced non-archived `emotional_moment`
  rows with durable third-person text + correct valence/intensity, neutral turns
  stayed silent, quality gate passed; `check_emotional_thread.py` reports UNBLOCKED.
  Honest reliability caveat for the memory side: on the 4B brain emission is real but
  not yet 100% (fires on most negative/high-stress turns, sometimes misses positive/joy
  turns, does NOT over-emit on chit-chat) — recall wiring can proceed on the real rows;
  doctrine/trigger hit-rate tuning can follow.
- 2026-07-04 — **Memory-side recall wiring BUILT** (`feat/emotional-thread-recall`),
  contract item #1, default OFF behind `ZOE_EMOTIONAL_RECALL_ENABLED`. Two pieces:
  (a) `memory_gate.message_needs_emotional_recall` — a SEPARATE emotional-cue detector
  (both valences, catches statements + third-person the base gate misses), OR'd into
  the for-prompt search gate only when the flag is on; (b) `_build_memory_prompt_packet(
  boost_emotional=)` floats `emotional_moment` rows by `candidate_intensity` ahead of
  plain facts, **only on an emotional turn** (floating on every packet would be
  always-on surfacing = criterion #3, out of scope). Lab-proven on the LIVE store via
  the deployed `execute_intent` handler (demo users only, cleaned up): heavy user (20+
  facts) whose emotional moment is crowded out of the generic top-12 — OFF it stays
  lost, ON it surfaces; neutral turns byte-for-byte no-op; 68 gate/packet tests green.
  **Not yet enabled in prod** — flag flip is the deliberate follow-up after merge+deploy
  and a real-user live-verify. Item #2 (intensity → 2a importance arm) still to follow.
- 2026-07-04 — **Enabled in prod + live test found a gap + fixed it** (`ZOE_EMOTIONAL_RECALL_ENABLED=1`,
  deployed via #1004). Live-testing on the real service exposed that float+search is NOT
  robust for a heavy user: a *generic* emotional query ("how have I been doing") has no
  lexical overlap, so semantic search ranked ordinary facts above the emotional row, and
  `load_for_prompt`'s top-N had already truncated it — the row never surfaced. Fix (PR
  #1005, `feat/emotional-thread-recall-pin`): on an emotional turn, **explicitly fetch**
  the user's `emotional_moment` rows (type-filtered, intensity-ordered) and PIN them to
  the front of the packet, ahead of search hits, dedup by id. Re-proven on the live
  service against the exact worst case: OFF the row stays lost, ON the generic emotional
  query surfaces it AND it leads; neutral stays a no-op. 109 tests green.
