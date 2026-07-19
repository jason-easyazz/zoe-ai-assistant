# services/zoe-ui/ — frontend and nginx

## Purpose

The Zoe web frontend. `dist/` is the nginx docroot (hand-maintained HTML/CSS/JS, NOT build output) and `nginx.conf` is the production reverse proxy and static server.

## Ownership

- `dist/*.html` — all pages (chat, dashboard, calendar, lists, memories, journal, touch panel, ...). ALL are critical files.
- `dist/css/**`, `dist/js/**` (including `js/widgets/core/**`), `dist/components/**` — ALL critical files; never delete without the cleanup safety process.
- `dist/sw.js` — service worker with Workbox precache.
- `dist/lib/**` — pinned third-party vendor source, served straight to the browser and **tracked in git** (`gridstack/`, and `livekit/livekit-client.umd.min.js` — livekit-client 2.5.0, Apache-2.0, from jsDelivr `/npm/livekit-client@2.5.0/dist/`). Nothing fetches or builds these: if it is not committed, a fresh clone serves a 404. `.gitignore` ignores the rest of `dist/lib/livekit/` (source maps etc.), so add a negation when vendoring a new file there.
- `dist/workbox/` — VENDORED Workbox 7.0.0 runtime (`workbox-sw.js` + the `.prod.js` modules the SW uses). Third-party code: refresh it, don't hand-edit it.
- `nginx.conf` — CRITICAL FILE; routes static content, API proxies, module routes, and security headers across three server blocks (80, 443 static; 18790 proxy).

## Local Contracts

- ALWAYS bump `SW_VERSION` in `dist/sw.js` when editing ANY file in the Workbox `precacheAndRoute([...])` array (currently `chat.html`, `/`, and other precached HTML/JS/CSS). Skipping this is the #1 cause of "my changes aren't showing up" on mobile.
- **Workbox is served LOCALLY from `dist/workbox/`, never from a CDN** (Zoe is local-first: the box may be offline, and a CDN import pings Google from every client on every SW boot). Two settings hold this together and BOTH are load-bearing:
  - `importScripts('/workbox/workbox-sw.js')`, and
  - `workbox.setConfig({ modulePathPrefix: '/workbox/' })` — `workbox-sw.js` is only a lazy *loader*; on first access of `workbox.core` / `workbox.routing` / … it `importScripts`es that module, defaulting to Google's CDN. Vendoring the loader alone does NOT remove the CDN dependency.
  - `debug: false` pins the `prod` variant; only `.prod.js` files are vendored, so `debug: true` would request non-existent `.dev.js`.
  - Using a NEW `workbox.<namespace>` means vendoring that module too, or it 404s at runtime.
  - `tests/unit/test_sw_workbox_local.py` enforces all of the above, plus that the nginx CSP keeps `storage.googleapis.com` out of `script-src`.
- Refresh/upgrade Workbox with `npx workbox-cli@<version> copyLibraries <tmp>`, then copy `workbox-sw.js` + the needed `.prod.js` modules into `dist/workbox/` (the `sourceMappingURL` trailer is stripped because `.map` files are not vendored).
- Web push: never re-save an existing browser subscription; `existing.unsubscribe()` before re-subscribing (FCM endpoints expire with HTTP 410). After VAPID rotation, clear stale `push_subscriptions` rows.
- The estate (`dist/touch/home.html`) may load the LiveKit client **only on entry to Ask-card conversation mode**, never on boot: requesting `/api/voice/livekit-token` starts the on-demand ~560MB LiveKit container, and the box is memory-tight. Every exit path (stop, navigate, sleep, idle, pagehide/unload, error) must release the mic and disconnect — a live session also suppresses ambient-return and idle→sleep, so nothing else will tear it down for you.
- The estate dock renders **operator-pinned controls** from `GET /api/panels/{device_id}/config` (`device_id` = `_panelDevId()`). Rules that are load-bearing and easy to break:
  - **Branch on `kind`** (`toggle` | `scene` | `temp`), never on key existence — every pin carries the full key set, with `min`/`max`/`step`/`unit`/`setpoint` null on non-temp pins.
  - **Render the server's `icon`**; never derive one from the entity domain. `input_boolean.fan` and `input_boolean.tv` share a domain with the lights, so a domain-derived icon draws a lightbulb on a fan.
  - `pins_configured:false` keeps the legacy `slice(0,3)` fallback; `true` with an empty list means **show nothing**. They are not the same state.
  - `ha_available:false` still returns pins (with `state:null`) — render them muted, never blank the dock.
  - A **scene never shows state** (its HA state is `unknown` or a last-fired timestamp) and flashes accent blue, not the light pill's amber.
  - Temp bounds come from the pin (`min`/`max`/`step`, live 16/30/0.5) and arrive as strings — parse with `parseFloat`; writes use the pin's `write_action`.
  - `refreshHA()` must keep skipping re-render while `.pc.temp.open` exists, or a poll destroys the popover mid-drag. Pins are polled through the same path, so this guard covers them too.
  - Verify with `node dist/test_touch_dock_pins.js` (headless 1280×720; asserts its fixtures against the live API and writes screenshots). There is no JS lane in CI — this is a local gate.
- The estate's **music surface condenses the dock** (`#dock.solo`): `#dbody` and `.ddiv` are hidden and only `#apps` shows, moved beside `#orb`. Rules that are load-bearing:
  - Music is the ONLY surface that condenses — it is the only one carrying its own transport. Every other surface, **including Browse**, keeps the full dock. `renderDock()` still runs underneath, so leaving music restores live pins rather than a stale snapshot.
  - **Browse is a surface, not an overlay.** Nothing overlays the music card any more, so the dock now-playing chip's rule is exactly `_cur==='music'` → no chip. Do not reintroduce an overlay here: an "…except while the overlay is open" exception is what that costs.
  - `CF_K` is the **single knob** for the Cover Flow scale. It drives x/z in the script AND `--cfcard`/`--cfpersp` via `cfApplyScale()`; the CSS carries fallbacks but must never hardcode a size, or changing CF_K shears the projection. The cap is `.cfmeta`'s top, NOT the dock: the centre cover's projected bottom is `cf.top + 336.67*CF_K`.
  - Verify with `node dist/test_touch_music_breathe.js` (headless 1280×720; asserts fixtures against the live music/panel APIs, bounding-box collision checks, screenshots). Local gate, not CI. Its assertions are mutation-tested — if you change one, re-break the behaviour and confirm it goes red. Two traps this suite has already fallen into: a *visibility* check for the now-playing chip passes for the wrong reason (the condensed dock hides it), so assert EXISTENCE; and stub cover art must be opaque and off-origin-matched, or the fan screenshots blank while every assertion passes.
  - The box is memory-tight: an OOM-killed harness run (exit 137) orphans its chromium children and cascades into further OOMs, so reap `chrome-linux/chrome` before trusting a red.
- NEVER place an `AGENTS.md` (or any agent/internal doc) inside `dist/` — it is the public docroot. nginx denies `/AGENTS.md` as defense in depth; this doc deliberately sits one level above.
- After editing `nginx.conf`, reload nginx and verify both static server blocks; module routes (`/modules/...`) live here too.

## Work Guidance

Check the browser console (F12) after UI changes; test on mobile where the service worker cache bites hardest.

## Verification

- Load the touched page with devtools network tab confirming fresh assets after an SW_VERSION bump.
- `curl -I http://localhost/AGENTS.md` returns 404.

## Child DOX Index

No child AGENTS.md files (none permitted under `dist/`).
