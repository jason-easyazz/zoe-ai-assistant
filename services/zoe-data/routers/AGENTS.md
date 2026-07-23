# services/zoe-data/routers/ — API routers

## Purpose

FastAPI routers for every Zoe API domain: chat, calendar, lists, memories, reminders, notifications, push, voice, weather, skybridge, system, and more.

## Ownership

- `chat.py` — THE ONLY production chat router (intent fast path + agent path via `intent_router`, `ag_ui_stream`; it still CONTAINS the OpenClaw/Hermes lanes via `openclaw_ws`, but those are retirement targets per the 2026-07-22 decision — do not extend them). CRITICAL FILE.
- `system.py` — system/status endpoints. CRITICAL FILE.
- `panel_config.py` — per-panel config (`GET`/`PUT /api/panels/{device_id}/config`): the panel's room, its default speaker, and its pinned dock controls. Storage is the `panels` row (`location` + `default_player` + `pinned`), keyed by `panel_id` == the panel's `device_id`. Also serves `GET /api/panels/{device_id}/sleep-gate` — whether the kiosk should stay awake because its room looks occupied.
- `rooms.py` — Zoe-owned rooms (`/api/rooms`): create a room, put devices in it. Storage is `rooms` + `room_devices` (alembic 0026), plus `panels.room_id`.
- One router module per domain (`calendar.py`, `lists.py`, `memories.py`, `journal.py`, `music.py`, `push.py`, `skybridge.py`, ...).

## Local Contracts

- NEVER create `chat_v2.py`, `chat_new.py`, `chat_optimized.py`, or any parallel chat router. Use git branches, not file duplication.
- No hardcoded NLU command detection (if "add" in message and "shopping" in message...) in `chat.py` or any router; natural-language understanding goes through `intent_router.py` patterns or Zoe Agent. (Hermes/OpenClaw are retirement targets — never new destinations.)
- Validate user input at the API boundary; parameterized queries only.
- Routers hold domain policy; reusable mechanics (provider calls, parsing, payload transforms) belong in service-layer helpers, not duplicated across routers.
- **Panel-scoped settings live in ONE place per concern — do not add a second store.** `panels.location` is THE panel location (it predates `panel_config.py`); `display_preferences` (`system.py`) stays the *display* store (brightness/idle/off). A new panel-scoped fact goes in the `panels` row.
- `panel_config.py` invariants (each is load-bearing and non-obvious — a "cleanup" that flattens one reintroduces a shipped bug):
  - **A pin is a read/write PAIR.** The thermostat is two entities (`sensor.current_temperature` read + `input_number.thermostat_temperature` write); a single `entity_id` cannot express it. The `{entity_id}` input form is sugar that canonicalises to an equal pair — the response is ALWAYS the pair.
  - **Never validate `entity_id` against a domain allow-list.** This house has zero `light.*`/`climate.*`; its controls are `input_boolean.*`/`input_number.*`/`scene.*`. Shape (`domain.object_id`) only.
  - **`kind` and `icon` resolve server-side, never from the domain.** `input_boolean.fan`/`.tv` share the lights' domain; HA's own `attributes.icon` is the truth.
  - **`pinned` NULL != `[]`.** NULL = never configured (dock falls back to its own default); `[]` = the operator explicitly pinned nothing. `pins_configured` carries the distinction.
  - **An empty MA player list means "cannot validate", not "no players".** `music_service._ma` never raises — it returns None on transport failure, which `get_players()` turns into `[]`. Treating `[]` as an empty set rejects every id and locks the operator out during an MA outage.
  - **The sleep gate FAILS TOWARD SLEEPING.** `GET /{device_id}/sleep-gate` answers "is this panel's room occupied" from the room's own toggles (`light`/`switch`/`input_boolean` = `on`), resolved via `rooms.room_entity_ids_for_panel` — there is NO presence sensor in this house (44 HA entities, zero `binary_sensor`), so do not "improve" this to look for one. Every unknown (HA unreachable, panel in no room, stale entity) returns `block: false`, i.e. SLEEP: a panel that latches awake because a lookup failed burns its screen all night. Readings (`sensor`/`input_number`) are never occupancy. The client races this against the music check with a timeout that decides on the votes ALREADY RECEIVED — a hard `false` there would discard a "music is playing" vote and sleep mid-song.
  - **PUT writes ONLY the columns the request supplied** (`ON CONFLICT DO UPDATE SET` built from the fixed `_WRITABLE` whitelist, never from body keys) and returns the post-write row via `RETURNING`. Do not "simplify" this back to a read-merge-write that always writes all three columns — concurrent PUTs of different fields would clobber each other.
