---
type: Reference
title: The member register (ZOE_MEMBER_REGISTER, flag-dark)
description: Identity as four stored layers and a per-member register - how Zoe speaks with THIS person (length, formality, humour, pace, liked topics), learned only from stated or confirmed signals, editable and forgettable, a fixed kid-safe register for children, the default for guests, carried after the stable cached prefix so the KV cache is untouched.
tags: [personality, register, identity, memory-authority, flag-dark, samantha-initiative, lane-f]
timestamp: 2026-11-05T00:00:00Z
---

# The member register

Samantha initiative roadmap item 16, lane F E4 ([lane F](../research/samantha-initiative-2026-11-03/lane-F-personality-proactive.md) section 2.2).
Everything is behind `ZOE_MEMBER_REGISTER` (default `off`, read per call); the child path has its own second flag
`ZOE_MEMBER_REGISTER_KIDS` (default `off`). Flag off = every wire message and every system prompt is byte-identical to today
(`tests/test_member_register.py`).

## The four layers

| Layer | What | Where it lives | This change |
|---|---|---|---|
| L0 invariants | honesty, hold the fact, no flattery, no hooks, one question | soul + doctrines in the sidecar prompt, guards in code | untouched (the stable cached prefix) |
| L1 manner | counted behaviours, one voice | `manner_block.py` / `lexicons_data` | untouched |
| L2 register | length, formality, humour, pace, up to 5 liked topics, per member | table `member_register` (alembic `0068`), `member_register.py` | **new** |
| L3 situational | late-night quiet (22:00 to 06:00 household time) | computed per turn, no storage | **new** |

Only L2 + L3 are rendered, as ONE line (`MAX_REGISTER_TOKENS` = 100, measured chars/4; the longest possible render is a test).
Words are enum-mapped or a sanitised topic (`[a-z0-9 -]`, 24 chars): a stored value cannot carry prompt text.

## What may be learned, from what

`provenance`: `stated` (the member's own words, or an edit they or an owner make) > `confirmed` (an observation the member said yes to)
> `inferred` (a proposal). **Only stated and confirmed rows are rendered.** An inferred row is a proposal and never reaches a prompt.
A lower rank never overwrites a higher one; inference never overrides what the member stated. **Forget** writes a tombstone
(`status='forgotten'`, value blanked): inference and confirmation cannot re-add it, only the member stating it again. `forget_user`
(`MemoryService.delete_user`, fail closed) erases every row; the table is a `reached` store in `forget_reach.py`.
An **entity forget** ("forget Dana") runs `member_register.erase_entity`, which deletes every topic row whose label or slot names the
person (tombstones too: the slot spells the label; fail closed), and `live_entries` re-checks the forgotten ledger at serve time
(strict: an outage withholds all topics). Probed in `tests/test_forget_completeness_probe.py`.

No producer of `inferred` rows ships here: the routes (below) are the editor, and the proposer (a night-mind or struggle-detector
hook that calls `set_entry(..., "inferred")` and asks the member to confirm) is a follow-up. A member's utterance such as "keep it
short" needs a per-language lexicon (words are data, not Python) and is the other follow-up.

## Who gets what

| Speaker | Gets |
|---|---|
| a guest, `voice-guest`, blank id, unknown account, unknown role, any failed or slow lookup | the default: no line, today's bytes |
| an adult (`manner_block.is_adult`, fail closed) | their rendered register (plus the late-night line) |
| a child (`member_modes` minor / kid, or a child role) | `KID_REGISTER` only - plain short words, no teasing, never guess feelings or age, hand off to a grown-up - and **only while `ZOE_MEMBER_REGISTER_KIDS=on`**; stored entries are never rendered for a child |

The id string is never read (a child account may be named like a harness id). The kid text only restricts; it adds no persona. It has
its own flag because the persona layer holds minors on the fixed persona until the distress hand-off ships
(`persona_layer.MINORS_GET_PERSONA`); the owner flips the kid flag on the same decision.

## How it reaches the model, and why the cache survives

zoe-data renders the line per turn (`zoe_flue_client._register_context_block`) and sends it as `zoe-register:<JSON string>` between the
manner line and the identity line. The sidecar (`labs/flue-zoe-brain-2x/src/register.ts`, hooked in `applyPolicies`) strips it and
appends it to THAT turn's system prompt **after** the doctrines, persona and manner block and **before** the user-model card.
Everything before it is the same bytes for every member (pinned through the real sidecar in the tests), so a member switch costs a
re-prefill of the line's few tokens and the card, not the shared prefix. The wake warm carries the member's register line too
(`_kv_warm_outbound`, `prefix_key`, `tests/test_first_turn_prefix_invariant.py`), so the first turn after a wake still matches.
Wire 1 never gets the line; a typed `zoe-register:` first line is stripped, flag on or off, exactly as the persona and manner lines are (the
seam is the only thing that may set a trusted envelope line; the cross-review's "flag-off byte identity" objection to this was declined
for that reason). The wake warm renders the CURRENT register every time rather than replaying the member's last turn's line: the register
is the one block a member edits or forgets between turns, and a forgotten entry must not reach even a throw-away warm.

## Routes (mounted only while the flag is on; absent = 404)

`GET /api/member-register/{user}` (stored entries, the exact line, the vocabulary), `PUT /api/member-register/{user}/{key}`
(`{"value"}`, always `stated`), `DELETE /api/member-register/{user}/{key}` (`?value=` for a topic), `POST .../confirm`
(`{"key","value"}`). A member edits only their own register; an admin may edit anyone's; guests and synthetic ids get 404.

## Drift probes

Register-consistency probes belong beside the persona drift battery (`scripts/perf/persona_drift.py`, PR #2282, open when this was
written, so its files are not touched here). The pure half ships in `scripts/perf/register_consistency.py`: given a member's stored
register and a reply, it checks the reply honours the register by counting (a `terse` reply under N words, no joke after `humour:
off`, no question stack), and the same member's register line is identical across sessions. Run it over replayed replies; it is
count-based, never a model judge.

## Owner steps

Nothing is live. Order: merge and deploy (the migration `0068` runs on deploy; the sidecar restarts because `src/` changed) ->
`ZOE_MEMBER_REGISTER=on` in `services/zoe-data/.env` and restart zoe-data -> set a register through the route for one adult and read the
line back with `GET` -> run the replay gate and `register_consistency.py` -> only then consider `ZOE_MEMBER_REGISTER_KIDS=on`, together
with the minor rows being set and the distress hand-off decision.
