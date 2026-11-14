---
type: Reference
title: Presence fusion - who is home, in which room, who just left, arrived or woke (flag-dark core + lab)
description: The pure presence-fusion module (services/zoe-data/presence_fusion.py, ZOE_PRESENCE_FUSION off|shadow, default off), every presence signal Zoe has or could get without new hardware (where it is produced, latency, failure, spoofing, person or someone), the declared hardware adapters, the model (bits, groups, decay to unknown, hysteresis events), per-person strictness and tracking-off, child and guest handling, privacy and forget, and the owner's acceptance list.
tags: [presence, routines, privacy, identity, lab, flag-dark]
timestamp: 2026-11-14T20:00:00+08:00
---

# Presence fusion

Owner, 2026-11-14: *"when zoe expands to have cameras, motion sensors, and when it's linked to other devices in the house, it
will be able to tell when people wake up or help them with a routine ... zoe realises you have just stepped out the door and
she sends you a message"*. Presence is the missing sense behind routines and the leaving catch
([IDEAS.md](../IDEAS.md), "Routines"), the panel privacy gate, and the "I heard you" reflex's carried speaker.

**State:** built, unwired, flag-dark. `services/zoe-data/presence_fusion.py` is pure and in memory; no producer calls
`record()` and no consumer reads it. `ZOE_PRESENCE_FUSION` = `off` (default: `record()` stores nothing) | `shadow` (fuses in
memory, nothing acts). The lab is `labs/presence/`. The existing `ZOE_HA_PRESENCE` reader (OBSERVED rows in
`presence_observations`) is unchanged and stays separate: observed and inferred never merge.

It applies the scale of [identity-presence-engine.md](../architecture/identity-presence-engine.md) section 2.3 (log-odds in
bits, capped per group, decayed after a hold) to the house instead of one panel. That design answers "who is talking at this
panel?"; this one answers "who is home, and where?".

## The model

- **Per person, home/away.** `L_home` = sum over evidence groups of the capped, decayed bits of the person's newest
  observation of each signal. `home` at `L >= 3` bits, `away` at `L <= -3`, otherwise `unknown`. Confidence =
  `2^|L| / (1 + 2^|L|)` (3 bits = 0.89).
- **Unknown is the default, and stale evidence decays to unknown, never to away.** Absence of evidence is not evidence of
  leaving: only a signal that positively says "away" (the phone tracker, a BLE beacon unseen by every scanner) can produce
  `away`. A tracker that stops reporting fades to unknown too.
- **A level reading that changes to away breaks older home evidence** (it decays from the change with a 120 s half-life).
  Home evidence never breaks an away reading; it only adds, so one voice (or a TV voice falsely accepted) cannot outweigh a
  phone that left.
- **Room.** The newest room-capable observation wins while its decayed bits are at least 3; a person believed away is in
  no room.
- **Events** (`step(now)` once a minute, and `observe`): `left` and `arrived` with hysteresis (the new state must hold 10
  min for `left`, 3 min for `arrived`; a door event within 10 min before / 2 min after the change cuts that to 1 min / 0);
  `left` also needs some household activity in the 30 min before the change AND the person's own activity in the 3 h
  before it, because a sleeping house, or a sleeping person, does not leave (a suppressed `left` keeps the person `home`,
  so the phone coming back is not an `arrived`); the door shortcut applies only to a person active in the last 30 min,
  because a door is "someone" and is often another member walking out; `woke` = the first activity after
  4 h quiet inside 04:30-11:00 local, once a day; `room_change`. Each event carries its confidence and its evidence classes.
- **Explainable.** `explain(person, now)` answers "why do you think I'm home?" with the supporting observations, strongest
  first (`signal`, `bits`, `age_s`). For that person only.
- **Weights are starting values** [proposal] in `SIGNALS`, to be replaced by shadow data; the lab's numbers are only as good
  as they are.

## The signals: what exists without new hardware