- `rooms.py` invariants — **a room is a ZOE record, not a mirror of Home Assistant.** Zoe is the product; HA is an organ she hides for a normal user and opens for a power user, so a room must be creatable with no HA involvement at all.
  - **`ha_area_id` is OPTIONAL enrichment, never the source of truth.** NULL is a completely normal room, not an unconfigured one. It exists so a power user who already keeps HA areas can link one (and later import its devices); nothing may start requiring it.
  - **A device is in exactly ONE room** — `room_devices.entity_id` is UNIQUE across the whole table, not per-room. "The light in HERE" needs a single non-arbitrary answer. Consequently linking a device that already sits in another room MOVES it (the writer deletes the old row first); a plain INSERT would 500 on an ordinary move, and rejecting it would make the user hunt for its previous room.
  - **Never validate `entity_id` against a domain allow-list** — the same rule `panel_config.py` carries, for the same reason (this house has zero `light.*`).
  - **A device HA does not return is KEPT and marked `available:false`, never dropped** — the opposite of a stale *pin*. A pin that cannot resolve is unrenderable, but a room membership is the user's own record and must outlive HA's current view of it, or an HA blip silently empties their rooms.
  - **`slug` is not re-derived on rename.** It is the stable key a panel or intent points at; rotating it on a rename would silently unbind them.
  - **`GET /unassigned` filters to `_PICKABLE_DOMAINS`; storage does NOT.** The picker is a suggestion list, so it hides the diagnostic noise (this house exposes 48 entities — `event.backup_automatic_backup`, every `sensor.backup_*`, the assist-satellite plumbing — and offering those made it unusable). `normalize_entity_id` stays shape-only, so anything remains storable by an importer or a power user with an odd device. Narrowing storage to the picker's list would be the domain allow-list this file forbids twice over.
  - **A panel's room is `panels.room_id`, written through `PUT /api/panels/{id}/config`** (whitelisted in `panel_config._WRITABLE`). `panels.location` is the older free-text label and is deliberately NOT mirrored — nothing reads it for behaviour, so syncing them would be a dual-write with no consumer. `room_id` is authoritative; the config payload carries `room_id`/`room_name`/`room_slug` flat.
  - **A deleted room degrades a panel to "no room", never an error.** `_load_room` returns None for a dangling id, because a panel that cannot boot its own config over a room someone deleted is worse than one that reports having no room.
  - Rooms are the eventual replacement for `smart_home_service._room_of()` name-parsing (which cannot see a device like `switch.bedroom_1_switch_1`), but that path is still live — do not delete it until the Rooms surface and `_group_rooms()` read this model.
  - **Room CRUD is API-only by design today — the chat/voice intent path is DEFERRED, not forgotten** (`.greptile` `chat-voice-first`, the same deferral the music-grouping entry below carries). Nothing is user-visible yet: no surface consumes these endpoints, so there is no feature to operate by voice. The sequencing is deliberate — the settings UI comes first, then voice *uses* rooms to resolve "the light in here" (voice-path work, so it must be replay-gated). Speaking room ADMIN ("create a room called Bedroom") is a separate, weaker case and is not planned: naming a room and assigning devices to it is fiddly configuration rather than a glance-and-speak action, and a mis-heard name creates a duplicate room the user then has to hunt down. `GET /api/rooms` is unauthenticated precisely so an integration or the kiosk can already read the resolved state without one.
