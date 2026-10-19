---
type: Reference
title: LAN intercom and PA - a member's words spoken on another panel (ZOE_INTERCOM)
description: The native, Zoe-only LAN PA and intercom (deep dive 2026-10-18 section 8.3, P0) with the owner's call rules of 2026-10-18 - everyone rings first, only adults drop in, guests can't, quiet hours hold, and a person-started announcement or call is outside the no-unprompted-speech rule. What works today (announce to a named panel or every other panel, ring), what is not built (the live two-way audio leg), flag, delivery lane, proof with simulated panels, operator steps and rollback.
tags: [voice-path, intercom, pa, comms, panel, flag-dark]
timestamp: 2026-10-19T00:00:00+08:00
---

# LAN intercom and PA (`intercom.py`)

## What a member can say (with `ZOE_INTERCOM=on`)

| Said at a panel | What happens |
|---|---|
| "tell the kitchen dinner's ready", "let the kitchen know ...", "announce to the kitchen that ..." | the kitchen panel says "Announcement from Ann: dinner's ready" |
| "announce: shoes on", "tell everyone ...", "tell the whole house ..." | every OTHER speaker panel says it |
| "call the kitchen", "ring the hallway" | the kitchen panel says "Ann would like to talk to you, from the living room panel." The caller hears that two-way calls are not set up yet |
| "drop in on the kitchen" | an adult: rings, and says drop-in needs a live audio leg that is not built; anyone else: "Only adults can drop in", rings instead |

A target is a room name (`rooms.name` through `panels.room_id`), a panel location or a panel name, matched whole-word, longest
first. Anything that names no speaker panel ("tell me a joke", "call mum", "tell the kids ...") is not an intercom request and
falls through to the rest of the turn unchanged. Messages to PEOPLE (not rooms) are the Matrix lane's job (deep dive 8.3, P1).

## The owner's rules (2026-10-18), enforced server side in `intercom.decide`

1. **Guests can't** use it at all. The identity is the voice lane's resolved turn identity (`extra_ctx["scope_user"]`: a fresh PIN,
   a verified voice session or a named speaker), never a payload field. The identity-presence engine's
   `site_security.guests.intercom` setting is design-only (no store yet); `GUESTS_MAY_USE_INTERCOM = False` stands in for it.
2. **Quiet hours hold**: nothing is announced or rung inside the household window (`ZOE_QUIET_START_HOUR` / `_END_HOUR`, the same
   window `comm_prefs` uses).
3. **Everyone rings first; only adults drop in** (`manner_block.is_adult`: an allowlisted adult role and no minor / kid mode, fails
   closed). A child's drop-in becomes a ring.
4. Never to the panel the member is standing at, and only to panels holding a live device token.
5. **Outside the no-unprompted-speech rule.** A person-started announcement or call is that person speaking through Zoe, not Zoe
   speaking first. It does not read `ZOE_PROACTIVE_SPOKEN`. Recorded as a principle in `docs/VISION.md`.

`others_present` (identity-presence engine, a member's private-answers setting) does not apply: the sender chooses to say the words
to a room.

## Delivery

The existing daemon lane, no daemon change: one `voice_announcements` row per target panel, trigger `intercom_announce` (TTL 90 s)
or `intercom_ring` (TTL 45 s), both in `voice_announce.PANEL_SCOPED_TRIGGERS`, so only the daemon whose device token IS that panel
can claim it, even in the default claim-any mode. The daemon speaks it on its next poll (5 s), defers while busy, and ACKs
`played_at`. The row's `user_id` is the sender (the audit trail). Nothing is pushed to the kiosk browser.

## Not built yet (next steps, in order)

- The live audio leg: P2P WebRTC between panels over the panel WebSocket (deep dive 8.3), for an answered call and for drop-in.
  The daemon must mute wake word and STT while a stream is live.
- A chime before an announcement, and music ducking through the daemon's `pactl` duck.
- A brain tool for the intercom (the 22 tools in `zoe-tools.ts` have none); today only the deterministic tier answers.

## Proof

`services/zoe-data/tests/test_intercom.py` (`ci_safe`) builds a simulated house: living room (origin), kitchen and hall panels
with live device tokens, a garage panel with no token and an office panel with a revoked one. It pins that a named panel's row is
claimable by that panel only, that "announce" reaches every other speaker panel exactly once, each rule above, shadow / off / dry
behaviour, and the wiring in `fast_tiers.resolve`. The house has one panel today, so multi-panel behaviour is proven only there.

## Operator steps

1. Replay gate (voice path): `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py` with
   `ZOE_INTERCOM=shadow`, then `on`. Off is byte-identical (the tier returns before any work).
2. `ZOE_INTERCOM=shadow` in the zoe-data env, restart, and read `INTERCOM` lines in `~/.zoe-logs/` for a week.
3. `ZOE_INTERCOM=on`. With one panel, "announce" answers that there is no other panel; a second panel (with its own device token
   and a room) is the first real test.

Rollback: `ZOE_INTERCOM=off` (read per call, no restart).
