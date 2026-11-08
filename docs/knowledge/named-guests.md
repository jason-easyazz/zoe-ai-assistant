---
type: spec
title: Named guests - Zoe knows a non-member by voice, greets them by name and remembers them (ZOE_NAMED_GUESTS, flag-dark)
description: Acceptance spec + design record. A friend who is not a member is recognised by voice at the panel, addressed by name with guest permissions only, and remembered in their own conversation space; enrolled by a verified member or learned from repeated conversation. Acceptance checks, where the profile, scope and memory live, the guarantees, flags, rollout and what is not built yet.
tags: [voice, speaker-id, guests, biometrics, panel, memory, forget, flags, spec]
timestamp: 2026-11-08T00:00:00Z
---

# Named guests

## Acceptance — true when done

Synthetic names: Quinn and Rowan are guests, Avery is the owner. Each line is checkable by a QA chat or a panel test; a line
marked **(not yet)** is a known gap with a follow-up, not a pass.

1. When Quinn, a named guest, speaks on the panel, Zoe knows it is Quinn and addresses them by name.
2. Quinn is always treated as a guest: no member's lists, calendar, reminders or memory, even while Avery's PIN session is open
   on the panel, and Quinn's voice never unlocks a member.
3. When Avery speaks, nothing changes: Avery can still do everything, and the flags off leave Zoe exactly as before.
4. Zoe remembers her own past conversations with Quinn and picks them up again with Quinn, and only with Quinn - never with
   Rowan, an unknown voice, or Avery.
5. Zoe learns a voice from enough real conversation and then asks "I don't think we've met. What's your name?"; she also learns
   it at once when a verified member says "learn Quinn's voice" with Quinn at the panel.
6. A verified member can name an unknown voice Zoe has heard ("that was Quinn this morning", through the unknown-voices API);
   no guest can. **(not yet:)** naming a saved panel clip, and hearing a clip before naming it.
7. Saying "I'm Quinn" can never take over an existing guest or a member: an unverified voice can only create a new guest from
   its own voice.
8. "Forget Quinn" (or "forget Quinn's voice") erases Quinn's voice and every remembered conversation for good, and says so
   honestly if it could not.
