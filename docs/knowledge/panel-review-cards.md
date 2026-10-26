---
type: Reference
title: Panel review cards - open loops and naming new voices on the panel
description: Owner decisions of 2026-10-18 on the panel. D5 - something said with no time is an open loop (no alarm, nothing proactive), reviewed by voice ("what's on my plate?") and on a private card (Done / Remind me / Drop). The day review card offers at most three new pendant voices to name (Play, That's me, someone known, TV / radio, Not now; someone new goes to the phone by QR). Flag ZOE_PANEL_REVIEW_CARDS (default off), the card token and its security model, operator steps, rollback and what is unproven.
tags: [panel, touch-ui, voice-path, open-loops, omi, day-review, privacy, flag-dark]
timestamp: 2026-10-19T00:00:00+08:00
---

# Panel review cards (`panel_review_cards.py`, `routers/panel_review_cards.py`, `js/touch-review-cards.js`)

## What the owner sees and says

| say (verified voice session or fresh PIN) | Zoe says | the panel shows |
|---|---|---|
| "what's on my plate?" / "what did I say I'd do?" / "my open loops" | the count and the first three, "and N more on the screen" | **On your plate**: each loop with Done / Remind me (This evening / Tomorrow 9 AM / Saturday 10 AM) / Drop |
| "I've done the plumber one" / "tick off the plumber one" / "the plumber one is done" | "Ticked off: ..." | the card again, without it |
| "drop the plumber one" / "I'm not doing the plumber one" | "Dropped: ..." | the card again |
| "remind me about the plumber one tomorrow at 9" | "Okay, I'll remind you tomorrow at 9 AM: ..." | (the reminder goes through the normal reminder writer; the loop closes) |
| "review my day" / "name the new voices" / "what did my pendant hear" | "You've got 2 things from your day and 3 new voices to name. They're on the screen." | **Your day**: day-review items (Keep / Skip, at most 5), then at most **3** new voices (Play, That's me, Someone I know -> their own people + "Someone new", TV / radio, Not now) |

"Someone new" needs a typed name, so it shows a QR to the phone's Pendant page (`/pendant.html`, #2118); the panel never shows a
keyboard. A done / drop / remind phrase whose words match none of the member's loops is NOT claimed: it goes on to the list or
reminder intent exactly as before. Two matching loops are asked about ("That could be X or Y. Which one?").

## Where the data lives (no new store)

- Open loops are the existing `open_loops` table (the nightly extractor `memory_digest._extract_open_loops` mines them from
  conversation). Closing one sets `resolved` and expires its proactive candidate.
- **D5:** an approved pendant follow-up (`omi_review_apply.write_follow_up`) is now an open loop (`context='pendant:<candidate>'`),
  not a `tasks` list item. `proactive/selector._gather`, `morning_checkin` and the legacy agent's `[OPEN LOOPS]` block
  (`zoe_agent._load_open_loops`) skip `pendant:` loops, so they are never raised.
  Loops mined from chat keep the existing proactive behaviour (gentle check-ins under the proactive flags).
- The day review items and the voices are read and written ONLY through their own owner-checked routes
  (`routers/omi_day_review`, `routers/omi_voices`, called in-process); this module has no SQL on any pendant table.

## Privacy

- The member is the TURN's identity (`extra_ctx['scope_user']`: an open voice session or a fresh PIN sign-in). With none, the
  reply is "That's private..." and the panel gets the existing locked card: no row, no count, no name.
- Loops classed health / money / grief / family conflict / feelings by `restraint` are never read out or shown (memories-space
  D11); a typed chat answer lists them, so an approved loop is never unreachable. No transcript line is on these cards (D13,
  owner answer 2026-10-19: a voice session may show the short item text; a FULL transcript on the wall needs a FRESH PIN - not
  built here, see Follow-ups): a voice row is "Voice 3, heard in 4 recordings".
- The card goes to the panel's own push channel only (`voice_cards.push`) and is removed when the voice session closes (the
  existing `touch-voice-session.js` lifecycle; a PIN-path card fades after 90 s).
- **Card token.** Each card carries an opaque token; its rows are opaque ids (`1`, `c1`, `v1`), so a tap can only act on what was
  on screen, and person choices are card keys (`p1`), never raw ids. The token travels in the `X-Panel-Card` header (never a URL:
  nginx logs query strings). A tap is honoured only while THAT member's voice session on THAT panel is open, or when the request
  carries that member's own session (PIN). One live card per panel; 10 minutes at most. Every write goes through the existing
  writer with its owner rule (reminders: `create_reminder_record`; day items: `omi_review_apply.approve(via="panel_touch")`;
  voices: the omi voice routes, including their legacy-owner rule).
- **Residual risk (stated, not fixed):** the kiosk has no strong identity of its own (it runs as a bare guest), and the panel push
  channel admits a session-less socket. Anyone on the LAN who subscribes to `panel_<id>` already sees voice-card rows; with this
  feature they could also act on the card while the member's voice session is open (at most 120 s after their last verified
  turn). The same class as the existing voice cards; closing it needs a panel device credential for the kiosk browser.

## Voice path

`fast_tiers.py` gains one tier call (after `session_status`), so this PR is replay-gated. The tier is whole-utterance anchored and
returns before any work with the flag off. It runs on `voice` and `chat` (chat: text only, no card).

## Operator steps

1. Land through the voice landing loop (replay probe against the merged commit).
2. Set `ZOE_PANEL_REVIEW_CARDS=1` in zoe-data's env, restart zoe-data, deploy the UI (`touch/home.html` loads
   `touch-voice-session.js?v=vs-2` and `touch-review-cards.js?v=rc-2`; not precached, no `SW_VERSION` bump). The renderer is shared
   with the memories card ([memories-voice.md](memories-voice.md)): a frame may name its own routes (`endpoint`, only `/api/panel/<word>`).
3. The day review part also needs `ZOE_OMI_DAY_REVIEW` (items) and `ZOE_OMI_SYNC_ENABLED` (voices); each part is empty while its
   flag is off.
4. Verify at the panel: "what's on my plate?" with a verified voice (badge "<name> · voice") shows the card; tap Done; say "lock"
   and the card goes; ask again as a guest and hear "That's private".

**Rollback:** unset the flag and restart; the tier returns None and the routes are absent. The D5 change (pendant follow-ups as
open loops) is not behind this flag: it applies to every approval once merged.

## Follow-ups

- **D13 transcript view (owner 2026-10-19):** a "show me what was said" view of a day item's or voice's full transcript on the
  wall, behind a FRESH PIN only (a voice session is not enough; the existing PIN step-up challenge). These cards show none today.

## Unproven

- Not run on the live panel (flag off; the kiosk was not reloaded). The node harness drives the real scripts against a DOM shim.
- The loop texts the nightly extractor writes are sentences ("The user plans to ..."), so the spoken read-back can be long.
- Matching "the X one" is word containment; a loop whose words never name the thing ("sort out the thing for Sam") needs the card.