| signal (`kind`) | where it is produced today [code] | latency | how it fails | how it is spoofed | identifies |
|---|---|---|---|---|---|
| verified voice turn at a panel (`voice_verified`) | the server's verdict `_speaker_verified` (`routers/voice_tts.py:3683`), fed to `voice_session.observe_turn` (`:3905`); ResNet34 score on the Pi | at the turn (score p50 ~70 ms on the Pi) | far-field FRR (a member not recognised = a `someone_voice`); shadow mode on the panel sends no claim | a recording or clone of the member's voice; FAR 1 % / 0.1 % by design; a TV voice | person (room = the panel's room) |
| unverified voice / no match (`someone_voice`) | the same verdict, False/None | at the turn | n/a | TV, radio, a guest | someone |
| PIN at the panel (`pin_unlock`) | `POST /api/panels/auth/pin` (`routers/panel_auth.py:762`) | immediate | forgotten PIN; 5 tries then 300 s lockout | shoulder-surfing a 4-digit PIN | person (room = panel) |
| panel touch with a member session (`panel_touch_member`) | the member's own foreground bind/sync, `routers/ui_actions.py:72` `_note_owner_presence`; `ui_panel_sessions.last_seen_at` | the 5 s state sync; NOTE the kiosk heartbeat always sends `is_foreground: true` (`dist/js/touch-ui-executor.js:734`), so a touch is not itself visible | the session lapses after the idle window (900 s default) | anyone tapping an unlocked member session | person, weakly (room = panel) |
| face match (`face_match`) | **no runtime producer**: only the enrolment flow calls the Pi module (`scripts/setup/zoe_enroll_flow.py:178`); the server stores profiles (`routers/face_id.py:139`) | would be per wake | no liveness; USB power shared with the speaker | a photo without liveness | person; declared as a hardware adapter |
| phone on the home network / HA person (`phone_tracker`) | `presence/ha_presence.py:110` `poll_once`, every 60 s, `person` + `device_tracker` states mapped by `map_state` (`:58`); rows are sha256-keyed, so a household mapping entity -> member is needed (owner step) | HA's own: a router tracker reports away after its `consider_home` (often 180 s) [inferred]; companion-app GPS minutes | phones sleep Wi-Fi at night (false away); HA or the bridge down = nothing written; the tracker needs operator config | a phone carried by someone else; a phone left at home says "home" | the phone, so its owner (assumed) |
| phone app last contact | `phone_app.py:241` (`phone_devices.last_seen_at`) | per request | **no app exists** (M0 box side only) | the device credential | person, but no place: not used |
| alarm dismissed (`alarm_dismissed`) | `POST /api/alarms/{id}/dismiss` (`routers/alarms.py:129`) | immediate | a snooze or an unattended alarm | anyone at the panel presses it | the alarm's owner (assumed) |
| pendant sync (`pendant_sync`) | omi-sync on the box: `_note_clean_sync` (`services/omi-sync/omi_sync/runner.py:110`), a connect about every 120 s (`OMI_SYNC_COOLDOWN_S`) while in BLE range of the box; nothing is recorded as "seen" today | about 2 min | the pendant on its charger while the wearer is out | someone else wearing it | the wearer (assumed) |
| member typed chat / Telegram (`member_chat`) | any member-authenticated chat turn | immediate | n/a | the member's session | person, **no place** (0 bits; activity only, for `woke`) |
| room toggles (`room_toggle`) | the panel sleep gate's evidence, `routers/panel_config.py:391` `resolve_sleep_gate` (light / switch / input_boolean on in the panel's room) | HA state | a light left on | anyone | someone (room) |

`voice_presence.py` is the wake acknowledgement, not a presence signal. `proactive/presence.py` `panel_presence_tier` is a
consumer of the panel-session signal above (owner / bound_guest / absent within 900 s), not a separate source.

## Hardware adapters: declared interface, no implementation

Same `Observation` interface, `hardware=True` in `SIGNALS`, no producer anywhere.

