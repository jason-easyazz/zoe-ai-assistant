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
- The estate dock **and the sleep screen** (#1444) render **operator-pinned controls** from `GET /api/panels/{device_id}/config` (`device_id` = `_panelDevId()`). Rules that are load-bearing and easy to break — they apply to both surfaces:
  - **Branch on `kind`** (`toggle` | `scene` | `temp`), never on key existence — every pin carries the full key set, with `min`/`max`/`step`/`unit`/`setpoint` null on non-temp pins.
  - **Render the server's `icon`**; never derive one from the entity domain. `input_boolean.fan` and `input_boolean.tv` share a domain with the lights, so a domain-derived icon draws a lightbulb on a fan.
  - `pins_configured:false` keeps the legacy `slice(0,3)` fallback; `true` with an empty list means **show nothing**. They are not the same state.
  - `ha_available:false` still returns pins (with `state:null`) — render them muted, never blank the dock.
  - A **scene never shows state** (its HA state is `unknown` or a last-fired timestamp) and flashes accent blue, not the light pill's amber.
  - Temp bounds come from the pin (`min`/`max`/`step`, live 16/30/0.5) and arrive as strings — parse with `parseFloat`; writes use the pin's `write_action`.
  - `refreshHA()` must keep skipping re-render while `.pc.temp.open` exists, or a poll destroys the popover mid-drag. Pins are polled through the same path, so this guard covers them too.
  - The sleep screen keeps its own **night-sized** tiles and `.slp`-scoped popover styling rather than reusing the dock's markup — those pills are `#dock`-scoped and sized for a glance at a lit screen, while these are hit in the dark. `wireTempTile` is element-scoped, so the temp drag works on both.
  - Verify with `node dist/test_touch_dock_pins.js` (headless 1280×720; asserts its fixtures against the live API and writes screenshots, and covers the sleep surface too). There is no JS lane in CI — this is a local gate. Reaching the night clock in a harness needs BOTH clocks driven (the idle path awaits a real request before `show('sleep')`), and `.slp` is a zero-size box — wait on `#slClock`, which has a real one.
- The estate **music card** has four contracts that are easy to break and cheap to keep:
  - **The favourite heart follows the FOCUSED cover, not what is playing.** Its uri and its lit state both come from `_cf.items[cfF()].media_item` (`.uri`, `.favorite`) and it is painted inside `cfPaintMeta()` — the function that writes the title/artist directly above it — so the button and its label cannot disagree. `GET /api/music/now-playing` returns **no `uri` field**; reading one is how the heart shipped permanently dead (every tap short-circuited to "Nothing playing" without ever calling the API). Un-favourite posts `/api/music/unfavorite`, never a second `/favorite`.
  - **A poll must never repaint a control a finger is holding.** `_seek` is non-null for the life of a scrub drag and BOTH repainters (`tickMusic` 1s, `loadMusic` 5s) bail while it is set; the volume slider guards on `document.activeElement` **plus** a `dataset.drag` flag, because activeElement alone is false during a touch drag. Same rule as `refreshHA()`'s `.pc.temp.open` guard above.
  - **Volume is behind a popover; ∞ "keep playing" holds the left flank.** The speaker tile `.mvolt` sits in the transport and toggles `.open` on itself to reveal `.vpop` — the `.pc.temp`/`.tpop` pattern, including "a click inside the popover does not dismiss it". `.mvolt` is a **`div`, not a `button`**, and both reasons are load-bearing: `#mTransport`'s delegated handler matches `closest('button')`, so a div cannot fall through to `musicControl()`; and a native range input inside a `<button>` is invalid interactive content. Unlike the dock, `.open` needs no explicit repaint guard — `tickMusic`/`loadMusic` patch values in place and only `show('music')` rebuilds the card — so **do not move `.open` onto anything a repainter rewrites**. `#mDS` left the transport, so it is wired directly instead of riding that delegation.
  - **Layout here is measured, not eyeballed.** At `CF_K=1` the covers fan from y39 (centre) out to y78/y91/y103 and end at y412, so the top strip cannot host a 48px control without clipping artwork — the **∞ pill** lives on the scrub's left flank (x44, clear of `.mscrub` at x360; it inherited that slot from the volume pill). The open `.vpop` must clear `.mscrub` **and** the duration label: at its first offset it sat across the seek bar, passed every assertion, and only the screenshot showed it. The transport still carries the inert `.tsp` spacer so its 6th item (now the speaker) does not shove play/pause off-centre.
  - Verify with `node dist/test_touch_music_polish.js` and `node dist/test_touch_clean_interface.js` (headless 1280×720; assert fixtures against the live `/api/music/now-playing` + `/api/music/queue/{id}`, including that now-playing still has no `uri`, and write screenshots). Local gates — no JS lane in CI. **Look at the PNGs**: the first volume placement passed every assertion while sitting on top of the cover art, and its replacement passed again while covering the seek bar. Two traps these suites have already hit: a collision check against `.cfmeta`/`.mtitle` must measure the **inked text** (a Range over the contents), not the block box — those are `left:0;right:0` with centred text, so their boxes span the card and overlap everything; and `/api/music/queue/{id}` must be stubbed with **real items**, because an empty queue renders no covers and every "clears the artwork" assertion then passes against nothing.
- The estate's **music surface condenses the dock** (`#dock.solo`): `#dbody` and `.ddiv` are hidden and only `#apps` shows, moved beside `#orb`. Rules that are load-bearing:
  - Music is the ONLY surface that condenses — it is the only one carrying its own transport. Every other surface, **including Browse**, keeps the full dock. `renderDock()` still runs underneath, so leaving music restores live pins rather than a stale snapshot.
  - **Browse is a surface, not an overlay.** Nothing overlays the music card any more, so the dock now-playing chip's rule is exactly `_cur==='music'` → no chip. Do not reintroduce an overlay here: an "…except while the overlay is open" exception is what that costs.
  - `CF_K` is the **single knob** for the Cover Flow scale. It drives x/z in the script AND `--cfcard`/`--cfpersp` via `cfApplyScale()`; the CSS carries fallbacks but must never hardcode a size, or changing CF_K shears the projection. The cap is `.cfmeta`'s top, NOT the dock: the centre cover's projected bottom is `cf.top + 336.67*CF_K`.
  - Verify with `node dist/test_touch_music_breathe.js` (headless 1280×720; asserts fixtures against the live music/panel APIs, bounding-box collision checks, screenshots). Local gate, not CI. Its assertions are mutation-tested — if you change one, re-break the behaviour and confirm it goes red. Two traps this suite has already fallen into: a *visibility* check for the now-playing chip passes for the wrong reason (the condensed dock hides it), so assert EXISTENCE; and stub cover art must be opaque and off-origin-matched, or the fan screenshots blank while every assertion passes.
  - The box is memory-tight: an OOM-killed harness run (exit 137) orphans its chromium children and cascades into further OOMs, so reap `chrome-linux/chrome` before trusting a red.
- The estate's **calendar week and month views are TODAY-ANCHORED and scroll**; only `day` stays a static list. Rules that are load-bearing:
  - Week is a horizontal strip of `CAL_WEEK_COLS` (4) columns at ~294px; month is `CAL_MONTH_WEEKS` (3) rolling Mon..Sun rows of ~167×144 cells. Both open with today at the leading edge and scroll BACK to `CAL_BACK_DAYS` — the past is de-emphasised (`.past`/`.dim`), never hard-stopped at today.
  - Month rows are **rolling weeks from today, not the calendar month**, so a six-row month cannot occur and `maxChips` no longer varies: `CAL_MONTH_CHIPS` is a flat 4. Do not reintroduce a `weeks>5` squeeze.
  - `grid-auto-rows` on `.calmonth` must be a **fixed** length, never `minmax(0,…)`: with a definite grid height, minmax lets every row shrink to fit instead of overflowing, which collapses cells to ~39px and silently removes the scroll entirely.
  - `.calmonth` needs `scroll-padding-top` = weekday-strip height + row gap (31px). `scroll-snap-align:start` otherwise snaps a row to the container top — *under* the sticky `.cmh` strip — hiding its date numbers and undoing the anchor.
  - `calAnchorToday()` corrects in **two passes** for month: a `position:sticky` strip's resting box is only knowable after a scroll has actually happened.
  - **Leave `touch-action` at its default** on `.calweek`/`.calmonth`. This is the Cover Flow trap from the other side — the container's `touch-action` decides who owns the gesture, and these want the *browser* to own it so native momentum works. `overscroll-behavior`, not `touch-action`, is what stops a fling chaining to the surface behind.
  - ONE fetch spans the whole scrollable window (`calWindow()`); scrolling issues no requests at all. Changing that window is behavioural — `/api/calendar/events` filters on `start_date` ALONE, so an event that merely *overlaps* the window is not returned.
  - Verify with `node dist/test_touch_calendar_anchor.js` (headless 1280×720; screenshots; local gate, not CI). Fixtures are derived from `routers/calendar.py` + `alembic/0001_initial_schema.py` because the endpoint answers `{"events":[]}` unauthenticated and cannot confirm the shape. Its stub **filters by the query string exactly as the router does** — a stub that returns everything makes the fetch window untestable, which is how a deliberately narrowed fetch first passed green. Assertions are mutation-tested; re-break a behaviour and confirm red before changing one.
- **No card carries a settings cog.** All 13 `.fcog` buttons were identical `show('settings')` shortcuts — card-scoped settings were never actually built — so they were removed in favour of one route: the launcher's Settings tile (`DESTS`/`ORDER`). Do not reintroduce per-card chrome for settings; if a card genuinely needs its own options, that is a new design decision, not a cog. Two navigation facts a harness needs: `home` is deliberately **not** a launcher destination (it is the `#home` corner button), and the **sleep surface hides the dock entirely** — the only way off it is tapping the night clock.
- NEVER place an `AGENTS.md` (or any agent/internal doc) inside `dist/` — it is the public docroot. nginx denies `/AGENTS.md` as defense in depth; this doc deliberately sits one level above.
- After editing `nginx.conf`, reload nginx and verify both static server blocks; module routes (`/modules/...`) live here too.

## Work Guidance

Check the browser console (F12) after UI changes; test on mobile where the service worker cache bites hardest.

## Verification

- Load the touched page with devtools network tab confirming fresh assets after an SW_VERSION bump.
- `curl -I http://localhost/AGENTS.md` returns 404.

## Child DOX Index

No child AGENTS.md files (none permitted under `dist/`).
