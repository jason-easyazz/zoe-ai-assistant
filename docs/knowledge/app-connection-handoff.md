---
type: Reference
title: App-connection handoff (QR on the panel, finish on the phone)
description: How connecting an app or account works from the touch panel (B7.5 a+b) — the auth_handoff engine, its status machine and live panel push, the shared authCard, the Telegram "send to my phone" channel, how the music flows report into it, and what is still unverified on a live panel.
tags: [panel, auth, qr, telegram, music, handoff, b7.5]
timestamp: 2026-09-28T00:00:00Z
---

# App-connection handoff

VISION principle 8: the panel is voice first, touch second, never a keyboard.
Connecting an app or account shows a QR on the panel; the person finishes on
their phone; the panel card follows along live. A known member may instead get
a Telegram "send to my phone" link. A guest always gets the QR. The panel PIN is
out of scope here.

## Pieces

| Piece | Where | Role |
|---|---|---|
| Engine | `services/zoe-data/auth_handoff.py` | `start` / `status` / `send_to_phone` / `complete`, plus `mark_ref` / `complete_ref` / `reporter` for provider flows that only hold their phone token |
| Record | table `auth_handoffs` (alembic `0030`) | one row per attempt: kind, provider, member, panel, status, detail, reason, expiry, and `ref` = the provider token's **nonce** (never the token) |
| Panel routes | `services/zoe-data/routers/handoff.py` | `POST /api/handoff/start`, `GET /{id}/status`, `POST /{id}/send-to-phone`, `GET /{id}/qr/{handle}` |
| Panel card | `authCard()` in `services/zoe-ui/dist/touch/home.html` | QR + live status line + "Send to my phone" (only when available) + Close; no text inputs |
| Push relay | `handoff_update` in `services/zoe-ui/dist/js/touch-ui-executor.js` | turns the `ui_action` into a `zoe:handoff` window event that the card listens for |
| Phone page | the provider flow's own page (`dist/setup-music.html` for music) | unchanged; its token stays in the `#fragment` and the `X-Setup-Token` header |

## Status machine

`pending` (QR shown) → `awaiting_phone` (the phone opened the link) →
`completing` (Zoe is finishing with the provider) → `done` | `error`.

- Forward only. A phone reload cannot move `completing` back to `awaiting_phone`.
- `done` is final. An **expiry** (`error`, `reason: expired`, applied when
  `status()` reads an overdue record) is also final. Any other `error`, such as
  a cancelled YouTube Music sign-in or a rejected cookie, can move on to
  `completing`/`done`, because the person may retry on the phone with the same
  valid link.
- Every change is pushed to the panel with
  `broadcast_to_panel(panel_id, "ui_action", {action_type: "handoff_update", …})`.
  The payload is the public view (id, kind, provider, status, detail, reason,
  expiry). It never carries the token or the phone URL. The action id starts
  with `push_`, so it is never acked into the ui-action ledger.
- On `done`/`error` the engine also sends a best-effort `POST http://<pi_host>:8765/wake`
  (`{"hold_s": 20}`), so a dozing panel brightens to show the result. `pi_host`
  comes from `display_preferences.pi_host` for the panel, falling back to
  `ZOE_PI_HOST`. The host must pass `is_allowed_panel_host`. An unreachable
  agent is logged at INFO and never raises.
- The card polls `/status` only every 10 s, as a fallback for a missed push.

## Secrets stay out of URLs, logs and the database

- The phone URL (`…/setup-music.html#provider=…&t=<token>`) is held **in process
  memory only** (`auth_handoff._LINKS`). It is never stored, broadcast or
  returned to the panel. `start` strips it from the route response.
- The panel fetches the QR by an opaque, single-use handle from `setup_qr`
  (`/api/handoff/{id}/qr/{handle}`). A handle issued for another handoff returns 404.
- After a zoe-data restart, a handoff that was in flight keeps its status row,
  but its QR renewal and send-to-phone answer "start again". The in-flight
  OAuth/browser tasks die with the process anyway.
- No handoff route takes a query parameter. This is pinned by
  `tests/test_setup_token_not_in_query.py`.

## Who may do what

