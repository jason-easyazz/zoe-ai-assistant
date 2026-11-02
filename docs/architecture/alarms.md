# Alarms - wake-up alarms for the household

Owner request 2026-11-02: "we have no alarms on zoe" - Timers/Alarms sections on the panel, a friendly snooze and off on the
sleep clock (tap anywhere snoozes, one deliberate button turns it off), a settable tone, and old-school clock-radio music.

Status: P1 backend (this PR, flag-dark `ZOE_ALARMS`), P2 panel and P3 sounds/clock radio follow. Code: `services/zoe-data/alarms.py`
(model, schedule maths, ring engine), `alarm_voice.py` (grammar), `routers/alarms.py` (`/api/alarms`), alembic `0063_alarms`.

## Principles

Voice first, touch second, no keyboard (complex input hands off to the phone); local and private; **an alarm must never be silent**
(music fails over to a tone); **exactly once** across zoe-data restarts (it restarts several times a day); a ringing alarm is always
stoppable by whoever is in the room. Panel UI lives in `services/zoe-ui/dist/touch/` only (desktop is desktop, touch is touch).

## Data model

`alarms` (one row = the whole truth) and `alarm_events` (append-only log: created, updated, rang, snoozed, dismissed,
auto_stopped, missed). Both created by alembic `0063` on top of head `0058`; single head asserted by the test.

| column | meaning |
|---|---|
| `user_id` | owning member id, or `__household__` for a household alarm |
| `time_hhmm`, `repeat_rule`, `once_date` | local wall time (household timezone, `time_utils.zoe_timezone`, Australia/Perth); `once` / `daily` / `weekdays` / `weekends` / `days:1,3,5` (ISO, Mon=1); `once_date` the local date of a one-off |
| `label` | optional, 60 chars |
| `sound_kind`, `sound_id`, `sound_name` | `tone` + tone id (`sunrise` default, `chime`, `bell`, `beep`) or `music` + a source: `last` (what played last), `playlist:<name>`, `radio:<station/artist>`, `search:<free text>`; music always falls back to a tone |
| `snooze_minutes` (9), `ramp_seconds` (30) | per-alarm snooze length (1-30) and gentle volume ramp (0 = off, max 300) |
| `enabled`, `next_fire_at` | the schedule: epoch seconds of the next ring, NULL when none |
| `state`, `ring_started_at`, `snooze_until`, `snooze_count`, `last_fired_at` | lifecycle bookkeeping |

Why not APScheduler: the scheduler's job store would be a second copy of "when does this ring", and exactly-once needs a
compare-and-swap on the row anyway. The row IS the schedule; a 1 s engine loop (`alarms.run_loop`, started from the lifespan only
when the flag is on) asks "what is due" with one indexed query. Limits: 30 alarms per owner.

## Ring lifecycle

```
idle --(next_fire_at <= now)--> ringing --snooze--> snoozed --(snooze_until <= now)--> ringing
                                   |  \--dismiss/off--> idle      (any state: edit/disable --> idle)
                                   \--(RING_MAX_S = 10 min unanswered)--> idle  [logged auto_stopped]
```

* **Exactly once.** Every transition is `UPDATE ... WHERE id=? AND state=<expected> AND <next_fire_at|snooze_until|ring_started_at>=<value I read>`;
  rowcount 0 means someone else won and nothing changes. Two engines, two ticks, or a restart mid-tick cannot ring an occurrence twice
  (pinned by a two-connection `asyncio.gather` test). A ring in progress survives a restart (`state` is in the DB) and is not re-rung.
* **Ringing advances the schedule.** A repeating alarm moves `next_fire_at` to its next occurrence at the moment it rings; a one-off
  switches itself off. So dismissing never has to "compute" anything and a crash cannot lose the next day.
* **Missed while down.** Found due by more than `MISSED_GRACE_S` (10 min): skipped, logged `missed`, next occurrence scheduled from now
  (a one-off ends). Within the grace it rings late (logged `rang late`). An alarm ringing into the morning an hour late is worse than none.
