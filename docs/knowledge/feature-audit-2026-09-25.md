---
type: audit-record
date: 2026-09-25
audience: Jason first, then every agent
status: complete — read-only probe of the LIVE box, 23:00–23:10 AWST; household data redacted
---
# Feature audit — what actually works on the live box (2026-09-25)

> Jason: *"Zoe's features need to actually work; we started a lot of them but some aren't
> functioning."* This is the evidence for each user-facing feature, read from the running
> system on 2026-09-25 between 23:00 and 23:10 AWST — **not** from docs. Strictly read-only:
> GET probes with the panel device token, read-only `psql` SELECTs, the app/stderr logs,
> journald, `systemctl show`, and `/proc/<zoe-data pid>/environ` for flag state. No POST that
> writes, no restart, no pytest, nothing above ~150 MB RAM (the box had ~500 MiB available
> with the voice brain live). Anything I could not prove is marked **UNVERIFIED**. Items the
> state-of-Zoe review's Appendix A already established (that record lands in PR #1680) are
> included in the table with their register ID and not re-derived.
>
> **Privacy:** this is a public repository. Every household name other than the operator's
> first name, every health fact, relationship, account handle, session/chat identifier and
> quoted memory or message text has been replaced with a placeholder such as `<person-A>`,
> `<health-query>`, `<telegram-session>`. Only what proves a verdict (HTTP codes, counts,
> timings, log signatures) is kept. Do not re-add the raw values in a follow-up.

Verdict key: **WORKS** (probe succeeded and evidence agrees) · **PARTIAL** (some of it works,
some does not) · **BROKEN** (probe failed with a reproducible cause) · **DARK** (flag off or
not wired into any live turn) · **DORMANT** (running, but has had no effect because there
has been no input) · **UNTESTABLE** (needs the powered-off panel, a phone, or a browser
session) · **UNVERIFIED** (could not be proven either way today).

Live context that frames every row: last real conversation **2026-09-03 13:05** (a panel
voice turn); the touch panel is **off by choice**; Jason has been away since 08-10. So most
"no effect" rows below are *no input*, not failure — I say which is which.

## 1. Summary table

