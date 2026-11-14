---
type: Reference
title: The anticipation engine
description: How Zoe thinks ahead - a pure situation model over a person's next 36 hours, a rule pack that turns situations into single-action offers, the offer lifecycle on pending_suggestions, a deterministic learning loop, and the lab that measures it. Flag ZOE_ANTICIPATION (off | shadow | on).
tags: [anticipation, proactive, offers, calendar, privacy, learning]
---

# The anticipation engine

Owner direction 2026-11-06: **"Zoe thinks ahead, holistically"** - she notices (a flight in the calendar tomorrow) and
OFFERS ("want me to wake you earlier?"). She does not act unasked, and she does not speak out of nowhere: an offer rides a
turn the person started (and, later, the panel card or their phone if they opted in).

Phase 1 (`services/zoe-data/anticipation.py`, design in
[anticipation-loop.md](../architecture/anticipation-loop.md)) is one loop with three offers. The engine keeps that loop and
its store and puts a model and a rule pack under it. Flag: `ZOE_ANTICIPATION=off|shadow|on` (unchanged).

## True when done (the owner's words)

| # | true when | proven by |
|---|---|---|
| 1 | "She notices": a flight tomorrow, a clash, a birthday nobody set a reminder for, bins night | the situation model: `tests/test_anticipation_situations.py` |
| 2 | "She offers, she does not act unasked": one sentence, one action, nothing happens until a yes | the rule pack: `tests/test_anticipation_rules.py` |
| 3 | "Never reveals another member's private event" | the one clamp (`view`), tested with negative controls; the lab counts violations (must be 0) |
| 4 | "No speech out of nowhere": an offer waits for a turn the person starts | phase 1 surfacing (chat turn only) |
| 5 | "You cannot manage what you cannot measure" | the lab's numbers (see *The lab*) |

## The situation model

`anticipation_situations.situations(facts, person)` - pure, no model, no I/O. Facts in: the person's next 36 hours of
events (with times, places, visibility, attendees), alarms, reminders, their contacts' birthdays (a week ahead), their usual
number of events a day, and the other members' events AS THE PERSON MAY SEE THEM. Situations out, each with `sources` (the
fact ids it came from) and `data` (the numbers it was decided on):

| situation | what it is |
|---|---|
| `travel_day` | a flight, train or trip of the person's (leave-by time, whether it spans nights) |
| `appointment` | a timed appointment with a place |
| `early_start` | the first timed thing of a day needs a wake time 15+ minutes before the usual alarm |
| `no_alarm` | a day starts before 9 am and no alarm rings that day |
| `double_booked` | two of the person's own timed events overlap |
| `clash` | the person's own event overlaps another member's event they are an attendee of |
| `child_uncovered` | a child's short event starts while every adult is visibly busy (adults only) |
| `birthday` | a contact's birthday within a week, with no reminder or event that names them |
| `chore_due` | a bin / chore event on a later day with no reminder near it |
| `full_day` / `empty_day` | the next whole day against the person's usual (4+ and twice the usual; clear when usually 2+) |
| `late_then_early` | an evening that ends after 10:30 pm before a day that starts before 8 am |
| `tight_gap` | two events in different places under 30 minutes apart |

**Privacy: one clamp.** `view(facts, person)` is the only door to another member's facts: a `family` event as it is, a
`busy` event as a bare block (no title, no place), a `personal` event not at all; nobody else's alarms, reminders or
birthdays; a guest sees nothing. Every detector reads the view. Not even by inference: if the only thing making "every
adult is busy" true is a personal event, the other adult gets no offer.

## The rule pack

`anticipation_rules.offers(facts, person, disabled=..., tune=...)`: each rule turns one situation into at most one offer -
one sentence, one proposed action through an existing writer with exact arguments, for one person. A sentence names no
event title and no place. An offer has a priority, an expiry (the instant its action would be pointless), a `surface_from`
(18 hours before the expiry by default) and a dedupe key (the same situation makes the same key every night). A rule can
be disabled per person, and tuned (priority, lead, quiet) by the learning loop; below priority 20 an offer is not made.

Generated from the pack (`python3 services/zoe-data/anticipation_rules.py`; a test fails when this copy is stale):

| rule | offers when | a yes does | tool | priority | who |
|---|---|---|---|---|---|
| `wake` | the first timed thing of a day needs an earlier start than the usual alarm (15 minutes or more) | a one-off alarm that morning (the usual alarm is untouched) | `alarm.create` | 90 | adults |
| `child_cover` | a child's short event starts while every adult is visibly busy | a personal reminder to sort out who takes them | `reminder.create` | 85 | adults |
| `alarm_for_event` | a day starts before 9 am and no alarm rings that day | a one-off alarm that morning | `alarm.create` | 80 | adults |
| `clash_heads_up` | the person's own event overlaps another member's event they are asked to | a personal reminder to sort it out | `reminder.create` | 75 | members |
| `leave` | a timed flight, train, trip or an appointment with a place | a personal reminder at the leave-by time | `reminder.create` | 70 | members |
| `tight_gap_leave` | two of the person's events in different places are under 30 minutes apart | a personal reminder ten minutes before the first one ends | `reminder.create` | 65 | members |
| `double_booked` | two of the person's own timed events overlap | a personal reminder to sort it out | `reminder.create` | 62 | members |
| `flight_checkin` | a timed flight on a later day | a personal reminder at 7 pm the evening before | `reminder.create` | 60 | members |
| `birthday_reminder` | a contact's birthday within a week with no reminder or event that names them | a personal reminder at 9 am on the day | `reminder.create` | 55 | members |
| `packing` | a flight, train or trip that spans more than one day | the person's PRIVATE packing list with a few basics | `list.create` | 50 | members |
| `chore_night_before` | a bin / chore event of the person's on a later day with no reminder near it | a personal reminder at 7 pm the evening before | `reminder.create` | 45 | members |
| `wind_down` | an evening that ends after 10:30 pm before a day that starts before 8 am | a personal reminder at 10 pm to head home | `reminder.create` | 40 | members |
| `full_day_prep` | four or more timed events in the next whole day, and at least twice the person's usual | a personal reminder the evening before (or an hour before the first) | `reminder.create` | 35 | members |
| `empty_day_catchup` | a usually busy person has a clear day and reminders with no date | a personal reminder at 10 am | `reminder.create` | 20 | members |

Tools: `alarm.create` = `alarms.create_alarm` (a one-off; adults only), `reminder.create` =
`reminder_service.create_reminder_record` (personal), `list.create` = `list_service.create_list_record` (personal).
