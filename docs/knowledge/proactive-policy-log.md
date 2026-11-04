---
type: Reference
title: The proactive policy, log-only (ZOE_PROACTIVE_POLICY_LOG)
description: Samantha Initiative lane F E3. The 14-row proactive decision table run on real signals (selector candidates, the night mind's one raise thread, panel presence, quiet hours, recent voice activity), recording the rung Zoe WOULD choose. Strictly log-only - nothing is shown, said, sent or stored; logs carry hashed member id, item id, coarse kind, row, rung and a fixed reason code, never text or names.
tags: [proactive, samantha-initiative, lane-f, log-only, privacy, flag-dark]
timestamp: 2026-11-04T00:00:00+08:00
---

# The proactive policy, log-only (`proactive/policy_log.py`)

Design: [lane F section 3](../research/samantha-initiative-2026-11-03/lane-F-personality-proactive.md) (table 3.3,
ladder 3.2), experiment E3. Owner rules: no unprompted speech (`ZOE_PROACTIVE_SPOKEN=0`), pull by default.

**Flag:** `ZOE_PROACTIVE_POLICY_LOG` = `off` (default; anything but `log` is off) | `log`, read per call. Off returns
before any signal read. It needs the pull orb path (`ZOE_PULL_NOT_PUSH` on, `ZOE_PROACTIVE_SELECTOR` on) because the
seam is `pull.pending_state` (the panel's orb poll): one observation per poll, de-duplicated per (member, item,
decision, local day).

## What it does and does not do

Does: `decide(Signals)` (pure) maps a candidate moment to a row, a rung and a fixed reason; `observe` logs it.
Never: composes text, calls any LLM, writes a DB row, rings the orb, makes a card, speaks, or marks anything raised
(the night mind's `note_raised` is not called). A failure in the observer is swallowed; the orb response is
byte-identical off and on.

## Rungs and the table as implemented

Rungs: `silent` (0), `dot` (1; surfaces at the next pull / first turn - the gentle line), `card` (2), `speak_on_pull`
(3, unused: spoken-on-pull is already the pull tier), `speak` (4, row 3 only).

Rows 1-4 gate first (first match wins); 5-11 and 14 pick a rung; **12 and 13 downgrade a surfacing pick** (read
strictly in order they could never fire, so they are applied after selection).

| Row | Condition (signal) | Rung |
|---|---|---|
| 1 | guest holds the panel (presence tier `bound_guest`) or member unverified | silent |
| 2 | a restraint class on the item, or kind `emotional` | silent |
| 3 | member asked for it at a time (`reminder`) | speak (never emitted by a real signal yet) |
| 4 | a voice turn within 90 s, or quiet hours | silent, hold |
| 5 | kind `event` / `change_notice` | dot; card if the member's own panel is fresh |
| 6 | the night mind's one `raise` thread | dot |
| 7 | `inferred_fact` | card |
| 8 | `date` | dot; card on the day if panel near |
| 9 | `keep_in_touch` | silent unless cadence opt-in |
| 10 | kind `open_loop` | card if panel near, else dot |
| 11 | `routine` | silent unless enabled |
| 12 | two earlier items of the kind raised and not taken up | silent, suppress |
| 13 | weekly cap (3 cards, 7 dots per member; in-memory, resets on restart) | silent, hold |
| 14 | everything else (`commitment`, unknown kinds) | silent |

Real signals today: selector candidates (`open_loop`, `emotional`, `event`, `commitment`), the night raise, panel
presence, quiet hours, `voice_activity`. Not yet emitted by any source: `date`, `keep_in_touch`, `routine`,
`inferred_fact`, `change_notice`, `reminder` (the table is ready for them). "Mid-task" uses voice activity only; there
is no presence sensor, so `absent` panel means "no card", never "away".

## The log

`PROACTIVE_POLICY user=<8 hex of sha256> item=<candidate id> kind=<enum> row=<n> rung=<rung> action=<none|hold|suppress|surface|speak> reason=<code>`
and one `PROACTIVE_POLICY_PASS` counts line per poll. Logs go to `~/.zoe-logs/` (zoe-data), not journald. Item ids
are opaque; the night raise is hashed. No text, names, or class names are logged.

## Owner step

Set `ZOE_PROACTIVE_POLICY_LOG=log` in zoe-data's environment and restart; read the lines for two weeks
(`grep PROACTIVE_POLICY ~/.zoe-logs/*`), then count rungs per row. E3's welcome / neutral / unwelcome tagging of
sampled "would have shown" items is the owner's, done against the item ids (look the item up in the app); success
bar and failure rule are in lane F E3 (any deny-list item reaching rung 1+ is a failure: rows 1-2 are tested never to).
