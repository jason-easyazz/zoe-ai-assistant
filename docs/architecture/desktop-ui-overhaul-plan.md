---
type: architecture-plan
status: proposed (audit complete, waves not started)
owner: jason
date: 2026-07-19
---

# Desktop UI Overhaul — audit + executable plan

**What this is:** the full deep-dive over the desktop web UI (`services/zoe-ui/dist/` root
pages + `js/` + `css/` + `sw.js` + nginx) — what's live, what's broken, what's dead — and the
sequenced plan to get it up to scratch. This is the re-scope the
[skybridge cutover plan](skybridge-cutover-plan.md) left open (its PR 3/PR 5 "retire legacy
pages/live-sync" items were "directionally valid — re-scope against the estate before acting").

**How it was produced (2026-07-19):** 145-agent audit workflow on branch
`claude/desktop-ui-overhaul-a512f5` — 10 parallel area audits (every page + js/css/sw/nginx),
**329 client-called endpoints verified against zoe-data source** (negative-controlled),
every P1/P2 finding adversarially re-verified (6 refuted, 105 confirmed), a live logged-out
smoke pass against the running nginx (curl all 26 pages + headless-chromium console capture
on 6), and a 3-stance direction panel with a judge.

## The verdict in three sentences

The desktop surface splits into a **live core with real value** (chat.html — the invested
DeerFlow surface; settings.html — a genuinely live admin console; calendar — the richest
desktop page; notes/lists/jukebox/setup-* — working), a **broken-but-alive tier** (24
confirmed P1s: 8 XSS sink areas, calendar data-loss on drag/resize, dead people.html,
placebo controls, a PWA manifest that doesn't exist in git), and a **large verified-dead
tier** (~30k+ lines: the widget dashboard stack, dist/developer/, the browser-streaming
music era, 6 orphan pages, orphan js/css). 61 of 329 client-called endpoints are MISSING
or MOVED server-side — the honest measure of the rot. Direction (judge-picked, evidence
over stance): **desktop is Zoe's keyboard-first deep-work power surface** — invest in chat,
PIM depth, and the admin console on the shared skybridge token foundation; retire everything
the estate already owns or that never worked.

## Direction

- **Desktop = the deep-work face** (chat workspace, bulk PIM editing, fleet/admin console).
  The estate panel stays the ambient face. PLANS.md already names chat.html the
  DeerFlow-grade target — this plan is on-plan.
- **One foundation, two faces:** the touch `skybridge-ds.css` token layer becomes THE token
  layer for both surfaces; auth, nav, theme, and landing page consolidate to one of each.
  No page keeps a private design dialect (6 exist today).
- **Honesty-by-removal is the default** for any UI without a backend. Never build a backend
  to save placebo UI without a deliberate product decision pinned to IDEAS.md.
- **Desktop keeps its own surfaces (Jason, 2026-07-19).** Two audit recommendations were
  overruled, and a verification pass proved the overrule correct:
  - **Landing page = the dashboard**, not chat.html. So `dashboard.html` is **rebuilt, not
    retired** — it is the surface seen on every login. (Today's logins already land there;
    no repoint needed.)
  - **`music.html` is rebuilt to touch parity**, not redirected. Target surface is the
    proven one: `POST /api/music/control` + `/seek` (music.py:292/:304),
    `GET /api/music/now-playing` (normalized title/artist/image/state/volume/elapsed/
    duration, music_service.py:228-270), queue re-fetch on `queue_updated`.
  - Desktop `updates.html` still retires (notifications panel deep-links exclusively to
    the touch copy); the calendar fork converges to a single source *later*, after the
    desktop data-loss fix is ported INTO the touch fork (port direction matters — the
    touch fork still calls removed attendee endpoints).
- **The legacy touch pages are mostly orphaned — but NOT all of them (verified 2026-07-19,
  corrected after review).** `touch/home.html` (the estate, and the kiosk's actual boot URL
  per `scripts/setup/touchscreen/config.json`) has **zero outbound `.html` links**, and most
  legacy per-domain touch pages link only *each other* via `touch/js/touch-menu.js` +
  `touch-nav.js`. **Consequence that still holds:** the widget stack serves **desktop
  dashboard + desktop lists only** — the "one repair fixes 4 pages" leverage assumed in the
  first draft does **not** exist.
  **But a blanket deletion is wrong — four pages have LIVE inbound paths:**

  | Page | Live referrer | Rule |
  |---|---|---|
  | `touch/voice.html` | `chat.py:71` `lets_talk` → `/touch/voice.html?conv=1` ("phone-call voice mode — still its own surface") + `voice_tts.py:1111` | **DO NOT RETIRE HERE.** This is the live voice path and is **replay-gated**. It retires only with the Ask-card conversation cutover (PLANS Phase 1c), not in this overhaul |
  | `touch/updates.html` | `notifications-panel.js:345/:358` (loaded on 10 desktop pages) | Repoint the deep-links **before** deleting; otherwise notification taps 404 |
  | `touch/cooking.html`, `touch/smart-home.html` | desktop `cooking.html` / `smart-home.html` meta-refresh stubs, linked from the desktop nav | Repoint the nav (Wave 2) **before** deleting stub+target |
  | `touch/settings.html` | loads `touch-menu.js` (`:6170`); touched 2026-07-17 | Resolve its fate **before** deleting the shared scripts |

  Not nav sources (cleanup, not reachability): `auth.js:13` `TOUCH_PATH_TO_PAGE_ID` is a
  reverse map; `orb-loader.js:11` is a skip-list; `sw.js:735` is a cache route;
  `voice_tts.py:524` is a **supersede/cancel** list commented "Legacy per-domain pages
  (retired)" — it cancels stale navigations rather than issuing them.
  **Ordering rule for the whole retirement: repoint or delete every referrer FIRST, delete
  the target SECOND, and delete the shared `touch-{menu,nav,widgets}.js` LAST — only once
  no consumer remains.** This closes the skybridge cutover plan's open PR 5 for the pages
  that are genuinely dead, and records the rest as sequenced work rather than pretending
  they are dead.
- **Installable Zoe:** ships as **PWA polish inside this overhaul** (manifest/SW are ~80%
  there once committed to git). A thin **Tauri native shell** (global hotkey summon,
  tray/autostart, later computer-use) is a **parked separate arc** — pinned in
  [IDEAS.md](../IDEAS.md); the shell wraps the same served UI and never grows its own screens.

## Page verdicts (audit-confirmed)

| Page | Verdict | Why (one line) |
|---|---|---|
| `chat.html` | **keep-fix** | The flagship; AG-UI dispatcher + compose bridge are good; needs 6 P1 fixes, ~700 dead lines excised, module split |
| `settings.html` | **keep-fix** | Real admin console, ~44/46 calls live; 2 dead features (placebo Display card, `/api/tools/call` buttons); owns fleet admin |
| `calendar.html` | **keep-fix** | Richest desktop page; P1 metadata data-loss + zero escaping; ~87% fork of touch calendar — converge later, desktop fix ports in |
| `notes.html` | **keep-fix** | Healthiest PIM page; one stored-XSS pattern to fix, then tokens |
| `lists.html` | **keep-fix** | Works for core lists; strip dead Projects/Marketplace/AI-Generate wings + broken library widgets |
| `people.html` | **redo (fix-by-deletion)** | Dead on arrival since 2026-05-18 (null-canvas crash); healthy CRM backend + salvageable card-grid code underneath |
| `memories.html` | **redo (descope)** | Flagship collections/tiles canvas has NO backend (stubs); rebuild around MemPalace review-queue + search which are live |
| `journal.html` | **redo (slim)** | Photo upload targets nonexistent route, tags silently dropped, Journeys tab is hardcoded fiction, CDN/Unsplash externals |
| `music.html` | **redo (rebuild to touch parity)** | Transport POSTs to never-existed `/api/ha/service`; now-playing reads wrong MA shape. Rebuild on `/api/music/control` + `/now-playing` + queue — same functionality as the touch music card |
| `index.html` | **keep-fix (the ONE auth surface)** | It's the nginx entry + SPA fallback; fix XSS/demo-bypass/"Welcome, undefined"; absorb auth.html's flows |
| `auth.html` | **fold into index.html → stub** | Second drifted login with reflected DOM XSS via `?setup=` |
| `dashboard.html` | **redo (rebuild as THE landing page)** | Jason's call: desktop lands on the dashboard. Rebuild on the token layer, salvaging the widget bindings that work (time, weather, events, notes, 4 list types); drop the singleton-flawed widget-system/gridstack lineage and the broken/fake widgets |
| `updates.html` | **retire (touch copy wins)** | Notifications panel deep-links only to `/touch/updates.html`; backend stays |
| `voice.html` (+ `touch/voice.html`) | **retire** | Zero inbound links; Confirm posts to nonexistent `/api/chat/confirm`; PLANS Phase 1c already slates it |
| `jukebox.html`, `setup-music.html`, `setup-device.html` | **keep-polish** | Recent, QR-linked, verified against live routes — already at the estate bar |
| `offline.html` | **keep-fix** | Live SW fallback; probes wrong `/api/health` (real: `/health`) and never checks `res.ok` |
| `clear-cache.html` / `-v2` / `clear-session.html` | **consolidate to ONE** | Orphan near-triplicates; `_v2` violates repo rules; keep v2's session-preserving behavior under the v1 name |
| `games.html` + `touch/games.html` | **retire** | Whole chain dead-ends in a 26-line placeholder |
| `cooking.html`, `smart-home.html` | **stubs, delete after nav repoint** | Nav-linked meta-refreshes to /touch/ |
| `week_planner_widget.html`, `dist/developer/`, `dist/_preview/*` | **retire** | Orphan mock / 4,406-line dead prototype (nonexistent APIs, hardcoded localhost:8000) / stale artifacts behind a hard-404 |
| Widget stack (`widget-system.js`, `dashboard.js`, `lists-dashboard.js`, `js/widgets/**`, gridstack) | **retire after the dashboard rebuild** | Serves desktop dashboard + desktop lists ONLY (the touch copies are orphaned — see Direction). No cross-surface coordination needed: rebuild the dashboard, migrate lists, then delete the lineage |
| Legacy touch pages (`touch/{dashboard,lists,calendar,notes,people,timers,weather,memories,journal,chat,games}.html`) | **retire** | No live inbound path: the estate `home.html` has zero outbound `.html` links and is the kiosk boot URL; these link only each other. Closes skybridge-cutover PR 5 |
| `touch/{updates,cooking,smart-home,settings}.html` + `touch/js/touch-{menu,nav,widgets}.js` | **retire, but SEQUENCED** | Each has a live referrer (notifications deep-links, desktop nav stubs, settings loads touch-menu). Repoint referrers first; shared scripts die last. See the Direction table |
| `touch/voice.html` | **keep — NOT this overhaul** | **Live** `lets_talk` server navigation (`chat.py:71`, `voice_tts.py:1111`); replay-gated voice path. Retires with the Ask-card cutover (PLANS Phase 1c) |
| Orphan js/css set | **retire** | `navigation.js` (5-line stub), `js/lib/{module-widget-loader,widget-registry}.js`, `js/voice/*`, 4 unloaded music widgets, `mini-player.js` (+6 tags), `chat-sessions.js`, `ai-processor.js`, `components/zoe-orb.html`, `css/{glass,memories-enhanced,widgets-enhanced}.css` |

## The triage register (all adversarially confirmed; 23 P1 entries + item 11, a P2
promoted into Wave-1 triage — scripted P1 sweeps should include it)

**Deploy integrity**
1. `.gitignore:155` blanket `*.json` — `widget-manifest.json` AND the PWA `manifest.json`
   exist ONLY untracked on the live box; every fresh clone/deploy gets an empty widget grid
   (4 pages, incl. the live touch kiosk) and serves index.html AS the manifest.
2. SW cross-origin kill (smoke-confirmed, reproduced): `sw.js:183-236` NetworkFirst on
   no-cors strips all 9 CDN libs from chat.html on every repeat visit — markdown,
   DOMPurify, highlighting, charts, maps gone in production.

**Security (XSS + auth)**
3. `auth.html:1207` reflected DOM XSS via `?setup=`.
4. `chat.html:3768` stored XSS via session titles (derived from raw user messages).
5. `chat.html:7410` stored XSS via reminder notification text (in both duplicate copies).
6. `notes.html:700/:769` stored XSS via JSON.stringify-in-onclick + unescaped `'` in tags
   (family-visible notes = cross-user blast radius).
7. `journal-api.js:365` stored XSS on the LIVE render path (#895 patched only the
   deprecated renderer).
8. `memories.html:2312` stored XSS in review-queue/search render.
9. `calendar.html:2539` zero HTML escaping page-wide.
10. `index.html:1043` + `auth.html:889` demo-mode client-side auth bypass (admin/admin
    mints a fake session when zoe-auth is unreachable).
11. `orb-loader.js:60` logout purge listens on `window`, auth.js dispatches on
    `document` — orb transcripts survive logout. (Severity P2, deliberately promoted
    into this register: privacy blast radius on shared machines.)

**Data loss**
12. `calendar.html:3582` drag/resize/task-link PUTs send `metadata:{linked_tasks}` only;
    server replaces metadata wholesale → silently destroys description/prep/attendees/
    reminders. Plus `:3925` attendees/reminders re-loaded from two nonexistent routes →
    wiped on every re-save.

**Broken user-facing features**
13. `people.html:880` page dead on arrival (null-canvas crash kills all init).
14. `music.html:1327/:1064` every transport button a silent no-op; now-playing can never populate.
15. `agent-activity.js:145` Agent Activity feed destroyed by `loadSessions()` innerHTML wipe.
16. `chat.html:5628` action_menu buttons syntactically invalid onclick + zero-arg sendMessage.
17. `chat.html:7488` proactive push-tap lands on blank chat (no listener, `?session=` never read).
18. `chat.html:6176` builder preview iframe always 404s (nginx hard-404s `/_preview/` while
    the server still synthesizes those URLs); sandbox `allow-scripts+allow-same-origin` is
    self-nullifying.
19. `chat.html:2598` compose cards render white-ink-on-white in light theme (loads
    compose.css without skybridge-ds.css).
20. `push-notifications.js:132` strict-mode ReferenceError kills every push subscribe on 13 pages.
21. `settings.html:2230` Restart-Kiosk/Logs POST nonexistent `/api/tools/call`; `:1338`
    Display card reads/writes a schema that never existed — "Save" is a placebo.
22. `dark-mode-shared.css:145` white-on-white dark mode on calendar/journal/lists/memories.
23. Widgets: tasks.js always "All tasks completed!", home.js dead toggles, system.js
    Math.random() stats, week-planner/project/journal-photo → nonexistent routes.
24. `index.html:1175` "Welcome, undefined!" on every real login (wrong response shape).

**Endpoint truth:** 329 checked → **51 MISSING + 10 MOVED**. Biggest dead families:
`/api/projects/*` (8), `/api/developer*` + `/api/docker/*` (5), collections/tiles (6),
journeys (3), retired music-streaming era (7), `/api/media/upload`, `/api/tools/call`,
`/api/chat/confirm`, `/api/chat/warm`, `/api/ha/service`, `/api/health` (real: `/health`).

**Logged-out smoke:** dashboard/calendar/lists half-render and error-storm (7 failed API
calls with literal `user_id=undefined` + ~20 failed WS handshakes in 10s on calendar);
only settings.html redirects properly.

## Execution waves (each item ≈ one small PR)

Ordering follows [foundation-before-features]. Waves 0–1 are pure triage (no strategy
dependency); 2 is the foundation; 3 the funeral; 4–6 the product investment.

### Wave 0 — Deploy integrity (land first, tiny)
- Commit `manifest.json` + `js/widgets/widget-manifest.json` with negated `.gitignore`
  rules (the `!package.json` exception pattern already exists); register both in
  `validate_critical_files.py`; drop the manifest's stale `/dashboard.html` shortcut.
- Fix the SW cross-origin kill (`sw.js:183-236`) using the same pattern as the documented
  image-route fix at `sw.js:238-251`; SW_VERSION bump.

### Wave 1 — Security + data-loss triage (single-finding PRs)
- XSS: auth.html `?setup=`; chat session-titles + reminder-messages (delete the duplicate
  7357–7469 function set in the same PR); notes DOM-built items/chips; journal live render
  path; calendar page-wide escapeHtml; memories review/search.
- Kill the demo-mode auth bypass on both login pages → honest "auth service offline" error.
- Calendar data-loss: spread `{...event.metadata}` into all four PUT bodies; read
  attendees/reminders/description from `event.metadata`; delete the two guaranteed-404 fetches.
- One-liners: `const subscription` (push-notifications.js:132) + orb-loader logout listener
  on `document`; then verify one real end-to-end push.
- Vendor chat's 7 CDN libs under `/js/lib/` (fixes local-first violation AND pairs with the
  Wave-0 SW fix); SW_VERSION bump; CSP CI stays green.