| adapter (`kind`) | what it would say | identifies | notes |
|---|---|---|---|
| camera person-detect (`camera_person`) | someone in this room (PanaCast-class, at most 1 fps on the Pi, IDEAS P8) | someone | frames never leave the Pi |
| face match at a camera (`face_match`) | this member in this room | person | liveness required before it carries bits |
| BLE IRK (`ble_irk`) | this member's phone in this room, or unseen by every scanner for 3 scans = away | the phone's owner | Bermuda-class; shares the radio with Wi-Fi, omi-sync and panel-bt |
| mmWave (`mmwave`) | someone in this room (occupancy, continuous) | someone | HA `binary_sensor` |
| door contact (`door_contact`) | the front door opened | someone | used as corroboration: it shortens the left/arrived dwell |

## People

- **Members** are registered (`adult` or `child`). Only a registered person is tracked; an observation naming anyone else
  is dropped, not stored.
- **Tracking off**: a member can switch it off for themselves (`set_tracking(person, False, actor=person)`); their evidence is
  erased at once, new observations are dropped, and they are always `unknown`. Nobody else can switch it back on.
- **Strictness** is each person's own: `relaxed` 0.80, `balanced` 0.90 (default), `strict` 0.97 = the confidence a
  privacy-relevant use (`sure_enough(person, room, now)`) needs.
- **A child** [proposal]: tracked like a member; the child's tracking and strictness are set by a guardian (not by the
  child), and a child's strictness cannot go below `balanced`. A guardian sees the child's HOME state only under the declared
  `guardian_home` rule; never the room or the evidence.
- **A guest** is never a tracked person (`register(role="guest")` raises). A guest exists only as "someone" room occupancy
  (`others_present`), nothing about them is kept per person, and nothing about a guest survives a restart.

## Privacy and forget

