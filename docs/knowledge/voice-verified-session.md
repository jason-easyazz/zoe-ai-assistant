---
type: Reference
title: Voice-verified panel session - "an identified voice is a password"
description: Why a spoken calendar / list / reminders answer was speech-only on the live panel, and the flag-dark fix - a short-lived per-panel voice session that a verified voice opens (model-gated to ResNet34), private result cards on the panel's own push channel, a PIN step-up tier that a voice never satisfies, and an audit line per open / renew / close. Flags ZOE_VOICE_SESSION (default off), ZOE_VOICE_SESSION_TTL_S, ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER.
tags: [voice-path, auth, speaker-id, skybridge, touch-ui, zoe-data, flag-dark]
timestamp: 2026-10-13T00:00:00+08:00
---

# Voice-verified panel session (`voice_session.py`, `voice_cards.py`)

Owner requirements (2026-10-13):

- "if i ask what is on my calendar, i want to see the results as well as hear them"
- "I want it for everything, like an identified voice is a password"

## 1. Diagnosis: why a voice read was speech-only (live config `ZOE_SKYBRIDGE_ONLY=true`)

Four separate causes, measured on the live box and in the code on 2026-10-13:

1. **The estate never renders a Skybridge card.** `_broadcast_skybridge_ui` enqueues a page-reloading
   `panel_navigate` (`/touch/home.html?heard=&say=&domain=calendar`) plus a `show_card` carrying the real
   `calendar_timeline` / list card. On the panel, `show_card` only puts `payload.data.summary` into the dock
   (`touch-ui-executor.js` `ZoeVoiceCard.renderCard` returns a text string); `card`, `cards` and `result` are
   ignored - there is no card renderer in `dist/`. The navigate opens the estate's own `day` screen, which fetches
   `/api/calendar/events` with the PANEL's session - the kiosk's guest session - so it shows a 403 "Sign in" or nothing.
2. **The legacy helpers are gated off.** `_broadcast_calendar_ui` / `_broadcast_reminder_ui` (the intent-lane path:
   `calendar_show` via the public short-circuit, reminders via the brain) return early under `ZOE_SKYBRIDGE_ONLY`.
   With them off, an intent-lane read is speech only by construction.
3. **The spoken Skybridge calendar answer is a COUNT** ("You have 2 events today."), not the events.
4. **No voice identity reaches the server.** The live panel daemon runs with `SPEAKER_ID_SHADOW` on (the default), so it
   never attaches a claim: every live turn logs `identified=None`, and a private read is answered only inside a
   fresh PIN sign-in (`scope_user=jason`, the log of 2026-10-12 18:24) - otherwise it is a PIN challenge.

**`voice/command skybridge auth challenge failed: panel auth unavailable` is test noise, not a live defect.** The string
exists in exactly one place, the `request_voice_identity_challenge` stub in
`services/zoe-data/tests/test_voice_weather_queue.py`, and every occurrence in `~/.zoe-logs/zoe-data.app.log*`
(the six rotated files, 2026-10-05 .. 10-13) is for `panel=panel-auth session=session-auth` - that test's fixture -
next to `db_pool not initialised` tracebacks from a `.claude/worktrees/...` checkout. pytest runs used to log into the
live app log; #2016 stopped that. The real challenge path (`_request_voice_identity_challenge` ->
`panel_auth.create_pin_challenge_internal`) was never seen failing for the live panel `zoe-touch-pi`.

## 2. The design (flag `ZOE_VOICE_SESSION`, default OFF)

**One session per panel, in process memory only** (`voice_session._SESSIONS`): `member_id`, `opened_at`,
`last_verified_at`. Nothing is persisted, nothing biometric is kept (the id of the member, nothing else).

| event on that panel | effect |
|---|---|
| a spoken turn whose claim the server ACCEPTED (threshold + consent row; `speaker_verified is True`) and the model gate passes | opens, or renews if it is the same member (TTL restarts) |
| an unverified voice (`speaker_verified is False`) | closes (`unverified_voice`) |
| a device turn with no verdict at all (gate off, shadow, errored) | closes (`no_verdict`) - unknown is not the member |
| a DIFFERENT member's verified voice | closes (`different_voice`), then opens theirs |
| `ZOE_VOICE_SESSION_TTL_S` (default 120, 10..900) after the last verified turn | closes (`timeout`); a timer tells the panel |
| "lock", "lock the screen", "sign me out", ... (`lexicons_data/<lang>.json` `voice_session.lock`) | closes (`lock`), Zoe answers "Locked." (only when a session was open; with none, the words take the ordinary path - no claimed lock) |
| "that's all", "I'm done", ... (`voice_session.close`) | closes (`close`); the turn itself continues |
| the member's consent is revoked (`/api/voice/enroll` `consent: false`) or the voice profile is deleted (settings or "forget my voice") | closes (`consent_revoked` / `voice_profile_deleted`) on every panel |