### Wave 2 — One foundation (tokens, theme, auth, nav, landing)
- Land `skybridge-ds.css` as THE token layer on desktop, before page styles; rename the
  colliding `--text-primary/-secondary/-tertiary` on the 4 pages defining them with
  opposite values; one-line `common.js` bridge setting `data-theme` alongside the
  dark-mode class; converge settings.html off its inverted third theme mechanism; early
  anti-flash snippet on calendar/dashboard/journal.
- Move `widgets-premium.css` + `widgets-fluid.css` under shared-token ownership and
  tokenize their off-brand gradients (inverts the fragile live-kiosk-depends-on-desktop-CSS
  arrangement: touch/lists.html:37 etc.).
- ONE auth surface: fold auth.html's unique flows (register, password-setup, remember-me,
  panel-bind, server session validation) into index.html; auth.html → redirect stub (the
  proven skybridge.html pattern); fix "Welcome, undefined"; consume the stored-but-never-read
  `zoe_redirect_after_login`.
- Landing page: **no repoint** — logins already land on `dashboard.html`, which is Jason's
  chosen landing surface. `dashboard.html` **stays in the SW precache**; fix the
  offline.html / clear-cache references to it rather than removing them.
- ONE shared nav (JS-injected via common.js): kills per-page drift, points cooking/smart-home
  at `/touch/` directly, drops Developer/Games entries; standardize `auth.js` + enforceAuth
  on every authed page (ends the logged-out 401/WS-churn half-render).

