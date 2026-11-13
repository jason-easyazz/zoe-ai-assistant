---
type: record
title: Household recall on every turn (ZOE_RECALL_EVERY_TURN)
description: The ambient recall path for household turns no trigger claims - the recall gate's lexical scoring over the member's own durable rows, privacy and forget walls, a 90-token budget, placed after the cached prefix; flag off|shadow|on, default off; the coverage line; offline measurement; owner steps.
tags: [recall, memory, flue, voice-path, samantha, flag]
timestamp: 2026-11-03T00:00:00Z
---

# Household recall on every turn (`ZOE_RECALL_EVERY_TURN`)

Code: `services/zoe-data/recall_every_turn.py`, wired in `zoe_flue_client._run_flue_brain_streaming_turn`. Tests: `services/zoe-data/tests/test_recall_every_turn.py` (ci_safe). Offline measurement: `scripts/perf/recall_every_turn_replay.py`.

## Why

[recall-not-firing-2026-10-29.md](recall-not-firing-2026-10-29.md): both injectors on the Flue lane are trigger-gated (the recall floor on a recall-shaped question, continuity on a mood statement) and about 94% of household brain turns match neither, so the brain answered from the turn alone. Live 10-30 onwards: `SEAM_CONTINUITY` matched 0 of 91, the recall gate's shadow served 0, the brain called `recall_memory` 0 times in 51 turns.

## Design

* **Who.** Household traffic (`traffic_class`), an identified member, on a SPOKEN turn a speaker the gate CONFIRMED (no verdict is not a yes: voice is a password, strict until live), not an off-record turn. Guests, unidentified voices, probes and CI: no read at all. `ZOE_RECALL_EVERY_TURN_HARNESS=1` also admits harness ids (`demo_*`) for a measured bar run only.
* **Which rows.** `MemoryService.load_durable_for_hop`: the member's own approved durable rows at authority owner-derived or above (inferred rows never; superseded / pending / disputed never; another member's rows never). Then removed: every private class (`provenance_answers.sensitive_classes`), every off-the-wall restraint class (`panel_review_cards.OFF_THE_WALL`: health, money, grief, family_conflict, affect), every muted topic, every forgotten or tombstoned name (fails closed). A row about another person enters only when the owner's own words pull it (restraint's packet rule). A direct question about anything withheld still goes to the recall floor.
* **Relevance gate.** The recall gate's own scoring (`recall_gate.derive` / `row_feat` / `_score`, threshold 2.0): names, keys weighted by rarity in the member's own rows, clock times, routine x plan. No model call, no embedding. A turn the rows do not touch gets nothing.
* **Budget.** 3 rows, 90 estimated row tokens (a row that alone exceeds it never enters; the delimiters add a fixed ~35), each row cut at 160 characters. Basis: household panel turns re-prefill a p50 of 222 tokens (p90 1,258) after a prefix of ~4,000 cached tokens (system ~2,940, tools ~580, history ~120; `FLUE_PROMPT_CACHE` / `FLUE_CONTEXT_BUDGET`, 24 household panel turns, 2026-11-03), so a turn that passes costs at most ~125 extra prefill tokens, and the common turn costs none.
* **Placement.** After the user's words, inside the latest user message, where the hop and risk blocks ride - behind the system prompt, tools and history. `prefix_key` and the leading envelope lines are byte-identical with and without it (pinned by a test), so the prefix keeper and the wake warm are unaffected. The `[MEMORY CONTEXT` delimiters let the sidecar elide it from older turns, like every other block.
* **One memory block per turn.** It is injected only when no recall, verify, continuity, hop, offer, risk, raise or brief block fired.
* **Cost on the voice path.** The member's pool is featurised once into a cached index (TTL 120 s, refreshed in the background, built off the event loop; the wake warm prewarms the likely speaker). A turn never waits for a cold build: with no index yet it carries no block (`skipped=warming`) and the build finishes for the next turn. Per turn: set intersections plus one by-id re-read of at most nine picks (`MemoryService.recheck_durable`), so a correction or a forget is honoured on the very next turn. The cache can omit a just-written row until its refresh; it can never resurrect a retired one.

