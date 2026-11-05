---
type: Reference
title: Samantha warmth - how Zoe meets a feeling, a self-disclosure and the household's locale
description: The warmth doctrine (every member), the reconciled manner block (adults), the household conventions line (ZOE_LOCALE_HINT, default on), the emotional-turn guard (ZOE_EMOTIONAL_TURN_GUARD, default off) and the four warmth probes that measure them.
tags: [samantha, warmth, doctrine, manner-block, locale, emotional-turn, flue, zoe-data, voice-path]
timestamp: 2026-11-06T00:00:00Z
---

# Samantha warmth (2026-11-06)

**The miss** (QA rounds 2 and 3, demo household, 2026-11-05/06): emotional turns got one generic line and no follow-up ("kinda lonely"
got "I'm here to listen ..." plus stale weather); a new member naming a job, a partner and a child got "Thanks for sharing that with me
... busy household" and no question back; intro replies parroted the user's words ("So you've got A, B and C with you now"); recipes
for an Australian household came in Fahrenheit with US spelling.

**Why.** Two layers said opposite things. The soul says "acknowledge first, ask a real question"; the live manner block (adults,
`ZOE_MANNER_BLOCK=on`) said "if they are upset, one short clause, then carry on ... otherwise end on a statement" and, being later in
the prompt, won. Nothing told the brain the household's units or spelling. Nothing stopped an injected contact offer or a stale info
paragraph from riding on a feeling.

## What changed

| piece | where | reaches | default |
|---|---|---|---|
| Warmth doctrine | `labs/flue-zoe-brain-2x/src/doctrines.ts` `WARMTH_DOCTRINE` (overlay slot `warmth`, right after voice delivery) | every member, children included | compiled in (the cached prefix changes ONCE; ~200 tokens) |
| Manner block reconciled | `services/zoe-data/lexicons_data/{en,es}.json` `manner.block` (+ `COMPILED_MANNER_EN` in `src/harness.ts`) | adults (`manner_block.eligible`) | live wherever `ZOE_MANNER_BLOCK=on` |
| Household conventions line | `household_locale.py` + `locale_data/household_locale.json`, placed by `zoe_flue_client._with_household_locale` | every turn (in the `[Today — household context]` block) | `ZOE_LOCALE_HINT` on |
| Emotional-turn guard | `emotional_turn_guard.py`, wired in `zoe_flue_client.run_flue_brain_streaming` + `latent_intent_detector` | brain turns | `ZOE_EMOTIONAL_TURN_GUARD=off` |
| Warmth probes | `warmth_probes.py` (offline instrument + library) | measurement only | - |

- **Warmth doctrine** - feelings: name the specific thing in your own words (never echo), ONE gentle open question, nothing unrelated
  added, follow up on an earlier hard thing; not a therapist, never diagnose; possibly unsafe -> someone they trust now (a trusted adult,
  for a child) or emergency help; simple gentle words with a child (`docs/governance/emotional-safety-note.md` 4, 7). Self-disclosure:
  warm to one specific detail, ONE natural follow-up, no list-back, no stock lines. Phrasing only, names no tool, sits ahead of every
  tool doctrine. Golden hash re-pinned in `test/persona_layer.test.ts` and `test/harness_overlay.test.ts`.
- **Manner block** - the "one short clause, then carry on / end on a statement" sentence became "if they share a feeling or something
  about their life, react to one specific detail in a short clause, then ask one gentle, open question about it; otherwise at most one
  question". Everything else (no "It sounds like", no restating, no flattery) is unchanged. Shipped seed for the self-improver (#2304),
  which reads this lexicon text; the improver itself is untouched.
- **Conventions line** - the country is `ZOE_HOUSEHOLD_COUNTRY` (code or name), else `ZOE_LOCATION_COUNTRY`, else the country of an
  EXPLICITLY set `ZOE_TIMEZONE` (data `zones`), else nothing. `explicit_country()` is also the distress hand-off's country (which never
  falls back to the timezone). Data, not code: a new country is a JSON entry; `ZOE_LOCALE_FILE` overrides. Every field is kept free of
  the sidecar's tool-group trigger words (the line rides in the user message; a test pins it). About 60 tokens per turn, after the
  cached prefix, elided from history like the identity block.
- **Emotional-turn guard** - an emotional turn = the distress lexicon scores it, or `memory_gate.extract_affect` finds a first-person
  feeling, or an `emotional_turn.cues` phrase (en lexicon). In `on`: no contact-offer directive and no first-turn day brief on that turn
  (`SEAM_OFFER ... deferred=1 reason=emotional`; the offer ager skips it), and a reply sentence AFTER the first that carries unrequested
  weather / a contact offer / a "by the way" aside is dropped (asked-for info is kept; the first sentence streams with no delay; a
  non-emotional turn is never held). `[RAISE]` is not held (it is the person's own thread). `shadow`: byte-identical plus a label-only
  `EMOTIONAL_TURN_GUARD mode=shadow ...` line.

## Measured (offline, the QA transcripts, counts only)

`python services/zoe-data/warmth_probes.py <transcript.jsonl> --country AU [--after-guard]`, both QA rounds pooled:

| probe | before (replies as served) | after the guard (`--after-guard`) |
|---|---|---|
| `follow_up` (a specific question on an emotional / self-disclosing turn) | 2 / 7 | 2 / 7 (the doctrine's job; needs the live brain) |
| `no_extras` (no unrequested weather / offer / aside) | 2 / 3 | 3 / 3 |
| `locale` (no off-locale words for AU) | 87 / 91 | 87 / 91 (the conventions line's job; needs the live brain) |
| `no_parrot` (no handed-back sentence, no stock line) | 3 / 7 | 3 / 7 (the doctrine's job; needs the live brain) |

The doctrine, the manner text and the conventions line change what the brain WRITES, so their "after" is a live run (owner steps).

## Owner steps

1. Shadow the guard first (no behaviour change): add `ZOE_EMOTIONAL_TURN_GUARD=shadow` to `services/zoe-data/.env`, restart zoe-data,
   then read `grep EMOTIONAL_TURN_GUARD ~/.zoe-logs/*.log` for a few days; flip to `on` after the voice replay gate is green.
2. Nothing to build by hand: the deploy job rebuilds and restarts the brain sidecar when `labs/flue-zoe-brain-2x/` changed (the
   doctrine), and zoe-data carries the manner text and the conventions line. Check `/health` on both after the landing.
3. Live measurement (night window rules apply): replay the QA script, then score its transcript with the probes above; the manner A/B
   (`scripts/perf/manner_block_ab.py`) re-measures the edited block.
4. Kill switches: `ZOE_LOCALE_HINT=0` (the line), `ZOE_EMOTIONAL_TURN_GUARD=off` (the guard). The doctrine and the manner text roll back
   by revert (or an overlay `doctrines/warmth.md` / `manner.md` under `ZOE_BRAIN_HARNESS_DIR`).

## Limits

- The cues and info words are English; another language gets only the distress lexicon (en/es) and nothing from `extract_affect`.
- A self-disclosure that is not a feeling (a job, a partner) still gets the contact offer: the guard is about feelings; the doctrine asks
  for the follow-up question there.
- Tests: `services/zoe-data/tests/test_emotional_turn_guard.py`, `test_household_locale.py`, `test_warmth_probes.py`,
  `test_manner_block.py` (ci_safe); `labs/flue-zoe-brain-2x/test/warmth_doctrine.test.ts`.
