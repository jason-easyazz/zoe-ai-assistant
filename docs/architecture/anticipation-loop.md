# Anticipation loop (phase 1)

Owner direction 2026-11-06: Zoe is the holistic entity that thinks ahead for you ("I see you've added a flight to your calendar - should I
wake you earlier?"). Offline and private, so a member can tell her everything. Phase 1 is calendar-driven, flag-dark and code-only.

Module: `services/zoe-data/anticipation.py`. Flag: `ZOE_ANTICIPATION=off|shadow|on` (default `off`, read per call).

## The loop

```
night (dreaming cycle, memory_digest phase 1.65)            next natural turn (the member's own)            the answer
  events, next 14 days ─▶ deterministic extraction ─▶ offers   ─▶ "By the way - ..." appended to a      ─▶ yes: the existing tool runs
  usual alarm (repeating, that weekday)                  stored     chat brain reply + a Yes/No card         (alarm / reminder / private
                                                         (dedupe     (ONE per turn, member only)              list), code-composed receipt
                                                          per event)                                        no: cooldown + outcome edge
                                                                                                            silence: ignored after 2 asks
```

* **Never unprompted.** Owner W2 (`ZOE_PROACTIVE_SPOKEN=0`) stands: nothing is spoken or pushed. An offer rides a reply the member
  asked for, as one short line at the end, plus a card.
* **Deterministic first.** Kind, times and wording are code. No model is called at night or by day; the night window and the "no
  daytime 4B outside a turn" policy are untouched. (A 12B wording pass is phase 2 and may only rephrase, never add a fact.)

## Phase 1 offers

| offer | when | yes does |
|---|---|---|
| wake earlier | a timed travel/appointment event whose leave-by time minus 60 min is at least 15 min before the member's usual alarm that weekday (a repeating alarm); adults only | one-off alarm on that date (`alarms.create_alarm`, needs `ZOE_ALARMS`; off = honest "alarms aren't on, nothing set") - the usual alarm is untouched |
| leave by | a timed travel/appointment event | personal reminder at the leave-by time (`reminder_service.create_reminder_record`) |
| packing list | a travel event spanning more than one day | the member's PRIVATE list (`list_service.create_list_record`, `list_type=personal`, `visibility=personal`), never a household list |
| weather at destination | - | not in phase 1: a destination lookup needs an off-box geocode, which private-by-design forbids at night |

Event kinds (title/location keywords): flight (flight, airport, boarding, a flight number like `QF642`), train, trip (trip, holiday,
travel), appointment (doctor, dentist, clinic, meeting, interview, ... AND a location). Anything else - "lunch" - is nothing.
Leave-by leads (no travel API, no external call): flight 150 min, train 45, trip 60, appointment 30. Recurring and all-day events
get no time-based offer.

## Store

No migration: offers are rows of the existing `pending_suggestions` table with `session_id = action_type = 'anticipation'`
(the session-scoped offer readers never see them). Columns: `description` = event title (what a forget matches), `list_type` =
offer kind, `when_hint` = expiry (UTC ISO), `amount_hint` = dedupe key `kind:event_id:start`, `pre_filled_slots` = the facts
(event id, times, label, surface window, surfaced count/day/session, outcome). `resolved`: 0 open, 1 done, 2 declined,
3 ignored, 4 withdrawn/expired, 5 unavailable, 6 claimed (an answer in flight).

## Rules

* **Who sees it.** Only the event's owner (`events.user_id`), only on a turn that identified them: a real member account in their own
  chat session. Guests, panel principals, synthetic ids and voice sessions get nothing (voice surfacing is phase 1b, voice-path).
* **Kids.** A member who is not positively an adult (`manner_block.is_adult`) gets no alarm offers (an alarm change is a parent's
  call); leave-by and packing stay, same plain wording.
* **Pacing.** One offer per turn; at most 2 a local day; the same offer at most once a day and twice in all (then `ignored`);
  after a no, 24 h with no anticipation offers at all.
* **Relevance.** Each offer has a surface window (wake/leave: 3 days before; packing: 7) and expires at its own moment (the alarm
  time, the leave-by time, the trip start). Before an offer is shown or acted on, its event is re-read: deleted, moved or gone =
  withdrawn, never shown.
* **Answers.** A short yes/no binds only to the offer Zoe JUST made (within 15 min, and her previous message ends with it); the card
  buttons use the existing `/api/proactive/suggestions/{id}/accept|dismiss`. A yes runs the real tool; the reply is composed from what
  the tool returned (a reminder/list write leaves a `write_receipts` receipt for the write-claim guard).
* **Learning.** Every answer is an outcome: acted / dismissed / ignored, kept on the row and sent to the outcome edge
  (`proactive.outcome_edges`, if that module is present and `ZOE_OUTCOME_EDGES` on).
* **Forget.** The entity forget cascade has an `anticipation_offers` step: any offer whose title, location or line names the
  forgotten thing is deleted. Deleting the event withdraws its offers.
* **Logs.** Counts only: `ANTICIPATION mode=... user=... events=N wake=a leave=b packing=c stored=s`, `ANTICIPATION_SURFACE`,
  `ANTICIPATION_OUTCOME`. Never a title, a place or a line.

`shadow` = generate and log the counts; nothing is stored or surfaced. `off` = no I/O anywhere.

## Next

1b: voice surfacing (verified voice only, not crowded) in the voice lane. 2: wording pass by the 12B at night (rephrase-only, checked
against the code facts), weather via an on-box forecast cache, more sources (reminders, open loops).
