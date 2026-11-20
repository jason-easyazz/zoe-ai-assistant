---
type: Reference
title: Zoe OS lookups and messages doors - what leaves the box, and what the kernel knows about it
description: ZOE_OS_LOOKUPS=kernel and ZOE_OS_MESSAGES=kernel (both default off). Every outbound path for the weather, web search, page fetches and messages; which are a member's and which are Zoe's own; lookups told to the kernel afterwards (who and the class, never the query or URL); a member's send asked first (channel class and member ids, never the words or an address); findings (no child rule for the web; no member-driven send to an outside address exists).
tags: [zoe-os, kernel, lookups, web, weather, messages, telegram, push, intercom, door, egress, flag-dark]
timestamp: 2026-11-20T00:00:00Z
---

# Zoe OS lookups and messages doors

**Status 2026-11-20: built, flag-dark (both flags unset = `off`). Nothing on this page is live.**

## 1. Everything that leaves the box for a lookup [code]

| Class | Sender (module: function) | Goes to | Carries |
|---|---|---|---|
| weather | `routers/weather`: `_fetch_openmeteo_current`, `_fetch_openmeteo_forecast`, `_geocode`, `search_locations` | api.open-meteo.com, geocoding-api.open-meteo.com | latitude and longitude, or a place name typed into the search |
| weather | `routers/weather`: `_fetch_owm_current`, `_fetch_owm_forecast`, `search_locations` (only with an OpenWeatherMap key) | api.openweathermap.org | latitude and longitude or a place name, and the API key |
| weather | `mcp_server`: the brain's `weather_current` and `weather_forecast` tools | api.open-meteo.com | latitude and longitude |
| web_search | `research_evidence`: `_fetch_ddg` | duckduckgo.com | the query |
| web_search | `web_search_provider.tavily_search_outcome` (only with a Tavily key) | the Tavily API | the query, and the API key |
| web_search | `zoe_agent`: `_ddg_search_sync`, `_cloak_search`, `_google_maps_local_search` | duckduckgo.com (three hosts), google.com/maps (through the browser) | the query |
| web_fetch | `zoe_agent`: `_web_browse`, `_visit_with_search`; `browser_broker.fetch_page_text`; `research_evidence._fetch_page_price` | any site a result or the member named, through the local CloakBrowser | the URL (and whatever the site's own page requests) |

There is no separate path for the time: it is read from the box's clock.

**Entry points** (each calls `lookup_door.sent(<class>)` as its first statement): the six weather functions and the two
weather tools; `zoe_agent._web_search_ddg`, `_web_research`, `_web_browse`; `browser_broker.search_web`, `fetch_page_text`;
`research_evidence.fetch_web_fallback`, `fetch_web_fallback_results`. The senders were NOT merged into one per class: they are
eleven functions on three transports (urllib in worker threads, httpx, the browser), and merging them is a rewrite of the
research flow. A test lists every sender and fails on a new one.

## 2. The lookups door [code; `lookup_door.py`]

* **Told afterwards, never asked, never delayed.** Who, and the class (`lookup.weather` / `lookup.web_search` /
  `lookup.web_fetch`). Never the query, the place, the URL or the result: there is no field for them. One record per class
  per request, however many fetches a search fans out into.
* **Who** is the request's or turn's caller (`kernel_door.acting`). Nobody noted = automatic: the wall panel's weather card
  (its routes are not signed in), a background refresh. Not recorded.
* **One refusal:** a caller zoe-data KNOWS is off the kernel's roster is refused before anything is sent (it would leave the
  box in their name). Decided from the roster already in memory, with no kernel call.
* **Finding: there is no child rule for the web today.** No safe-search setting, no per-child block, in any sender. So a
  child's lookup is recorded like anyone's and is not asked first. If a rule is wanted it must be built in zoe-data first.

## 3. Everything that leaves the box as a message [code]

| Channel | Sender | Who starts it | Goes to |
|---|---|---|---|
| Telegram | `telegram_notify.send` | Zoe (reminders, change notices, the day-review digest and raise) | the bot's loopback route, which messages a LINKED member only; then Telegram |
| Telegram | `auth_handoff.send_to_phone` | a member ("send the sign-in link to my phone") | the member's OWN linked chat (through the bot, or straight to api.telegram.org with the legacy token) |
| Web push | `routers/push.send_push_to_user` | Zoe (reminders, proactive nudges) | the browser vendor's push service for that member's own subscriptions |
| Panels | `intercom.handle` (announce, ring; `ZOE_INTERCOM`, default off) | a member | the house's own panels, over the LAN |
| Panels | the brain's `panel_announce` tool | a member (through the brain), or the bare panel | a panel toast, over the LAN |
| Panels | `voice_announce`, `proactive.engine` | Zoe (reminders, alarms, arrivals) | the house's own panels |

There is no email and no SMS sender. **No path lets a member send to another person's phone or to an address outside the
household.**

## 4. The messages door [code; `message_door.py`]

A member-driven send is ASKED FIRST at the three places that exist: the intercom (`send.panel`), the panel toast
(`send.panel`), the sign-in link (`send.telegram`, recipient = the member themself). A refusal sends nothing and the member
is told why. Kernel down or slow: the send proceeds, `not_checked`. zoe-data keeps its own rules and makes them first (guests
cannot use the intercom; only an adult drops in; quiet hours; only a linked member is ever messaged).

`send.external` (an address that is not a known contact or channel of the household) is never sent unchecked: a kernel that
cannot be asked means NO. Nothing sends that today; the rule is in place for the first sender that does. `send.push` is
reserved the same way.

Zoe's own notifications are neither asked nor recorded.

| Who | `send.panel` | `send.telegram` / `send.push` | `send.external` |
|---|---|---|---|
| an adult | allow | allow | allow |
| a child, a guest on the roster | allow | to themself: allow; to anyone else: refuse | refuse |
| the panel, nobody identified | allow | refuse | refuse |
| not on the roster | refuse | refuse | refuse |

**On the wire:** the channel class, the acting member's id, and the recipient's member id when the recipient is a known
person. Never the words, a phone number, a chat id or an address.

## 5. Switch-on (owner steps)

1. This change deployed and the kernel unit restarted on it.
2. The roster current, with roles.
3. `ZOE_OS_LOOKUPS=kernel` and / or `ZOE_OS_MESSAGES=kernel` in zoe-data's environment; restart zoe-data.
4. Live check: a signed-in member asks for the weather and a web search (two `recorded` lines, no query in either); the
   sign-in link sent to their own phone (`allow`, `done`); with the kernel stopped, both still work.

## 6. Not done, not proven

* Not run live; the voice replay has not been run (the spoken weather, web and intercom paths are touched).
* No fixture of outbound calls against main was made for lookups: no sender moved, the door is one call that returns after a
  flag read with the flag off, and the senders' own suites run unchanged.
* A lookup started in a worker thread with no caller noted is not recorded. The entry points are all in the request's own
  context; a new one must be too.
* The brain's tool dispatcher now notes its member when nothing upstream did, so a tool call that reaches music or the
  house outside the service's own dispatch is treated as that member's, not as automatic.
