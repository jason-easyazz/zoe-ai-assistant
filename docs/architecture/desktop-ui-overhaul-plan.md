---
type: architecture-plan
status: proposed (audit complete + verification pass complete; waves not started)
owner: jason
date: 2026-07-20
---

# Desktop UI Overhaul — audit + executable plan

**What this is:** the full deep-dive over the desktop web UI (`services/zoe-ui/dist/` root
pages + `js/` + `css/` + `sw.js` + nginx) — what's live, what's broken, what's dead — and the
sequenced plan to get it up to scratch. This is also the re-scope the
[skybridge cutover plan](skybridge-cutover-plan.md) left open (its PR 3/PR 5).

**Provenance — two passes, and the second one matters:**
1. **Audit (2026-07-19):** 145-agent workflow — 10 area audits, **329 client-called endpoints
   verified against zoe-data source** (negative-controlled), every P1/P2 adversarially
   re-verified (6 refuted, 105 confirmed), a live logged-out smoke pass, a 3-stance direction panel.
2. **Verification pass (2026-07-20):** 8 agents re-checked every *remedy* in this plan against
   code, after four external-review P1s all landed on retirement sequencing rather than on
   audit findings. **~40 corrections, a dozen P1.** The lesson, now a standing rule:

> **The audit was reliable about what code DOES. Nearly every error was in what this plan
> proposed to DO about it.** Diagnosis ≠ remedy. Every remedy below has now been code-verified;
> when adding a new one, verify it the same way — especially "just call the existing helper"
> and "delete the duplicate", which produced the two worst errors in the first draft.

## The verdict in three sentences