- Every panel route needs a signed-in **member** session (`require_signed_in`).
  An anonymous kiosk, a guest session and a degraded-auth principal are all refused.
- Only the member who started a handoff may read its status or send it to their phone.
- The QR route is gated by its single-use handle, which is the same model as
  `/api/music/setup/qr/{handle}`.

## Send to my phone (Telegram)

The member needs a linked Telegram (`user_preferences.prefs.telegram_id`, set by
the `telegram_link` flow). zoe-data needs the bot token in
**`ZOE_TELEGRAM_BOT_TOKEN`**. This is the same token as the `flue-zoe-telegram`
unit's `TELEGRAM_BOT_TOKEN`. Sending a message does not disturb the bot's long
poll. If either is missing, `start` returns `channels: ["qr"]`, the card hides
the button, and `send-to-phone` answers `unavailable`. zoe-data sends with Bot
API `sendMessage` and an "Open on this phone" URL button. If Telegram refuses the
button URL (it rejects some LAN hostnames), it retries once as plain text with
the link. The link passes through Telegram's cloud. It is short-TTL, it still
needs the provider token to be valid, and it only works on the home Wi-Fi.

## Music flows (the first kind)

`kind: "music"` mints a `music_setup` token through
`music_setup.handoff_link(provider, user_id)`. The phone endpoints in
`routers/music_setup.py` report to the handoff by the token's nonce:

| Phone step | Handoff |
|---|---|
| `GET /form` succeeds | `awaiting_phone` |
| `POST /save` (Qobuz form, YT Music cookie) | `completing`, then `done` / `error` from the save result |
| `POST /oauth/start` (Spotify / Tidal / Deezer) | `completing`; `music_oauth.watch()` reports the attempt's end (MA 2.10 FINISH/ABORT event, 2.8.x `auth` action, socket loss, timeout or prune) |
| `POST /browser/start` (YouTube Music) | `completing`; `ytmusic_signin.watch()` reports the session's end (connected, timeout, error, or `/browser/cancel`) |

A free provider (Radio Browser, TuneIn) connects at once, with no handoff and no
QR. Reconnect still happens **in place**: the router and `save_provider` pass
the existing `instance_id`, and the MA 2.10 `reconfigure` flow does not change.
A token minted by the legacy `POST /api/music/setup/start` has no handoff and
reports nothing.

## Shared token mechanics

`music_setup`, `smart_home_setup` and `telegram_link` used to carry three copies
of the HMAC + base64 + single-use ledger. They are now thin adapters over
`auth_handoff.SignedTokens` (the `<b64 JSON claims>.<b64 HMAC>` format, with the
key from `<FLOW>_SECRET` → `ZOE_INTERNAL_TOKEN` → a per-process random),
`SingleUseLedger` (reserve / spend / release) and `b64`/`b64d`. Every wire format
is unchanged. `telegram_link` keeps its compact deep-link codec and its own key.
`tests/test_handoff_token_dedupe.py` pins byte equality against the pre-dedupe
algorithms. `phone_base_url()` is also the one copy of the LAN-origin helper
that both setup routers use.

## Adding a new kind

1. Add a phone-link minter `(provider, user_id) -> {token, ref, path, ttl}` next
   to the flow's token module, and register it in `auth_handoff._KINDS`.
2. Add the kind's policy (validation, the free-provider case) to
   `routers/handoff.py::handoff_start`.
3. Call `mark_ref` / `complete_ref` (or hand a `reporter(...)` to a background
   task) from the flow's phone endpoints.
4. Open it on the panel with `authCard({kind, provider, title, onDone})`.

## Unverified until a live panel run

The Pi was off when this landed. These have not been tested end to end on a
real panel:

- the kiosk receiving `handoff_update` on its `panel_<id>` channel;
- the `/wake` call from the Jetson to the Pi agent;
- a real Telegram send;
- a phone completing each provider against live MA 2.10.3.

Browser behaviour is covered by `services/zoe-ui/dist/test_touch_music_sources.js`
(Playwright, run by hand). Backend behaviour is covered by
`tests/test_auth_handoff.py`, `tests/test_handoff_routes.py` and
`tests/test_migration_0030_auth_handoffs.py`, all `ci_safe`.