### Wave 3 — Retire the dead tier (retire-by-removing; each PR: critical-files manifest
update + SW_VERSION bump where precached + touch-consumer grep + panel smoke)
- Orphan pages: voice.html + touch/voice.html (keep shared `/ws/voice/` + livekit routes),
  week_planner_widget.html, games.html + touch/games.html + nav entries.
- Recovery pages → ONE clear-cache.html (v2 behavior, v1 name, absorbs clear-session).
- `dist/developer/` (4,406 lines) + both nginx `/developer/` blocks (separate PR — nginx/CSP is CI-audited) + More-menu entries.
- Orphan js/css set (see verdict table) + the 4 stale `dist/_preview/` dirs.
- Music-era zombies: mini-player.js + 6 script tags; music widget stack + MCPMusicStateManager
  alias block from BOTH dashboard.html AND touch/dashboard.html.
- Chat dead weight: chat-sessions.js (port escapeHtml-on-title + warm-on-open first),
  legacy pre-AG-UI SSE alias block, ai-processor.js + its memories.html tag.
- **Legacy touch pages — sequenced, NOT a blanket delete** (see the Direction table).
  Order within this wave:
  1. Delete the pages with no live inbound path: `touch/{dashboard,lists,calendar,notes,
     people,timers,weather,memories,journal,chat,games}.html`.
  2. Repoint `notifications-panel.js:345/:358` to the surviving updates surface, **then**
     retire desktop `updates.html` + `touch/updates.html` together (backend
     `/api/system/updates` stays). Neither dies before the deep-links move.
  3. After the Wave-2 nav repoint lands, delete the desktop `cooking.html`/`smart-home.html`
     stubs and their touch targets.
  4. Decide `touch/settings.html` (reachability + whether desktop settings absorbs it).
  5. **Last:** delete the `touch/js/touch-{menu,nav,widgets}.js` **files** once no page loads
     them; then prune the dead `auth.js` `TOUCH_PATH_TO_PAGE_ID` entries, the
     `orb-loader.js` skip-list entries, and the `sw.js:735` cache route.

  **The shared scripts are BOTH referrer and consumer — treat the entries and the file
  separately.** `touch-menu.js` carries an 18-entry link registry (`:17-39`, incl.
  `updates` at `:38`) and `touch-nav.js` a 12-path `PAGE_ORDER` swipe array (`:14-25`),
  each pointing at pages in this deletion set. So **every step above must prune that page's
  menu + nav entries in the SAME PR that deletes the page** — otherwise any page still
  loading the shared menu (settings/cooking/smart-home, live until steps 3-4) renders a tile
  that navigates to a deleted page. The step-5 "delete last" rule applies to the **files**,
  not their entries. Note the `_nav` allowlist (`touch-menu.js:540-558`) does **not** save
  you here: a deleted-but-still-listed path is still in `allowed`, so it navigates to a 404
  rather than falling back to home.

  Panel smoke after each step. Closes skybridge-cutover PR 5 for the genuinely dead pages.
