---
type: Audit
title: Usage audit 2026-10-30 to 2026-11-02
description: What actually happened to the household's panel turns over three days - per-class counts, the failure patterns with log evidence, which were fixed in the same PR, and which need an owner decision. Counts and shapes only.
tags: [usage, voice, lists, smart-home, audit]
timestamp: 2026-11-02T00:00:00Z
---

# Usage audit, 2026-10-30 00:00 to 2026-11-02 09:30 (AWST)

Sources: zoe-data app log, the panel daemon log on the Pi, the live DB (`chat_messages`, `struggle_events`), the systemd /
journal history of zoe-data, Kokoro and llama-server, `deploy.yml` runs, the night-window and voice-regression logs.
Counts and turn SHAPES only - no household content. See [index](index.md).

## Scope facts

- All real household use in the window was the **wall panel by voice** (78 turns with a measured first-word time). There was
  no web chat; the `ask_*` sessions are probes and Telegram. `turn_outcomes` is empty (not enabled); `struggle_events`
  held 11 rows for the window.
- Activity was in bursts: 2026-10-30 22:21 (1 turn), 10-31 06:07-08:26 (47), 11-01 03:55 (2), 14:53-14:59 (9),
  18:55-20:15 (21), 11-02 08:00 (2).

## Turn classes (79 panel turns)

| class | count | notes |
|---|---|---|
| OK, answered by the fast path (lights, weather, time/date, calendar, list read/add/remove) | 31 | lights 14, weather 5, lists 7, calendar 2, time/date 3 |
| OK, answered by the brain (chat) | 28 | median first word ~1.8 s |
| Wrong action / "can't do" | 8 | lists 5 (below), a garbled phrase that opened the add-to-list form (1), a command that ran into following speech (1), item correction asks the system cannot do (counted in lists) |
| Misheard (STT text differs from intent) | 9 | the device noun of a light command (5 across three tries), a household name rendered as a different word (3), other fragments (1) |
| Ambient speech answered as the owner | 4 | 11-01 18:56-18:57, follow-up chain after a wake (see Pattern 5) |
| Slow (first word > 3 s) | 13 of 78 | median 1.0 s, p90 3.95 s, max 8.9 s; brain TTFT median 1.8 s, p90 4.0 s |
| Silent / no reply | 0 | every recorded clip produced a turn or a deliberate "held" |
| Error | 0 | no ERROR lines tied to a panel turn |
| Zoe unavailable while in use | 0 | see Availability |

## Patterns, by how often they hit

1. **Lists - a nameless "what's on the list" answered with a count (3 turns, plus 2 follow-ups that fell to chat).**
   "Lists has 4 items." was spoken for asks that named no list type; only "shopping list" read the items. `_resolve_lists`
   selected a list only by type/name, so with several lists and no type it fell to the count. The follow-up "what are they?"
   then went to chat (router `low_conf`). **Fixed** in this PR: a nameless show reads the single populated list (shopping when
   several are populated). Residual: two populated non-shopping lists still speak the count; "change X to Y" on a list item is
   not a supported write (remove + add works) - the member spent ~2 minutes on a spelling correction.
2. **Smart home - a command followed by other speech failed (1 turn) and the device noun was misheard (4 turns).** The device
   query ran to the end of the string ("couldn't find <the rest of the sentences>"); "watch/lies/lite" for "light". **Fixed**:
   the command sentence is re-parsed on its own and the noun is repaired inside an explicit turn/switch frame.
3. **Wrong route for a garbled question (1 turn).** "get all the what?" matched the implicit shopping add and opened the add
   form. **Fixed**: an item cannot end on a question word.
4. **Slow first word (13 turns > 3 s).** Cause is the brain's prefix cache being cold at wake: 6 of 10 wake warm-ups were a
   full ~3.2k-token prefill (`first_cache_n=0`, 5.4 s), and the real turn waits behind it (8.2 s TTFT on 11-01 14:58 with 11
   background brain calls in the preceding 255 s). Not fixed here - brain-slot isolation (see
   `docs/research`), voice path.
5. **Follow-up window answered ambient speech (4 turns).** After a wake, 8 chained follow-ups (no wake word) took in
   narration from the room; the panel's speaker scores for those were 0.16-0.41 against 0.52-0.66 for the owner, while the
   server's FAR-1 % bar is 0.23/0.27, so 3 of 4 were accepted and one session was closed as unverified. Owned by the
   follow-up speaker-attribution work; evidence only.
6. **Stage-2 router disagreed with a confident head (3 of 97 household routes; head >= 0.8).** A rain-timing ask went to
   `time` (head: weather 0.9997; 7.1 s to the answer, the brain then called the weather tool), a "what will you do" ask went to
   `calendar`, a spelling-fix ask went to `music` ("On it."). 20 of 97 routes differed from the head top overall, most are
   the benign `low_conf -> chat` startup probes. Evidence for the router self-train loop; not fixed here.
7. **Compound ask dropped half (1 turn).** "<greeting>? What's the weather like?" was routed `chat` (head 0.94), the weather
   half was never answered (8.2 s). Not fixed.
8. **Wake word near-misses just under the bar (10 events 0.45-0.55, one pair 20 s apart on 11-01 09:50).** Bar is 0.55 on the
   Pi. Owner decision (below).

## Availability (was she down when used?)

- zoe-data restarts: 8 in the window. Seven were the scheduled night window (02:50-03:00, 2-28 min down) or restarts inside
  it; three were **daytime deploys on 10-30 at 08:04, 08:34, 09:08** (10-30 landing of voice PRs), each also restarted Kokoro.
  No panel turn was recorded at those times (first wake event of the window is 10-30 22:21), so no used-while-down case was
  found. Since 10-30 09:08 there have been no deploys.
- Pi announce polling saw 502/connection errors only at those restart moments (blind 10 s-28 min).
- No kernel OOM events; llama-server restarts matched the night window only.
- Night window: 3 of 5 recent nights aborted or fell back (memory floor 2x, 12B failed to load once on 11-02). Not owner
  facing, but the digest/dreaming jobs ran on the 4B that night.
- Voice-regression timer: `status=fail` on 11-02 04:32 ("route signature changed" on one clip + STT speed 1.6x baseline).
  This is the artifact the deploy gate reads; needs a look before the next voice landing.

## Not exercised in the window

Timers (set/ring/dismiss since #2208), music, and memory writes had **no panel or chat turns**, so nothing can be said about
them from usage. Voice ID: 82 speaker claims, one session closed as unverified (Pattern 5); no member turn was refused.

## Owner decisions

- Lower the Pi wake threshold 0.55 -> 0.50? Would have caught ~5 of the 10 near-misses; risk is more TV false wakes.
- Add the household's frequently used proper nouns to the Moonshine keyterms (6 terms set today); the same name was misheard
  three times in one session.
- Whether follow-up (no wake word) turns should need a stricter speaker score than the first turn.