## Similar rows (`ZOE_RECALL_SIMILAR` = off | shadow | on, default off)

The lexical gate needs the turn to share a name, a rare word or a clock time with the row. Oblique speech shares none, so the row it needs is never met: in the synthetic lab (`labs/recall-match-lab`) the gate found the needed fact on 18 of 20 direct turns and 2 of 20 oblique ones. With this flag on, a turn whose words trigger fewer than `MAX_ROWS` rows is topped up with the member's nearest durable rows by embedding similarity.

* **Same rows, same walls.** The candidates are the cached durable pool (the member's own approved, owner-stated rows); each pick goes through the same by-id recheck, private-class and restraint walls, mutes, forgotten check and token budget as a lexical pick. Lexical rows come first and keep the budget.
* **Same space as the store.** Rows and the turn are embedded with the palace's own MiniLM session (`memory_service._drawers_embedding_function`). Row vectors are computed when the index is built (off the event loop) and reused across refreshes; a turn costs one embed (p50 ~4.5 ms, p95 ~16 ms on the Orin CPU, 2026-11-13; dropped after `EMBED_TIMEOUT_S` = 200 ms) and one matrix product. No HNSW query. Two speed rules: (1) the embed pads the batch to its longest text, not to Chroma's fixed 256 tokens (`_embed_dynamic`: same model session and tokenizer file, attention-masked pooling, cosine 1.00000 to the fixed-pad vectors; the fixed pad made a 10-word turn cost 41-165 ms); (2) `recall_every_turn.begin` is called where the turn's words are final (top of `_run_flue_brain_streaming_turn`) and starts the embed beside the other pre-brain work, so `select` only awaits what is left. Measured and rejected: an int8-quantised model (cosine 0.86 to the fp32 vectors, below the 0.99 bar) and a lexical-overlap re-rank that drops low-margin similar rows sharing no key with the turn (it lost oblique hits, 9 to 4-7 of 20, without cutting wrong-row-only turns, 5 to 5-6 of 20).
* **Floor.** `ZOE_RECALL_SIMILAR_FLOOR` (cosine, default 0.30). Synthetic lab with MiniLM, 2026-11-12, needed fact in the top three / a row offered on an irrelevant turn: 0.20 = 15 of 20 oblique, 13 of 20 irrelevant; 0.30 = 9 of 20, 2 of 20; 0.40 = 2 of 20, 2 of 20 (direct turns 19 or 20 of 20 throughout). The default is the conservative end. Synthetic, so [Experimental]: the Samantha bar and the shadow line decide the live value.
* **Honest wording.** A block that carries a similar row opens with "MAY relate ... ignore any that do not", not "bear on".
* **Shadow.** Computes and logs (`sim`, `sim_top`), injects nothing: the live distribution on household turns before anything rides.
* **Fails to nothing.** No vectors, a slow or failed embed, or any error: the turn carries exactly what the lexical gate alone would.

## Why not just `ZOE_RECALL_GATE`

This is the recall gate's scoring on a new surface. The gate itself cannot serve every turn: it runs only inside `/for-prompt` and the hop, which are reached only when a trigger already claimed the turn (hence 0 served live). Its packet path also embeds and searches and re-reads the whole pool per call (100 to 250 ms at 400 to 1,000 rows), and its sticky / cooldown / delay / filler state is tuned for a packet that is being sent anyway. Its own shadow-to-enforce decision is unchanged and separate.

## The coverage line

In shadow and on, every eligible household brain turn logs one line (ids, counts, sizes; never text):

`RECALL_EVERY_TURN user=<id> mode=<shadow|on> rows=<n> ids=<8-char ids> tokens=<n> pool=<n> ms=<gate ms> claimed=<recall,hop,...|-> injected=<0|1> skipped=<reason|-> similar=<off|shadow|on> sim=<n> sim_in=<n> sim_top=<cosine>`

`sim` = similar rows at or above the floor this turn (before the walls; counted in shadow too); `sim_in` = rows in the block that came from similarity; `sim_top` = the best similar row's cosine.

`rows` = stored facts the turn's words touch (relevant memory existed); `claimed` = which other block fired; `injected` = this block rode. `rows>0 claimed=-` is the gap this closes. Whether the reply then used the memory is not visible to zoe-data.

## Measured (offline, hermetic, 2026-11-03)

`recall_every_turn_replay.py`: temp MemPalace, real `MemoryService`, the bar's fixture rows, synthetic ids.

* Household-shaped turns the floor does not claim: 6 of 6 that name a stored row got it (dog, project, race, sister by name and by city, father); 0 of 12 controls got anything (timer, weather, lights, greeting, music, trivia, shopping list, thanks, joke, pasta timer). "Remind me to ring <mum>" got nothing by design: the mum row carries a health fact.
* Bar asks: on asks the floor claims, nothing is added (one block per turn). Unclaimed bar asks the rows touch gain the answer row (X2 crowded-world Juniper and Kestrel asks, the S8 father ask).
* Per-turn wall time (cached index, Orin CPU), p50 / p95: 100 rows 2.9 / 3.5 ms on a match, 0.4 ms no match; 400 rows 3.1 / 3.4 ms (0.5 no match); 1,000 rows 3.6 / 4.8 ms (0.9 no match). Cold index build: 26 ms (100 rows), 98 ms (400), 262 ms (1,000), never on the hot path.

## Owner steps

1. **Shadow** (no reply changes): set `ZOE_RECALL_EVERY_TURN=shadow` in the zoe-data environment, restart. Watch a few days of `grep RECALL_EVERY_TURN ~/.zoe-logs/zoe-data.app.log`: the share of household turns with `rows>0 claimed=-` (the gap), `ms` (should stay under 10 at p95), `skipped=timeout|error` (should be rare), and `ids` that look wrong for the turn (check by id, never by printing text into a transcript).
2. **Night bar run, off vs on** (prepared, not run; household-quiet hours only, the night gate first):
   ```
   ~/.zoe/agent-tools/night_busy.sh voice && exit 0        # exit 0 = paused
   cd ~/assistant
   flock /tmp/zoe-voice-harness.lock python3 scripts/perf/samantha_bar.py --axis d,e --samples 3 --results ~/.cache/zoe/bar_recall_off.json
   # drop-in ~/.config/systemd/user/zoe-data.service.d/90-recall-every-turn.conf:
   #   [Service]
   #   Environment=ZOE_RECALL_EVERY_TURN=on
   #   Environment=ZOE_RECALL_EVERY_TURN_HARNESS=1
   systemctl --user daemon-reload && systemctl --user restart zoe-data   # then poll /health until ok
   flock /tmp/zoe-voice-harness.lock python3 scripts/perf/samantha_bar.py --axis d,e --samples 3 --results ~/.cache/zoe/bar_recall_on.json
   python3 scripts/maintenance/voice_regression_probe.py            # cold_first_turn cache_frac must not drop
   # remove the HARNESS line (keep shadow or on as decided), daemon-reload, restart
   ```
   KEEP if no recall / abstention cell regresses and the probe's `cold_first_turn.cache_frac` holds (`first_cache_n` is unchanged by construction; `cache_frac` falls only by the block's own tokens on a turn it rides).
3. **On**: `ZOE_RECALL_EVERY_TURN=on`. Watch `injected=1` turns for a reply that recites a stored fact out of place (the block says use it only where it helps), `FLUE_PROMPT_CACHE first_prompt_n` on those turns (+~125 at most), and `PANEL_KV_MISS` (should not rise). Roll back by setting `off`.

## Not in this change

* The per-member "now card" (speaker, open threads, next date, one stated fact) is its own flag: [now-card.md](now-card.md).
* The voice history window. Widened separately (2026-11-05, lane E3): the live history is the sidecar session window, whose floor is now 8 earlier turns (`ZOE_BRAIN_KEEP_TURNS`, default 8, rollback 3; see `services/zoe-data/AGENTS.md`). `routers/voice_tts.py`'s `limit=3` only feeds non-Flue lanes and was left alone.
