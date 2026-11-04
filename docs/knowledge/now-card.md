---
type: record
title: The now card (ZOE_MEMORY_NOW_CARD)
description: A nightly, per-member, prefix-stable card (speaker, up to two open threads in their own words, the next date within 7 days, one thing they told Zoe) read by key on a household turn and placed in the SYSTEM prompt after the user-model card; flag off|shadow|on, default off; sources, content rules, placement evidence, log lines, the shadow-to-on plan and owner steps.
tags: [memory, now-card, flue, voice-path, samantha, flag, night-mind]
timestamp: 2026-11-04T00:00:00Z
---

# The now card (`ZOE_MEMORY_NOW_CARD`)

Samantha Initiative lane C, E1 ([lane C](../research/samantha-initiative-2026-11-03/lane-C-memory.md) section 3.1). Code: `services/zoe-data/now_card.py` (compose, store, serve, forget), the seam in `zoe_flue_client.py` (` zoe-nowcard:` envelope line), the sidecar half `labs/flue-zoe-brain-2x/src/now-card.ts`, the table `now_cards` (alembic `0066`). Tests: `services/zoe-data/tests/test_now_card.py` (ci_safe, includes the real sidecar when its `node_modules` exist), `labs/flue-zoe-brain-2x/test/now_card.test.ts`, the warm/turn lock in `tests/test_first_turn_prefix_invariant.py`.

## Why

Push beats pull on this brain: the 4B searched memory first in 36 of 100 bake-off turns, and 83% of real replies used no memory. [Recall on every turn](recall-every-turn.md) adds stored facts the turn's WORDS touch; the now card adds what is live for the member TODAY whatever they say, read by key, no search and no model.

## What is on it

At most 180 estimated tokens, header included (Latin text measures ~90-155 with the per-line cuts; the budget binds on CJK text). Nothing is padded; a card with nothing but the speaker is no card.