| # | Feature | Entry point | Flags (LIVE, from process env) | Verdict | Evidence (probe → result) |
|---|---|---|---|---|---|
| 1 | Chat turn (brain lane) | `POST /api/chat/` (Telegram bot, kiosk, web chat all re-slot through it) | `ZOE_BRAIN_BACKEND=flue`, `ZOE_FLUE_WIRE=2`, `ZOE_FLUE_BRAIN_URL=:3579`, `ZOE_FLUE_STREAM_ENABLED=1`, `ZOE_BRAIN_FAILOVER` unset (=0, B2) | **WORKS** (lane) — `POST /api/chat/` itself **UNVERIFIED** since 09-03 | Not POSTed (it writes). `/readyz` → brain ok, canonical GGUF seen; Flue `:3579/health` ok; KV cache warmed 22:27:23; the 22:39 replay gate drove **13× `POST /api/voice/turn_stream` → 200** through the same `fast_tiers`/brain core, said-vs-did 13/13. Last `chat_messages` row 2026-09-03 13:05. |
| 2 | Chat streaming (AG-UI runs) | `POST /api/chat/` stream + `GET /api/chat/runs/{sid}/latest` | same | **UNVERIFIED** | `chat_ag_ui_runs`: 1,727 rows, newest **2026-08-09 07:49**; `/api/chat/runs/<voice-session>/latest` → 200 `{"run": null}`. Only the web chat UI produces runs and nobody has used it since 08-09. |
| 3 | Voice turn stream (server side) | `POST /api/voice/turn_stream`, `/transcribe` | `ZOE_STT_BACKEND=moonshine`, `ZOE_MOONSHINE_ARCH=MEDIUM_STREAMING`, `ZOE_TTS_MODE=hybrid`, `ZOE_EXPRESSIVE_TTS=1`, `ZOE_SMART_TURN_ENABLED=1`, `ZOE_VOICE_BARGE_IN=1` | **WORKS** | Replay gate **PASS 22:39** (`~/.cache/zoe/voice_regression_last.json`: 20 samples, 13 OK / 0 fail / 7 empty, medians STT 374 ms · brain 2,983 ms · e2e 2,077 ms, bound to commit `01e2e365`, clean tree). `voice_stt.jsonl` shows `route=turn_stream` entries at 22:40; stdout log has 13 `turn_stream` 200s. Kokoro `/health` cuda, pipeline loaded. Panel-side (VAD, wake, playback) **UNTESTABLE** (V2 withdrawn — panel off by choice). |
| 4 | Two-stage router | in-turn (`fast_tiers`) + `GET /api/router/classify` (internal-token) | `ZOE_ROUTER_HEAD=active`, `ZOE_ROUTER_MODE` default `shadow` (tier-1), `ZOE_ROUTER_SHADOW_TEXT=1`, `ZOE_ROUTER_SELFTRAIN` unset (off, R1) | **WORKS** | 8/8 synthetic utterances routed correctly with 5–6 ms latency: weather→`get_weather{forecast}`, "add milk"→`shopping_list_add{item:milk}`, bedroom light→`home{off}`, jazz→`media{play}`, "remind me…at 7"→`add_reminder`, time→`get_time`, "who is <person>"→`people`, dragon story→`chat` (gated=True, correct). Head conf ≥0.96 on every tool case; sidecar `:11436/health` ok; MLP + logreg heads loaded at startup (13 classes). |
| 5 | Memory recall (for-prompt packet, search, self-recall) | `GET /api/memories/search`, `/for-prompt?user_id=`, `/health` | `ZOE_HYBRID_RETRIEVAL_ENABLED=1`, `ZOE_SEAM_RECALL_INJECT=true`, `ZOE_MEMORY_COMPOSE_ENABLED=1`, `ZOE_GRAPH_RECALL_BOOST=1`, `ZOE_EMOTIONAL_RECALL_ENABLED=1` | **WORKS** (post-rebuild, M1 closed) | `/health` → `memory_capture: ok — self-recall ok`; `search?q=<health-query about person-A>` → 200 in 0.55 s, top hit the matching person memory; `/for-prompt?user_id=jason` → 200 packet (non-empty, correctly framed); `/api/memories/?limit=50` → 50 rows (22 person, 15 fact, 7 emotional_moment, rest misc). Data-quality flag: the packet's first "user's name is …" line carries a name that is **not** the operator's first name — Jason to confirm it is intended. |
| 6 | Memory capture (per-turn extraction) | in-turn after `/api/chat` | `ZOE_IDLE_CONSOLIDATION_ENABLED=1`, `ZOE_MEMORY_LINK_RESOLVER_ENABLED=1` | **DORMANT** | Newest memory from a real turn: 2026-08-11 (`idle_consolidation`). The three short 09-03 turns contained nothing to capture. Nothing since — no input, not a failure. Rows dated 09-19 20:00Z (= Sunday 09-20 04:00 AWST) are person memories re-stamped by the weekly consolidation pass. |
| 7 | People / contacts | `GET /api/people/*`, `/api/memories/people` | `ZOE_CONTACT_BACKFILL_ENABLED=1`, `ZOE_PERSON_{DOSSIER,MERGE,SUGGEST}_ENABLED=1` | **PARTIAL** | `/api/people/?limit=3` 200 (30 people), `/search?q=<person-A>` 200 (1 hit), `/relationship-types` 200, `/pending-contacts` needs `user_id` (422 as designed). **`GET /api/memories/people` → 500** — see §2.1. `pending_suggestions` 37 rows. |
| 8 | Relationship graph | `GET /api/people/{id}/graph`, `/relationships` | `ZOE_RELATIONSHIP_GRAPH_ENABLED=1`, `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED=1`, `ZOE_GRAPH_RECALL_BOOST=1` | **WORKS** (traversal); recall-boost effect **UNVERIFIED** (M5) | Alembic head **0028** (migration `0015_temporal_relationship_edges` applied, so M5's "0015 missing" fear is closed). `person_relationships` = 3 edges; `/people/<person-B id>/graph` → 200 with 3 depth-1 nodes (one `love`-group edge, two `family`-group edges). Whether the boost changes recall ranking was not measured. |
| 9 | Calendar | `GET/POST /api/calendar/events`, brain tools `add_calendar_event`/`show_calendar` | — | **WORKS** (read); voice create **UNVERIFIED** | `/events/today` 200 (empty today); `/events?limit=3` 200; `events` table 829 rows, newest 2026-08-11. |
| 10 | Lists | `GET /api/lists/*`, tools `shopping_list_add`/`add_to_list`/`list_remove` | — | **WORKS** (read); voice add **UNVERIFIED** | `/lists/types` 200 (7 types); `/lists/shopping` 200 → one family shopping list with **128 items**. |
| 11 | Reminders | `GET /api/reminders/*`, tool `add_reminder`; APScheduler per-reminder jobs + 5-min `Reminder Scan` autopilot | `ZOE_REMINDER_MAX_ATTEMPTS` default 5 | **PARTIAL** | Read 200 (7 rows: 5 active, 2 deleted). Scan job **is registered and runs** (`multica_autopilot_d8b0ba21…` every 5 min, "reminder_scan complete" 23:05). But **no reminder of Jason's has fired since 2026-07-04**: none of the five active rows has a `due_time` — four are date-only (`efaf8540`, `9e70dd0b`, `a064482a`, `62b563a1`; dates 07-21 … 08-11) and one has neither date nor time (`bdca4e87`) — and the scan skips rows without a `due_time` by design. A sixth row (`1368beaa`, `due_time` 07:00, `due_date` = the literal string `"tomorrow"`) is already `deleted=1`: evidence of the date-normalisation bug, not an active reminder. 5 `reminder_created` notifications from 08-10 are still undelivered. See §2.2. |
| 12 | Weather / time expert | `GET /api/weather/*`; in-turn `expert_dispatch` | `ZOE_EXPERT_MODE=active`, `ZOE_EXPERT_ALLOW_WRITES=1`, `OPENWEATHERMAP_API_KEY` set | **WORKS** | `/weather/current` → 200 in 1.5 s (live OpenWeather for the configured `ZOE_LOCATION_CITY`); `/forecast` 200. Time expert last fired 2026-09-03 04:37 (`EXPERT_ACTIVE domain=time`, 0 ms). A weather-expert spoken reply in September was not found in logs (no weather asks) — **UNVERIFIED** as a turn, proven as an API. |
| 13 | Music (Music Assistant) | `GET/POST /api/music/*`, tool `media` | `MUSIC_ASSISTANT_URL=:8095`, `ZOE_MUSIC_HISTORY=on`, `ZOE_MUSIC_DISCOVERY=on` (Mu1) | **PARTIAL** | MA **2.8.7** `/info` running; `/music/status`, `/providers`, `/players` (several household players listed), `/playlists` (7), `/now-playing` (one player idle) all 200. **YouTube Music provider dead** (Mu3): `available-providers` → `ytmusic connected:false`; MA log every ~2 min "cookies are no longer valid… User does not have Youtube Music Premium". `recently-played` and `recommendations` return `available:false`. **"Zoe Discovery" playlist exists (`library://playlist/7`) with 0 tracks**; weekly discovery job ran 09-06/13/20 and **exited rc=2 (memory gate)** each time. Listening-journal observer runs every 5 min (`observe_once`) but has recorded nothing since 08-09 (nothing played). |
| 14 | Home Assistant control | `GET /api/ha/entities`, `/state/{id}`, `POST /api/ha/control` (bridge `:8007`) | `ZOE_HA_BRIDGE_URL=:8007`, `ZOE_HA_VOICE_ENABLED=true`, `ZOE_DEFAULT_MEDIA_PLAYER=media_player.living_room` | **PARTIAL** | Bridge reachable; `/ha/entities` → 200, **44 entities** (`system/status` agrees: `ha_bridge: ok:44_entities`). `POST /control` not exercised (write) → **UNVERIFIED**. **`/ha/state/media_player.living_room` → 404**: no entity of that id exists in HA (there is exactly one `media_player.*` entity, with a different id) — the configured default is stale. HA core itself on 2026.5.2 (upgrade needs the tool-name sweep first). |
| 15 | Telegram bot | `flue-zoe-telegram.service` (`:3582`) → `POST /api/chat/` | drop-in `NODE_OPTIONS=--network-family-autoselection-attempt-timeout=1500` (T1 fix) | **WORKS** (health) — end-to-end **UNTESTABLE** (phone) | `:3582/health` → `{"ok":true,"polling":true}`; journal 22:30:31 "polling (took the bot over)"; watchdog now passes every minute. Last served turn **2026-09-03 01:29Z** in `<telegram-session>` (user message → assistant reply, 2 s). Nobody has messaged since the fix. T2 (token in journald) still open. |
| 16 | Proactive engine / morning brief (Zoe speaks first) | `proactive.engine` triggers; `multica_autopilot_4cdf6e8e…` cron 07:30 AWST; Pi `voice_announcements` queue | `ZOE_PROACTIVE_SPOKEN=1`, `ZOE_PROACTIVE_SPOKEN_TRIGGERS` default `morning_checkin`, `ZOE_CONVERSATION_OPENER_ENABLED=1`, `ZOE_EMOTIONAL_FOLLOWUP_ENABLED=1` | **DORMANT** (mechanism proven, no eligible user, no panel) | Engine started 22:27 with 8 triggers; slow loop 300 s; the 07:30 job **is registered** (next 2026-09-26 07:30 AWST) and fired every day — but "fired for **0** user(s)" since 09-11 because eligibility = *chatted in the last 7 days* (`morning_checkin.py:214`). Last spoken delivery on record: **2026-08-16 07:30** to `zoe-touch-pi` (`voice_announcements.delivered_at`). `/api/voice/announcements` → empty queue; `/proactive/schedule` → `[]`. |
| 17 | Idle consolidation + nightly memory digest | in-process loop; `memory_digest` daily 03:00; `memory_consolidation` weekly, **Sunday 04:00** (`_memory_consolidation_loop`, `routers/system.py:831`) | `ZOE_IDLE_CONSOLIDATION_ENABLED=1` | **DORMANT** (M3 re-classified) | Idle loop swept the two 09-03 sessions (`turns=4 stored=0`, `turns=2 stored=0`). Digest: **44 consecutive zero-effect runs**, but every one logs `0 users processed` / `insufficient_activity` — the streak began the week Jason left (08-12). It is idle, not broken; it cannot be shown working until there is a day with chat. Next digest 03:00; next consolidation Sunday 2026-09-27 04:00 (the loop logged exactly that at startup). |
| 18 | Speaker-ID (voice) | Pi daemon match + `POST /api/voice/identify`; `GET /api/voice/profiles` | `ZOE_VOICE_IDENT=1`, `ZOE_SPEAKER_ID_THRESHOLD=0.70` | **UNTESTABLE** (panel off) | `speaker_profiles` = **1** (the operator). `/voice/profiles` → 403 with a device token ("requires a signed-in household member" — correct scope guard). P2 shadow week never run. |
| 19 | Face-ID | Pi camera match; `GET /api/face/profiles` | `ZOE_FACE_ID_ENABLED=true` (P1 contradiction), threshold default 0.45 | **UNTESTABLE** (panel off) | `face_profiles` = **3** (all the operator, enrolled 2026-07-19). Same 403 scope guard. Policy says it must be off until a delete UI exists — decision still open. |
| 20 | LiveKit lane (browser Talk) | `GET /api/voice/livekit-token` → on-demand `docker start livekit` | `ZOE_LIVEKIT_ONDEMAND=true`, `ZOE_LK_USE_AIORTC=1`, `ZOE_LIVEKIT_STREAM_TTS` unset (V3) | **DARK** (dormant by design) | Container `livekit` **exited 2026-08-07** (policy `unless-stopped` = it was stopped deliberately); `/voice/livekit-health` → `status: stopped, connection_count: 0`. On-demand start path exists (`voice_livekit.py:277`) but was not triggered (it would load RAM). V10: the token endpoint is unauthenticated on the LAN. |
| 21 | Generative UI / compose cards | after-answer `compose_card()` on chat + voice paths | `ZOE_COMPOSE_UI=1` (U2), `ZOE_COMPOSE_VOICE_BUDGET_S=20`, `ZOE_LAYOUT_MEMORY` default on | **UNVERIFIED** (worked until 08-09) | `ui_layouts` = **27 stored layouts, last used 2026-08-09** — proof that compose produced valid cards while chat was in use. Zero `compose_card` log lines since (none are emitted on success; failures would log). Needs one chat turn to re-prove. |
| 22 | Push notifications | `GET /api/push/vapid-public-key`, `POST /api/push/subscribe` | — | **PARTIAL** (server ready, nobody subscribed) | VAPID public key served (200); `data/vapid_keys.json` present; `pywebpush` importable. **`push_subscriptions` = 0** → no device can receive anything; the 5 pending reminder notifications have nowhere to go. |
| 23 | Multica / engineering harness | board poll loop, 8 autopilot cron jobs, `/api/agent/board` | `ZOE_MULTICA=true`, kill-switch file `~/.zoe/multica_dispatch_paused` (since 2026-08-04), `ZOE_MULTICA_POLL_REF_TIMEOUT_S=300` (D9) | **DARK** (paused by design; H1) | Poll loop logs "runtime dispatch pause active" every 5 min; 8 `multica_autopilot_*` jobs registered; `/api/board/summary` → 200 (todo 4, in_progress 1, backlog 37) — so the H2 401 is gone after the restart. "Platform Health Check" still **failed at 06:00 today** (pre-restart); confirm tomorrow. `hermes-agent` inactive, `openclaw-gateway` inactive. |
| 24 | Skills / A2A | `GET /api/agent/card`, `/registry`, `/squad`; `POST /api/agent/delegate` | `ZOE_A2A_TOKEN` set | **DARK** (card served, no live peer) | `/agent/card` 200 (A2A 1.0); `/registry` and `/squad` list **hermes + openclaw, both offline**; `/runtimes` → hermes/openclaw `online:false`. 33 Hermes + 34 OpenClaw skills on disk, read by nothing live (retirement in progress, H4/H5). `system/platform` still reports `engine: "hermes"`. |
| 25 | Web search / research | (a) chat research fallback: `routers/chat.py:1794` classifies a research-class request and, with a complete brief, builds an evidence package whose `package_needs_web_fallback` step fetches DuckDuckGo HTML directly (`research_evidence.fetch_web_fallback_results`, `chat.py:1500`); (b) `research` delegation seam (`/api/system/delegate-sync`); (c) legacy `web_search` brain tool | `ZOE_SEARCH_PROVIDER` default `auto`, `TAVILY_API_KEY` **set**, `cloakbrowser` installed | **PARTIAL** — (a) wired but returning nothing today; (b) broken; (c) absent by design | (a) needs no Hermes, no delegate-sync and no brain tool. Probed read-only 2026-09-26 by importing `research_evidence`: a complete brief classifies as `research` with `missing: []`, but `fetch_web_fallback_results` returned **0 results on three different queries** — DuckDuckGo answered the scripted fetch with its anomaly/challenge page (HTTP 202, ~14 kB, zero `result__a` links; `curl` gets a 302), which the function swallows and reports as an empty list. So the chat fallback is reachable but currently empty, and the user gets the "deterministic research brief with … evidence placeholders" text. (b) the `research` ability's target (Hermes) is inactive and the planned re-point to Flue has not landed; the browser broker still binds to the OpenClaw gateway (`:18789`, offline). (c) the live Flue 2.x brain registers 20 tools and — **by the approved cut list** ([flue-cutover-tool-cut-list.md](flue-cutover-tool-cut-list.md) item 10) — no direct `web_search`. `chat/capabilities` still advertises "web search". PR #1610 (spike) is stale/conflicting (H9). |
| 26 | Panel presence / sleep gate | `GET /api/panels/{id}/sleep-gate`, `/public`, `/api/ui/actions/pending` | `ZOE_PANEL_SESSION_TRUST_WINDOW_S=1800` | **WORKS** (server); panel **UNTESTABLE** | `sleep-gate` → 200 `{"block": false, "reason": "room-dark"}`; `/panels/zoe-touch-pi/public` 200; `/ui/actions/pending?panel_id=zoe-touch-pi` → 0 queued. Presence *events* table was dropped in 0028 (the house has no presence sensors — P4). `online_panels_30s: 0`. |
| 27 | Backup / restore | `zoe-backup.timer` 02:33 nightly; `zoe-backup-verify.timer` weekly | — | **PARTIAL** (D14 fixed, unproven) | 02:32 today **failed** (`tar: chroma.sqlite3: file changed as we read it`, Postgres step skipped). Fixed script test-ran 22:14 → a 33 MB MemPalace tarball. Last Postgres dumps 2026-09-24. Verify last ran 09-20 (journal was volatile; no record). Tonight's run is the proof. |
| 28 | Web UI (nginx static) | `https://<host>/`, `/touch/home.html`, `/chat.html` | — | **WORKS** (served) | All three → 200. Behaviour inside the kiosk/desktop pages not exercised (no browser). |
| 29 | Notes / journal / transactions / portrait | `GET /api/notes/`, `/journal/entries`, `/transactions/summary/week`, `/portrait/me` | — | **WORKS** (read; mostly unused) | notes 200 (2 rows), journal 200 (0 rows), transactions 200 (0 rows), portrait 200 (narrative present). Feature-complete APIs with no household use yet. |
| 30 | Pi-intent hybrid lane (legacy) | `/api/system/pi-intent/*` (admin) | `ZOE_PI_INTENT_ENABLED=true`, `ZOE_PI_HYBRID_PRODUCTION_ENABLED=true`, `ZOE_PI_INTENT_PROMOTED_GROUPS=` (empty → shadow only) | **DORMANT** (superseded) | Evidence file last written **2026-07-14**; no `pi_intent` log lines in September. The two-stage router (row 4) took this job. Candidate for retire-by-removal. |
| 31 | Intent-dispatch token gate | `POST /api/system/intent-dispatch` (brain → actions) | `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN=1`, `ZOE_INTERNAL_TOKEN` present in **both** zoe-data and the Flue brain process env | **WORKS** (configured) | Zero "intent-dispatch requires a valid X-Internal-Token" rejections across all September logs; the replay gate's 13 tool-bearing turns passed. Board ticket ZOE-6116 ("provision the token in Flue") is already satisfied on the box. |
| 32 | HA voice ingress (Assist → Zoe) | bridge `/voice/turn`, `/voice/wake` | `ZOE_HA_VOICE_ENABLED=true`, `ZOE_HA_VOICE_TOKEN` set | **UNVERIFIED** | Routes exist on the bridge; no HA Assist satellite exercised them in the logs. |

## 2. BROKEN / PARTIAL items — root cause and smallest fix

### 2.1 `GET /api/memories/people` → 500 (BROKEN, new)
**Cause (reproduced, traceback in `zoe-data.stderr.log`):**
`routers/memories.py:807` orders with `ORDER BY name COLLATE NOCASE` — a SQLite collation.
On Postgres asyncpg raises `UndefinedObjectError: collation "nocase" for encoding "UTF8" does
not exist`. The handler's own docstring says it was added because the consumer "previously
404ed"; it now 500s instead, and the unit lane runs on SQLite so CI is green.
**Smallest fix:** `ORDER BY lower(name)` (portable), and grep the tree for `COLLATE NOCASE`
to fix the class, not the instance. Add a `ci_safe` test that runs the SQL through the
Postgres-compat path.

### 2.2 Reminders never fire for the way Jason actually creates them (PARTIAL)
**Cause:** two independent gaps, both reproducible from the rows.
1. `proactive/triggers/reminder_scan.py:13` — *"Reminder has no due_time → skip"*. None of
   the five active reminders has a `due_time`: four are **date-only** (`efaf8540`,
   `9e70dd0b`, `a064482a`, `62b563a1` — due 07-21, 07-26, 07-30, 08-11) and one
   (`bdca4e87`) has **neither** date nor time. So the scan (which does run every 5 min)
   schedules nothing, and `proactive_scheduled` shows no fire for `jason` after 2026-07-04.
2. The voice path once stored the literal string **`"tomorrow"`** as `due_date` on a
   reminder that did have a `due_time` (row `1368beaa`, 07:00). `build_run_at()` does
   `date.fromisoformat("tomorrow")` → `ValueError` → `return None` → silently never
   scheduled. That row is already `deleted=1`, so it is not a live problem — but nothing
   stops the next voice-created reminder from storing the same literal.

**Smallest code fix:** (a) in `reminder_scan.py`, give date-only reminders a household
default time (e.g. 09:00 local, env-tunable) instead of skipping; (b) normalise relative
dates ("tomorrow", "next Friday") to ISO in the `add_reminder` tool before insert, and reject
anything that is not ISO at the API.

**Overdue / data-repair policy (explicit, because the fix alone does not touch the five
existing rows):** `schedule_due_reminder()` rejects `run_at <= now`, so a reminder whose
date is already past will **never** be scheduled by the fixed scan either — and that is the
right behaviour (firing a stale "on the 11th" reminder weeks late is noise). The existing
rows therefore need a deliberate one-off repair, not silent auto-firing:
- the four date-only rows with past dates (`efaf8540`, `9e70dd0b`, `a064482a`, `62b563a1`):
  either **acknowledge** (`POST /acknowledge`) or **re-date** (`PUT` with a new `due_date` +
  `due_time`) — operator's call per row; the reminder scan then picks up any re-dated one on
  its next 5-minute pass;
- the no-date, no-time row (`bdca4e87`) is effectively a to-do, not a reminder: give it a
  date + time via `PUT`, or acknowledge it;
- the `"tomorrow"` row (`1368beaa`) is already deleted, so nothing to repair — but any future
  row that stores a non-ISO `due_date` stays unscheduled until repaired via `PUT` with an ISO
  date or deleted; nothing in the scan will ever coerce it.
Add a `ci_safe` test that inserts a date-only reminder and a non-ISO `due_date` and asserts
the post-fix behaviour (scheduled with the default time; rejected at the API respectively).
Note the fired notification still has no delivery channel while `push_subscriptions` = 0 and
the panel is off (rows 22, 26).

### 2.3 Music: YouTube provider dead, discovery empty (PARTIAL — Mu1/Mu3)
**Cause:** MA's `ytmusic` cookies expired (Google rotates them; MA logs it every ~2 min);
the weekly discovery batch exits `rc=2` on its own RAM gate (09-06, 09-13, 09-20), so
"Zoe Discovery" has 0 tracks; `recently-played`/`recommendations` report `available:false`
(their gating condition not traced today — UNVERIFIED whether it is the provider or the
empty history).
**Smallest fix:** panel Sources → Reconnect (needs the panel or the MA web UI) then a
zoe-data restart; leave `ZOE_MUSIC_DISCOVERY=off` until the RAM plan clears the gate, or the
job will keep failing silently every Sunday.

### 2.4 Home Assistant default media player does not exist (PARTIAL, new)
**Cause:** `ZOE_DEFAULT_MEDIA_PLAYER=media_player.living_room` in the live env, but HA's
entity list has no such entity (HA exposes exactly one `media_player.*` entity, under a
different id). Any "play/pause/volume" that falls through to HA instead of Music Assistant
hits a 404 (`ha_control: ha/state bridge status error entity=media_player.living_room` at
23:05).
**Smallest fix:** set the env to the entity id HA actually exposes (or to the real
living-room player once one exists). **Do not unset or blank the variable** — unset falls
back to the hard-coded `media_player.all`, and an empty string produces an empty `entity_id`;
neither is a valid target. The durable fix is to validate the configured id against the
bridge's entity list at startup and refuse with a clear message, which PR #1686 implements.
HA control writes were not probed — verify `POST /api/ha/control` against a real switch
entity when someone is in the room.

### 2.5 People/contacts, push, backup — the smaller ones
- **Push (row 22):** nothing wrong server-side; zero subscribers means no one has opened
  the web UI and accepted notifications since the VAPID keys were generated (04-14).
  Subscribe one phone/browser and the pending reminder notifications become deliverable.
- **Backup (row 27):** already fixed today; the only thing missing is one clean nightly
  run — check `systemctl --user status zoe-backup` after 02:35 tomorrow and that a fresh
  `postgres/zoe-2026-09-26-*.dump.gz` exists.
- **Proactive (row 16):** not a bug. Eligibility is *a chat session created in the
  preceding 7 days* (`morning_checkin.py:214`), so the window keeps Zoe silent **while the
  user is absent**, and a returning user's first new session makes them eligible for the
  very next 07:30 — there is no lag after a return. What the rule does mean is that the
  morning brief can never be the *first* contact after a gap: Zoe waits for the user to
  speak first. If "speaks first" should also cover the return itself, key eligibility on
  household membership (with a do-not-disturb bound) rather than recent chat.

### 2.6 Web search / research (PARTIAL — three distinct paths, only one of them live)
Correction (2026-09-26, after review): the first version of this section said "nothing on a
live turn can reach the web". That was too broad. There are three paths and they must not be
conflated:

1. **Chat research fallback — WIRED, currently EMPTY.** A research-class chat request whose
   brief is complete (`research_evidence.classify_query` → `research`,
   `missing_brief_fields` → `[]`) gets an evidence package, and when that package has no
   usable per-site evidence (`package_needs_web_fallback`) `routers/chat.py:1500` fetches
   DuckDuckGo's HTML endpoint directly via `fetch_web_fallback_results` — no Hermes, no
   `delegate-sync`, no brain tool involved. Read-only probe from the box on 2026-09-26: the
   classifier accepted a complete brief, but the fetch returned **0 results on three
   queries** because DuckDuckGo served its anomaly/challenge page (HTTP 202, ~14 kB, no
   `result__a` links) to the scripted request; the function catches everything and returns
   `[]`, so the turn silently degrades to the "deterministic research brief with … evidence
   placeholders" text. Whether this is a temporary rate-limit or a lasting block is
   **UNVERIFIED** — re-probe on another day before treating it as a fix target.
2. **`research` delegation seam — BROKEN.** The approved cut list
   ([flue-cutover-tool-cut-list.md](flue-cutover-tool-cut-list.md) item 10) deliberately cut
   direct `web_search`/`deep_web_research` tools in favour of the `research` ability routed
   through `/api/system/delegate-sync`, because a direct tool double-books and bypasses the
   validation layer there. That seam's target (Hermes) is inactive and the planned re-point to
   Flue (cut-list items 8/15) has not landed; the browser broker still targets the retired
   OpenClaw gateway.
3. **Flue brain `web_search` tool — ABSENT BY DESIGN** (same cut-list item). Not a bug.

**Fix, in the approved direction:** (i) restore the `research` → `delegate-sync` seam with a
Flue-backed target (per the cut list; do **not** add a direct `web_search` tool to the
brain); (ii) make the chat fallback honest — surface "search returned nothing" instead of
swallowing the DDG challenge page, and consider routing it through `web_search_provider`
(Tavily key is set; `ZOE_SEARCH_PROVIDER=auto` prefers it) so the fallback is not hostage to
DDG's bot heuristics; (iii) only if neither lands, narrow the `chat/capabilities` claim to
what actually works rather than removing it — a working chat fallback must not be hidden by
the broken seam. The memory note `project_zoe_web_search_browsing` still wants the
CloakBrowser-vs-search-API recommendation before the seam is rebuilt.

### 2.7 Noticed in passing (security / hygiene, not features)
- **Postgres credentials are written in plaintext to the app log on every start**:
  `proactive/scheduler.py:55` logs the full SQLAlchemy jobstore URL including the password
  (`zoe-data.app.log` 22:27:15 today, and every previous restart). Smallest fix: log
  `make_url(url).render_as_string(hide_password=True)`. Rotate the DB password after
  vacuuming the logs (same class as T2).
- `system/platform` and `chat/capabilities` still say `engine: hermes` / "Hermes engineering
  loop"; `/api/system/openclaw` still enumerates 34 retired OpenClaw skills — cosmetic, but
  it is what an agent reads first (H4/H5 doc drift).

## 3. Not testable today

| Feature | Why | What would test it |
|---|---|---|
| Panel voice end-to-end (wake, VAD tail 640 ms, barge-in, spoken morning brief, ask-cards) | `zoe-touch-pi` is powered off by choice | Power it on; say "Hey Zoe, what's the time"; expect a `voice-panel-…` session row and a `PROACTIVE_SPOKEN … outcome=delivered` line at 07:30 |
| Speaker-ID / Face-ID matching | needs the Pi camera/mic | Panel on; check the daemon's match log against the one voice + three face profiles |
| Telegram end-to-end | needs Jason's phone | Send "hi" to the bot; expect a new user/assistant message pair in `<telegram-session>` within ~5 s |
| `POST /api/chat/` (text) and AG-UI streaming | write endpoint — deliberately not exercised | One message from `chat.html`; expect a `chat_ag_ui_runs` row and (with compose on) a new `ui_layouts.last_used_at` |
| LiveKit Talk lane | container stopped; starting it costs RAM the box does not have tonight | After the zram shrink: open Talk, expect `docker start livekit` in the log and `livekit-health.connected: true` |
| HA control writes, calendar/list/reminder *creation* via voice | writes | One voice or Telegram command each; verify the row |
| Weather *expert* spoken reply, compose cards | no turn since 09-03 | Same as above; expect `EXPERT_ACTIVE domain=weather` |
| Nightly digest / consolidation doing work | needs a day with conversation | Watch `~/.zoe/zoe-data-memory-loops.log` at 03:00 after a day of use — the zero-effect streak should reset |
| Backup success | timer-driven | 02:35 tomorrow |

## 4. Method notes (so the next audit is comparable)

- Flags were read from `/proc/<zoe-data pid>/environ` (the uvicorn started 22:27:12), not
  from `.env`; the brain sidecar's env from its own MainPID. Values containing
  `TOKEN|KEY|SECRET|PASSWORD` were redacted before display.
- Auth: `X-Device-Token` (panel token from the operator's Hermes `.env`) resolves to
  `jason`/member. Admin-only routes (`/api/panels`, `/api/system/memory-loops/status`,
  pi-intent) return 403 with it — expected, and left untested rather than escalated.
  `/api/router/classify` and `/metrics` need `X-Internal-Token`, read from the process env
  into a shell variable.
- Scheduled work was checked in `apscheduler_jobs` (10 rows: 8 Multica autopilots,
  `music_discovery_weekly`, `music_history_observe`) **plus** the in-process loops that never
  appear there (idle consolidation, digest, proactive slow loop, Multica poll, runtime health)
  — those were confirmed from the 22:27 startup lines in `zoe-data.app.log`.
- Row counts are from `docker exec zoe-database psql -U zoe -d zoe` SELECTs only; no row
  contents are reproduced here beyond counts, timestamps and column names.
