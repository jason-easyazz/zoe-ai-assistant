---
type: Reference
title: Music search finds nothing - provider reconnect
description: When jukebox or panel music search "finds nothing" it is usually Music Assistant's YouTube Music provider being down, not a Zoe bug; how to confirm, the panel QR reconnect, and the in-place-save invariants not to regress.
tags: [music, music-assistant, runbook, incident-signature]
timestamp: 2026-11-12T00:00:00Z
---

# Music search finds nothing - provider reconnect

Operational record (live since the 2026-07-26 panel release). See [index](index.md); the music system itself is described in
[CANONICAL.md](../CANONICAL.md) ("Music - the name collision").

## Symptom and confirmation

Jukebox or panel search returns nothing. YouTube Music is the only general song/album/artist catalogue in Music Assistant here;
when its provider is down, search returns only radio. Confirm with `music_service.catalog_health()` or
`GET /api/music/search?q=...`: the response carries `degraded: true` and Music Assistant's `last_error`. Seen 2026-07-24/26:
"User does not have Youtube Music Premium" - MA's ytmusic provider needs a Premium account, and a reconnect with one fixes it.
A non-Premium account will not.

## Fix (from the panel)

Music card -> Browse -> **Sources** tab -> YouTube Music shows an amber **Reconnect** -> tap -> a **QR** appears -> scan on a
phone and sign in.

Opening the Sources tab wakes Music Assistant first (the idle reaper stops it for most of the day; the first open can take
a few seconds), so the amber **Reconnect** shows even when MA was asleep. Before 2026-11-12 the catalogue read MA without
waking it, and every provider showed plain **Connect**. If MA cannot be started, the tab says "Music isn't available right
now - tap Sources again to retry" and voice "set up music" says the music engine isn't available; neither claims that
nothing is connected (`music_service.provider_catalogue` raises `MusicEngineUnavailable`, the route answers 503
`music_engine_unavailable`). Tapping Connect or Reconnect while MA is down shows "Music isn't available right now - try
again"; only an auth refusal opens the sign-in card (`POST /api/handoff/start` answers 503, not 400 "unknown provider"). The credential only touches the provider's own sign-in page; Zoe never sees it. This follows the standing
rule that connecting an account hands off to the phone. Saying "connect youtube music" by voice answers honestly: a connected provider whose `needs_attention` is set gets "YouTube Music needs signing in again - open Music, then Sources on the panel, and tap Reconnect" (a first connect gets the matching "tap Connect" wording). The returned Skybridge QR card is NOT rendered on a voice turn, so the reply never promises a code. The tap path reconnects the existing instance in place; a free provider needing attention is refreshed in place. A healthy connected provider still answers "already connected". Opening the authCard QR directly from a voice turn is recorded in [open-problems.md](open-problems.md). Pinned by
`services/zoe-data/tests/test_music_voice_reconnect.py`.

## Voice "play X"

`intent_router._execute_music_intent` (`music_play`) tries Zoe's own music engine first:
`music_service.search_and_play(query, player_id=<preferred speaker, or the MA speaker matching a spoken room>)`. Only when
that plays nothing does it fall back to a Home Assistant `media_player` (unchanged replies). With no HA speaker either, it
says "I couldn't find <query> to play." (MA up, nothing found) or "Music isn't available right now. Try again in a moment."
(MA could not be woken or lists no speakers). The turn's home-control refusal runs first, as before. Pinned by
`services/zoe-data/tests/test_intent_router_music.py`.

## Invariants (do not regress)

- Reconnect is IN PLACE: both `setup_save` and `music_oauth._run_flow` pass the existing `provider_instance_id` to
  `save_provider`; otherwise MA mints a DUPLICATE provider and leaves the broken one.
- `save_provider` on reconnect merges new auth over the instance's CURRENT `values` (from `config/providers`), not fresh
  defaults - otherwise it resets library-sync and other settings.
- `catalog_health` reports degraded only when NO streaming catalogue is available (a second healthy provider covers search)
  and only for the catalogue types that were requested (a radio-only search is not degraded).
- The YouTube account identifier is never logged: `ytmusic_signin` logs that a sign-in or refresh happened (and a redacted
  cookie length), not who. Pinned by `tests/test_ytmusic_signin.py`.
- The backend needs a zoe-data restart for the health flag and the in-place save to take effect.