- **NOT retired here** — carve-outs that a blanket sweep would have broken:
  - `touch/voice.html` — **live** `lets_talk` voice navigation (`chat.py:71`); replay-gated;
    retires with the Ask-card cutover (PLANS Phase 1c), not in this overhaul.
  - `dashboard.html` (rebuilt in Wave 4b) and `music.html` (rebuilt in Wave 6) — Jason-designated
    desktop surfaces.

### Wave 4 — Chat as the deep-work workspace
- Revive Agent Activity (reattach on `container.isConnected===false` / move out of
  `#sessionsList`).
- Session persistence (URL/localStorage) + honour `?session=` on cold load + make
  proactive push-tap `loadSession()` — one persistence fix closes both P1s.
- action_menu via the action-registry pattern chat already uses (`:4742-4746`).
- Compose-card legibility on light theme (with the Wave-2 token PR).
- Gate touch-ui-executor init on touch context (desktop stops registering as a PANEL with
  2s/5s polling + duplicate `/ws/push`).
- Preview decision — **404-is-truth**: delete the iframe path + server `_detect_preview_urls`
  synthesis + the self-nullifying sandbox; resolve the `openclaw_ws.py:40` staging wording.
  (A real auth-gated preview route is a pinned deferred decision.)
- Module split: carve the 4,430-line inline script into `/js/chat/*.js` along the proven
  seams (SSE dispatcher / render catalog / sessions / notifications) — the node harness
  already proves no-build-step extraction.