9. **(not yet:)** guest clips are labelled in the replay corpus and kept out of owner-voice metrics (open-problems.md, "Guest
   clips in the owner corpus").

## Status

Built, FLAG-DARK, landing as four stacked PRs: (1) the registry (0071), the panel feed, the verdict and guest scope;
(2) enrolment (`named_guest_enrol.py`); (3) the guest's own memory and forget (`named_guest_memory.py`, 0072); (4) passive
learning (`named_guest_learning.py`, 0073). A section about (2)-(4) is not live until that PR lands. Owner request 2026-11-08: a
friend talked to Zoe on the panel and she did not know who it was. The friend "doesn't need an account, just be identified, so
when I'm talking I can do stuff but if she talks she just gets treated like a guest", and Zoe "will remember the conversations
she has had with that person".

Code: `services/zoe-data/named_guests.py` (who, the claim, the panel feed, the brain block), `named_guest_enrol.py`,
`named_guest_memory.py`, `named_guest_learning.py`, migrations `0071`-`0073`, the daemon's guest scoring in
`scripts/setup/zoe_voice_daemon.py`. Pinned by `services/zoe-data/tests/test_named_guest*.py` and
`tests/unit/test_named_guests_daemon.py`; the forget probe row is `named_guest_turns` in `tests/test_forget_completeness_probe.py`.

## Where things live

| Piece | Where | Notes |
|---|---|---|
| The person | the enrolling member's own `people` row (`visibility='personal'`) | An existing person of that name is LINKED (people graph), never duplicated. Another member's Quinn is their own row. |
| The voice | `learned_voiceprints`, `channel='panel'` | ONE person-voice registry with Omi (`channel='pendant'`). 0071 put the channel in the key: the pendant and the panel's microphone never share a vector, and a person can hold one of each. |
| The memory | `named_guest_turns` (user_id = the member layer, people_id) | What Zoe and that guest said. Read only on a turn matched to that guest by VOICE. Never served to any other speaker, the enrolling member included (default: never; no owner rule overrides it). |
| The verdict | `speaker.guest` / `speaker.guest_score` beside the member candidate | The daemon scores members and guests; the server judges both. |

## The guarantees

1. **A guest is never a member.** The turn's user stays the guest sentinel; the guest claim is a separate field the member gate
   never reads; a guest id sent as a member claim has no consented `speaker_profiles` row and is dropped (negative control in the
   tests). No PIN-free session, no member lists / calendar / reminders / memory.
2. **A voice closer to a guest than to the member is not the member** (`on` only): the member claim is dropped, and the daemon
   never offers it to adapt the member's profile.
3. **Only a verified adult member enrols a guest** ("Hey Zoe, learn Quinn's voice": a voice the server accepted, or a fresh PIN),
   into their own layer, through a single-use, panel-bound, 4-minute server ticket. `POST /api/voice/guests/enroll` refuses a
   profile without one, so an unverified voice can never create, overwrite or re-enrol a guest or a member. A guest's memory is
   reached only by a voice match, never by saying the name.
4. **Never a child, never a member's name.** A person who is (or may be) a child is not enrolled or matched; the name of a
   household member is refused (they say "learn my voice").
5. **Forget is complete and fails closed.** "Forget everything about Quinn" (entity forget -> `contacts_store.forget_voiceprints`
   -> `omi_voices.purge_people` -> `named_guest_memory.erase_people`), "forget Quinn's voice" and "forget Quinn" (only when Quinn
   is one of the speaker's guests) and `DELETE /api/voice/guests/{id}` erase the voiceprint AND every kept turn. The forget ledger
   is also a serve wall: a forgotten name is never matched, fed to a panel or recalled even if an erase did not land, and a ledger
   outage means no guest at all. A member re-enrolling a name they forgot releases it (an explicit re-teach). Erasing a member
   (`contacts_store.forget_user`) erases their guests' turns.

## On a guest turn

The brain gets one block in the identity slot (both lanes: the Flue sidecar's identity slot, the core lane's portrait): "You are
talking with Quinn, a guest of this household: not a member and not the owner. Address them as Quinn ...", plus, with memory on,
up to `ZOE_NAMED_GUEST_RECALL_TURNS` of their own past exchanges. A follow-up in a listening window from a matched guest (at the
window's stricter bar) is kept as a guest turn instead of being dropped as background speech.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `ZOE_NAMED_GUESTS` | `off` | `off`: no guest is served or matched, the tier is inert. `shadow`: guests are served to the panel and scored; the server logs `NAMED_GUEST mode=shadow would_greet=1` and changes nothing. `on`: matched, greeted, enrolable, member claim guard live. |
| `ZOE_NAMED_GUEST_MEMORY` | off | Keep and recall a guest's own exchanges (needs `on`). |
| `ZOE_NAMED_GUEST_RECALL_TURNS` | 6 | How many past exchanges ride into a guest turn (0-20). |
| `ZOE_GUEST_VOICE_LEARN` | `off` | Passive learning: `shadow` keeps unknown voices and logs, `on` also asks their name. |
| `ZOE_GUEST_LEARN_*` | see above | `JOIN` 0.45, `TIGHT` 0.50, `MIN_TURNS` 8, `MIN_SESSIONS` 2, `MIN_WAKE` 2, `EXPIRE_DAYS` 14. |

## Rollout ("test it, if it works, turn it on")

1. Merge, deploy zoe-data (runs `alembic upgrade head` for 0071), deploy the daemon to the panel (Pi) - it must have the guest
   scoring before guests are served, or it simply ignores the field.
2. `ZOE_NAMED_GUESTS=on` briefly to enrol (a member at the panel: "Hey Zoe, learn Quinn's voice", with Quinn there), then
   `shadow` for a few days: `grep NAMED_GUEST ~/.zoe-logs/zoe-data.stderr.log` shows who WOULD be greeted and with what score;
   check no `would_greet` line coincides with a member speaking.
3. `ZOE_NAMED_GUESTS=on`, then `ZOE_NAMED_GUEST_MEMORY=1`.

## Passive learning (`named_guest_learning.py`, 0073; `ZOE_GUEST_VOICE_LEARN`, its own flag; the fourth stacked PR)

Owner, 2026-11-08: "Can't Zoe learn a person's voice with enough conversation?"

- **What is kept:** with the flag on, the sync tells the panel `learn_unknown`, and the daemon attaches the turn's speaker
  EMBEDDING (never audio) to the `speaker` block. The server keeps it (`panel_voice_samples`, at most 30 per voice, under a
  household `panel_voice_clusters` row) ONLY when the gate scored the voice and no member and no named guest matched.
- **Never a member's voice:** a sample within the FAR-1 % bar of any consented member profile or any named guest is dropped
  before it is kept (negative control in the tests). Voices are never merged (one person can form two acoustic clusters: they are
  then asked twice, never mixed with someone else). A sample joins a voice at `ZOE_GUEST_LEARN_JOIN` (0.45, the cut the Omi
  calibration chose for the same model).
- **Confident enough** = `ZOE_GUEST_LEARN_MIN_TURNS` (8) turns, over `ZOE_GUEST_LEARN_MIN_SESSIONS` (2) conversations or days,
  at least `ZOE_GUEST_LEARN_MIN_WAKE` (2) of them woken by the wake word (a TV that only leaks into listening windows never
  qualifies), and a mean sample-to-centre cosine of `ZOE_GUEST_LEARN_TIGHT` (0.50). These are STARTING values from the repo's
  calibration records, not measurements of this household: re-derive them from the shadow week's `GUEST_LEARN` lines (counts and
  scores only, never a vector or a word).
- **The ask:** that turn's brain block says to ask once, kindly: "I don't think we've met. What's your name?" (at most once a
  day). An answer from the same voice within 10 minutes ("I'm Quinn", "it's Quinn", "Quinn") creates a NEW named guest from that
  voice under the household's first admin (the people row only: the name is the guest's own introduction, not a member's word). A name already held by any named guest, or a
  household member's name, is refused: an unverified voice can only create, never claim.
- **Expiry:** a voice with no new turn for `ZOE_GUEST_LEARN_EXPIRE_DAYS` (14) is dropped with its samples.
- **The owner:** `GET /api/voice/unknown-voices` (admin; counts and dates only), `POST /api/voice/unknown-voices/{id}/name`
  `{"name"}` ("that was Quinn this morning": a named guest in the naming member's layer), `DELETE /api/voice/unknown-voices/{id}`
  (or `all`). The nightly backup carries no row data of either table.
- **Shadow first:** `shadow` keeps samples and logs `GUEST_LEARN ... action=ask` where Zoe WOULD ask, and never asks.

## Not built yet (follow-ups)

- **Omi wiring:** the registry and the channel key are shared; Omi does not yet read panel prints (by design until the
  cross-device match is MEASURED - memories-space.md 3.4) and does not yet offer a pendant-tagged person as a panel guest.
- **Seeding a guest from saved panel clips** and **playing a clip back** to a verified member ("who's this?").
- The replay corpus treats every clip as the owner's voice (open-problems.md, "Guest clips in the owner corpus").