Lock and close phrases match the WHOLE utterance (a wake word / "please" at either end removed): "lock the front door"
is a smart-home request, not a lock.

**What the session unlocks.** The member scope a PIN sign-in confers on that panel, for voice: the private reads
(calendar, lists, reminders), the member's ordinary writes, memory recall, and the Skybridge identity wall
(`auth_required` is not raised, so no PIN challenge). In `voice_command` the flag replaces the scope chain for a
panel device's spoken turn: `_scope_identity_user` = the verified member (session open), or the panel's FRESH PIN
sign-in, or nobody. A verified claim never writes `_VOICE_SESSIONS[panel]["bound_user_id"]` (flag on or off: that
slot only mirrors a FRESH PIN session - the voice-identity freshness rule, `routers/AGENTS.md`), and an unverified voice
never inherits a stale one. The open voice session is the one source this flag adds to that rule; it only ever names
the member this turn's verified voice claims. A caller that names its speaker itself (a trusted `identified_user_id`,
the PIN-resumed replay) stays on the freshness chain. Pinned by `tests/test_voice_identity_freshness.py` section 6.

**What it never unlocks.**

- **Admin.** It confers the member scope, nothing more (voice policy never computes an admin role).
- **Another member's PIN sign-in.** A verified voice of B on a panel A signed into with a PIN gets neither A's data nor a
  session (`refused reason=pin_session_other_member`).