- Orb decision on chat: handle `zoe.ui_orb_prompt` inline or load the orb (today the only
  dispatching page never loads the listener).

### Wave 4b — The dashboard: rebuild the landing page
The surface seen on every login. Rebuilt, not patched — the existing stack is
singleton-flawed, half-fake, and (post-island-retirement) serves only two desktop pages.
- Audit what survives: the verified-working widget data-bindings are **time, weather,
  events, notes (v2), and the four list types** (shopping/personal/work/bucket). Everything
  else in the library is broken or fabricated (project → 8 missing routes; week-planner →
  `/api/calendar/week` missing; system → `Math.random()` stats; tasks → wrong response
  shape, permanent "All tasks completed!"; home → dead instance ref; music widgets → dead).
- Rebuild the dashboard on the skybridge token layer as a real desktop home: those working
  bindings as tokenized cards, honest empty/error states, no fabricated data.
- Layout persistence: `routers/dashboard.py` already implements full per-user layout
  storage that the old client never called (`dashboard.js:424` was a `TODO`) — wire the
  rebuild to it instead of device-local `localStorage`.
- Migrate `lists.html` off the old stack, then delete the lineage: `widget-system.js`,
  `widget-base.js`, `dashboard.js`, `lists-dashboard.js`, `js/widgets/**`,
  `dashboard-protection.js`, `js/lib/{module-widget-loader,widget-registry}.js`, and the
  vendored gridstack.
