# services/zoe-ui/ — frontend and nginx

## Purpose

The Zoe web frontend. `dist/` is the nginx docroot (hand-maintained HTML/CSS/JS, NOT build output) and `nginx.conf` is the production reverse proxy and static server.

## Ownership

- `dist/*.html` — all pages (chat, dashboard, calendar, lists, memories, journal, touch panel, ...). ALL are critical files.
- `dist/css/**`, `dist/js/**` (including `js/widgets/core/**`), `dist/components/**` — ALL critical files; never delete without the cleanup safety process.
- `dist/sw.js` — service worker with Workbox precache.
- `nginx.conf` — CRITICAL FILE; routes static content, API proxies, module routes, and security headers across three server blocks (80, 443 static; 18790 proxy).

## Local Contracts

- ALWAYS bump `SW_VERSION` in `dist/sw.js` when editing ANY file in the Workbox `precacheAndRoute([...])` array (currently `chat.html`, `/`, and other precached HTML/JS/CSS). Skipping this is the #1 cause of "my changes aren't showing up" on mobile.
- Web push: never re-save an existing browser subscription; `existing.unsubscribe()` before re-subscribing (FCM endpoints expire with HTTP 410). After VAPID rotation, clear stale `push_subscriptions` rows.
- NEVER place an `AGENTS.md` (or any agent/internal doc) inside `dist/` — it is the public docroot. nginx denies `/AGENTS.md` as defense in depth; this doc deliberately sits one level above.
- After editing `nginx.conf`, reload nginx and verify both static server blocks; module routes (`/modules/...`) live here too.

## Work Guidance

Check the browser console (F12) after UI changes; test on mobile where the service worker cache bites hardest.

## Verification

- Load the touched page with devtools network tab confirming fresh assets after an SW_VERSION bump.
- `curl -I http://localhost/AGENTS.md` returns 404.

## Child DOX Index

No child AGENTS.md files (none permitted under `dist/`).
