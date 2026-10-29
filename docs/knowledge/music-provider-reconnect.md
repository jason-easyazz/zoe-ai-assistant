---
type: Reference
title: Music search finds nothing - provider reconnect
description: When jukebox or panel music search "finds nothing" it is usually Music Assistant's YouTube Music provider being down, not a Zoe bug; how to confirm, the panel QR reconnect, and the in-place-save invariants not to regress.
tags: [music, music-assistant, runbook, incident-signature]
timestamp: 2026-10-29T00:00:00Z
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
phone and sign in. The credential only touches the provider's own sign-in page; Zoe never sees it. This follows the standing
rule that connecting an account hands off to the phone. Saying "connect youtube music" by voice works too: a connected provider whose `needs_attention` is set gets the same QR hand-off
("YouTube Music needs signing in again - scan the code on the panel with your phone"), and the phone's save reconnects the
existing instance in place. A voice turn does not render the Skybridge QR card itself, so `voice_tts._broadcast_skybridge_ui`
navigates the panel to `home.html?domain=music_sources&connect=<provider>` and home.html opens the shared `authCard` there (the
same QR as the Sources tab; also used for a first connect). If the navigation was not delivered to a live panel, the reply says
to open Music then Sources on the panel instead of promising a code. A healthy connected provider still answers "already connected". Pinned by
`services/zoe-data/tests/test_music_voice_reconnect.py`.

## Invariants (do not regress)

- Reconnect is IN PLACE: both `setup_save` and `music_oauth._run_flow` pass the existing `provider_instance_id` to
  `save_provider`; otherwise MA mints a DUPLICATE provider and leaves the broken one.
- `save_provider` on reconnect merges new auth over the instance's CURRENT `values` (from `config/providers`), not fresh
  defaults - otherwise it resets library-sync and other settings.
- `catalog_health` reports degraded only when NO streaming catalogue is available (a second healthy provider covers search)
  and only for the catalogue types that were requested (a radio-only search is not degraded).
- The backend needs a zoe-data restart for the health flag and the in-place save to take effect.