- Wave-0's `widget-manifest.json` commit is a **stopgap** for the current stack; the rebuild
  should register its cards statically so no untracked runtime file can ever empty the
  landing page again.

### Wave 5 — PIM depth on tokens (bulk editing is what keyboards are for)
- people.html fix-by-deletion: remove canvas/polar/legacy-detail code; keep the
  card-grid/dp-tab CRM code that matches the live backend; tokens.
- memories.html descope to MemPalace review-queue + search; delete the dead
  collections/tiles canvas + its server stubs. (Collections-as-a-real-feature → IDEAS.)
- journal.html slim: one publish path with tags; wire Edit to the PUT route that already
  exists server-side; delete Journeys fiction, Unsplash demo entries, CDN FilePond, dead
  enhancement layer; photo UI returns only when `/api/media/upload` exists.
- lists.html: strip Projects/Marketplace/AI-Generate wings + the 115-line dead
  script-with-src block; fix or drop broken library widgets (tasks loader, home instance,
  system fake stats, week-planner).
- notes.html: tokens + wire-or-drop the color picker. Calendar: tokenize; single-source
  convergence with the touch fork recorded as a deferred, port-direction-aware step.

### Wave 6 — The admin console (settings) + closeout
- Real endpoints for Restart-Kiosk/Logs (e.g. `POST /api/panels/{id}/restart` + `/logs`
  over panel_ssh_exec) or drop the buttons; rebuild the Display card on the real
  preferences schema or delegate to touch Display settings.
