# Skybridge Cutover Plan — retiring the legacy touch stack

**Status:** **PARTIALLY EXECUTED — but the consolidation landed the *other way*.**
**Owner:** jason

> **Read this before following any phase below.** The problem statement (two
> overlapping stacks) was real and *has been resolved* — but not by the plan
> below. This document proposed consolidating **onto `skybridge.html`**. What
> actually shipped consolidated onto the **estate (`touch/home.html`)** and
> retired the Skybridge front-end instead.
>
> **What landed — [#1345](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1345)
> (merged 2026-07-15), "retire the Skybridge front-end (estate is the panel chrome)":**
> - `skybridge.html` (~5.6k lines) + `touch/js/skybridge{,-capabilities,-renderer,-voice}.js`
>   + `touch/css/skybridge-{data-widgets,stage}.css` — **removed**. `skybridge.html`
>   is now only a compat redirect stub → `/touch/home.html`.
> - `dashboard.html` — superseded by the estate.
> - **Kept:** the server-side `/api/skybridge/*` resolve/timers engine
>   (`skybridge_service.py`, `routers/skybridge.py`) that the estate depends on,
>   and the shared design-system trio (`skybridge-ds.css` / `skybridge-type.css` /
>   `skybridge-theme.js`) still loaded by the login page.
>
> So "Skybridge" now names a **server-side resolver engine + design system**, not
> a front-end surface. The phased PRs below are written against the old
> front-end-consolidation direction and are **obsolete as written** — in
> particular PR 1 (point the kiosk at Skybridge), PR 2 (`ZOE_SKYBRIDGE_ONLY`),
> and PR 4 (screen-wake on Skybridge) target a surface that no longer exists.
>
> **Still open** (tracked from [`docs/PLANS.md`](../PLANS.md) → Consolidation):
> converge the 4 card producers onto the one validated component contract, and
> tame the z-index/CSS sprawl. The legacy per-domain pages
> (`calendar/lists/people/notes/journal/...html`) do still exist, so PR 3
> (retire legacy live-sync WebSockets) and PR 5 (remove legacy pages) remain
> directionally valid — re-scope them against the estate before acting.

**Context (historical):** The touch panel ran two overlapping UI stacks. This
document was the master plan for consolidating onto Skybridge and disabling the
legacy surfaces, pages, and live-sync plumbing. Each phase below maps to a
separate, independently reviewable PR.

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

## Keystone finding (root-caused) — legacy nav is Skybridge's silent fallback

Original hypothesis (Skybridge can't resolve voice/guest weather) was **disproven**
by reproduction: with the Postgres pool initialised,
`resolve_skybridge_request("what is the weather", "guest")` returns
`handled=True` with a weather card ("It is 13.0 degrees in Geraldton with
overcast"). So the resolver is fine.

The real cause: in `voice_command` the Skybridge block
(`voice_tts.py` ~3122–3200) is wrapped in `try/except` whose handler only logs at
`logger.debug` ("skybridge fast path failed (non-fatal)"). When the resolver throws
at runtime — most likely the external weather HTTP fetch during load/restart
turbulence — the turn **silently falls through to the legacy intent path**
(`_broadcast_weather_ui` → `panel_navigate` to `/touch/weather.html`). i.e. the
**legacy domain-nav paths double as Skybridge's exception fallback**, which is why
the panel intermittently bounces to legacy pages.

Implication for the cutover: there is **no resolve gap blocking Phase 2**. PR 2 must
(a) raise that swallowed `debug` log to `warning` so these failures are visible, and
(b) make the fallback **degrade within Skybridge** (speak the answer / keep the
surface) instead of navigating to a legacy domain page.

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
- In `voice_command` (`voice_tts.py`): when the flag is on, never emit
  `panel_navigate` to per-domain pages (`/touch/weather.html`, `/touch/calendar.html`, …).
  Weather/calendar/reminder still produce their card, but the panel stays on Skybridge.
- Raise the swallowed Skybridge `except` log (~3204) from `debug` → `warning` so
  resolver failures are observable (see keystone finding).
- On Skybridge-resolve failure with the flag on, degrade in place (speak the answer)
  rather than falling back to legacy navigation.
- No resolve gap to fix first — `resolve_skybridge_request` already handles
  voice/guest weather/calendar/clock. Flag gating keeps it reversible.

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
