---
type: Reference
title: The recall relevance gate (ZOE_RECALL_GATE) - which durable rows enter the packet, and in what order
description: Register item BM4 / blueprint 2.8. A per-turn gate over the owner's durable rows - entity triggers derived from the rows at write time, the floor's similarity search as one signal, sticky / cooldown / delay, a token budget and a stable order, authority respected. Flag off|shadow|enforce, default shadow (the packet is byte-identical to the floor's). The shadow-identity proof, the offline replay (what enforce would add and drop per Samantha-bar / day-sim / person cell) and what enforce needs.
tags: [samantha, recall, relevance-gate, memory, voice-path, zoe-data, BM4, language-independence]
timestamp: 2026-10-09T00:00:00Z
---

# The recall relevance gate (`recall_gate.py`, `ZOE_RECALL_GATE`)

Spec: [Samantha brain blueprint](../architecture/samantha-brain-blueprint-2026-10-09.md) sections 2.3 (what the packet carries),
2.8 (the borrowed piece: SillyTavern World Info **semantics** - entity-triggered injection with sticky / cooldown / delay and a
token budget; AGPL-3.0, so no code is taken) and 2.9 (language independence by construction). Memory rules:
[memory-authority](memory-authority.md). Code: `services/zoe-data/recall_gate.py`; wired in `routers/memories.py`
(`memory_for_prompt`), `personalisation_hop.py` (`build`), `memory_service.py` (`_build_metadata`, the write-time stamp) and
`zoe_flue_client.py` (`note_turn`). Tests: `services/zoe-data/tests/test_recall_gate.py`. Replay: `scripts/perf/recall_gate_replay.py`.

**This is a voice-path change** (the memories router builds the packet the Flue seam prepends to the owner's words, and
`zoe_flue_client` calls `note_turn` on every brain turn), so the replay gate applies to the PR.

## The class it replaces

Which durable rows enter the brain's packet was decided by a floor: the ranked read (a 70-day decay), one similarity search (keyword
gated) and, for advice requests, the hop's English word lists. A fact the owner stated reached the brain only when one of those
happened to match. Day-sim S9b is the standing example (PR #1953): "Every morning at 6am I walk our kelpie Juniper..." was stored,
was durable, and still did not reach "What should I wear tomorrow?" because the hop only read rank >= 4 rows and its topic regex had
to recognise the habit. The gate makes the choice a decision with several signals, a budget, a memory of the session and a log line.

## Flag and modes

| `ZOE_RECALL_GATE` | What happens |
|---|---|
| `off` | nothing runs: no state, no pool read, no log, no write-time stamp |
| `shadow` (**default**) | the packet and the hop are **byte-identical** to the floor's. The gate runs as a background task after the packet is built (nothing before first audio), keeps its own session state as if it were enforcing, and writes one `RECALL_GATE` line per turn and surface |
| `enforce` | the gate's selection replaces the floor's (`memory_for_prompt` rebuilds the packet over the gate's rows in the gate's order; `personalisation_hop.build` returns a hop built from the gate's picks). Every failure falls back to the floor |

The log line (ids are the last `_` segment of the row id, 8 characters - `zoe_<user>_<hash>` ids share their first characters; counts,
never text):

```
RECALL_GATE user=<id> served=<n> would_add=<ids|-> would_drop=<ids|-> budget=<tokens> surface=packet|hop turn=<n> mode=shadow|enforce cap=400 held=<n> cooled=<n> delayed=<n> skipped=<-|guest|off_record|empty|error>
```

`would_add` = rows the gate selects that the floor's packet did not carry; `would_drop` = rows the floor carried that the gate does
not select. The flag is registered in `docs/knowledge/flag-inventory.md` (regenerated, not hand-edited).

## Design in ten lines

1. **Candidates** = the floor's own rows (`facts`, `hits`) + the owner's durable pool (`MemoryService.load_durable_for_hop`: approved,
   owner's own, rank >= user_stated_derived, never pasted or instruction-shaped). A pool row is added only if `restraint.decide`
   (as if enforcing) would let it in; guests never; an off-record turn abstains and arms nothing.
2. **Triggers derived from the row** (`derive`, stamped at write time beside restraint's class as `gate_t` / `gate_v` / `gate_h`,
   re-derived while the version or text hash no longer matches): stem keys, capitalised names, clock times (`6am` = `6:00` = `6 Uhr` =
   `6時`), and a routine flag (a habit cue AND a time of day).
3. **Signals** (sum; threshold 2.0 = *triggered*): a name the owner said 2.5; a rare key 1.5 / mid 0.8 / common 0.3 (rarity within
   the owner's own rows); a shared clock time 2.0; a plan cue ("tomorrow") + a routine row 1.2; the floor's similarity hit 2.0 + 1/rank
   if **near** (distance <= 0.69, or the nearest hit <= 0.80), 1.0 if mid (<= 0.90), else nothing - the search returns its k nearest
   whatever they are; the hop's word-list match 2.5 (the legacy floor, one signal among the others).
4. **sticky** (2): a triggered row stays eligible for 2 more turns of the session. **cooldown** (3): a row that is not freshly
   triggered by the owner's words is not re-served within 3 turns of being served. **delay** (2): the floor's generic filler is not
   eligible in the first 2 turns. **filler_max** (2) caps that filler. Defaults are in `Config`; `filler_max=0` is pure abstain.
5. **Authority**: owner-verbatim > owner-derived > inferred. An inferred (or unverified-speaker) row enters only by a near similarity hit
   or a name the owner said, is never held by sticky, and loses ties (bonus 0.30 / 0.15 / 0 for ordering only, never for the threshold).
6. **Budget**: 400 tokens (about the 1,600-character recall block of blueprint 2.3), 12 rows; the hop 120 tokens, 2 rows. Rows are admitted by score.
7. **Stable order**: rows are *presented* in the order they entered the session's selection, so while nothing changes the same rows
   come in the same order and a new row appends (prefix-cache friendly); a row in last turn's selection wins a near-tie at the budget edge.
8. **State** is in memory, keyed `(user, session)`, bounded (128 sessions, 6 h), never crosses users. The Flue seam calls
   `note_turn(user, session, message)` once per turn (the turn counter advances once per distinct message; a `recall_memory` tool query in
   the same turn is judged with the owner's own words). The noted turn is ALSO per `(user, session)` - never one "current turn" per user:
   a web turn that starts while the same user's voice turn waits on a tool cannot steer the voice turn's recall or its sticky / cooldown
   counters. The in-process packet and hop calls find their session from the task that noted the turn (`current_session()`, a
   `ContextVar`) or an explicit `session_id` (`/api/memories/for-prompt?session_id=`, `personalisation_hop.build(session_id=)`); a call that
   carries only the user while SEVERAL of their turns are live (the sidecar's `recall_memory` HTTP tool today) gets no gate decision
   (`skipped=ambiguous_session`, the floor stands) rather than a guess.
9. **Abstain** (enforce): nothing triggered and no filler eligible = an empty packet (`Config.abstain=False` keeps the floor's instead).
10. **Fail-open**: any error, a guest, an off-record turn, continuity mode (the S4 block is budgeted around its closing ask) = the floor stands.

## Language independence (blueprint 2.9, L4)

Nothing in `recall_gate.py` reads English. Matching is on ids, NFKC-casefolded stem keys (CJK by character bigram, split at the
language's function characters and at script boundaries), clock times and the row's own structure. The per-language **words** - stop
words, plan cues ("tomorrow", "mañana", "demain", "morgen", "明天", "明日"), habit cues, times of day, am/pm and clock suffixes, whether a
capital letter marks a name (not in German) - are data in `services/zoe-data/lexicons_data/<lang>.json` under `recall_gate` (en, es, fr,
de, zh, ja; es / fr / de / zh / ja carry `reviewed: false` - no native speaker has read them). A text with no language evidence
(three words) is read with every Latin-script language's data unioned; a language with no entry gets no cue words and only
rarity-weighted key overlap - it fails **safe** (fewer additions). A word list can only ADD a trigger; it never authorises (authority
is the row's stored class). Honest limit: restraint's sensitivity classes (`restraint.py`) are still English regexes, so a pool row in
another language is classified by structure only (`memory_type`, `affect`, `entity_type`); that is restraint's gap (blueprint 2.9 table), not widened here.

## The shadow-identity proof

`test_shadow_packet_is_byte_identical_to_off` runs the real `memories.memory_for_prompt` over a **seed set of 14 cases** (a plain ask, a
named row the search found, a pool-only row, a conflict pair, an unverified voice, a pasted instruction row, near-duplicates, a
superseded row, an emotional ask with `ZOE_EMOTIONAL_RECALL_ENABLED`, evidence + quotes with `ZOE_RECALL_EVIDENCE`, continuity mode,
`limit=3`, an empty store, an empty message) three ways - `ZOE_RECALL_GATE=off`, `=shadow`, and **unset** (the default) - and asserts the whole
result dict equal and the packet bytes equal, plus exactly one well-formed `RECALL_GATE` line (none on continuity / empty message).
`test_shadow_packet_survives_a_gate_that_raises` forces `evaluate_packet` to raise and asserts both `shadow` and `enforce` still return
the floor's result. `test_shadow_hop_is_identical_to_off_and_logs` does the same for the hop over four asks. Red-when-removed: making
shadow return the rebuilt packet turns `[plain]` red; removing the `packet_surface` call turns it red (the wiring is pinned too).

## Break-the-fix (each rule neutered once, the named test goes red)

| Neutered | Red test |
|---|---|
| sticky window (hold removed) | `test_sticky_holds_a_triggered_row_for_n_further_turns` |
| cooldown check removed | `test_cooldown_blocks_a_served_row_that_is_not_retriggered` |
| delay check removed | `test_delay_keeps_filler_out_of_the_first_turns_but_not_a_triggered_row` |
| token budget removed | `test_budget_caps_the_selection_and_keeps_the_best_scoring_rows` |
| admission order replaced by score order | `test_a_new_row_appends_and_the_prefix_does_not_move` |
| authority bonus AND tie-break removed | `test_authority_orders_ties_verbatim_then_derived_then_inferred` |
| inferred wall (key overlap alone) removed | `test_an_inferred_row_is_not_triggered_by_key_overlap_alone_but_is_by_similarity_or_a_name` |
| inferred rows allowed to stick (both sides) | `test_an_inferred_row_is_never_held_by_sticky` |
| off-record check removed | `test_an_off_record_turn_abstains_and_arms_nothing` |
| guest check removed | `test_a_guest_gets_no_gate_and_no_pool_read` |
| restraint check on the pool removed | `test_the_gate_never_adds_a_sensitive_pool_row_restraint_would_withhold` |
| per-user state key dropped | `test_state_never_crosses_users` |
| similarity floor removed | `test_the_floors_search_returns_its_k_nearest_whatever_they_are_the_gate_keeps_the_near_ones` |
| abstain removed | `test_enforce_abstains_when_nothing_is_relevant_unless_told_to_keep_the_floor` |
| write-time stamp not called | `test_a_row_written_through_the_service_carries_its_triggers` |
| stamp trusts a stale hash | `test_stamp_is_read_back_and_invalidated_by_a_text_change` |
| CJK keys removed | the ja / zh cases of `test_a_named_entity_triggers_in_every_language_...` |
| shadow == enforce | `test_shadow_packet_is_byte_identical_to_off[...]` |
| packet / hop surface not wired | the same identity test / `test_shadow_hop_is_identical_to_off_and_logs` |

(Where two independent defences guard one rule - the authority tie-break and its bonus, the two sides of "inferred never sticky" - neutering
one side alone survives by design; both together go red.)

## The offline replay - what enforce WOULD have added and dropped

`scripts/perf/recall_gate_replay.py` (hermetic: a throwaway MemPalace, real ONNX embeddings, the real `memory_for_prompt` /
`personalisation_hop.build`, restraint's mutes stubbed, `ZOE_RESTRAINT=enforce` and `ZOE_RECALL_EVIDENCE=1` as the live floor runs them,
no brain, no network, no live service or database, `demo_bar_*` users only) plays each cell's ask twice on the same rows - once in
`shadow` (the floor's packet + the gate's `RECALL_GATE` line) and once in `enforce` - and checks the cell's needle (and anti-needle)
against both packets. The asks and needles are imported from `samantha_bar.py`, `samantha_day_sim.py` and `samantha_person.py`. The
rows are **fixtures** (what the writers store for each seed turn), not the live store: **no live zoe-data ran the gate** (it is not
deployed until this merges), so there are no shadow lines from live bar runs yet; this table is the substitute and the enforce
decision still needs item 1 below. Default configuration:

| cell | what the ask is | surface | floor rows -> gate rows (per ask) | would add | would drop | needle in the packet, floor / enforce | verdict |
|---|---|---|---|---|---|---|---|
| S1 | same-day recall (1 ask) | packet | 1->1 | - | - | yes / yes | match |
| S2 | changed fact: the newer wins (1 ask) | packet | 1->1 | - | - | yes / yes | match |
| S3 | decline when nothing was said (1 ask) | packet | 0->0 | - | - | yes / yes | match |
| S4 | emotional thread (continuity block) (1 ask) | continuity | 1->- | - | - | yes / yes | n/a (gate abstains) |
| S6 | user isolation (B asks, A's rows exist) (1 ask) | packet | 0->0 | - | - | yes / yes | match |
| S7 | keep the richer fact (1 ask) | packet | 2->2 | - | - | yes / yes | match |
| S8 | recall after filler (2 asks) | packet | 11->1 ; 12->2 | - | 10/10 rows | yes / yes | match |
| D-mum | day-sim: how is my mum (correction) (1 ask) | packet | 6->1 | - | 5 rows | yes / yes | match |
| D-quote | day-sim: the Kestrel project (1 ask) | packet | 5->1 | - | 4 rows | yes / yes | match |
| D-race | day-sim: still doing the half-marathon (1 ask) | packet | 5->1 | - | 4 rows | yes / yes | match |
| D-time | day-sim: the dentist on Friday (1 ask) | packet | 8->1 | - | 7 rows | yes / yes | match |
| D-S9a | day-sim S9a hop: sleep tips, night shift (1 ask) | hop | 1->1 | - | - | yes / yes | match |
| D-S9b | day-sim S9b hop: what to wear, 6am walker (1 ask) | hop | 2->2 | - | - | yes / yes | match |
| P2.b | person P2.b hop: diet asks (5 asks) | hop | 1->1 ; 1->1 ; 1->1 ; 1->1 ; 1->1 | - | - | yes / yes | match |
| P2.a | person P2.a task turns (no leak of the dentist worry) (4 asks) | packet | 2->0 ; 2->0 ; 2->2 ; 2->0 | - | 2/2/0/2 rows | yes / yes | match |
| X2 | crowded world: the answer row is outside the floor's ranked 12 (2 asks) | packet | 12->1 ; 12->2 | leads the Kestrel billing migration,; walks their kelpie Juniper along | 12/12 rows | NO / yes | ENFORCE BETTER |
| H1 | hop: a routine in words the hop's list does not know (1 ask) | hop | 0->1 | goes sculling on Lake Pedder | - | NO / yes | ENFORCE BETTER |
| X1 | crowded world: 12 hobby rows + the week (3 asks) | packet | 10->1 ; 10->1 ; 9->3 | - | 9/9/6 rows | yes / yes | match |

Cell names: `S1`-`S8` are the Samantha bar's scenarios (their asks and needles); `D-*` are day-sim asks over the week's rows (`D-S9a` / `D-S9b` = day-sim S9a / S9b); `P2.a` / `P2.b` the person bench's task and diet asks; `X1` / `X2` / `H1` are synthetic crowded-world and word-list-miss probes added here (the cases the small bar worlds cannot show).

Totals over 29 asks: the floor served 121 rows, the gate would serve 30 (enforce packets 31 rows);
4 rows would be added, 94 dropped; verdicts {'match': 25, 'n/a (gate abstains)': 1, 'ENFORCE BETTER': 3}. **No cell regresses**: every needle that the floor's
packet carries survives in the gate's, every anti-needle (a superseded home, a stale mum city, the dentist worry on a task turn,
user A's rows for user B) stays out, and the three ENFORCE BETTER cells are the class the gate exists for.

Reading it:

* **S1, S2, S3, S6, S7, S9a, S9b, P2.b** (small worlds, or the hop): the gate agrees with the floor - nothing added, nothing dropped (S9a /
  S9b through the hop's own topic signal; see "what enforce needs" item 4 for what that does and does not prove). S3 declines (0 rows), S6
  serves B nothing of A's.
* **S8, D-mum / quote / race / time, X1**: the floor serves its generic filler (5-12 rows); the gate serves the one or two rows the ask is about and
  drops the rest. The needle survives every time. This is the saving (and the risk) of enforce.
* **P2.a** (task turns): the dentist worry stays out of both (restraint withholds it upstream); the gate also drops the generic
  filler rows on three of the four task turns; on the third it serves two (the delay has passed and the cooldown allows it).
* **X2, H1** (ENFORCE BETTER): the class of the S9b miss. X2: 18 hobby rows + the week, so the floor's ranked twelve do not carry
  the Juniper / Kestrel row and the keyword gate sends no search; the gate's name trigger adds it. H1: an early-morning routine in words the hop's
  list does not know ("sculling") - the hop selects nothing (floor 0 rows), the gate adds the row by the name the owner said.
* **S4** (continuity block), **S5** (the raise), **S23 / S24** (deterministic provenance / "what do you know about me?" lanes), **1r / 7r**
  (the raise selector) and the live-judged wording of **S2** are not packet-or-hop surfaces: the gate abstains on continuity and never sees the others. They stay as they are by
  construction (no code path from them reaches `recall_gate`), and shadow is byte-identical anyway.

Sensitivity (same replay, same fixtures; every needle and anti-needle verdict is identical in all three):

| configuration | floor rows | gate rows | enforce packet rows | would add | would drop |
|---|---|---|---|---|---|
| default (`filler_max=2`, abstain) | 121 | 30 | 31 | 4 | 94 |
| `--filler 0` (pure abstain) | 121 | 26 | 27 | 4 | 98 |
| `--abstain 0` (an empty selection keeps the floor's packet) | 121 | 30 | 37 | 4 | 94 |

Re-run: `python3 scripts/perf/recall_gate_replay.py [--only S1,X2] [--filler N] [--abstain 0|1] [--markdown out.md]` (about a minute; the
JSON lands in `~/.cache/zoe/recall_gate_replay_last.json`).

## What enforce needs (do not flip before these)

1. **Live shadow evidence.** The gate is not in the running service until this PR is merged and deployed; the numbers above are a
   hermetic replay over fixtures, not the live store. Read `RECALL_GATE` lines for at least a few days of real turns
   (`grep RECALL_GATE ~/.zoe-logs/*`): the share of turns with `would_add`, what `would_drop` removes on turns the owner went on to
   answer well, and `skipped=` reasons. A `would_drop` that held the row a reply needed is the regression to look for.
2. **Calibrate the similarity floor on the live embedder.** `SIM_NEAR / SIM_BEST / SIM_MID` (0.69 / 0.80 / 0.90) were measured on
   the replay fixtures with Chroma's default MiniLM (answering row 0.46-0.69, irrelevant 0.70-1.05; one irrelevant row sat at 0.699).
   They belong to the embedder: re-measure from the live distribution (the log can carry it) before the flip.
3. **Decide the two policy knobs from the shadow data**: `filler_max` (2 now; `0` is pure abstain - the replay's needles are identical) and
   `abstain` (an empty selection is an empty packet; `False` keeps the floor's packet when nothing is triggered).
4. **A semantic signal for the S9b class in every language.** The language-independent signals alone reach only 1.2 for "What should I
   wear tomorrow?" against "...every morning at 6am" (a plan cue + a routine row), below the 2.0 threshold; today the row is found by
   the hop's English topic list (the `topic` signal) and the gate agrees with it. Without that list (another language) the gate
   would add nothing there. The blueprint's answer is one embedding per turn, to be measured (2.9): not added here because
   `MemoryService.search` writes access ticks, so a shadow call would not be read-only.
5. **Native review of the `recall_gate` lexicon entries** (es / fr / de / zh / ja are `reviewed: false`; a language is "on" only when its fixtures pass).
6. **The replay gate for the voice path** (`~/.zoe-voice-samples`, serial, brain stopped for the training window rules) on the
   enforce branch - this PR changes nothing the owner hears while the flag is `shadow`.
7. **Pi core path and `recall_memory` tool**: both reach `memory_for_prompt` and are gated by it, but only the Flue seam calls `note_turn`;
   the core client's turns are counted by distinct message (implicit), which is fine for a shadow, not yet proven for enforce.

## Limits and findings (also in `open-problems.md`)

* The packet cite `[mem:<first 8 characters of the id>]` is not unique: ids are `zoe_<user>_<24 hex>`, so the first 8 characters are
  `zoe_<4 characters of the user id>` for every row of a user (seen in the replay: all `[mem:zoe_demo]`). The hop uses the same cite.
  Changing it would change the packet bytes, so it is not touched here; the gate logs the last `_` segment instead.
* `restraint.py`'s sensitivity classifier is English regexes plus structure; the gate inherits that for pool rows in other languages.
* Sticky and cooldown count turns of a session; a restart is a new session (in-memory state by design, like restraint's marks).
  Two identical short messages inside 120 s count as one turn.
* In shadow the gate adds one durable-pool read and one `restraint.list_mutes` read per recall / advice turn, in the background.
* The replay's rows are fixtures (what the writers store for each seed turn, wording modelled on rows quoted in the bar docs);
  the extractor is not run, so it replays the SELECTION, not the whole chain. The bar's judged scenarios (S2, S4's check-in wording, S5,
  S23, S24, 1r / 7r) are not gate surfaces: they take the continuity block or a deterministic lane; they are listed so the table is
  complete, not because the gate touched them.