- Close the rooms gap: panel→room assignment in desktop Touch Panels (the W2a/W2b backend
  landed touch-only — fleet admin is what the desktop console is FOR).
- Settings ownership split: desktop keeps admin (users, AI profiles, panel provisioning,
  rooms); device-local concerns delegate to touch; delete duplicated drifting sections;
  fix the Push section SW-registration hang; give settings the shared nav.
- **music.html rebuilt to touch parity** (Jason's call — a real desktop music surface, not
  a redirect). Target the proven API the touch card and jukebox already use: transport via
  `POST /api/music/control` ({action, player_id, value}) + `/api/music/seek`
  (music.py:292/:304); now-playing via `GET /api/music/now-playing` (normalized —
  title/artist/image/state/volume/elapsed/duration, music_service.py:228-270); queue via
  `GET /api/music/queue/{queue_id}` **re-fetched on `queue_updated`** (the MA event payload
  carries no items — trusting it is what wipes the list today). Replace the dead
  `/api/ha/service` transport, the never-populating now-playing card, and the
  ReferenceError-throwing nav. Search box → real `GET /api/music/search` +
  `POST /api/music/play_media` (jukebox.html:75-99 is the working reference), not a
  fire-and-forget chat turn. Feature parity target = the touch music card's contracts
  documented in services/zoe-ui/AGENTS.md (favourite-follows-focused-cover, poll-never-
  repaints-a-held-control). Then repoint `touch-ui-executor.js:762` "show music" and
  settings' "Go to Music" at it.
- Platform hygiene: offline.html probes `/health` + checks `res.ok`; sw-registration stale
  `v=` param + cargo-cult gtag removal; prune remaining superseded precache entries
  (dashboard.html **stays** — it is the landing page).
- **PWA polish (installable Zoe):** committed manifest verified installable, correct icons
  + shortcuts, install prompt surfaced; logged-out smoke gate green.
- DOX/PLANS closeout: update PLANS.md statuses, services/zoe-ui/AGENTS.md, critical-files
  manifest deltas; pin every deferred decision.

## Execution guardrails (all learned the hard way)

- Every dist file is a **critical file** — deletions go through the cleanup safety process
  + `validate_critical_files.py` manifest updates per PR.
- **SW_VERSION bump** whenever any precached file changes.
- **Touch-consumer grep before touching shared files** — the live kiosk loads desktop CSS/JS
  (`widgets-premium.css`, `widget-system.js`, `dashboard.js`, `js/widgets/core/**`).
  Panel smoke after every shared-file PR.
- nginx/CSP changes are CI-audited (`ensure_nginx_security_headers.py`) — pair audit+test
  updates in the same PR; keep nginx-only PRs isolated.
- Keep every PR under the Greptile ~50-file silent-skip threshold.
- Promote the audit's logged-out smoke run to a per-wave gate (curl statuses + console
  capture on the 6 key pages).
- Voice-path files are replay-gated; this plan deliberately touches none of the voice path
  (`voice.html` deletion removes only an orphan client page — `/ws/voice/`, livekit routes
  and the daemon path stay).

## Deferred decisions (pinned, not forgotten)

- ~~Desktop landing page~~ — **DECIDED (Jason, 2026-07-19): the dashboard.** Rebuilt as the
  landing surface in Wave 4b; not retired.
- ~~music.html redirect~~ — **DECIDED (Jason, 2026-07-19): rebuild to touch parity** on
  `/api/music/*` (Wave 6); not a redirect.
- ~~Legacy touch pages~~ — **RESOLVED by verification (2026-07-19): orphaned, retire them**
  (Wave 3). Open sub-question: `touch/settings.html` reachability.
- Real `/​_preview/` route (auth-gated, distinct origin) vs. permanent 404 — deferred; 404
  chosen for now (Wave 4).
- Calendar single-source collapse (desktop↔touch fork) — after tokens + compose mature;
  desktop metadata fix must port INTO the touch fork first.
- Memory collections as a real backed feature — product decision → IDEAS.md.
- Thin Tauri desktop shell (hotkey/tray/computer-use) — parked arc → IDEAS.md.