* **Auto-stop** after 10 minutes unanswered, logged, back to idle with the next occurrence intact.
* **Snooze** moves to `snoozed` for `snooze_minutes`; after `MAX_SNOOZES` (8) tap-anywhere stops snoozing (only Turn off or the
  auto-stop ends it) so a half-asleep tap loop cannot postpone forever; each ring still auto-stops at 10 minutes.
* DST gaps: a wall time that does not exist (spring forward) resolves via zoneinfo and still fires that day (tested).

## API (`ZOE_ALARMS=on`, otherwise every route 404s before auth or DB)

`GET /api/alarms`, `POST /api/alarms`, `PATCH /api/alarms/{id}`, `DELETE /api/alarms/{id}`, `GET /api/alarms/ringing` (the panel's
poll: ring payload incl. tone, ramp, snooze, `stops_at`; for a PERSONAL alarm the panel gets the label and music source blanked, so the ring
screen shows just the time - the owner and household alarms show their label), `POST /api/alarms/{id}/snooze`, `POST /api/alarms/{id}/dismiss`.
Auth: `require_member_or_panel`; ownership by `alarms.can_*` below. Errors are 400/404/403/409 with a speakable `detail`.

## Who sees and controls what (the household fridge rule vs personal)

| | sees | edits / deletes | snooze / turn off a RINGING alarm |
|---|---|---|---|
| member | own + household | own + household | own + household |
| panel (guest at the wall) | household in full; personal alarms **masked** (time and on/off only - no label, sound or owner) | household only | **any** ringing alarm (whoever is in the room must be able to stop it) |
| other member | never another's personal alarm (404) | no | no |

A voice "set an alarm" from a recognised member is personal; from the panel/guest it is a household alarm; a guest off the panel is
asked to sign in. Recommendation (decision 3): keep it this way; personal-alarm labels are private even on a wall.

## Panel UX (P2)

* The Timer screen becomes **Timers | Alarms** tabs. The Alarms tab lists alarms (time large, repeat chips, label, on/off switch);
  tapping one opens the editor: a **time wheel** (hour, minute, am/pm columns, scroll-snap), **repeat chips** (Once / Every day /
  Weekdays / Weekends / Mon..Sun), a **sound picker** (tones with a preview tap; "Music" lists playlists/radio from Music Assistant),
  snooze length chips (5/9/10/15) and a Delete. No keyboard anywhere; a label is voice-set ("called gym") or phone-set.
* **Ringing = full-screen animated alarm on the sleep clock page** (`show('sleep')`; the orb stays hidden): a big pulsing clock
  with a soft expanding ring animation, the label (household alarms only - a personal alarm shows just the time on the wall), and "Tap anywhere to snooze 9 min". **Tap anywhere = snooze** (the whole screen
  is the target; this is hit half-asleep). A separate **Turn off** control sits low-centre, large, away from where a thumb rests,
  and is **press-and-hold 1 s** (a fill ring shows progress; releasing early does nothing and never snoozes). If the panel was on
  another screen it first navigates to sleep. After 8 snoozes tap no longer snoozes and the hint says "Hold to turn off".