- Everything is per person and private by default. `view(viewer, subject, now, rules)` returns the subject's full view
  (state, room, confidence, why) only to the subject; to another member it returns the HOME state alone and only under a
  declared `HouseholdRule` (`share_home`: the subject's own opt-in; `guardian_home`: a child to their guardians); otherwise
  `None`. Events (`events_for`) are the subject's only.
- `others_present(room, member, now)` answers the panel privacy gate ("is anyone else here?") as `yes` / `none_observed`
  / `unknown` (the store's first 10 min; a consumer treats it as crowded) without naming anyone. Evidence of someone ELSE
  is another tracked person believed in the room, a voice that did not verify in the last 10 min, or a person-detect
  counting two or more. Occupancy that cannot count (mmWave, a light switched on) says nothing here, and it is not
  "activity" either: mmWave sees a sleeping person.
- `forget_user(member_id)` erases the member's observations, settings, tracker and events; `MemoryService.delete_user`
  calls it whatever the flag says (`forget_reach.py` entry `presence_fusion_memory`, pinned by
  `tests/test_forget_reach_inventory.py`). Nothing is written to disk.

## The lab numbers [lab]

`labs/presence/harness.py` (stdlib only), 3 seeds x 28 days of the synthetic household in `labs/presence/household.py`:
two adults with phones (one forgets it at home once a week, one wears the pendant), a child with no phone, a weekly evening
guest, one panel in the kitchen, a TV whose false wakes are accepted as an adult 1 time in 100, phones that drop off the
Wi-Fi for 4-30 min at night and 3-12 min by day, two people arriving together every weekday. Truth is per minute; the
fusion is sampled every 5 min. Run 2026-11-14 on this branch's code; the two door rows (`phone+door`, `phone+all`) were re-run 2026-11-15 after the last change (a door shortens the ARRIVED dwell whether or not the person was recently active), which moved only their `arrived` delay (3.0 and 1.5 min to 0). **These are synthetic numbers: they rank the sensors
and expose the failure modes; they are not a measurement of this house.** The real numbers come from shadow mode.

seeds=3 days=28 tracked people=3 false-left target <= 0.03/adult-day

| scenario | home P | home R | away P | away R | unknown | left found | left found (phone owners) | left delay min | false left /adult-day | arrived found | arrived delay min | false arrived /adult-day | woke found | woke delay min | false woke /person-day | room known | room right | gate: others? yes when alone | gate: yes when others there |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| today | 0.83 | 0.19 | 0.00 | 0.00 | 0.83 | 0.00 | 0.00 | nan | 0.000 | 0.00 | nan | 0.000 | 0.94 | 20.50 | 0.004 | 0.04 | 0.97 | 0.075 | 0.258 |
| today+door | 0.83 | 0.19 | 0.00 | 0.00 | 0.83 | 0.00 | 0.00 | nan | 0.000 | 0.00 | nan | 0.000 | 0.94 | 20.50 | 0.004 | 0.04 | 0.97 | 0.075 | 0.258 |
| today+ble | 0.89 | 0.68 | 0.67 | 0.59 | 0.32 | 0.59 | 0.86 | 18.00 | 0.012 | 0.60 | 3.00 | 0.000 | 0.93 | 20.00 | 0.004 | 0.66 | 0.99 | 0.067 | 0.903 |
| today+mmwave | 0.83 | 0.19 | 0.00 | 0.00 | 0.83 | 0.00 | 0.00 | nan | 0.000 | 0.00 | nan | 0.000 | 0.94 | 20.50 | 0.004 | 0.04 | 0.97 | 0.075 | 0.258 |
| today+camera | 0.91 | 0.50 | 0.00 | 0.00 | 0.58 | 0.00 | 0.00 | nan | 0.000 | 0.00 | nan | 0.000 | 1.00 | 15.00 | 0.000 | 0.27 | 0.98 | 0.091 | 0.990 |
| phone | 0.88 | 0.66 | 0.62 | 0.59 | 0.33 | 0.59 | 0.86 | 18.00 | 0.030 | 0.60 | 3.00 | 0.018 | 0.93 | 20.00 | 0.004 | 0.04 | 0.97 | 0.075 | 0.258 |
| phone+door | 0.88 | 0.66 | 0.62 | 0.59 | 0.33 | 0.59 | 0.86 | 10.00 | 0.036 | 0.60 | 0.00 | 0.024 | 0.93 | 20.00 | 0.004 | 0.04 | 0.97 | 0.075 | 0.258 |
| phone+ble | 0.88 | 0.66 | 0.67 | 0.59 | 0.34 | 0.59 | 0.86 | 16.00 | 0.012 | 0.60 | 3.00 | 0.000 | 0.93 | 20.00 | 0.004 | 0.66 | 0.99 | 0.067 | 0.903 |
| phone+mmwave | 0.88 | 0.66 | 0.62 | 0.59 | 0.33 | 0.59 | 0.86 | 18.00 | 0.030 | 0.60 | 3.00 | 0.018 | 0.93 | 20.00 | 0.004 | 0.04 | 0.97 | 0.075 | 0.258 |
| phone+camera | 0.93 | 0.74 | 0.62 | 0.58 | 0.26 | 0.61 | 0.89 | 19.00 | 0.036 | 0.61 | 3.00 | 0.036 | 1.00 | 15.00 | 0.000 | 0.27 | 0.98 | 0.091 | 0.990 |
| phone+all | 0.93 | 0.74 | 0.67 | 0.59 | 0.26 | 0.63 | 0.92 | 8.00 | 0.000 | 0.63 | 0.00 | 0.000 | 1.00 | 15.00 | 0.000 | 0.71 | 0.99 | 0.079 | 0.998 |

Read it like this:

- **Today, with no phone tracker, Zoe cannot know anyone left.** Nothing positively says "away", so `left` is never
  decided (by design: silence is not leaving) and a member is "not sure" 83 % of the time. "Woke" works already (94 %
  found, median 20 min after the real wake: the first word at the panel or the alarm).
- **The HA phone tracker (configuration, no hardware) is the first step.** `left` found for 86 % of the phone owners'
  real departures, a median 18 min after the door. Misses are the forgotten-phone day and the 20-min school pick-up (back
  before the dwell ends). **False `left`: 0.030 per adult-day, exactly at the target of 0.03 (about one a month per
  person), so the target is met only just.** In a debug breakdown of 2 seeds taken BEFORE the "own activity in the last
  3 h" rule, all 11 false `left`s were Wi-Fi drops: 10 while that person slept (around 06:00-07:00 and 23:00, while
  someone else was up) and 1 at 07:00 while awake; that rule was added for the early-morning ones, and the table above is with it.
- **The child is never "left" or "arrived"**: no phone, no BLE beacon. Only a camera with face match, or the child's own
  device, would change that.
- `gate`: with voice alone, "someone else is here" is said 7.5 % of the time the member is alone (their own unrecognised
  voice, the TV: the safe direction) and caught only 26 % of the time someone else really is there (a silent person is
  invisible to voice).

### Which sensor buys the most

Each row adds ONE adapter to the phone-tracker baseline (same traces, same seeds).

| added to "phone" | left delay (min) | false left / adult-day | room known | "others here" caught | woke delay (min) | what it buys |
|---|---|---|---|---|---|---|
| nothing (baseline) | 18 | 0.030 | 4 % | 26 % | 20 | the leaving catch, just inside the cry-wolf target |
| door contact | 10 | 0.036 | 4 % | 26 % | 20 | speed, at a cry-wolf cost: a door is "someone", often another member leaving |
| **BLE IRK (phones)** | 16 | **0.012** | **66 %** | **90 %** | 20 | **the most:** a phone seen in a room vetoes a Wi-Fi drop, gives rooms, and finds other people for the privacy gate |
| mmWave | 18 | 0.030 | 4 % | 26 % | 20 | nothing on these numbers: it cannot say who, nor count (it serves the panel sleep gate, not measured here) |
| camera person-detect + face | 19 | 0.036 | 27 % | 99 % | 15 | the privacy gate (counts people), faster "woke", the child's room |
| all four | 8 | 0.000 | 71 % | 100 % | 15 | |

BLE alone, with no phone tracker at all (`today+ble`), finds the same departures (86 %, 18 min) at 0.012 false per
adult-day: the BLE scanner on the Pi is a substitute for the HA tracker, not only an addition. The answer to "which sensor
buys the most" is **BLE IRK** for leaving and rooms, and a **counting camera** for the privacy gate. The cheapest next step
needs no hardware: map the household's HA phone trackers to members.

## Acceptance (owner words, true when done)

1. "Zoe knows who is home" - for each member, home / away / not sure, and she says "not sure" rather than guess.
2. "She never thinks I left just because I went quiet" - silence fades to "not sure", never to "away".
3. "When I step out the door she knows within a few minutes, and she almost never cries wolf" - a "left" within the
   delay the lab states for the sensors the house has, and at most one false "left" a month per person.
4. "She knows when I wake up" - the first thing I do in the morning after a quiet night, once a day.
5. "Ask her why and she tells me" - "why do you think I'm home?" lists what she saw and how long ago.
6. "Where I am is mine" - no member learns where another is unless that person (or, for a child, the household rule)
   allowed it, and then only home or away, never the room.
7. "I can turn it off" - a member who turns presence off is always "not sure" and nothing about them is kept.
8. "I choose how sure she has to be" before anything private shows, per person.
9. "Forget me means forget where I was too."
10. "A guest is just someone" - a visitor is never tracked by name.

## Verification

- `services/zoe-data/tests/test_presence_fusion.py` (`ci_safe`): unknown by default; decay to unknown with a negative
  control (no decay: a person stays home forever); only an away reading makes away; explain; a TV voice cannot outweigh a
  phone that left; the left dwell, door corroboration, a sleeping house, a sleeping person while another is up (with a
  negative control: rule off -> a false "left"); arrived; occupancy that cannot count never crowds the room; woke; room change; tracking off; child and
  guest rules; the visibility clamp with a negative control (no clamp: another member's location is exposed); declared
  rules; strictness; forget; flag off stores nothing.