- Per-panel `default_player` OVERRIDES the household-global `/api/music/preferred-player`, which remains the fallback and an unchanged public contract.
- **`GET /api/music/players` resolves device TYPE server-side** via the pure helper `music.py::resolve_player_kind(player)` — the panel gets flat `kind` (`speaker`|`tv`|`display`|`group`|`computer`) + `kind_label` fields alongside the raw player, and never parses a vendor model string (the same flat-resolved-fields doctrine as `panel_config.py`'s dock pins). MA's own `icon` is useless here (`mdi-speaker` for almost everything); derive from `type`+`provider`+`device_info.model` ONLY, never the user-editable `name`. There is deliberately no `airplay` kind — AirPlay is a transport, not a form factor, and every AirPlay device here is an Apple TV (→`tv`) or a Mac (→`computer`). This exists so the panel can tell the two identically-named **"Bedroom"** players apart (a live Sonos Beam → speaker/"Sonos Beam" vs a dead AirPlay Apple TV → tv/"Apple TV", `available:false`). Keep the resolver pure + unit-tested (`tests/test_music_player_kind.py`, `ci_safe`, fixtures matched against live) — do not inline the mapping into a test.
- `music.py` multi-room grouping (`GET /groups`, `POST /group`, `POST /ungroup`) is **additive to `/transfer`, never a replacement**: transfer MOVES playback to one speaker, grouping SPREADS it across several. Both stay.
  - **The panel never re-derives grouping from MA's payload.** MA spreads it over five fields with non-obvious semantics, so `music_service.build_group_view()` resolves them into flat fields (`role`, `leader_id`, `group_member_ids`, `can_group_with`, `name`). Read the field notes above `build_group_view` before touching it — each was verified against MA 2.8.7 source, not guessed:
    - a sync **leader**'s `group_members` includes its OWN id FIRST; a `type=="group"` player's does NOT include itself;
    - `synced_to` (sync member) and `active_group` (permanent-group member) both mark a FOLLOWER, and `active_group` wins — the group player owns the queue;
    - `static_group_members` non-empty = provider-FIXED membership (the Chromecast "House" group): not editable, so never offer a member picker for it;
    - `can_group_with` has TWO documented shapes — player ids, or a single provider instance_id meaning "all of that provider's players".
  - **`/group` and `/ungroup` are deliberately separate.** Unjoin is not "group with an empty add": the caller does not know which target to remove FROM, and it differs by topology (sync member → its leader, permanent member → its group player, leader → dissolves the group). MA's `players/cmd/ungroup` owns that; don't re-derive it.
  - **An empty MA player list means "cannot validate", not "no players"** — the same rule as `panel_config.py` above. `group_players()` only rejects unknown ids when it could actually SEE the list; otherwise it forwards and lets MA arbitrate. Reversing this locks the operator out of their own speakers during an MA blip.
  - Unavailable players stay LISTED and stay groupable (`available: false`), so a flapping speaker never disappears from the picker.
  - **Grouping is panel-only by design today — the chat/voice intent path is DEFERRED, not forgotten** (`.greptile` `chat-voice-first`). Voice grouping needs multi-speaker name resolution that this house actively breaks: two players are both called **"Bedroom"** (a Sonos zone and an AirPlay endpoint), so "group the bedroom and the kitchen" has no unambiguous referent, and `_match_player_by_name` (`music_service.py`) returns the first name hit — fine for the existing single-target `transfer` intent, wrong for a set. Shipping voice grouping needs a disambiguation strategy (room-scoped resolution via `panels.location`, or a spoken clarifying question) first; that is its own change, and it is voice-path work that must be replay-gated. The read endpoint `GET /api/music/groups` already gives any future intent the resolved state it needs.

## Work Guidance

Match the existing router style: APIRouter per module, explicit auth dependencies, structured error responses.

## Verification

Focused pytest in `../tests/` for the touched router plus a live `/health` check after restart. These tests import service modules, so they run ONLY on the self-hosted Jetson runner, never GitHub-hosted runners (see `../tests/AGENTS.md`).

## Child DOX Index

No child AGENTS.md files.
