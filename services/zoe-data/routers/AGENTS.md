# services/zoe-data/routers/ — API routers

## Purpose

FastAPI routers for every Zoe API domain: chat, calendar, lists, memories, reminders, notifications, push, voice, weather, skybridge, system, and more.

## Ownership

- `chat.py` — THE ONLY production chat router (intent fast path + OpenClaw/Hermes agent path via `intent_router`, `openclaw_ws`, `ag_ui_stream`). CRITICAL FILE.
- `system.py` — system/status endpoints. CRITICAL FILE.
- `panel_config.py` — per-panel config (`GET`/`PUT /api/panels/{device_id}/config`): the panel's room, its default speaker, and its pinned dock controls. Storage is the `panels` row (`location` + `default_player` + `pinned`), keyed by `panel_id` == the panel's `device_id`.
- One router module per domain (`calendar.py`, `lists.py`, `memories.py`, `journal.py`, `music.py`, `push.py`, `skybridge.py`, ...).

## Local Contracts

- NEVER create `chat_v2.py`, `chat_new.py`, `chat_optimized.py`, or any parallel chat router. Use git branches, not file duplication.
- No hardcoded NLU command detection (if "add" in message and "shopping" in message...) in `chat.py` or any router; natural-language understanding goes through `intent_router.py` patterns, Zoe Agent, Hermes, or OpenClaw.
- Validate user input at the API boundary; parameterized queries only.
- Routers hold domain policy; reusable mechanics (provider calls, parsing, payload transforms) belong in service-layer helpers, not duplicated across routers.
- **Panel-scoped settings live in ONE place per concern — do not add a second store.** `panels.location` is THE panel location (it predates `panel_config.py`); `display_preferences` (`system.py`) stays the *display* store (brightness/idle/off). A new panel-scoped fact goes in the `panels` row.
- `panel_config.py` invariants (each is load-bearing and non-obvious — a "cleanup" that flattens one reintroduces a shipped bug):
  - **A pin is a read/write PAIR.** The thermostat is two entities (`sensor.current_temperature` read + `input_number.thermostat_temperature` write); a single `entity_id` cannot express it. The `{entity_id}` input form is sugar that canonicalises to an equal pair — the response is ALWAYS the pair.
  - **Never validate `entity_id` against a domain allow-list.** This house has zero `light.*`/`climate.*`; its controls are `input_boolean.*`/`input_number.*`/`scene.*`. Shape (`domain.object_id`) only.
  - **`kind` and `icon` resolve server-side, never from the domain.** `input_boolean.fan`/`.tv` share the lights' domain; HA's own `attributes.icon` is the truth.
  - **`pinned` NULL != `[]`.** NULL = never configured (dock falls back to its own default); `[]` = the operator explicitly pinned nothing. `pins_configured` carries the distinction.
  - **An empty MA player list means "cannot validate", not "no players".** `music_service._ma` never raises — it returns None on transport failure, which `get_players()` turns into `[]`. Treating `[]` as an empty set rejects every id and locks the operator out during an MA outage.
  - **PUT writes ONLY the columns the request supplied** (`ON CONFLICT DO UPDATE SET` built from the fixed `_WRITABLE` whitelist, never from body keys) and returns the post-write row via `RETURNING`. Do not "simplify" this back to a read-merge-write that always writes all three columns — concurrent PUTs of different fields would clobber each other.
- Per-panel `default_player` OVERRIDES the household-global `/api/music/preferred-player`, which remains the fallback and an unchanged public contract.
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
