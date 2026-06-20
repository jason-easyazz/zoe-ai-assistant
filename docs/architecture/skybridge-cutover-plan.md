# Skybridge Cutover Plan — retiring the legacy touch stack

**Status:** proposed (no code applied)
**Owner:** jason
**Context:** The touch panel currently runs two overlapping UI stacks. This document
is the master plan for consolidating onto Skybridge and disabling the legacy
surfaces, pages, and live-sync plumbing. Each phase below maps to a separate,
independently reviewable PR.

## Problem: two complete stacks are both live

| Concern | Legacy stack | Skybridge stack |
|---|---|---|
| Surface | `dashboard.html` + per-domain pages (`calendar/lists/weather/people/notes/journal/timers/music/chat/voice`.html) | single `skybridge.html` |
| Kiosk loads | **yes** (`/opt/TouchKio/config.json` url → `…/touch/dashboard.html`) | only when navigated to |
| Card delivery | `touch-ui-executor.js` + `/api/ui/actions` polling + `/ws/push` | `skybridge.js` / `SkybridgeRenderer` + `/ws/voice/` events + same polling |
| Live data sync | per-resource WS `/api/{calendar,lists,people,reminders,notes,journal}/ws/{user}` via `websocket-sync.js` + `notifications-panel.js` | none — `/api/skybridge/resolve` on demand |
| Voice routing | `_broadcast_weather_ui`→`/touch/weather.html`, `_broadcast_calendar_ui`→`/touch/calendar.html`, `_broadcast_reminder_ui`, `_broadcast_lets_talk_ui` | `_broadcast_skybridge_ui`→`/touch/skybridge.html` |

**Tug-of-war:** the kiosk boots the legacy dashboard, but `voice_command`
(`services/zoe-data/routers/voice_tts.py`) contains *both* routing families.
A weather voice command fires `_broadcast_weather_ui` → `panel_navigate` to
`/touch/weather.html`, yanking the panel off Skybridge. Verified live:
voice "what's the weather" returns the legacy reply and a `voice_weather_card`
action (not `voice_skybridge_card`).

## Keystone gap (must fix before disabling legacy voice routing)

Voice weather/calendar resolve through the **legacy** intent path, not Skybridge —
i.e. `resolve_skybridge_request()` returns `handled=False` for the guest/voice
identity at runtime even though the typed `/api/skybridge/resolve` path (real user)
returns `handled=True`. Root-cause candidate: `_resolve_weather` /
`_resolve_with_db` requiring per-user prefs that a guest panel session lacks, vs the
system-default location used elsewhere. **This must be confirmed in a testable env
(Postgres pool initialised) before Phase 2**, or the cutover will turn voice cards
off instead of moving them to Skybridge.

## Phased PRs

### PR 1 — Kiosk points at Skybridge  *(device config, reversible)*
- Change `/opt/TouchKio/config.json` `url` →
  `https://192.168.1.218/touch/skybridge.html?panel_id=zoe-touch-pi&kiosk=1`.
- Pi-side only (not in this repo's working tree, so it survives the git automation).
- Restart `zoe-kiosk.service`. Instantly reversible by restoring the URL.
- **Acceptance:** panel boots into Skybridge; typed command renders a card
  (already verified working).

### PR 2 — `ZOE_SKYBRIDGE_ONLY` flag: stop legacy domain navigation  *(zoe-data)*
- Add env flag `ZOE_SKYBRIDGE_ONLY` (default off → today's behavior).
- In `voice_command` + the `_broadcast_*_ui` helpers (`voice_tts.py`):
  when the flag is on, route weather/calendar/reminder/lets_talk through
  `_broadcast_skybridge_ui` (target `/touch/skybridge.html`) instead of the
  per-domain `panel_navigate` targets.
- **Depends on the keystone fix** so Skybridge actually resolves voice weather/calendar.
- Flag gating keeps the change fully reversible and reviewable.

### PR 3 — Retire legacy live-sync WebSockets  *(zoe-data + zoe-ui)*
- Once nothing loads dashboard/domain pages, these are dead:
  `/api/{calendar,lists,people,reminders,notes,journal}/ws/{user}` (`main.py`),
  `websocket-sync.js`, `notifications-panel.js`.
- Removing them also eliminates the current `403` handshake noise (these clients
  never send `session_id`).
- Keep `/ws/push` (Skybridge still uses the panel-bound channel) and `/ws/voice/`.

### PR 4 — Decide screen-wake / orb-tap support on Skybridge  *(zoe-ui + Pi)*
- The loopback daemons `:7777` (wake word `/activate`, orb-tap) and `:8765`
  (`zoe-panel-agent` screen keep-awake) are blocked by the page CSP
  (`connect-src 'self' ws: wss:`).
- If tap-to-talk + screen-keep-awake are wanted on Skybridge: add the loopback
  origins to the nginx CSP `connect-src` and exempt `localhost`/`127.0.0.1` from
  auth.js's http→https upgrade (recoverable from reflog commit `cf04978c`).
- If not wanted: leave CSP as-is; spoken wake word still works (daemon-driven).

### PR 5 — Remove legacy pages  *(zoe-ui, last)*
- After PRs 1–4 are stable, delete/redirect the legacy touch pages and their
  widget JS (`dashboard.html`, per-domain `*.html`, `widget-*.js`, `dashboard.js`).
- Stage as redirects to `skybridge.html` first; delete in a follow-up.

## Explicitly OUT of scope (do not touch)
- **Multica webhooks** (`multica_webhook_emitter.py`, `/board/webhook`) — autonomous
  dev/PR pipeline, unrelated to the panel.
- `/ws/voice/`, `/api/skybridge/*`, `/api/ui/actions` polling — Skybridge depends on these.

## Rollback
Every phase is independently reversible: PR 1 by restoring the kiosk URL; PR 2 by
unsetting `ZOE_SKYBRIDGE_ONLY`; PRs 3/5 by revert. Sequence is kiosk → flag →
prune, so the panel is always on a working surface.