| Line | Source | Rule |
|---|---|---|
| `Speaking with: <name>` | `users.name` | always, when any other line exists |
| up to 2 × `<day> they said “<quote>”` | the night mind's CURRENT observations (`night_mind.snapshot`: significant threads, forget re-checked) | thread `open`/`changed`, last spoken of within 14 days, not `leave`, not a negative feeling, observation kind not `health`/`feeling`; the night's `raise` thread first, then salience; exact quotes only (held / replaced quotes never); only while the night mind's readers are on (`ZOE_NIGHT_MIND` serve/enforce) |
| `Coming up: <name>'s birthday, <Sat 8 Nov>` | `person_important_dates` (any type) + legacy `people.birthday` (#2262's two sources) | the soonest date 0-7 days ahead of the build day; the member's own contacts; absolute date (the card serves on its build day); a private / off-the-wall / muted label never |
| `They told you: <fact>` | `MemoryService.load_durable_for_hop` | the newest approved row the member STATED or CONFIRMED (`user_stated` / `user_confirmed`); a model's paraphrase (`user_stated_derived`), `inferred`, pending, superseded never; an unconfirmed special-category trait never |

Every line is dropped when it is a private class (`provenance_answers.sensitive_classes`), an off-the-wall restraint class (`panel_review_cards.OFF_THE_WALL`: health, money, grief, family_conflict, affect), a muted topic, or names something forgotten (tombstones + the durable ledger, strict: an outage drops the line). Affect stays on for everyone in memory (owner decision); it is simply never on this unprompted surface, for adults and children alike.

**Not a source: `open_loops`.** Their text is the 4B's paraphrase of a turn with no approval step, i.e. inferred-unapproved content; the night mind's threads cover the same ground with the member's exact words.

**Children (D7).** A member who is not positively an adult (`manner_block.is_adult`: a minor `member_modes` row, a child role, an unknown role or a failed lookup) gets no night-mind thread (no night-window model output), only speaker, date and their own stated words.

## When and who

* **Built at night, never by day.** `memory_digest.run_dreaming_cycle` (after the user-model card) and again by `zoe-night-mind.py` in enforce after tonight's pass (`NOW_CARD_REBUILD` on stderr), so tonight's threads reach tomorrow's card. Deterministic: the same stores give the same bytes (pinned). One row per member, `now_cards`. Yesterday's card serves until 05:00 local; after that a missing night means no card.
* **Served by key per turn** (`now_card.block_for`): household traffic, an identified member, and on a spoken turn a CONFIRMED voice (voice is a password: no verdict is not a yes; an unverified voice gets nothing and nothing is read). Guests, `voice-daemon`, probes, CI: nothing. `ZOE_MEMORY_NOW_CARD_HARNESS=1` also admits the bar's `demo_*` ids and lets them build lazily (seeded by day), for a measured bar run only.
* **Serve-time checks, every turn:** a thread line needs its observation still current (the night mind's snapshot cache, invalidated by every erase); the fact line is re-read by id (`recheck_durable`); mutes and the forget lookups per line. A dropped line re-renders the rest (a new version: one prompt-cache miss), the whole check is bounded by 0.4 s (a timeout is no card).
* **The wake warm** reads the card for the panel's likely speaker (`warm_speaker`: recorded only for a SERVER-VERIFIED member) and sends it in a replay-marked throwaway turn whose reply is discarded, exactly as it already sends that member's user-model card; nothing reaches a person. If the next turn turns out unverified it carries no card and pays one re-prefill from the card on.
* **The harness exception** (`ZOE_MEMORY_NOW_CARD_HARNESS`, synthetic `demo_*` ids only): a bar run seeds its ids by day, so their card is composed on their first turn (deterministic, no model - the owner policy bans daytime MODEL use, not code). Household cards are composed only at night.
* **Forget:** `MemoryService.delete_user` -> `now_card.forget_user`; `memory_forget_entity` -> `now_card.erase_entity` (deletes a card naming the entity; a store failure is NOT confirmed as forgotten); `forget_reach.py` lists `now_cards` as reached by both.

## Placement: in the system prompt, after the user-model card (decided with evidence)

The sidecar's system prompt is NOT shared across members: since 2026-10-27 it is soul + doctrines (shared) + manner + **user-model card (per member)**, then the tool block, then history and the turn ([brain-prefix-cache](brain-prefix-cache-2026-10-12.md) addendum). The card goes right after the user-model card, so every byte up to the end of today's prompt is unchanged and the card sits in the member's own cached region.

Live numbers (`~/.zoe-logs/zoe-data.app.log`, 35 household panel brain turns since 2026-11-01, counts only): `first_prompt_n` p50 214 / p90 1,012, `first_cache_n` p50 3,323, `cache_frac` p50 0.946; `FLUE_CONTEXT_BUDGET` p50 system 2,939 / tools 582 / history 102 / tail 143 (chars/4). Modelled for a 150-token card:

| Placement | Same-member turn | When the card changes |
|---|---|---|
| **system prompt, after the user-model card (chosen)** | cached: `cache_frac` p50 0.946 -> 0.948, +0 prefill | once per nightly rebuild / member switch / verdict flip: re-prefill from the card on (~830 + card tokens, past the 512-position SWA window) - the same event the user-model card already causes, absorbed by the wake warm, which sends the same card |
| user message (like recall-every-turn) | `cache_frac` p50 0.912, +150 prefill tokens EVERY turn (~0.23 s at 650 tok/s) | nothing |

The card changes at most nightly, so the system prompt wins. The wake warm (`_kv_warm_outbound`) renders the card fresh for the likely speaker (not the turn memo: it changes nightly), and `prefix_key` includes the card only when one is sent (a turn without a card keeps its old key). Pinned by `tests/test_first_turn_prefix_invariant.py` (warm == turn with the card, a warm without it goes red). A verdict flip (an unverified turn by the same member: 1 of 60 turns since 2026-11-01) drops the card for that turn and costs that turn a re-prefill from the card on.

**Deploy order:** the sidecar must parse ` zoe-nowcard:` BEFORE zoe-data sends it (an older sidecar would leave the line ahead of the identity line and bind the fallback member). Off and shadow send nothing; deploy rebuilds the sidecar on merge (`deploy.yml`), so flip to `on` only after a deploy that included `labs/flue-zoe-brain-2x/src/now-card.ts`.

## Log lines (ids, counts, sizes; never text)

* `NOW_CARD_BUILD user=<id> status=<ok|empty|error> tokens=<n> lines=<n> threads=<n> date=<0|1> fact=<0|1> version=<hash> errors=<source:Exc,...|->`
* `NOW_CARD user=<id> mode=<shadow|on> served=<0|1> tokens=<n> lines=<n> ids=<u:user,n:<obs8>,p:<person8>,m:<mem8>> version=<hash> ms=<n> skipped=<guest|unverified|not_household|no_card|stale|empty|timeout|error|->`

## Owner steps

1. **Merge and deploy** (migration `0066` runs with the deploy; the sidecar is rebuilt). Voice path: lands through the replay gate (`zoe_flue_client.py`, the sidecar).
2. **Shadow** (no prompt changes; the night builds cards): `ZOE_MEMORY_NOW_CARD=shadow` in `services/zoe-data/.env` (the night window and dreaming read the same file), restart zoe-data, poll `/health`. Next morning: `grep NOW_CARD_BUILD ~/.zoe-logs/zoe-data.app.log ~/.zoe/night-window/logs/*.log` (one `ok` line per member with a card; `errors=-`), then a few days of `grep "NOW_CARD " ~/.zoe-logs/zoe-data.app.log`: `skipped=` mix (expect mostly `-` on member turns, `unverified` rare), `tokens` <= 180, `ms` well under 400. Read a member's own card only privately (it holds their words), never into a transcript.
3. **Bar run, off vs on** (prepared, not run; household-quiet hours only, the night gate first):
   ```
   ~/.zoe/agent-tools/night_busy.sh voice && exit 0        # exit 0 = paused
   cd ~/assistant
   flock /tmp/zoe-voice-harness.lock python3 scripts/perf/samantha_bar.py --axis d,e --samples 3 --results ~/.cache/zoe/bar_nowcard_off.json
   # drop-in ~/.config/systemd/user/zoe-data.service.d/90-now-card.conf:
   #   [Service]
   #   Environment=ZOE_MEMORY_NOW_CARD=on
   #   Environment=ZOE_MEMORY_NOW_CARD_HARNESS=1
   systemctl --user daemon-reload && systemctl --user restart zoe-data   # then poll /health until ok
   flock /tmp/zoe-voice-harness.lock python3 scripts/perf/samantha_bar.py --axis d,e --samples 3 --results ~/.cache/zoe/bar_nowcard_on.json
   flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py   # said-vs-did, per-stage speed, cold_first_turn.cache_frac
   # remove the drop-in (keep the .env value as decided), daemon-reload, restart, poll /health
   ```
   The bar's ids build their card lazily on their first turn (the HARNESS switch), so the card carries speaker + a stated fact there (no night threads or dates for synthetic ids). KEEP if no recall / abstention / restraint cell regresses, no reply recites a card line on an unrelated ask (lane C's M3 <= 5%), and the probe's `cold_first_turn.cache_frac` holds.
4. **On:** `ZOE_MEMORY_NOW_CARD=on`, restart, poll `/health`. Watch `FLUE_PROMPT_CACHE` on member turns (`first_cache_n` up by ~the card, `first_prompt_n` unchanged), `PANEL_KV_MISS` (must not rise), `FLUE_PREFIX_KEY` warm vs turn keys (equal on the first turn after a wake), and replies that recite a card line out of place. Rollback: `off`, restart.

## Not in this change

* Liveness of a date line between nights (a contact deleted in the UI stays until the next build; a forgotten one is dropped at once by the forget checks).
* Raise permission ("may raise X once"): the morning brief owns the raise and its back-off; the card only lists the thread, with a header that says not to recite it.
* The coverage instrument (relevant memory existed / fired / used) for the card's lines: lane C M1/M3, a separate follow-up.