- **The STEP-UP tier** - the PIN is still required, even inside a session, reusing the existing panel PIN challenge
  (`_request_voice_identity_challenge(step_up=True)`; the PIN-resumed replay then runs as the PIN's member):
  - spending / payments (`transaction_*` intents; "pay the ...", "send money", "buy it", ...);
  - security / admin / settings ("unlock the front door", "disarm", "change my PIN", "add a user", the panel / board /
    build / setup intents, the household voice setting `voice:set`, "open settings");
  - enrolling or removing biometrics ("learn my voice", "forget my face", ...);
  - permanently forgetting a person (`memory_forget_entity`; "forget everything about ...", "delete the contact");
  - anything for ANOTHER member ("<name>'s calendar", "for <name>", "remind <name>"; names from `users`).
  The tier is DATA: intents + Skybridge pairs in `lexicons_data/voice_session_policy.json`, phrases in the lexicon's
  `voice_session.step_up`. A check that cannot run fails CLOSED (asks for the PIN). A fresh PIN sign-in is not stepped
  up again.

**Model gate.** A session opens only when the ACTING speaker model is WeSpeaker ResNet34 (`ZOE_SPEAKER_MODEL=resnet34`,
PR #2024; `speaker_models.acting_model()` when that module is present, the same env read when it is not) AND the panel's
claim names that model (`speaker.model_id` / `voice_model_id`; a claim naming none is Resemblyzer). With the flag on and
the gate unmet, a verified claim still ATTRIBUTES the turn (memory provenance `speaker_verified=True`) but no longer
unlocks private data by itself - private reads ask for the PIN.

> **WARNING - `ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER=1`.** This lets a Resemblyzer claim open a session. Resemblyzer
> misses the owner about **11 %** of the time at a 1 % false-accept rate; ResNet34 about **0.3 %** (record:
> `docs/research/speaker-resnet34-optimisation-2026-10.md`). Measured on public far-field speakers, not on a family
> member who sounds alike. A 1 % false-accept rate means roughly one stranger turn in a hundred is taken for a member,
> and with this override that turn reads the member's calendar, lists and reminders. Use it only as an explicit owner
> decision, and never with children's profiles enrolled.

**Rich result cards (voice reads).** For an AUTHORISED read (the session's member, or a fresh PIN sign-in) the panel
gets one `voice_card` push frame with the real rows - the events (title, time), the list items (done or not), the
reminders (title, due) - built by `voice_cards.py` from the rows the Skybridge card already carries (calendar, lists)
or read for that member (reminders, and a calendar / list ask the Skybridge was vetoed on, in the background). The
calendar answer also NAMES the events aloud - at most three, then "and N more on screen" ("You have 2 events today:
Dentist at 9 AM and Dinner at 6:30 PM.", "Just one today: Team meeting at 10 PM."; lexicon
`voice_session.spoken_calendar`), built in the Skybridge answer itself (`_resolve_calendar` ->
`voice_cards.spoken_events`). **None of this needs `ZOE_VOICE_SESSION`** (2026-10-14, live: a member signed in by a fresh
PIN heard only "You have 1 event today."): the read-back and the card follow the turn's IDENTITY - an open voice session, a
fresh PIN sign-in or a named speaker - and a turn with none hears only "That's private. Unlock with your PIN and I'll read it
out." (no title, no count). A short follow-up within `ZOE_CALENDAR_FOLLOWUP_TTL_S` (90 s) of such an answer ("what is it",
"what are they", "which one", "what's the one event") re-runs the same member-scoped read (`conversation_cues.calendar_followup`,
lexicon `calendar_followup`; a guest's follow-up is the PIN challenge). **Crowded room:** the identity-engine rule (nothing
private is spoken or shown while a second voice is present) is NOT enforced for these reads - the server has no signal for
"someone else is in the room" yet (open problem). A delivered card replaces the page-reloading Skybridge navigate (it would wipe
the card); an undelivered one (no live socket) falls back to today's broadcast. A turn that is NOT authorised gets the
privacy-safe card instead - no member, no rows: "Say it again so I know it's you, or unlock with your PIN." (only the
PIN line when no voice session can open), alongside the existing PIN challenge.

**Panel push, member-scoped.** `voice_session` (`open` / `renewed` / `closed`, member id, display name from `users`)
and `voice_card` frames go to `panel_<id>` ONLY (`voice_session.send_to_panel` -> `broadcaster.broadcast`), never
through `broadcast_to_panel`, whose 'all' fallback would put one member's data on every screen. The executor relays a
frame only when it names THIS panel (an absent `panel_id` is not "any panel" here, unlike `voice:*`). The estate
(`js/touch-voice-session.js`) shows a small "<name> · voice" badge while the session is open, renders rows with
`textContent` only, ignores a card for any other member, and removes every private card when the session closes; a
PIN-path card fades after 90 s, the privacy-safe card after 20 s. Nothing is stored in the browser.

**Audit.** Every open / renew / close / refusal / step-up is one line on the `zoe.voice_session` logger:
`VOICE_SESSION event=<open|renew|close|refused|step_up> member=<id> panel=<id> reason=<why>` (plus `model=` and `ttl_s=`
on open). Never audio, never a score.

## 3. Operator steps (to turn it on)

1. Land and switch the speaker model to ResNet34 on BOTH hosts (PR #2024, `docs/knowledge/speaker-resnet34-switch.md`:
   dual-run shadow week, then `ZOE_SPEAKER_MODEL=resnet34` in zoe-data and in the panel's `.env.voice`), and re-enrol
   each member (5 clips).
2. Lift the panel's speaker shadow so claims reach the server: `SPEAKER_ID_SHADOW=0` in the Pi's `.env.voice`, restart
   `zoe-voice` (only after the shadow week's false-accept / false-reject numbers are reviewed, per W5).
3. Set `ZOE_VOICE_SESSION=1` (optionally `ZOE_VOICE_SESSION_TTL_S`) in zoe-data's env and restart zoe-data; deploy the
   UI (`touch/home.html` loads `js/touch-voice-session.js`; the executor is cache-busted `?v=estate-10-heal`).
4. Verify: ask "what's on my calendar" by voice - the badge "<name> · voice" appears, the events show and are read out;
   a second voice (or "lock") removes both; "unlock the front door" asks for the PIN; `grep VOICE_SESSION` in
   `~/.zoe-logs/zoe-data.app.log` shows the open / close lines.
5. Voice-path change: the merged commit needs a passing `voice_regression_probe.py` artifact before deploy (the deploy
   gate enforces it). Flag OFF has no session machinery (nothing opens, closes or steps up) but the read-back and the card above still apply
   (`tests/test_voice_session.py`, `tests/test_calendar_voice_read.py`).

**Rollback:** unset `ZOE_VOICE_SESSION` (or `=0`) and restart zoe-data - every session check is skipped and the voice
path is the legacy one; open sessions vanish with the process. The UI script is inert without frames.

## 4. Limits (honest)

- A page reload on the panel loses the badge and card until the next verified turn (nothing is stored in the browser
  by design); the server-side session itself survives.
- The panel SCREEN's own fetches (tapping the calendar screen) still use the panel's session (guest unless PIN): the
  session delivers private data by voice and through its pushed cards, not by re-authorising the kiosk's HTTP session.
- The other-member tier knows members by their `users.name` (and first name); a nickname it has never seen is not
  caught. The phrase tiers are English today (a language with no `voice_session` section steps up by intent / Skybridge
  pair only).
- The step-up classification runs on the utterance before dispatch; a brain turn that decides on its own to call a
  sensitive tool is bounded by that tool's own checks, not by this tier.

## Related

- [Biometric retention policy](biometric-retention-policy.md) - what is stored (the session stores no biometric data).
- `speaker-resnet34-switch.md` (arrives with PR #2024) and [the ResNet34 record](../research/speaker-resnet34-optimisation-2026-10.md) - the model the gate requires.
- [Zoe-driven voice enrolment](zoe-driven-voice-enrollment.md) - how members enrol.
- [Write-claim guard](write-claim-guard.md) - a reply never claims a write that did not happen.