The desktop surface splits into a **live core with real value** (chat.html, settings.html,
calendar.html, notes, lists, jukebox, setup-*), a **broken-but-alive tier** (24 confirmed P1s:
8 XSS sink areas, calendar data-loss, dead people.html, placebo controls, a PWA manifest that
isn't in git), and a **verified-dead tier** (~30k+ lines). 61 of 329 client-called endpoints
are MISSING or MOVED — the honest measure of the rot. Direction: **desktop is Zoe's
keyboard-first deep-work power surface**; the estate panel stays the ambient face.

## Direction

- **Desktop = the deep-work face** (chat workspace, bulk PIM editing, fleet/admin console).
- **One foundation, two faces:** the touch `skybridge-ds.css` token layer becomes THE token
  layer for both surfaces; auth, nav, theme consolidate to one of each.
- **Honesty-by-removal** is the default for UI with no backend — but only after verifying the
  backend is really absent *and* that the UI is really unreferenced (see Referrer classes).
- **Decisions (Jason, 2026-07-19):**
  - **Landing page = the dashboard.** `dashboard.html` is **rebuilt** (Wave 4b), not retired.
    Logins already land there — no repoint needed; it **stays** SW-precached.
  - **`music.html` is rebuilt to capability parity**, not redirected (Wave 6).
- **Installable Zoe:** PWA polish rides this overhaul. A thin **Tauri shell** is a parked arc
  in [IDEAS.md](../IDEAS.md) — it wraps the same served UI and never grows its own screens.

## Referrer classes — check ALL FIVE before deleting any page

Each of these produced a P1 during review. This is the single most load-bearing list in the doc.

1. **Page links** — `href`/`location.href` in other pages, and the desktop meta-refresh stubs.
2. **Shared menu registries** — `touch-menu.js:17-39` (18 entries), `touch-nav.js:14-25`
   (12-path `PAGE_ORDER`), `touch-widgets.js:521` (a `smart-home.html` link *inside* a shared
   script). These **emit** links: they are referrers, not just consumers. Prune entries with
   each page; delete the files only when no page loads them.
3. **Server-driven navigation** — `chat.py:61-71` `PAGE_ROUTES`, `voice_tts.py` `panel_navigate`
   payloads. **Replay-gated when on the voice path.**
4. **Client voice-intent map** — `touch-ui-executor.js:754-771` `_buildPageMap()`. See the
   Wave-2 prerequisite: it is ONE map resolved by context, not two.
5. **Infrastructure** — `sw.js:116-128` precache + `sw.js:735` cache routes, nginx locations,
   `auth.js:13` `TOUCH_PATH_TO_PAGE_ID`, `orb-loader.js:11` skip-list,
   `notifications-panel.js:345/:358` deep-links, **and both critical-file manifests**.

**Not nav sources** (cleanup, not reachability): `auth.js` reverse-map, `orb-loader.js`
skip-list, `sw.js` cache routes, `voice_tts.py:526-530` supersede/cancel list.

## Page verdicts

| Page | Verdict | Why |
|---|---|---|
| `chat.html` | **keep-fix** | The flagship; AG-UI dispatcher + compose bridge are good. 6 P1 fixes, dead code excised, module split (harder than it looks — see Wave 4) |
| `settings.html` | **keep-fix** | Real admin console, ~44/46 calls live. Loads **zero `<script src>`** — a prerequisite for shared nav/theme |
| `calendar.html` | **keep-fix** | Richest desktop page; P1 metadata data-loss + zero escaping. **Sole surviving source** once Wave 3 deletes the 93%-identical touch fork — no convergence step needed |
| `notes.html` | **keep-fix** | Healthiest PIM page. **One root cause (`escHtml` misses `'`), two exploitable sinks + one latent** |
| `lists.html` | **keep-fix** | Core lists work. Dead-code strip is a **Wave 4b prerequisite**, not Wave 5 |
| `people.html` | **redo (fix-by-deletion)** | Dead since 2026-05-18. Deleting the canvas is **not sufficient** — sidebar+search route through `showPersonDetail`; repoint in the same commit |
| `memories.html` | **redo (descope)** | Collections/tiles canvas has no backend. MemPalace review/search are live and back a *richer* surface than the page uses. **XSS sinks are in the surviving code** |
| `journal.html` | **redo (slim)** | Photo upload 404s, Journeys is fiction, CDN/Unsplash externals. `PUT /api/journal/{entry_id}` **does** exist |
| `music.html` | **redo (rebuild)** | Rebuild on `/api/music/*` at capability parity (Wave 6) |
| `index.html` | **keep-fix (the ONE auth surface)** | nginx index + SPA fallback; absorbs auth.html's flows |
| `auth.html` | **fold into index.html → stub** | Reflected DOM XSS via `?setup=`. **`index.html:1036` must be rewritten in the same PR or it loops** |
| `dashboard.html` | **redo (rebuild as THE landing page)** | Jason's call. Rebuild on tokens; salvage the 8 working widget bindings |
| `updates.html` | **retire desktop copy; KEEP `touch/updates.html`** | The panel deep-links only to the touch copy — it is the repoint target, so it must survive |
| `voice.html` (desktop) | **retire** | True orphan. **`touch/voice.html` is NOT retired — see below** |
| `touch/voice.html` | **KEEP — not this overhaul** | Live `lets_talk` target (`chat.py:71`, `voice_tts.py:1111`); replay-gated. Retires with the Ask-card cutover (PLANS Phase 1c) |
| `touch/smart-home.html` | **KEEP** | **780 lines of live HA control** (`/api/ha/{states,areas,control,scene}`) — the only smart-home UI in the product. No estate replacement exists (`chat.py:58-60`) |
| `touch/cooking.html` | **KEEP (flag to IDEAS)** | 678 working lines, but `localStorage`-only with no backend. Keep-and-back vs retire is a product decision |
| `touch/music.html` | **KEEP — decide its entry point** | Live, healthy, the panel's **only** search-and-play surface. Wave 3 step 5 would orphan it (see Wave 3) |
| `jukebox.html`, `setup-music.html`, `setup-device.html` | **keep-polish** | QR-linked, verified against live routes |
| `offline.html` | **keep-fix** | Probes `/api/health` (real route is `/health`) and never checks `res.ok` |
| `clear-cache{,-v2}.html`, `clear-session.html` | **consolidate to ONE** | Orphan near-triplicates; `_v2` violates repo rules |
| `games.html` + `touch/games.html` | **retire** | Chain dead-ends in a 26-line placeholder |
| `cooking.html`, `smart-home.html` (desktop) | **retire the 11-line stubs only** | Meta-refreshes. Their touch targets **stay** |
| `week_planner_widget.html`, `dist/developer/`, `dist/_preview/*` | **retire / see Wave 4** | Orphan mock; 4,406-line dead prototype. `_preview` is NOT dead — see Wave 4 |
| Legacy touch pages (11) | **retire** | `touch/{dashboard,lists,calendar,notes,people,timers,weather,memories,journal,chat,games}.html` — no live inbound path |
| Widget stack (`widget-system.js`, `widget-base.js`, `dashboard.js`, `lists-dashboard.js`, `js/widgets/**`) | **retire after the Wave 4b rebuild** | Serves desktop dashboard + lists only once the touch copies go |
| `lib/gridstack`, `dashboard-protection.js` | **KEEP** | Drag-and-drop is retained (Jason, 2026-07-20), so the grid engine stays and the corrupt-layout guard matters more, not less |
| Orphan js/css set | **retire** | `navigation.js`, `js/lib/{module-widget-loader,widget-registry}.js`, `js/voice/*`, 4 unloaded music widgets, `mini-player.js`, `chat-sessions.js`, `ai-processor.js`, `components/zoe-orb.html`, `css/{glass,memories-enhanced,widgets-enhanced}.css` |

## The triage register (23 P1 + item 11, a P2 promoted for privacy blast radius)

**Deploy integrity**
1. `.gitignore:155` blanket `*.json` — the PWA `manifest.json` AND `widget-manifest.json` exist
   only untracked. **They are actively gitignored: `git add` is refused without `-f`** — use a
   negation rule (`!package.json` at `:156` is the existing pattern).
2. SW cross-origin kill (smoke-reproduced): `sw.js:183-236` routes cross-origin no-cors
   scripts/styles through NetworkFirst; chat.html loses **9 assets (7 scripts + 2 stylesheets)**
   on every repeat visit.

**Security (XSS + auth)**
3. `auth.html:1207` reflected DOM XSS via `?setup=`. **No escape helper in file — define one.**
4. `chat.html:3768` stored XSS via session titles. Helper exists (`chat.html:2852`).
5. `chat.html:7410` **and `:7108`** — the same sink in both duplicate copies. Fixing only one
   leaves the other live once the duplicate set is removed (hoisting decides which wins).
6. `notes.html:700` + `:769` — root cause is `escHtml` (`:633`) not escaping `'`. `:700`
   **bypasses the helper entirely** (single-quoted attribute around `JSON.stringify`). Plus a
   **latent** sink: `style="border-left-color:${color}"` goes live the moment colour persists.
7. `journal-api.js:365` — live render path. **Define a LOCAL helper**: `journal-api.js` is also
   loaded by `touch/journal.html:677`, which has no `escapeHtml`, so a cross-file reference is a
   `ReferenceError` on the live kiosk. (#895 patched *neither* renderer.)
8. `memories.html:2312`/`:2362` — no helper in file; sinks are in the **surviving** code.
9. `calendar.html:2539` — zero escaping page-wide; no helper in file.
10. Demo-mode client-side auth bypass — **four sites, not two**: `index.html:1043` (password),
    `:1105` (PIN), **`:1152` (guest — no credential check at all; taking zoe-auth offline mints a
    session)**, `auth.html:892` (four credential pairs). Nothing depends on it.
11. `orb-loader.js:60` — logout purge listens on `window`; `auth.js` dispatches on `document`.
    Orb transcripts survive logout. (P2 severity, promoted: privacy on shared machines.)

**Data loss**
12. `calendar.html:3582/:3622/:3702/:3790` send `metadata:{linked_tasks}` only; `calendar.py:178`
    replaces metadata wholesale → destroys description/prep/attendees/reminders. Also `:3925`/`:3940`
    read from two nonexistent routes (404-tolerant reads; the *write* path that wiped them lived
    only in the touch fork, which Wave 3 deletes).

**Broken user-facing features**
13. `people.html:880` null-canvas crash kills all init.
14. `music.html:1327` transport → `/api/ha/service` (never existed); `:1064` reads `current_item`
    off a **player** instead of the **queue**. The page never calls `/now-playing` at all.
15. `agent-activity.js:145` feed wiped by `loadSessions()` at **three** sites (`chat.html:3758/:3765/:3788`).
16. `chat.html:5628` action_menu onclick is syntactically invalid.
17. `chat.html:7488` proactive push-tap never opens the session.
18. `chat.html:6176` preview iframe 404s — **but the feature is ~90% built, not dead** (see Wave 4).
19. `chat.html:2598` compose cards unreadable in light theme.
20. `push-notifications.js:132` strict-mode `ReferenceError` kills every subscribe.
21. `settings.html:2230` Restart-Kiosk/Logs → nonexistent `/api/tools/call`; `:1338` Display card
    is a placebo.
22. `dark-mode-shared.css:145` white-on-white dark mode on 4 pages.
23. Widgets: `tasks.js` returns a **silent 200** with the wrong shape (permanent "All tasks
    completed!"); `system.js` fabricates stats with `Math.random()`; `week-planner.js` calls a
    nonexistent route **and isn't even loaded by lists.html**; `project.js` needs ~5-6 missing routes.
    **`home.js` is NOT broken** — its endpoints exist and its instance ref is assigned; it is dead
    only because `widget-registry.js` is loaded by nothing.
24. `index.html:1175` "Welcome, undefined!" on every real login.

**Endpoint truth:** 329 checked → **51 MISSING + 10 MOVED**.
**Logged-out smoke:** dashboard/calendar/lists half-render and error-storm (calendar: 7 failed
API calls with literal `user_id=undefined` + ~20 failed WS handshakes in 10s).

## Execution waves

### Wave 0 — Deploy integrity + the smoke harness
- Commit `manifest.json` + `js/widgets/widget-manifest.json`.
  **⚠ SOURCE THEM FROM THE LIVE CHECKOUT — they do not exist anywhere else.** Both files are
  untracked *and* gitignored, so they are present ONLY in `/home/zoe/assistant` (verified
  2026-07-20: `dist/manifest.json` 2652 B, `dist/js/widgets/widget-manifest.json` 7356 B, and
  nginx serves the latter **200** today). A clean clone or worktree has nothing to stage — which
  collides with this plan's own guardrail to work in a worktree. **These are the only copies in
  existence; if the live box loses them there is no recovery.** Sequence:
  1. `cp /home/zoe/assistant/services/zoe-ui/dist/manifest.json <worktree>/services/zoe-ui/dist/`
     and the same for `dist/js/widgets/widget-manifest.json` (copy first — do not `git checkout`
     over them, and never run a cleanup that deletes untracked files in the live checkout until
     this has merged).
  2. Add `.gitignore` **negation** rules (`!services/zoe-ui/dist/manifest.json`,
     `!services/zoe-ui/dist/js/widgets/widget-manifest.json`) — a plain `git add` is *refused*
     under `.gitignore:155`, and `git add -f` would stage them while leaving the ignore rule to
     bite the next file.
  3. Verify with `git ls-files --error-unmatch <both paths>` before pushing — existence checks
     pass on the live box whether or not tracking actually worked.
  4. Register in **both** critical-file manifests (see guardrails).

  **The PWA manifest's `/dashboard.html` shortcut is correct — leave it** (dashboard is the landing page).
- Fix the SW cross-origin kill: add `url.origin === self.location.origin` to the three
  script/style predicates (`sw.js:183/:201/:221`), the same **remedy** as the documented image-route
  fix at `:238-251`. (The mechanism differs — those routes permit opaque caching via
  `statuses:[0,200]` — but the remedy is right and same-origin caching is unaffected.) **SW_VERSION bump.**
- **Write the smoke harness** — `tools/verification/ui_smoke.sh`: curl status for every root page +
  headless-chromium console capture on index/dashboard/chat/calendar/lists/settings, logged out.
  There is **nothing to "promote"** — the audit's run was ad-hoc and uncommitted. Every later wave gates on it.

### Wave 1 — Security + data-loss triage (single-finding PRs)
- XSS, per register items 3-9. **Define a local escape helper in each of `auth.html`,
  `memories.html`, `calendar.html`, and `journal-api.js`** — do not reach across files.
- Duplicate-function cleanup in chat.html: delete **only `:7366-7443`** (the true duplicates).
  **KEEP `openMoreOverlay`/`closeMoreOverlay` (`:7358-7365`, called from `:2614`/`:2636`) and
  `updateTimeDate` (`:7445`, called 4×).** Fix the surviving sink at `:7108`.
- Kill all four demo-bypass sites.
- Calendar data-loss: spread `{...event.metadata}` into all four PUTs; read
  attendees/reminders/description from `event.metadata`; delete the two 404 fetches.
- One-liners: `const subscription` (push-notifications.js:132); orb-loader logout listener → `document`.
- Vendor chat's **9 CDN assets (7 scripts + 2 stylesheets)** under `/js/lib/` + `/css/lib/`.
- **SW_VERSION bump** — `chat.html`, `calendar.html`, `index.html`, `push-notifications.js` are all
  precached. Without it these security fixes never reach existing clients.

### Wave 2 — One foundation (tokens, theme, auth, nav)
- **PREREQUISITE — split `_buildPageMap()` before any Wave-3 deletion.** It is ONE map resolved by
  `_page()` context (`touch-ui-executor.js:751-771`); pruning "the touch entries" also kills desktop
  voice-nav to 8 pages we keep. Split into `DESKTOP_PAGES` / `TOUCH_PAGES`. Fold in the pre-existing
  bug: `_page('weather.html')`/`_page('home.html')` resolve to desktop files that don't exist.
- **Token layer — the link position is NOT the fix.** Every colliding `:root` token lives in an
  inline `<style>` that source-orders after any `<link>`, so a linked sheet loses on equal
  specificity. The colliding definitions must be **removed from the inline blocks**; model the link
  position on `dark-mode-shared.css`, which is deliberately linked last. **5 desktop pages** define
  `--text-primary/-secondary` (people, journal, dashboard, lists, memories); only 3 also define
  `--text-tertiary`.
- **Theme:** add `data-theme` alongside the dark-mode class in `common.js:80-87` — **and in the ~11
  per-page inline head snippets**, which never delegate to `common.js`; patching only `common.js`
  leaves `data-theme` unset through first paint. (All 11 pages already have the anti-flash snippet.)
  Converging settings.html off its inverted mechanism is a small CSS surface (2 rules) but a
  **visible default change** ('dark' → 'auto') on the page that shows the theme picker.
- **Stamp `data-theme="light"` on chat.html** — a hard dependency of the Wave-4 compose-card fix.
- Move `widgets-premium.css` + `widgets-fluid.css` under shared-token ownership (the live kiosk
  currently depends on desktop CSS).
- **One auth surface:** fold auth.html's flows into index.html; auth.html → redirect stub.
  **`index.html:1036` sends users to `/auth.html?setup=` — rewrite it in the same PR or the stub
  loops forever.** Flows to port: `?setup=` receiver, register, remember-me, panel-bind
  (`:1080-1089`), server session validation, **and `redirectToApp()` + its open-redirect guard
  (`:1170-1180`)** — index.html has no `?redirect=` handling at all, and `touch/pair.html:241`
  depends on it. Do **not** port `handleForgotPassword` (an `alert()` stub). Consume
  `zoe_redirect_after_login` on desktop (it *is* read — at `touch/index.html:718`).
  `auth.html` is a **critical file in both manifests**.
- **One shared nav.** Specify the **complete post-Wave-3 entry list**, not a delta — and **delete the
  hardcoded per-page nav markup in the same PR** (13 pages carry their own More-menu + mobile-nav
  blocks; adding a shared nav without removing them leaves ~40 links to 404). Keep cooking/smart-home
  pointing at `/touch/` (those targets survive). Drop Developer/Games. Keep Updates pointing at
  `touch/updates.html`.
- Add a `<script src>` to **settings.html** (it loads none) or it gets neither nav nor theme.
- Standardize `auth.js` + enforceAuth on every authed page — ends the logged-out 401/WS churn.
- **SW_VERSION bump** (`index.html`, `dark-mode-shared.css`, `js/auth.js`).

### Wave 3 — Retire the dead tier (sequenced; referrers first, targets second)
1. Delete the 11 orphaned legacy touch pages. **Prune each page's `touch-menu.js`/`touch-nav.js`
   entries and its `TOUCH_PAGES` voice-map entry in the SAME PR.**
2. **Retire desktop `updates.html` only — `touch/updates.html` SURVIVES** as the deep-link target
   (`notifications-panel.js:345/:358`, loaded on 20 pages). No repoint needed.
3. Delete **only** the two 11-line desktop `cooking.html`/`smart-home.html` stubs. **Their touch
   targets stay.**
4. `dist/developer/` (4,406 lines) + both nginx `/developer/` blocks (separate nginx-only PR).
5. Orphan js/css set — **split into a CSS PR and a JS PR** (~25 files together).
6. Music-era zombies: `mini-player.js` + 6 tags; the dead music widget stack + `MCPMusicStateManager`
   alias from both dashboards.
7. Chat dead weight: `chat-sessions.js` (port escapeHtml-on-title + warm-on-open first), the legacy
   pre-AG-UI SSE alias block, `ai-processor.js`.
8. **Shared scripts — NOT a single step.** `touch-nav.js` and `touch-widgets.js` have **zero
   consumers today** — delete them freely. **`touch-menu.js` SURVIVES this overhaul**: `touch/voice.html:166`
   loads it and that page is kept until Phase 1c. Trim it to the entries voice.html needs. **Decouple**
   the `auth.js`/`orb-loader.js`/`sw.js:735` entry-pruning from the file deletion, or the whole tail deadlocks.
9. `orb-loader.js:11` — prune the `/touch/chat.html` and `/voice.html` entries **only**;
   **`/touch/voice.html` stays**, or the surviving voice surface gets a duplicate floating orb.
10. **Decide `touch/music.html`'s entry point BEFORE step 8** — its only four referrers are the files
    being pruned, and `home.html` doesn't link it. Options: (a) route home's Browse to it,
    (b) fold `/api/music/search` into home's browse surface then retire it, (c) accept losing panel
    search. **Recommend (b).**
11. `touch/settings.html` — resolve reachability before it loses its menu.
12. **Leave `voice_tts.py:526-530`'s supersede list stale.** Editing it takes a replay gate; a
    cosmetic cleanup is not worth one.

**NOT retired here:** `touch/voice.html`, `touch/smart-home.html`, `touch/cooking.html`,
`touch/updates.html`, `touch/music.html`, `dashboard.html`, `music.html`.

### Wave 4 — Chat as the deep-work workspace
- Revive Agent Activity (reattach on `!container.isConnected`; survives all three wipe sites).
  Data source verified live (`/api/agent/tasks` → `system.py:1174`).
- **Session restore and the push-tap are TWO fixes, not one.** URL/localStorage persistence handles
  cold reload; the push-tap needs its own `proactive_session` listener because (a) on cold load the
  claim POST is still in flight when `init()` runs, and (b) the warm-tab path fires long after
  `DOMContentLoaded`, so an init-time URL read can never run.
- action_menu via the action-registry pattern at `:4742-4746`.
- Compose-card legibility — **requires the Wave-2 `data-theme="light"` stamp**; linking
  `skybridge-ds.css` alone resolves `--sky-ink-0` to `#ffffff`, i.e. the current bug made explicit.
  (Collision risk is nil: chat.html has zero `var(--text-*)` references.)
- **Stop loading `touch-ui-executor.js` on chat.html** (delete `chat.html:7334`). Do **not** add a
  context branch inside that file — **21 live pages load it**, including the estate. Chat has its own
  push socket and needs nothing from the panel machinery.
- **Preview: REVERSE the earlier 404-is-truth call.** The staging is real and populated —
  `scripts/preview/stage_widget.py` writes to `dist/_preview/`, four staged widgets exist on disk,
  and `docker-compose.yml:22` already mounts `dist` as the docroot, so nginx's `return 404` is the
  **sole** reason they're unreachable. The feature is ~90% built. **Fix it instead:** serve
  `/_preview/` behind the existing auth gate and drop `allow-same-origin` from the sandbox
  (`chat.html:6178`). Collateral to fix: `_PREVIEW_WIDGET_URL_RE` matches a path that doesn't exist;
  `zoe-widget-builder/SKILL.md` calls a nonexistent `zoe_promote_preview` tool.
- **NEW — separate item: `zoe-page-builder` writes straight into `services/zoe-ui/dist/`**
  (`SKILL.md:3,40,68`), never staging. That is an agent with a live prod-write path into the docroot
  and needs its own decision.
- **Module split — much harder than the first draft claimed.** The cited harness proves only
  *syntactic* separability (it `vm.Script`-parses without executing and text-slices 8
  deliberately-dependency-free functions). Real blocker: **50 functions are reachable only via
  inline `on*=` attributes** (87 sites, ~30 built inside template literals and invisible to static
  analysis), and chat.html has exactly **one** `window.X =` assignment — everything else works by
  implicit global hoisting. A move breaks all 50 **silently at click time**, with no test catching it.
  Either 50 explicit `window.*` re-exports or a real delegation refactor. Also: new modules ride the
  generic `script` SW route (`NetworkFirst`, `maxEntries: 60`) — watch for a stale module against a
  fresh chat.html. Do not name a module `test_*.js` (nginx 404s that pattern).
- Orb prompt: handle inline. Lower priority than it looks — there is already a visual + TTS fallback.
- **SW_VERSION bump** (chat.html).

### Wave 4b — The dashboard: rebuild the landing page
- **Prerequisite: strip lists.html's dead code first** (Projects wing, Marketplace, AI-Generate, the
  114-line block at `:2377-2492` that never executes because its `<script>` has a `src`). Doing it
  after means re-editing a freshly migrated file.
- **DECIDED (Jason, 2026-07-20): KEEP drag-and-drop.** So **`lib/gridstack` SURVIVES and is NOT in
  the deletion set.** This is a **re-skin + re-wire on a kept engine**, not a layout rebuild — the
  scope is materially smaller than "rebuild the dashboard" implies. What changes: rendering
  (`innerHTML` templates → tokenized cards), registration (manifest-driven `window[className]` string
  lookup → static), persistence (localStorage → the server API). What stays: gridstack, the drag/
  resize/edit-mode UX, and the 8 working data-fetch paths.
- Salvage the **8 verified-working** widget data-fetch paths: time, weather, events, notes,
  shopping/personal/work/bucket. **Re-evaluate `home.js` as a survivor** — its HA endpoints are live.
- **MUST-FIX, and keeping drag-and-drop is what makes it mandatory: duplicate widgets break at THREE
  layers, not one.** A drag-and-drop dashboard with a widget library is precisely the surface where a
  user adds two of the same card, so all three must be fixed together or the bug just moves:
  1. **Frontend singleton** (`widget-system.js:223`) — `WidgetManager` registers ONE instance per
     type and `addWidget` re-`init()`s it per grid item, so two cards of the same type clobber each
     other's `this.element` and update timers. Instantiate **per grid item**.
  2. **Server dedupes by type** — `AVAILABLE_WIDGETS[].id` (`dashboard.py:13-30`) is a **type**
     identifier (`"weather"`, `"events"`), and `_requested_widget_ids` (`:64-81`) drops repeats
     (`if wid in valid_ids and wid not in seen`). So `POST /widgets/` silently discards the second
     same-type card.
  3. **The layout schema has no instance identity.** `PUT /layout/` (`:96`) does not dedupe — it
     stores raw jsonb — but that does not rescue it: two weather cards both carry `id: "weather"`,
     so any id-keyed restore collapses them. **Layout entries therefore need an instance key
     separate from the type key** (e.g. `{uid, type, x, y, w, h}`). This supersedes the earlier
     "emit `id` instead of `type`" instruction, which would have thrown away the only field
     distinguishing instances. `LayoutProtection` validates on `item.type`, so keeping an explicit
     `type` field also keeps that guard working.

  Decide whether the enabled-widget set keeps flowing through `POST /widgets/` at all — if it does,
  its type-dedupe has to be reconciled with instance identity too.
- **KEEP and update `dashboard-protection.js` (`LayoutProtection`).** With drag-and-drop retained, a
  corrupt-layout guard is more valuable, not less — it validates shape before save/load. But note it
  validates on `item.type`, so it must change **in lockstep** with the schema reconciliation below.
  (It is currently loaded by lists.html only; the rebuilt dashboard should load it too.)
- **Wire layout persistence** — `GET/PUT /api/dashboard/layout/` (`dashboard.py:84/:96`) has **zero
  callers repo-wide**. Keeping gridstack makes this *easier*: `saveLayout()` (`dashboard.js:409-421`)
  already walks `grid.engine.nodes`, so the geometry is right at the source. The reconciliation is
  narrow — **keep `type` and add a unique instance key** (see the duplicate-widget item above; do NOT
  collapse onto the server's type-level `id`, which is what makes two same-type cards indistinguishable),
  and drop `order` (gridstack's `x`/`y` already encode position, so it is redundant). Then extend
  `AVAILABLE_WIDGETS` (`:13-30`), a hardcoded 16-id allowlist that silently drops unknown cards.
- Static card registration is **easier than it looks**: `widget-registry.js` is loaded by no page, so
  the 24 `WidgetRegistry.register()` calls are already dead code. Nothing to fight.
- Keep the widget settings sheet — a customizable dashboard implies per-widget configuration.
- Then migrate `lists.html` onto the rebuilt stack and delete the **superseded lineage only**:
  `widget-system.js`, `widget-base.js`, `dashboard.js`, `lists-dashboard.js`, `js/widgets/**`,
  `js/lib/{module-widget-loader,widget-registry}.js`. **`lib/gridstack` and `dashboard-protection.js`
  are NOT in this set.** Consolidate the two divergent `Dashboard` classes onto the
  **`lists-dashboard.js` lineage** — it is the maintained one (user-scoped storage keys,
  `LayoutProtection`, deferred-widget fixes the desktop copy never received).
  **Split into ≥3 PRs** — `js/widgets/**` alone is 28 files and the whole set exceeds the ~50-file
  Greptile silent-skip threshold. Naming traps: **`dashboard-protection.js` belongs to lists.html;
  `mcp-client.js` belongs to dashboard.html** — the opposite of what they sound like.
- Remove the dead `generateWidgetWithAI` button from `dashboard.html:1918` too, or the rebuild inherits it.
- **SW_VERSION bump** (dashboard.html).

### Wave 5 — PIM depth on tokens
- **people.html:** delete the canvas/polar/draw cluster **and repoint `showPersonById` (`:1344`) and
  `selectPerson` (`:1889`) to `dpOpenCard` in the same commit** — the surviving sidebar and search
  route through `showPersonDetail`, so deleting alone turns the crash into a `ReferenceError` on
  every click. Drop the `updateLegend()` call at `:2081`; add `renderCardGrid()` to `toggleFilter`.
  Fold in the shape gaps (`metadata` never served, `address` written nested/read flat, dropped
  `health_score`/`circle` fields, `PUT /api/user/profile` 405).
- **memories.html:** descope to MemPalace review + search — **and fix the `:2312`/`:2362` sinks, which
  live in the surviving code**. The backend already exposes more than the page uses (paginated list,
  people view, export, opt-out, forget) — worth surfacing.
- **journal.html:** wire Edit to `PUT /api/journal/{entry_id}` (it exists; the `:2085` comment names
  the wrong prefix). Delete Journeys fiction, Unsplash entries, CDN FilePond. **Salvage
  `journal-ui-enhancements.js`, do not delete it** — its autocomplete half is dead by script order,
  but `showEntryModal` and `publishEntryEnhanced` own the live view and publish paths.
- **notes.html:** fix `escHtml` to escape `'`, rebuild `:700` with DOM APIs (it bypasses the helper),
  and escape the latent `border-left-color` sink. The colour picker drops at the **Pydantic** layer
  (`models.py:171-185`, `extra='ignore'`) — a real fix spans migration + model + INSERT.
- **lists.html:** token migration only — the dead-code strip moved to Wave 4b, and the broken library
  widgets are inside Wave 4b's deletion glob. **Do not repair widgets days before deleting them.**
- **calendar.html:** tokenize. **No convergence step** — Wave 3 deletes the 93%-identical touch fork,
  so desktop becomes the sole source by deletion. Remove its two 404-tolerant reads.
- **SW_VERSION bump** (lists.html, calendar.html, notes.html).

### Wave 6 — Admin console, music, closeout
- **settings.html:** real endpoints for Restart-Kiosk/Logs or drop the buttons; rebuild the Display
  card on the real schema or delegate to touch; add panel→room assignment (the W2a/W2b backend landed
  touch-only); ownership split vs `touch/settings.html`; fix the Push section's SW-registration hang.
- **music.html rebuilt — capability parity, not layout parity.** The estate music *card*
  (`touch/home.html`) and `touch/music.html` are **two different surfaces**; neither alone is "the
  touch music experience". Desktop owns transport/scrub/volume/favourite/speaker **plus** the
  keyboard-native things the panel lacks: full search and a **real queue editor**
  (`/queue/move|remove|clear|play-index|save` are live and used by **no** surface today). Drop Cover
  Flow, gestures, dock condensation.
  - `POST /control` `{action, player_id?, value?}` — `play|resume|pause|play_pause|stop|next|previous`,
    `volume_up|volume_down|mute`, `volume_set`, `shuffle_set`, `repeat_set`. The router docstring
    (`music.py:293`) is stale — trust `music_service.control`.
  - `POST /seek` `{position_seconds}` — **absolute integer seconds**.
  - `GET /now-playing` (`music_service.py:228-292`) returns **16 fields**; use `queue_id` to fetch the
    queue and **`queue_item_id` to detect track changes** (title-diffing misses a repeat).
    **There is no `uri`** — a favourite heart reading one ships dead. Null `elapsed`/`duration` ⇒ radio ⇒ no scrubber.
  - `GET /queue/{queue_id}` — send the **normalized `index`** to `/queue/play-index`; MA's raw index is 0 for every item.
  - Cross-surface contracts to honour: a repaint must never clobber a held/focused control; un-favourite posts `/unfavorite`.
  - **Prefer polling `/api/music/*` over the direct MA WebSocket.** Doing so removes the sole
    justification for the **unauthenticated** `/modules/music-assistant/` nginx passthrough
    (`nginx.conf:216-229`, duplicate at `:501`) — retire it in a separate nginx-only PR once
    `setup-music.html` covers the MA-UI popup.
  - Referrers need **verification, not repointing** — the path stays `dist/music.html`, so
    `touch-ui-executor.js:762` (context-resolving) and `settings.html:963` keep working. **Do not
    hardcode `:762` to the desktop path** or voice "show music" breaks on the kiosk.
- Platform hygiene: `offline.html` → `/health` + check `res.ok`; sw-registration stale `v=` param and
  cargo-cult gtag removal. **Keep `dashboard.html` precached.**
- **PWA polish (installable Zoe):** verify install from the committed manifest.
- Drop `cdn.jsdelivr.net`/`unpkg.com` from all four CSP headers once Wave 1's vendoring lands.

## Execution guardrails

- **Critical files — there are TWO manifests and they have already drifted.** Every deletion PR must
  remove the path from `CRITICAL_FILES` in `tools/audit/validate_critical_files.py` **and** from
  `critical_files` in `.zoe/manifest.json` (the Python dict lists 11 html_pages; the JSON lists 8).
  `validate_critical_files.py` is deliberately **not** in pre-commit — a bad deletion passes locally
  and fails only in CI. Pre-registered files this plan deletes: `glass.css`, `memories-enhanced.css`,
  `ai-processor.js`, `components/zoe-orb.html`, `widget-system.js`, `widget-base.js`, and the 8
  `js/widgets/core/*.js`.
- **`docs/governance/CLEANUP_SAFETY.md` is partly unexecutable** — its STEP 2 tool
  (`tools/audit/find_file_references.sh`) and `docs/CRITICAL_FILES_DO_NOT_DELETE.md` **do not exist**.
  Grep for referrers by hand using the five referrer classes above. **Never run its `git add -A`
  safety commit in the live checkout** — `data/music-assistant/*.db-wal` is always dirty.
- **SW_VERSION: unconditional check, every PR, every wave.** Diff the PR's file list against the
  precache array at `sw.js:116-128`; if any listed path is added, edited, or deleted, bump
  `SW_VERSION`. Deleting a precached entry requires removing it from the array in the same PR or the
  SW install fails on a 404. **A security fix that doesn't deploy is not a fix.**
- **nginx/CSP is NOT actually CI-audited.** No CI job runs `ensure_nginx_security_headers.py` against
  the real `nginx.conf`, and `missing_headers()` checks header *names*, never values — so "CSP stays
  green" is trivially true and would remain true through a silent weakening. Run the check manually
  and diff the CSP string by eye. Deleting the `/developer/` blocks trips nothing (they carry no
  `add_header`). Wave 4's `/js/chat/*.js` needs no nginx change (extension-matched).
- **Greptile ~50-file threshold — a silently skipped review looks identical to a clean one.** Verify
  with `gh pr view <n> --json files --jq '.files|length'`. Wave 4b's lineage deletion exceeds it;
  split into ≥3 PRs. Split Wave 3's orphan set into CSS and JS PRs.
- **Test coverage is thin and asymmetric.** Five pytest-wrapped node harnesses run in CI and **parse
  HTML as text** — deleting a constant or script tag can redden a lane from a file you never opened.
  The 8 `dist/test_touch_*.js` playwright suites are **local gates only, no CI lane**; run the relevant
  ones after any shared-file or estate-facing PR and **look at the PNGs**.
- **Smoke gate per wave** using the Wave-0 harness, **plus one voice-nav command per retired domain** —
  a visual smoke cannot catch referrer classes 3 and 4.
- **Voice path.** Desktop `voice.html` is a safe orphan delete. **`touch/voice.html` is NOT deleted in
  this overhaul.** Any PR editing `voice_tts.py` — including pruning its stale supersede set — is
  replay-gated: `scripts/maintenance/voice_regression_probe.py` under
  `flock /tmp/zoe-voice-harness.lock` against `~/.zoe-voice-samples`, no said-vs-did or speed
  regression. Prefer leaving that set stale.
- **Rollback + kiosk verification per deletion PR.** State the rollback in the PR body (`git revert` +
  a fresh SW_VERSION bump, since a reverted precache entry needs one to reach clients). After
  merge+deploy, confirm the deleted path 404s **and the estate still boots** — the kiosk loads
  `touch/home.html`, so a broken shared script shows up there, not on desktop. Check the panel console.
- **Never run a PR-merge driver against `/home/zoe/assistant` while wave work is open in a worktree.**
  Edit in `~/.worktrees/<slug>`; sync the live checkout only with `git merge --ff-only origin/main`.
  Serena's shared server is pinned to the live checkout — read via Serena, edit with file tools.
- **DOX closeout per PR, not at the end.** `services/zoe-ui/AGENTS.md` carries the contracts these
  waves invalidate; update it in the same PR. Never place an `AGENTS.md` under `dist/`.
- **Confirm a file is tracked before deleting it** (`git ls-files --error-unmatch`). `.gitignore:155`
  already hides two load-bearing files; an untracked file cannot be reverted.

## Open decisions

1. ~~Dashboard drag-and-drop~~ — **DECIDED (Jason, 2026-07-20): keep it.** `lib/gridstack` survives;
   Wave 4b is a re-skin + re-wire, not a layout rebuild. Consequences folded in: the singleton flaw
   becomes must-fix, `LayoutProtection` is kept and updated with the schema, and the widget settings
   sheet stays.
2. **`touch/music.html` entry point (Wave 3 step 10)** — route home's Browse to it / fold search into
   home and retire it / accept losing panel search. **Recommend folding in.**
3. **`/_preview/` revival (Wave 4)** — fix the two-line nginx block + sandbox, or delete a ~90%-built
   feature? **Recommend fixing.**
4. **`zoe-page-builder`'s direct prod-write into `dist/`** — acceptable, or must it stage?
5. **`touch/cooking.html`** — back it with a real API, or retire? It works but is `localStorage`-only.
6. **`touch/settings.html`** — reachability and whether desktop settings absorbs it.
7. **Calendar/settings fork endgame** — calendar resolves itself via Wave 3; settings still needs an
   ownership split.
8. Collections-as-a-real-feature → IDEAS. Tauri shell → IDEAS (parked).
