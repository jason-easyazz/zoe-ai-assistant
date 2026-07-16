# services/zoe-ui/ — frontend and nginx

## Purpose

The Zoe web frontend. `dist/` is the nginx docroot (hand-maintained HTML/CSS/JS, NOT build output) and `nginx.conf` is the production reverse proxy and static server.

## Ownership

- `dist/*.html` — all pages (chat, dashboard, calendar, lists, memories, journal, touch panel, ...). ALL are critical files.
- `dist/css/**`, `dist/js/**` (including `js/widgets/core/**`), `dist/components/**` — ALL critical files; never delete without the cleanup safety process.
- `dist/sw.js` — service worker with Workbox precache.
- `dist/lib/**` — pinned third-party vendor source, served straight to the browser and **tracked in git** (`gridstack/`, and `livekit/livekit-client.umd.min.js` — livekit-client 2.5.0, Apache-2.0, from jsDelivr `/npm/livekit-client@2.5.0/dist/`). Nothing fetches or builds these: if it is not committed, a fresh clone serves a 404. `.gitignore` ignores the rest of `dist/lib/livekit/` (source maps etc.), so add a negation when vendoring a new file there.
- `nginx.conf` — CRITICAL FILE; routes static content, API proxies, module routes, and security headers across three server blocks (80, 443 static; 18790 proxy).

## Local Contracts

- ALWAYS bump `SW_VERSION` in `dist/sw.js` when editing ANY file in the Workbox `precacheAndRoute([...])` array (currently `chat.html`, `/`, and other precached HTML/JS/CSS). Skipping this is the #1 cause of "my changes aren't showing up" on mobile.
- Web push: never re-save an existing browser subscription; `existing.unsubscribe()` before re-subscribing (FCM endpoints expire with HTTP 410). After VAPID rotation, clear stale `push_subscriptions` rows.
- The estate (`dist/touch/home.html`) may load the LiveKit client **only on entry to Ask-card conversation mode**, never on boot: requesting `/api/voice/livekit-token` starts the on-demand ~560MB LiveKit container, and the box is memory-tight. Every exit path (stop, navigate, sleep, idle, pagehide/unload, error) must release the mic and disconnect — a live session also suppresses ambient-return and idle→sleep, so nothing else will tear it down for you.
- NEVER place an `AGENTS.md` (or any agent/internal doc) inside `dist/` — it is the public docroot. nginx denies `/AGENTS.md` as defense in depth; this doc deliberately sits one level above.
- After editing `nginx.conf`, reload nginx and verify both static server blocks; module routes (`/modules/...`) live here too.

## Work Guidance

Check the browser console (F12) after UI changes; test on mobile where the service worker cache bites hardest.

## Verification

- Load the touched page with devtools network tab confirming fresh assets after an SW_VERSION bump.
- `curl -I http://localhost/AGENTS.md` returns 404.

## Child DOX Index

No child AGENTS.md files (none permitted under `dist/`).