* The ring is driven by polling `/api/alarms/ringing` every 2 s plus the server clock offset, so a reload, a voice-turn page reload
  or a zoe-data restart mid-ring all re-show it (the same lesson as the timer ack logic of #2208: the state lives on the server).
  Local audio plays from the page (P3), so the panel rings even if Music Assistant is down.
* Hold vs tap for Turn off (decision 1): **recommend hold 1 s**. Tap-anywhere already snoozes, so a tap on the off button by a
  sleepy hand is the likely accident, and "off" is the one irreversible choice (the person may then oversleep). Hold costs one second
  awake and prevents that. The cost of a hold on a stuck-finger/accessibility user is covered by voice ("turn off the alarm") and
  by the 10-minute auto-stop.

## Voice grammar (`alarm_voice.py`, consulted only when the flag is on, before timers/calendar)

| say | does |
|---|---|
| "set an alarm for 7" / "6:30 pm" / "half past six" / "quarter to seven" / "730" / "noon" | one-off at the next such time |
| "wake me up at 7:30 with music" | music alarm (`last`); "with some jazz" -> `search:jazz`; "with the bell tone" -> that tone |
| "... every weekday / weekends / every day / every monday and wednesday" | repeat rule |
| "... tomorrow", "... called gym" | one-off tomorrow; label |
| "what alarms do I have" / "when is my next alarm" | list (one alarm names its time; several give the count and the next) |
| "cancel my 7 am alarm" / "delete the gym alarm" / "cancel all my alarms" | delete; several matches ask "Which one?" |
| "turn off the alarm" | stops a ringing alarm; otherwise switches the matching one off ("turn my 7 am alarm back on" re-enables) |
| "snooze" / "snooze for ten minutes" / "five more minutes" | snooze what is ringing |

No am/pm: the next such time on the clock; a "wake me" request always means the morning. Narrow on purpose, like the timer grammar:
questions and statements about alarms ("should I set an alarm", "the smoke alarm is beeping", "alarm code") are never writes and a
phrase with "timer" is left to the timer grammar. The Skybridge domain `alarm` has no router class, so the router veto never
applies; the write is held while a speculative voice turn is pending (not a speculation-safe domain).

## Sounds and the clock radio (P3)

Bundled tones are generated, licence-clean (synthesised sine/bell partials, no samples). The panel plays them locally with the
volume ramp (`ramp_seconds`, linear from ~5% to full). A music alarm asks Music Assistant to start the source on the panel's player
("Zoe Panel", AirPlay 2) at the alarm time, ramping volume; **if MA/provider has not reported playing within ~10 s the page starts
the alarm's tone** (and the MA start is cancelled on dismiss so it cannot start under a silenced alarm). "An alarm is never silent"
is a test with a fake MA that never starts. Dismiss/Turn off also stops the music.

## Rollout

`ZOE_ALARMS` default off. To turn on after P2/P3 have landed: add `ZOE_ALARMS=on` to the zoe-data environment (systemd drop-in)
and restart zoe-data (poll `/health`); the ring loop starts at boot only when the flag is on. P1 alone is safe to leave on for
API/voice testing but has no ring screen, so do not rely on it to wake anyone until P2 is live.

## Open owner decisions (with recommendations)

1. Turn-off gesture: **press-and-hold 1 s** (recommended) vs single tap.
2. Snooze default 9 minutes (classic) and the 8-snooze ceiling: keep, or unlimited?
3. Personal alarm labels on the panel: **masked** (recommended) vs shown. Should a recognised voice at the panel (voice session) be able to list/edit that member's personal alarms? Recommend yes once verified-voice sessions are live (#2025).
4. Default sound: **Sunrise** (a soft rising tone) with a 30 s ramp - or music `last` for the owner's own alarm?
5. Missed grace 10 minutes: right, or should a missed *one-off* ring whenever the box comes back?
6. After a one-off rings and is dismissed it stays listed as off (re-enable with a tap); auto-delete instead?
7. Phone push / the iPhone app ringing the same alarm when away from the panel: out of scope here, a follow-up on `phone_app`.

## Tests

`services/zoe-data/tests/test_alarms.py` (ci_safe, real sqlite + real migration): migration up/up/down + single head, schedule maths
(Perth, DST gap), exactly-once with two concurrent engines, restart mid-ring, missed vs late, auto-stop, snooze cycle and budget,
dismiss idempotence, edit-silences-ring, visibility matrix and masking, the HTTP surface incl. flag-off 404, voice table with
negatives, Skybridge wiring flag on/off, and end-to-end spoken set/list/cancel/snooze/off. Negative controls are listed in its docstring.
