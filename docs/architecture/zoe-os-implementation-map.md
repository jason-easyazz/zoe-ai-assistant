# Zoe OS: the implementation map

**Status 2026-11-15: a reading of the code, nothing built.** This page is the engineering companion to
[zoe-os-cutover-plan.md](zoe-os-cutover-plan.md). The plan says *what* moves, in what order, and what each phase must prove.
This page says *where in the code* each move lands, what is duplicated there today, what the kernel cannot yet say, and the work
packages people will build from. It does not change a phase or an owner decision (D1 to D9). Where the code argues for a change of
order, that is raised as a question in section 8, not made here.

How to read the claims:

- [code] means the line was read. A path with a line number is a [code] claim even when the tag is left off.
- [inferred] marks judgement.
- Nothing here was run. No test was run, no live state was read, and no flag value was looked at. "Default off" means the code
  default, not what the box runs.

Path shorthand:

- `zd/` is `services/zoe-data/` on `main` (read at fec650907, one commit behind `origin/main` 3826afa24).
- `ui/` is `services/zoe-ui/dist/`.
- `brain/` is `labs/flue-zoe-brain-2x/`, the live brain sidecar.
- The kernel trees are named by their pull requests, because none of them is on `main`:

| Short name | Branch and pull request | What it holds |
|---|---|---|
| **K-lab** | `labs/zoe-os-combined` (#2630) | `labs/zoe-os/`: the kernel, record, skills, twin and brain bridge |
| **K-shadow** | `feature/os-shadow-6-widen` (#2631, stacked on #2622 to #2625) | `zd/os_shadow.py`: the shadow hook for four verbs |
| **K-auth** | `feature/os-authority-1-kernel` (#2637) | the authority kernel: durable list rows, erase reach, `health` |
| **K-prod** | `feature/os-authority-2-listadd` (#2638) | `zd/os_authority.py`: list add decided by the kernel |

## 1. Summary

- **Seams.** 38 production seams are mapped, across 14 feature areas (section 2). Fewer than a third have a single function where
  a kernel call can sit today. The rest need a convergence refactor first. Those refactors are ordinary zoe-data work and can start
  now, ahead of the kernel.
- **Duplicated checks.** I counted at least 74 duplicated permission, visibility and guest checks across the list, reminder, alarm
  and notification paths:

  | Where | Count |
  |---|---|
  | List visibility SQL | 22 copies |
  | List resolvers with their own rules | 4 |
  | Guest predicates that disagree | 6, plus 5 literal guest-id checks |
  | Reminder visibility sites | 13, five distinct rules |
  | Alarm viewer sites | 8 |
  | Separate quiet-hours implementations | 3, plus 5 wrappers |
  | Places coding the fridge rule | 4 |
  | Places deciding who is a child | 4 (section 7, R7) |

- **Receipt sites.** There are 22 direct `write_receipts.record` call sites, all in process memory. Three receipt kinds are
  declared but never written.
- **List add under authority.** The biggest single finding is that #2638 does not reach the main spoken path. It hooks
  `add_items_to_list`, which the keyword intent path and the MCP tool use. The panel's skybridge voice add has its own INSERT, and
  so do REST and `pending_suggestions`. Converging those writes into one function is on the critical path.
- **Kernel trees out of step.** The authority kernel (#2637) is built on an older lab commit. It lacks the later forget, snapshot
  scrub and privacy fixes in #2630. It must be rebased before it can be trusted with real rows.
- **Member delete.** Deleting a member from the Settings page goes to zoe-auth, and zoe-auth never calls zoe-data's erase. So the
  plan's rule 5 ("forget and delete this member reach the kernel") holds for the API path but not for the button people press.

## 2. Production seams, in the plan's migration order

Each block covers:

1. the entry points;
2. where a kernel call belongs;
3. the duplicated checks;
4. the receipt sites;
5. what the kernel can and cannot say;
6. the forget reach;
7. the tests that pin today's behaviour.

### 2.1 Lists

**Tables and module.**

- `lists` and `list_items`: `zd/alembic/versions/0001_initial_schema.py:52,67`.
- Unique active name per owner: `0011_unique_active_list_names.py:54`.
- Visibility is `personal` or `family` (`zd/list_sharing.py:3-4`).
- `zd/list_service.py:1-18` calls itself the "single canonical list writer". It also says skybridge and `pending_suggestions` still
  carry their own INSERTs (`:16-17`), and `services/AGENTS.md` repeats the same follow-up.

**Entry layers shared by every list operation:**

| Layer | Path |
|---|---|
| Panel voice | `zd/routers/voice_tts.py:4640` → `skybridge_service.resolve_skybridge_request` (`zd/skybridge_service.py:2878`), lists dispatch `:3567-3584` |
| Panel orb / typed | `ui/touch/home.html:4931` → `POST /api/skybridge/resolve` (`zd/routers/skybridge.py:121`) |
| Keyword intents | `zd/intent_router.py:4182` `execute_intent`, executors `:5238-5256`. Callers: `zd/expert_dispatch.py:283`, `zd/routers/chat.py:1875,2580`, `zd/routers/ui_actions.py:779`, `zd/routers/system.py:2402` |
| Brain tools | `brain/src/tools/zoe-tools.ts:431,540,740,768`; writes gated at `:161`. They POST to `/api/system/intent-dispatch`, whose allowlist holds `list_add`, `list_remove` and `list_show` only (`zd/routers/system.py:2271-2273`) |
| MCP tools | `zd/mcp_server.py:1429` `_execute_tool`. The actor comes from `:1343-1384` and falls back to the household admin unless strict mode is on (`:1376`) |
| REST | `zd/routers/lists.py` (prefix `/api/lists`) |
| UI | `ui/js/widgets/core/shopping.js:689-823`, `personal.js:166-248`, `work.js:163-242`, `tasks.js:161`, `ui/touch/home.html:3701-3712`, `ui/chat.html:4798-4834` |

#### List seam table

| Operation | Entry points [code] | Where the kernel call belongs | Receipts today | Kernel can say | Kernel cannot say |
|---|---|---|---|---|---|
| **Add item** | (a) skybridge `_add_spoken_items` raw INSERT `zd/skybridge_service.py:4839`; (b) intents `zd/intent_router.py:3047` → `add_items_to_list` `:3083`; (c) MCP `zd/mcp_server.py:1506` → `:1532`; (d) REST `zd/routers/lists.py:344` → `create_item_record` `:363`, with no dedup and no lock; (e) ui_actions → intents `zd/routers/ui_actions.py:751-779`; (f) `zd/pending_suggestions.py:859-891`, raw INSERTs `:878,887`; (g) Omi review `zd/omi_review_apply.py:135-151` → `add_item_to_list`; (h) anticipation `zd/anticipation.py:612-622` | **No single seam.** #2638's hook `_kernel_decides` sits in `add_item_to_list` but only runs with `authority=True` from `add_items_to_list` (K-prod `zd/list_service.py:670-703`). It therefore covers (b) and (c) only. (a), (d) and (f) need to move onto one item-insert function first (WP-20) | `zd/list_service.py:605,650,683`; skybridge `:4863`; anticipation `:622`. None on REST or `pending_suggestions` | `list.add {item, list, asked}` (K-lab `zoe_os/brain/drivers.py:23-31`); durable rows in K-auth | list id, list visibility, household-owned lists, the panel's household actor, guests, quantity/category |
| **Remove item** | skybridge soft-deletes `zd/skybridge_service.py:5081`; intents **tick** it, `completed=1`, `zd/intent_router.py:3200`; MCP ticks `zd/mcp_server.py:1582`; REST deletes `zd/routers/lists.py:558` | **No single seam, and the meaning differs.** "Remove" deletes on two paths and ticks on two (WP-27) | Only `list_removed` at `zd/intent_router.py:3205` | `list.remove` in K-auth only (`shadow_daemon.py:56-78`) | tick vs delete; put back; remove by id |
| **Tick / untick** | skybridge `zd/skybridge_service.py:5184` (UPDATE `:5249`, "put back" `:1479`); REST PUT `zd/routers/lists.py:438` (dynamic UPDATE `:505`); UI toggles | none (WP-27) | none | nothing | the whole verb |
| **Rename item** | `list_service.rename_item_on_list` `zd/list_service.py:812`, with visibility checked inside the lock `:799`. Callers: skybridge `:5110`, intents `:3219`. Bypass: REST PUT text `zd/routers/lists.py:468` | **Single seam** except REST | `zd/list_service.py:887` | nothing | the verb |
| **Create list** | 7 write sites: REST `zd/routers/lists.py:157` (default `family`, `zd/models.py:70`); skybridge `:4603` (forced `personal` `:4615`), `:4758`, `_ensure_default_lists` `:4455` (run on every add, remove, complete or show); `zd/list_service.py:618`; `zd/pending_suggestions.py:878` (its own visibility rule `:876`); anticipation `:617` | none: 5 raw list INSERTs | anticipation only | nothing (lists are implicit by name) | list objects |
| **Rename / delete list** | REST only, `zd/routers/lists.py:239,306` (UPDATE `:291`, soft delete `:335`), gate `_can_manage_list` `:91`. Maintenance script `scripts/maintenance/merge_duplicate_lists.py:110-123` | the REST handler (no voice path) | none | nothing | the verbs |
| **Share / visibility** | voice `zd/skybridge_service.py:4662` → `list_sharing.set_list_visibility` `zd/list_sharing.py:75` (owner only `:91-94`; household shopping locked `:106`). Bypass: REST PUT `visibility` `zd/routers/lists.py:276-278` takes any string and skips the owner rule for shared-owner lists | `set_list_visibility`, once REST calls it | skybridge `:4702` | nothing | visibility, sharing, the private-until-shared rule |
| **Clear / bulk** | no server verb. The UI loops per-item DELETEs (`ui/js/widgets/core/shopping.js:796-823`) | n/a | none | n/a | n/a |

**Duplicated checks (lists):**

- **22 copies** of `(visibility='family' OR user_id=?)`:
  - `zd/routers/lists.py`: 6 (`:76,147,213,258,324,415`)
  - skybridge: 8 (`:4439,4476,4676,4721,4737,4972,4989,5008`)
  - intents: 3 (`:2958,2967,3165`)
  - MCP: 4 (`:1479,1487,1566,1574`)
  - `zd/pending_suggestions.py:866`: 1
- **4 resolvers** with their own rules in `zd/list_service.py`: `:414,451,479,799`.
- **6 guest predicates that disagree** on which ids are guests:
  - `zd/panel_principal.py:115`
  - `zd/skybridge_service.py:3052`, which has no `guest-*` prefix
  - `zd/intent_router.py:3980`
  - `zd/guest_policy.py:269`
  - `zd/user_filters.py:39`
  - `zd/list_sharing.py:81-82`
  - plus 5 literal `{"guest","voice-guest"}` checks in skybridge (`:4875,5024,5142,5187,5276`).
- **The fridge rule**, "anyone at the panel may edit items on household lists", is coded in **4 places**:
  - `zd/routers/lists.py:49-53,86`
  - `zd/skybridge_service.py:2947-2950,3073`
  - `zd/intent_router.py:3977,4193-4203`
  - `zd/list_service.py:807-808`
- **No list path checks for a child** (grep). The authority table `zd/authority_table.json:17-20` allows children household lists,
  but it is log-only (`zd/household_authority.py:23-25`).

**Forget reach (lists).** On `main`, `lists` and `list_items` are GAP (`zd/forget_reach.py:145-146`). #2598 changes this: a member's
private lists and their items go, a family list stays, and items on a family list stay because an item records no author. That is
an "authorship gap" in its own inventory. The kernel's receipts *do* record who added each item [inferred from K-lab `receipts.py`],
so the kernel can close this gap. After a member delete it will also disagree with the read model unless the two are reconciled
(R4).

**Tests pinning behaviour:**

- `zd/tests/`: `test_list_service.py`, `test_list_add_postgres.py`, `test_list_direct_execution.py`, `test_list_item_correction.py`,
  `test_list_rename_postgres.py`, `test_list_target_privacy.py`, `test_voice_list_share.py`, `test_household_lists_panel.py`,
  `test_household_lists_skybridge.py`, `test_router_authz_regressions.py`, `test_skybridge_service.py`, `test_write_claim_guard.py`,
  `test_forget_reach_inventory.py`.
- `tests/unit/test_mcp_server.py`.
- `ui/test_lists_items_two_step.js`.
- `brain/test/write_path_http.test.ts`.

### 2.2 Reminders

**Model.**

- `reminders`: `zd/alembic/versions/0001_initial_schema.py:136-155`.
- `schedule_generation`: `0012_proactive_scheduled_retry.py:41,44`.
- Recurrence is an RRULE subset: DAILY, WEEKLY, MONTHLY or YEARLY; INTERVAL 1 to 52; BYDAY; BYMONTHDAY. COUNT, UNTIL and BYHOUR are
  refused (`zd/reminder_recurrence.py:8-11,47-101`).
- Date-only reminders fire at a default time, `ZOE_REMINDER_DEFAULT_TIME` (09:00) (`zd/proactive/triggers/reminder_scan.py:66,90-107`).
- Past dates are refused (`zd/reminder_service.py:57-63`).
- There is no upper horizon.

#### Reminder seam table

| Operation | Entry points [code] | Where the kernel call belongs | Receipts | Kernel can say | Kernel cannot say |
|---|---|---|---|---|---|
| **Create** | REST `zd/routers/reminders.py:93-100`; intents `zd/intent_router.py:2856-2890` (dispatched `:5263`); brain `brain/src/tools/zoe-tools.ts:620-641` → intent-dispatch; MCP `zd/mcp_server.py:1586-1600`; `zd/zoe_agent.py:261,2975,3212`; commitments `zd/commitments.py:637`; anticipation `zd/anticipation.py:601-610`; Omi review `zd/omi_review_apply.py:177-195`. **Bypass:** `zd/pending_suggestions.py:894-905`, a raw INSERT with NULL date and time | **Single seam:** `reminder_service.create_reminder_record` (`zd/reminder_service.py:219`), the shadow hook at K-shadow `:267`. The bypass must route through it (WP-40) | `zd/reminder_service.py:264` only | `remind.set {note, in_minutes 1..10080, asked}` (K-lab `drivers.py:52-60`) | absolute wall time, timezone, date-only, recurrence, anything past 7 days, visibility |
| **Edit** | REST PUT `zd/routers/reminders.py:192-281` (UI `ui/touch/home.html:3614`); MCP `zd/mcp_server.py:2357-2373`, which neither normalises the date nor reschedules nor bumps the generation | **No seam:** two SQL copies (WP-40) | none | nothing | the verb |
| **Snooze** | REST `zd/routers/reminders.py:316-378` (owner only `:334`); MCP `zd/mcp_server.py:2396-2432`, which has **no ownership check** (`:2399`) and rewrites the due time with a hard-coded UTC offset (`:2406-2416`) | **No seam** (WP-40) | none | the toy skill only (`zoe_os/skills/reminders.py:26`) | snooze on the brain path |
| **Done** | REST `zd/routers/reminders.py:381-443`. For a recurring reminder, done covers this occurrence only (`:401-415`) | REST handler | none | `reminder.ack` (skill only) | "this occurrence only" |
| **Delete** | REST `zd/routers/reminders.py:284-313`; MCP `zd/mcp_server.py:2375-2394`. The voice intent `reminder_cancel` is produced (`zd/conversation_context.py:113`) but has **no handler** | **No seam** | none (the `reminder_removed` kind is never written) | `reminder.delete` (skill only) | the verb on the brain path |
| **Fire** | `ReminderScanTrigger` `zd/proactive/triggers/reminder_scan.py:336-390` (registered `zd/main.py:1368-1374`) → `schedule_due_reminder` `:227-333` → APScheduler → `_fire_reminder` `zd/proactive/triggers/reminders.py:140-190` → `zd/proactive/engine.py:80-215` (quiet hours `:99-137`; spoken off by default `:218-228`; push `:169`; phone delivery `zd/reminder_delivery.py:158-209`) | `engine.fire_notification`, for the "may Zoe deliver now" question (Phase 5.2, last) | n/a | a `timer_fired` tick that makes an OFFER (skill), and `attention.route` (not called by the server) | quiet-hours deferral, channels, sensitive neutral text, adults-only phone delivery |

**Duplicated checks (reminders):** 5 distinct rules at about 13 sites.

1. Family-or-own SQL: `zd/routers/reminders.py:88-90,156` and `zd/mcp_server.py:1620,1627`.
2. Own only: `zd/mcp_server.py:2359,2377` and `zd/omi_review_apply.py:183`.
3. Owner-only snooze, done and visibility change, while any member may edit or delete a family reminder:
   `zd/routers/reminders.py:210-222,301,334,398`.
4. No check at all: MCP snooze.
5. Sensitivity by visibility: `zd/reminder_delivery.py:43-44,100-118`.

Role gates on top: `require_feature_access` (`zd/reminder_service.py:223`); guests read-only (`zd/guest_policy.py:171-174`); the
log-only `personal_write` class (`zd/household_authority.py:79`).

**Forget reach (reminders).** On `main`: GAP (`zd/forget_reach.py:149`), and so are `proactive_pending` and `proactive_scheduled`
(`:168-169`), which hold the message text. #2598 reaches them. [inferred] The APScheduler job kwargs carry the text
(`zd/proactive/triggers/reminders.py:233`).

**Tests:** `zd/tests/test_reminder_recurrence.py`, `test_reminder_date_only_and_relative.py`, `test_reminder_direct_execution.py`,
`test_reminder_scheduled_on_create.py`, `test_scheduler_reliability.py`, `test_reminder_phone_delivery.py`, `test_commitments.py`,
`test_anticipation.py`, `test_mcp_server_correctness.py`.

### 2.3 Alarms and timers

**Alarm model.**

- Tables: `zd/alembic/versions/0063_alarms.py:23-55` and `0070_alarm_overrides.py:22-29`.
- Up to 30 alarms per owner (`zd/alarms.py:40`).
- Repeat rules once, daily, weekdays, weekends, or `days:` (`:102-127`).
- Snooze 1 to 30 minutes, at most 8 snoozes (`:39-43`).
- Per-day overrides up to 366 days ahead (`:484,556`).
- The household owner id is used for panel alarms (`:36,210-211`).
- `ZOE_ALARMS` defaults to off (`zd/alarms.py:5,35`). The live value was not read.

**Alarms converge.** Every alarm write is in `zd/alarms.py`:

- create `:360`, update `:387`, delete `:439`
- snooze `:610`, dismiss `:631`
- override `:542,580`
- tick `:645`
- `forget_user` `:451`

That makes alarms the cleanest seam in the codebase.

**Alarm entry points:**

- REST: `zd/routers/alarms.py:58-155`.
- Panel: `ui/js/touch-alarms.js:123,216-236,509-574`.
- Voice: `zd/skybridge_service.py:2332,2977` → `zd/alarm_voice.py:507-675`.
- Brain, set and list only: `brain/src/tools/zoe-tools.ts:647-672` → `zd/alarm_voice.py:689-710`.
- Anticipation: `zd/anticipation.py:592-597`.
- Ringing is the server's `alarms.run_loop` (`zd/alarms.py:726-739`, started at `zd/main.py:1326`). The panel plays the tone. The
  voice daemon has no alarm logic.

**Timers.**

- They live in an in-process `_TimerStore`, snapshotted to a JSON file (`zd/skybridge_service.py:106-112,159-195`).
- Create: `:225-242,595-611`, intent `zd/intent_router.py:5198-5218`, brain `set_timer` (`brain/src/tools/zoe-tools.ts:557-607`).
- Cancel and adjust: `:293-305,683+`.
- REST: `zd/routers/skybridge.py:160-181`.
- Ring: an asyncio task queues a panel announcement, respecting quiet hours (`:356-418`).
- The default owner differs by path: `"guest"` (`zd/skybridge_service.py:591,684`) versus `"voice-guest"` (`zd/routers/skybridge.py:170,180`).

#### Alarm and timer seam table

| Operation | Seam | Receipts today | Kernel can say | Kernel cannot say |
|---|---|---|---|---|
| Alarm create | `alarms.create_alarm` `zd/alarms.py:360` (shadow hook K-shadow `:382`) | voice only, kind `timer` (`zd/alarm_voice.py:532,653,673`) | `alarm.set {time HH:MM}`, one per person (K-lab `drivers.py:80-92`) | several alarms, labels, repeat rule, once-date, sound, ramp, enabled, household owner |
| Alarm edit / enable | `zd/alarms.py:387` | none | nothing | the verb |
| Alarm delete | `zd/alarms.py:439` | none | nothing | the verb |
| Ring / auto-stop / missed | `zd/alarms.py:645-715` (compare-and-swap state) | `alarm_events` is a real action log (`:268-270`) | nothing | the ring state machine |
| Snooze / dismiss | `zd/alarms.py:610,631`; the panel may silence any ringing alarm (`:224-226`) | `alarm_events` | nothing | the verbs; "the panel may silence" |
| One-day shift / skip | `zd/alarms.py:542,580` (`zd/alarm_voice.py:608-675`) | voice only | nothing | overrides |
| Timer create / adjust / cancel | `zd/skybridge_service.py` `_TimerStore` | `:241,304` (cancel none) | nothing | timers as objects |

**Duplicated checks (alarms):** one model (`zd/alarms.py:203-226`) used at about 8 sites. The ringing route re-masks on its own
(`zd/routers/alarms.py:71-76`). Two viewer builders exist: `zd/routers/alarms.py:37-42` and `zd/alarm_voice.py:477-483`.

**Forget reach.**

- Alarms: REACHED (`zd/alarms.py:451-479`, called from `zd/memory_service.py:2437-2440`).
- Timers: **not inventoried**. The JSON snapshot keeps labels, and `zd/forget_reach.py` does not list it.

**Tests:** `zd/tests/test_alarms.py`, `test_alarm_ring_push.py`, `test_touch_alarms_harness.py`, `test_skybridge_timer.py`,
`test_timer_done_announce.py`, `test_timer_ring_owner_and_persistence.py`; `ui/test_touch_alarms.js`.

### 2.4 Write receipts and the claim guard

- **The store** is `zd/write_receipts.py`. It is in memory, 64 receipts per user, for 1 hour (`:49-50,62`). Receipts are ids only,
  never words (`:53-59`).
- **Kinds:** 12 (`:40-41`). `calendar_removed`, `reminder_removed` and `timer_removed` are declared but **never recorded** by
  production code (grep; only `zd/tests/test_session_poison_claims.py` uses them). [inferred] A true "I cancelled it" from the brain
  is therefore always judged unbacked.
- **22 production `record()` sites:**

  | Kind | Sites |
  |---|---|
  | list (7) | `zd/list_service.py:605,650,683,887`; `zd/skybridge_service.py:4702,4863`; `zd/anticipation.py:622` |
  | calendar (2) | `zd/calendar_service.py:141`; `zd/skybridge_service.py:3916` |
  | reminder (1) | `zd/reminder_service.py:264` |
  | note (1) | `zd/intent_router.py:3482` |
  | timer (5) | `zd/alarm_voice.py:532,653,673`; `zd/skybridge_service.py:241,304` |
  | memory (2) | `zd/intent_router.py:4761`; `zd/write_claim_guard.py:771` |
  | forget (2) | `zd/memory_forgotten.py:421`; `zd/memory_retire.py:400-402` |
  | list_removed (1) | `zd/intent_router.py:3205` |
  | device (1) | `zd/smart_home_service.py:667-678` |

- **The reader** is `write_claim_guard.supported` (`zd/write_claim_guard.py:710-729`), reached from `brain_dispatch._claim_guarded`
  (`zd/brain_dispatch.py:935-958`), which every brain turn ends in. The guard flag defaults to enforce (`zd/write_claim_guard.py:60`).
  [inferred] Deterministic fast-path replies do not pass through it.
- **Kernel seam.** `write_receipts.record` is a single function. Publishing to the kernel from inside it, in shadow (Phase 5.4),
  needs **one call site** (WP-30). The kernel's `ClaimAuditor` (K-lab `receipts.py:355-407`, durable consumption `spine.py:54`) can
  express "one receipt backs one claim". Production's "receipts with no turn stamp count for every turn" has no kernel counterpart.
- **Forget:** not inventoried. That is acceptable for an id-only, 1-hour store. A durable kernel copy changes that (section 4.9).
- **Tests:** `zd/tests/test_write_claim_guard.py` (74 tests), `test_false_write_claims_findings.py` (41),
  `test_session_poison_claims.py` (23), `test_write_claim_in_progress.py` (18).

### 2.5 Offers and anticipation

| Offer source | State | Store and seam |
|---|---|---|
| Anticipation engine | dark, `ZOE_ANTICIPATION` off (`zd/anticipation.py:43,74-80`) | `pending_suggestions` rows. Cooldown 24 h, 2 per day (`:53-56`). Entry points `zd/routers/chat.py:2182-2218`, `zd/intent_router.py:2527,4288`. A yes records a list receipt (`:614-622`) |
| Save offers | live, no master flag | `zd/pending_suggestions.py`, created by `zd/latent_intent_detector.py:292` after turns (`zd/routers/chat.py:1048`, `zd/routers/voice_tts.py:3577`). REST `zd/routers/proactive.py:169-210` |
| Contact offers | live | `zd/contact_offer_policy.py:1-12`, in-process state |
| Reminder-offer reply binding | dark | `zd/intent_router.py:2143-2149` |
| Voice-enrolment offer | live, **spoken unprompted** | `zd/voice_enrollment.py:320-366` → `voice_announce.enqueue_announcement` |
| Selector raises, pull/orb | dark | `zd/proactive/selector.py:1-9,67-68`; `zd/proactive/pull.py:293-294` |

**There is no single offer seam.** Two stores (`pending_suggestions`, in-process contact policy) and several gates exist. The
closest thing to an attention budget is `zd/proactive/policy_log.py:21-30`, which is log-only.

**What the kernel has and lacks.**

- It has `OFFER`, `OFFER_RESPONSE` and `ATTENTION` events (K-lab `events.py:23-32`).
- It has `attention.route` (`attention.py:49-70`) and yes-gated `needs_accept` (`receipts.py:335-352`).
- Attention settings live in memory (`attention.py:30-31`), and **nothing on the server calls `route`**. It is reached only from
  `replay.py:184`.
- There is no cooldown, no decline back-off and no channel beyond card, spoken or digest.

**Forget:** `pending_suggestions` REACHED (`zd/forget_reach.py:141`); anticipation's entity erase `zd/memory_forget_cascade.py:515-517`.
**Tests:** `test_anticipation.py`, `test_pending_offer_reply.py`, `test_contact_offer_once.py`, `test_offer_followup_binding.py`.

### 2.6 Notifications and the proactive engine

**Engine.** `zd/proactive/engine.py`:

- Tier 1 is APScheduler, Tier 2 a 300 s slow loop (`:1-10,408-494`).
- `fire_notification` is `:80-215`.
- Spoken delivery is off by default (`:218-228`).
- Slow triggers are registered at `zd/main.py:1374-1385`.

**Quiet hours: 3 production implementations.**

| # | Where | How it behaves | Consumers |
|---|---|---|---|
| Q1 | `zd/proactive/engine.py:34-35,70-77` | hour granularity, env read at import, no member preference | `engine.py:99,428`; `zd/proactive/arrival.py:433`; `zd/proactive/pull.py:277-283`; `zd/voice_enrollment.py:313-317`; `zd/mcp_server.py:3307-3310` |
| Q2 | `zd/comm_prefs.py:160-185` | minute granularity, env read per call, honours a member override | `zd/skybridge_service.py:355-362`; `zd/intercom.py:327`; `zd/reminder_delivery.py:176-191`; `zd/change_notices.py:818,864,878` |
| Q3 | `zd/voice_delivery.py:112-119` | own flags; changes tone only | — |

- No test asserts that Q1 and Q2 agree.
- `do_not_disturb` and `proactive_enabled` are stored (`zd/routers/stubs.py:74,80`) but **read by nothing**.

**Unprompted speech** all funnels through `voice_announce.enqueue_announcement` (`zd/voice_announce.py:120-166`). That function has
no quiet-hours, consent or role gate of its own. Callers:

- timer done (`zd/skybridge_service.py:364-412`)
- intercom (`zd/intercom.py:380-397`, dark)
- the enrolment offer (live)
- `zd/omi_linking.py:207`
- the engine's spoken lane (`zd/proactive/engine.py:231-297`, dark)
- the MCP `panel_announce` tool (`zd/mcp_server.py:2716-2728`), which has **no quiet check**

**Unprompted sends:**

- `fire_notification`
- `zd/reminder_delivery.py`
- `zd/change_notices.py`
- `zd/routers/push.py:132-200` `send_push_to_user`, which has **zero gates**
- 12 direct `INSERT INTO notifications` sites, e.g. `zd/distress_handoff.py:273-277`, `zd/mcp_server.py:2494`,
  `zd/routers/notifications.py:91`, `zd/proactive/engine.py:185`

**Seam.** There are three partial chokepoints: `fire_notification` (push), `enqueue_announcement` (speech) and
`notifications_store.create` (the bell, used by only some of the insert sites). Phase 5.6's "one gate" needs a new function that all
three call (WP-61). It needs one quiet-hours implementation first (WP-44).

**Forget:** `notifications` REACHED (`zd/forget_reach.py:161-162`). `voice_announcements`, `proactive_pending` and
`proactive_scheduled` are GAP on `main` (`:168-171`); #2598 reaches them.

**Tests:** `test_proactive_spoken.py`, `test_voice_announce_queue.py`, `test_intercom.py`, `test_pull_not_push.py`,
`test_notification_tail.py`, `test_reminder_phone_delivery.py`, `test_change_notices.py`.

### 2.7 Notes, journal, transactions

| Store | Writers [code] | Seam | Checks | Receipts | Forget on main / with #2598 |
|---|---|---|---|---|---|
| Notes | REST `zd/routers/notes.py:118,178,243`; intents `zd/intent_router.py:3445` (INSERT `:3464`); MCP `zd/mcp_server.py:1667,2435,2453`; `zd/pending_suggestions.py:926` | **none (4 modules)** | `require_feature_access` ×4 (`notes.py:91,125,186,250`); owner-only 403s (`:197,259`); family-or-own read (`:38-40`) | intents only (`:3482`) | GAP / REACHED |
| Journal | REST `zd/routers/journal.py:161,360,410`; intents `zd/intent_router.py:3492`; MCP `zd/mcp_server.py:2111` | **none (3 modules)** | ×9; always personal (`:69-71`) | none (no kind) | GAP / REACHED |
| Transactions | REST `zd/routers/transactions.py:71,233,297,327`; MCP `zd/mcp_server.py:2227`; voice intent parsed `zd/intent_router.py:1228,1237` with no direct executor | **none (2 modules)** | ×7; owner-only 403s (`:251-252,314-315`) | none (no kind) | GAP / REACHED |

[inferred] Deleting a note does not archive the palace fact it created (`zd/routers/notes.py:48-80`). The only production caller of
`archive_by_entity` is `zd/routers/people.py:570`. The kernel has none of these verbs. Phase 5.7 is "mainly for the forget
guarantee", and #2598 already delivers that in production. So the kernel's job here is receipts and why/undo, not erase. That
lowers the priority of these seams [inferred].

### 2.8 Calendar

- **One writer:** `calendar_service.create_event_record` (`zd/calendar_service.py:76`, receipt `:141`; rule in `services/AGENTS.md`).
  It is called from `zd/routers/calendar.py:305`, `zd/intent_router.py:3418`, `zd/mcp_server.py:1448`, `zd/calendar_ical.py:301` and
  `zd/omi_review_apply.py:129`.
- **Creates bypass it:**
  - `zd/skybridge_service.py:3894` (INSERT, receipt `:3916`)
  - `zd/pending_suggestions.py:912`
- **Edits and deletes bypass it:**
  - `zd/skybridge_service.py:4060,4205`
  - `zd/mcp_server.py:2336,2351`
  - `zd/routers/calendar.py:424,458`
- **Checks:**
  - `_calendar_writer` (`zd/routers/calendar.py:171-187`)
  - the read filter `:53-55`
  - the busy projection (`zd/calendar_service.py:445`)
  - `zd/calendar_read.py:248-253`
- **Feed import:** `zd/calendar_feed.py`, dark; nothing schedules `sync_account` (`:4,449`). **Calendar publishing does not exist**
  (`to_ics` `zd/calendar_ical.py:324` has no production caller).
- **Forget:** `zd/calendar_forget.py:31-56`; local family events stay, by owner decision.
- **Kernel:** no calendar verb. The `calendar_headsup` skill (K-lab `skills/calendar_headsup.py`) reads events only.

### 2.9 People and contacts

- **People rows are inserted by 9 modules** (contacts_store, pending_suggestions, intent_router, mcp_server, named_relations,
  person_extractor, routers/people, routers/voice_tts, skybridge_service).
- **The graph converges** on `zd/people_graph.py:404,421,444`, except raw UPDATEs in `zd/person_merge.py:412-426` and
  `zd/memory_forget_cascade.py:460,587`.
- **REST:** `zd/routers/people.py`, with 27 `require_feature_access` calls; `zd/routers/contacts.py`, 7 calls plus `require_signed_in`.
- **Forget:** contact tables are REACHED. `people` and the per-person tables are GAP on `main` (`docs/knowledge/forget-reach.md:181-202`).
  #2598 reaches private contacts and leaves family-visible ones (an authorship gap).
- **Kernel:** no people verb.

### 2.10 Presence

There are two unrelated notions:

1. **HA presence.** `zd/presence/ha_presence.py` writes hashed `presence_observations` (INSERT `:103-105`, prune 14 days `:38`). It
   is dark (`ZOE_HA_PRESENCE`), is never linked to a member, and its only reader is `zd/routers/presence.py:24`.
2. **Panel presence.** `zd/proactive/presence.py` reads `ui_panel_sessions`.
   - Writers: `zd/routers/ui_actions.py:185,190,449`, `zd/routers/panel_auth.py:1007`, `zd/routers/voice_tts.py:2162`.
   - Readers: `zd/proactive/engine.py:340`, `zd/proactive/arrival.py:436`, `zd/proactive/policy_log.py:135`.

#2593 adds a third (presence fusion). The kernel has no presence event kind beyond what K-lab's docs list as not built
(`docs/architecture/zoe-os.md:519-547`). The plan already parks presence at 5.8.

### 2.11 Memory writes and every forget path

- **Memory funnels through `MemoryService`** (`zd/memory_service.py`), with the authority layer `zd/memory_authority.py:12-29`.
- **31 writer modules** are named in `docs/knowledge/memory-writer-notes.json`; the generator is `tools/audit/memory_writer_map.py:43-57`.
- **Side stores** each carry their own forget function (`docs/knowledge/forget-reach.md:139-180`).

**Forget entry points: 17 (plus one un-forget).**

1. Admin member erase `zd/routers/memories.py:1538` → `MemoryService.delete_user` (`zd/memory_service.py:2232`).
2. Synthetic erase `:1602`.
3. One row `:335`.
4. Guardian forget `zd/routers/memories_curation.py:175`.
5. Voice entity forget `zd/intent_router.py:823,4368`, through the cascade `zd/memory_forget_cascade.py:267`.
6. Voice topic forget `zd/intent_router.py:790` → `zd/forget_topic.py:265`.
7. "Forget that" `zd/turn_forget.py`.
8. MCP `zd/mcp_server.py:3155`.
9. Curation by voice `zd/memories_voice.py:651,701`.
10. Alias `zd/memory_forget_alias.py:357`.
11. Guest erase `zd/routers/voice_tts.py:7653`.
12. Unknown voice `:7715`.
13. Biometric deletes `:7921`, `zd/routers/face_id.py:221`, `zd/routers/omi_voices.py:225`, `zd/routers/voice_speaker_adapt.py:49`.
14. Register key `zd/routers/member_register.py:73-80`.
15. People delete `zd/routers/people.py:536,570`.
16. Contact field `zd/routers/contacts.py:84`.
17. **zoe-auth user delete** `services/zoe-auth/api/admin.py:246-309`, which **never calls zoe-data**. The Settings page calls only
    this (`ui/settings.html:1783`).

The un-forget is "keep the contact" (`zd/intent_router.py:797,4342-4362`).

**Where the kernel is told.** K-shadow hooks three of these: `delete_user` (`zd/memory_service.py:2287` in K-shadow), the entity
cascade (`zd/memory_forget_cascade.py:256`) and topic forget (`zd/forget_topic.py:365`). K-prod adds `os_authority.erase_member`
beside it (`:2294`). Both fail closed. **The other 14 entry points do not reach the kernel.** For the stores a kernel will hold
(list items, reminder notes, alarm labels), the ones that matter are:

- 5, 6 and 7 (the voice forgets);
- 4 (guardian);
- 8 (MCP);
- 17 (the UI member delete).

Items 5 and 6 are covered. Items 4, 7, 8 and 17 are not (WP-24, WP-34).

**The forget-reach inventory** (`zd/forget_reach.py`, doc `docs/knowledge/forget-reach.md:133-274`) lists 134 stores: 67 reached,
31 out of scope, 36 gaps. #2598 closes most database gaps.

### 2.12 The roster: members, children, guests

- **Accounts** live in zoe-auth `auth_users` (`services/zoe-auth/api/admin.py:134,174,246`). Roles are admin, user, family_member,
  child and guest (`services/zoe-auth/models/database.py:18-23`). No change is announced to zoe-data.
- **zoe-data `users`** is a lazy mirror filled by `INSERT … ON CONFLICT DO NOTHING` at five sites (`zd/database.py:91`,
  `zd/routers/chat.py:1156,2746`, `zd/intent_router.py:3583`, `zd/pending_suggestions.py:279`).
- **Children.** "Adult" means no minor row in `member_modes` *and* an adult role (`zd/manner_block.py:56,111-120`). The guardian is
  implicit: every adult admin (`zd/memory_curation.py:5-8`, `zd/memory_space.py:298-333`). There is no guardian table.
- **Guests:** sentinel ids (`zd/user_filters.py:17`); named guests (`zd/named_guests.py:7-24`, dark); the panel's household
  principals (`zd/panel_principal.py:64,69,214`).
- **What D6 needs.** D6 says "the main service pushes add and remove". There is no member-change event in the main service to push
  from. Account changes happen in zoe-auth; child status lives in `member_modes`. WP-23 has to create that source first.
- **Kernel today.** The roster is a hand-written `people.json` (K-shadow `zd/os_shadow.py:665-686`), read once at start. Every member
  is built as an adult with no guardian (K-auth `shadow_daemon.py:92`, `authority.py:273`) and auto-granted the brain's acts. A
  member added later is never proposed: `not_in_roster` falls back (K-prod `zd/os_authority.py:190-191`).

## 3. The kernel's gaps, as build items

Each item belongs to the kernel builder unless marked. "Blocks" names the first cut-over it holds up.

| # | Gap [code] | Build item | Blocks |
|---|---|---|---|
| G1 | K-auth is built on an older lab commit: its `spine.py` has 690 lines vs 841, and lacks `purge_key` / `scrub_snapshot` | rebase #2637 onto #2630's head; re-run the erase and privacy tests | list add (Phase 3) |
| G2 | List rows keyed by person only; default list "shopping" (`drivers.py:21-31`) | `list.add {list_id, list_name, list_type, visibility, owner}`; household-owned lists; an actor separate from the owner | panel adds, family lists (Phase 3 fallback cases) |
| G3 | No household or panel principal | `__household__` actor with the fridge rule as a kernel rule, matching `zd/list_service.py:807-808` exactly | panel voice adds leaving fallback |
| G4 | No guests or named guests (the GUEST role exists, `capability.py:20,101`, but is never created) | guest persons in the roster; named guests as guest persons with their own forget | guest cases leaving fallback |
| G5 | Children are adults; no guardian (`authority.py:273`) | the roster carries role and guardians; until D7's ceremony exists, a child's verbs answer `fallback_child` (today's rule) | D7 compliance for every authoritative verb |
| G6 | `list.remove` only in K-auth; no tick, untick, put back, rename item, create, rename or delete list | the list verb set, matched to the converged production seams (WP-27) | Phase 5.1 |
| G7 | `remind.set` takes relative minutes 1..10080 only (`drivers.py:52-60`) | absolute wall time plus the household timezone (DST-aware); date-only with the default time and the same-day 2-minute rule; no 7-day cap | Phase 5.2 |
| G8 | No recurrence | the same RRULE subset as `zd/reminder_recurrence.py:47-101`. Share one pure module, or pin both with one golden table (the kernel is stdlib-only) | Phase 5.2 |
| G9 | No reminder edit, snooze, done (one occurrence) or delete on the brain path; snooze only in the toy skill | the verbs, with today's owner-only snooze and done and family-wide edit and delete (`zd/routers/reminders.py:210-222,334,398`) | Phase 5.2 |
| G10 | One alarm per person, `HH:MM` only (`drivers.py:80-92`) | several alarms (cap 30), labels, repeat rules, once-date, overrides, enabled, snooze limits, household owner, "the panel may silence any ringing alarm" | Phase 5.3 |
| G11 | No timers | timer create, adjust and cancel as objects, or an explicit decision that timers stay out (they are ephemeral) | Phase 5.3 |
| G12 | Reminder and alarm stores in memory; grants, manifests, attention and sharing in memory (`capability.py:86`, `attention.py:31-32`, `twin/unusual.py:63`) | durable stores, crash-tested like the record (the 50-kill test pattern) | every verb after list add; grants before any revoke matters |
| G13 | No roster op: the roster is fixed at construction (`kernel.py:41`, `capability.py:82-84`) | wire ops `member_upsert {id, role, guardians}` and `member_remove`, with the second chaining to `erase_member` | D6; Phase 4 |
| G14 | Nothing calls `retention.sweep` (`retention.py:27`). In K-auth a sweep would drop list rows, which are rebuilt from receipts (K-auth README `:199`) | make list rows a store that survives the sweep, then a `sweep` op the night job calls (D5) | growth; Phase 4 |
| G15 | Same OS user as the skills; the two second-user tests need root and skip (`test_boundary_attacks.py:253-286`) | its own system user (de-root programme), and the two tests repeated on the box | the trust boundary; Phase 4 |
| G16 | `attention.route` not called by the server; no cooldown, back-off or channels | the attention service: durable settings, decline back-off, channels (push, Telegram, bell, spoken), an urgency override matching `force_send` (`zd/proactive/engine.py:97`) and the distress floor | Phases 5.5 and 5.6 |
| G17 | No receipt for removals; no turn-binding | removal receipts as ordinary `act` receipts; a `turn` field the claim guard can read | Phase 5.4 |
| G18 | An uncertain receipt has no resolution step (`docs/architecture/zoe-os.md:519-547`) | a reconcile op: an uncertain list-add receipt is settled against the read model (#2639's digest) | Phase 3 drift proof |
| G19 | No schema-change or backup design (same doc) | a `meta.schema` version, a migration runner, and a WAL-safe online backup op | Phase 1 backup |
| G20 | Credentials rewritten on every start; no rotation (integration doc item 12) | stable credential files with mode 0600 and a rotation op | Phase 3 (a restart must not break zoe-data's socket auth) |
| G21 | Speed: an everyday call is about 1.1 ms against a 0.7 ms target; forget p99 is about 73 ms; `explain` scans every refusal; forget and the WAL checkpoint run under the global lock (`spine.py:423`) | the plan's section 4 track | nothing blocks; voice-turn budget |
| G22 | Power loss untested; one unexplained failure in about 50 full runs (K-lab README `:165`) | a power-cut test and a flake hunt | authority on any verb [inferred] |

## 4. Everything around the kernel

### 4.1 CI: getting the kernel into `validate`

**Today [code]:**

- `pytest.ini:1-17` collects only `services/zoe-data/tests` (with `pythonpath = services/zoe-data`).
- None of the 33 kernel test files carries `ci_safe`. No workflow names `labs/zoe-os`.
- The `validate` aggregator needs exactly `{checks, zoe-auth, zoe-data-shard, unit-shard, collect}` and fails if that set drifts
  (`.github/workflows/validate.yml:467,503-508`).
- Shards are cut by time: an unlisted file costs 0.05 s per test (`tools/ci/pytest_shard_ledger.py:36,81-106`), and the ledger
  proves exactly-once execution (`tools/ci/shard_ledger.py:39-73`).

**What breaks if the lab tests are just added:**

1. `-m ci_safe` deselects all of them.
2. Putting them into either existing lane mixes `pythonpath = services/zoe-data` with the kernel conftest's bare helper names
   (`world`, `procs`). A future zoe-data module named like one of them collides. `tests/AGENTS.md:20` forbids two service trees in
   one invocation.
3. In `tests/unit` they also inherit `tests/unit/conftest.py` (`ZOE_HARNESS_FORCE=1`).
4. The root-only tests skip forever under CI, which `tests/AGENTS.md:21` forbids without a rationale.
5. They start subprocesses and Unix sockets (`tests/procs.py:34-64`). They need the offline and `TZ=UTC` proof (`tests/AGENTS.md:17`).
6. `labs/AGENTS.md:24-55,97-99` says lab code is never in CI.

**Recommended [inferred].**

- Use a separate step inside the `checks` job, on the HA-bridge pattern (`validate.yml:191-205`):
  `pytest -o faulthandler_timeout=240 <kernel>/tests`, with its own `PYTHONPATH`, no zoe-data on the path, and `--strict-markers`.
- This keeps the aggregator's `needs` set unchanged, so `tests/unit/test_ci_instrument_honesty.py:213-315` needs no edit.
- It needs:
  - a `_STEP_BUDGET_MIN` row (`tests/unit/test_required_gate_workflows.py:452-462`);
  - the job budget re-summed (`:464-475`);
  - a `LANES` entry (`scripts/maintenance/affected_tests.py:88-110`);
  - the root-only skips allow-listed with a reason;
  - the README line `labs/zoe-os/README.md:61` changed.
- The suite is about 12 s of process start-up (`README.md:58`), small against the 25-minute budget.
- `.github/workflows/*` is landing-session-owned: builders prepare the change and the landing session applies it (builder brief,
  rule 5).

### 4.2 Where the kernel lives

The repo's rules:

- `labs/` is isolated from the runtime and never in CI (`labs/AGENTS.md:24-55,97-99`).
- The precedent for "lab-hosted but live" is the brain and the Telegram bot, deployed from `labs/` on any diff and named as
  temporary until promoted (`labs/AGENTS.md:10-15,56-85`; `.github/workflows/deploy.yml:609-734`).
- `services/` is production (`services/AGENTS.md:5,9-17`).

Recommendation [inferred]:

- Move the kernel package and its tests to `services/zoe-os/` before Phase 1 (WP-03), with its own `AGENTS.md` and a Child DOX line.
- The twin, the benches and `brain-lab-app` stay in `labs/zoe-os/` and import the service package.
- Do not copy the brain's lab-hosted exception: it was named temporary, and a trust kernel run from an experiment folder contradicts
  D2's reason for being.
- **One wording clash to settle:** `services/AGENTS.md:25` says "No `home`/`family`/`household` concepts in kernel code". That rule
  means Zoe's core router and schema. A Zoe OS kernel that models household-owned lists (G2, G3) would read as a violation. The
  child `AGENTS.md` must state the scope (Q5).

### 4.3 Deploy

- `.github/workflows/deploy.yml` restarts zoe-data (`:544-601`), the brain (`:609-673`) and Telegram (`:681-734`), and health-checks
  zoe-data only (`:736-759`).
- It never installs a unit or runs `daemon-reload` (grep). Only units already installed are restarted, guarded by `list-unit-files`
  (`:616-620`).

**Change:** a "restart zoe-os if changed" step on the brain's pattern:

- the `list-unit-files` guard;
- `git diff --quiet "$OLD_SHA" HEAD -- services/zoe-os/`;
- restart **before** zoe-data (zoe-data's client already tolerates a dead kernel through the fallback);
- a socket `health` probe added to the final check;
- mirrored in `scripts/maintenance/deploy_live.sh`, the manual twin named at `deploy.yml:122-125`.

The docs-only skip regex (`deploy.yml:567`) must not be reused for the kernel step. The kernel has no dependencies (stdlib), so it
needs no install step. The first unit install is an owner step.

### 4.4 The systemd unit and memory protection

- **Two templates exist, and they conflict with each other** (`Conflicts=`):
  - K-auth `scripts/setup/systemd/zoe-os-shadow-kernel.service` (MemoryMax 256M);
  - `labs/zoe-os/zoe-os-authority-kernel.service:20-51` (MemoryHigh 64M, MemoryMax 96M, MemorySwapMax 0).

  [inferred] One unit with a mode argument (shadow or authority) is simpler and removes a class of "both installed" mistakes.
- **The unit goes in `scripts/setup/systemd/zoe-os.service`** with:
  - `MemorySwapMax=0`, a `MemoryLow` floor and a `MemoryMax` ceiling (the plan requires one);
  - `NoNewPrivileges`;
  - no `Nice` or `OOMScoreAdjust` (silently dropped, `scripts/setup/systemd/zoe-data.service:88-90`).
- **Pins and installers to update:**
  - Add it to `NO_SWAP_UNITS` in `tests/unit/test_systemd_memory_protection.py:53-64` (and the ceiling tables `:73-134`).
  - Add a row to the unit table in `scripts/setup/systemd/README.md:11-32`.
  - Add it to `OPTIONAL_UNITS` in `scripts/setup/install-jetson.sh:54` until go-live, then to `SYSTEMD_SPINE` (`:45`).
- **Ordering:** `Before=zoe-data.service`, not `Requires=`, because a kernel fault must not stop zoe-data (D2).
- Unit files are landing-session-owned.

### 4.5 The voice replay gate and the regression probe

- **The gate lists do not cover the kernel.** `labs/` is covered only for the live brain
  (`scripts/maintenance/voice_gate_check.py:232-242`). `REUSE_SAFE_PREFIXES` treats any other `labs/` path as voice-irrelevant
  (`:705-709`). So `labs/zoe-os/**` takes the no-op pass today, and so would `services/zoe-os/**` (it is not under
  `services/zoe-data/*`, `:549`).
- **Change 1, the patterns.** Add the kernel package and its unit to `VOICE_PATH_NARROW_PATTERNS` and to `REUSE_EXTRA_PREFIXES`
  (`:652-673`). Mirror the narrow entries into `.github/workflows/greptile-gate.yml:282-336`, or `tests/unit/test_greptile_gate_workflow.py:278-306`
  goes red. Timing matters: the deploy gate runs the copy of `voice_gate_check.py` that is *already deployed* (`:604-605`). So this
  change must deploy **one landing before** any kernel change sits on the voice path. That means before shadow goes on, because the
  shadow hook runs inside list add, reminder create and alarm create.
- **Change 2, a kernel in the probe.** `voice_regression_probe.py` replays through `scripts/perf/measure_voice.py` with zoe-data
  imported in-process (`zd/tests/replay_samples.py:64-67,416,589`), and it loads `.env` with `setdefault` (`:88-96`). Nothing starts
  a kernel. With the authority flag on and no kernel reachable, the probe would measure the fallback only.
  - Give the probe a scratch kernel (a temporary state dir and socket, started and stopped by the probe, under the harness lock).
  - Point the replay's in-process client at it through `ZOE_OS_SHADOW_DIR` / `ZOE_OS_KERNEL_DB`.
  - Run each corpus clip once with the authority flag `off` and once with `kernel`.
  - Add a said-versus-did row: for a list-add clip, the kernel's receipt and the `list_items` row must both exist and agree (WP-07).
  - Never point the probe at the live kernel: replay adds would be real receipts.

### 4.6 Ground truth, health and self-heal

- **`scripts/maintenance/zoe_ground_truth.sh`.** Add the unit to `UNITS` (`:73-74`). Add the kernel flags to the `env_of` loop by
  name only (`:105-107`). Add a `ZOE OS KERNEL` block after BRAIN LANE (`:112`) with unit state, flag names and values, socket
  `health` (alive, schema, `last_write_ok`), and the last agreement figures from the shadow report.
- **Health and crash loops.** `scripts/maintenance/zoe_health.py:57-63` (`SPINE`) gets a socket check, not a port.
  `scripts/maintenance/zoe_crash_loop_watch.py:688-689` (`OTHER_UNITS`) gets the unit.
- **Self-heal** (`labs/self-heal/`) turns failing tests into draft PRs and never restarts units. It needs nothing beyond the CI lane
  (4.1) [inferred].
- **Restart policy.** Nothing restarts a hung but alive kernel. zoe-data's circuit (K-prod `zd/os_authority.py:35-41`) counts
  `unhealthy`. A watchdog (`WatchdogSec` with an `sd_notify` ping from the kernel loop) is the systemd answer [inferred].

### 4.7 Nightly backup and the restore drill

- `scripts/setup/systemd/zoe-backup.service:17-20` runs the Postgres, MemPalace and host-snapshot backups. The host archive's sources
  come from `scripts/maintenance/zoe_offbox.py:199-222`. A kernel database under `~/.zoe/zoe-os/` is in neither.
- **A WAL database cannot be copied as files mid-write** [inferred from `journal_mode=WAL`, `spine.py:215-218`]. Add a kernel
  `backup` op that uses SQLite's online backup into the backup staging directory, and add that file as a `Source` in `zoe_offbox.py`.
- Add `check_kernel_db` (integrity check plus the event-count manifest) to `scripts/maintenance/zoe_restore_drill.py:584-620`, with
  tests in `tests/unit/test_zoe_offbox.py` and `test_restore_drill.py`.
- **Forget and backups.** Backups are kept 35 days, and a backup taken before a forget still holds the forgotten words. So today an
  erase is **not complete until the 35-day window has passed**; re-issuing the delete after a restore keeps the words out of the
  live store, but it does not remove them from the backup files. That falls short of "forgotten means forever", and the kernel must
  not inherit the gap silently. Waiting out the window is not an option: the owner's standing rule is that a forget is permanent by
  default. So one of two builds must be in place before cut-over (it applies to Postgres backups as much as the kernel's), and
  **erase is not complete until one of them is**: backups encrypted with a per-member key that a forget destroys, so the old
  words become unreadable at once, or a backup-aware forget that rewrites or drops the affected backups. Which of the two is an
  owner choice. The one chosen is written in the kernel's store entry in `forget_reach.py`, and a restore always re-issues every
  forget recorded since the backup was taken before the kernel serves a read.
- **Which comes first, stated.** The shadow (Phase 1) already puts real member content in the kernel's database, so the gap would
  open the day the shadow is switched on if that database were backed up. The rule chosen here: **the kernel's state directory
  stays out of every backup until the backup-safe forget is built.** It is in no backup today (above); WP-09a pins that with a
  test, so the shadow can be switched on without creating a copy a forget cannot reach. Under shadow that costs nothing: production
  is still the authority and a lost kernel database is rebuilt. WP-09 (which adds the kernel to the backup) therefore **depends on
  WP-09b, the backup-safe forget**, and both must be done before Phase 3, because an authoritative kernel must be backed up. Shadow
  code keeps landing flag-dark meanwhile.
- **Open PRs on the same files:** #2516 (off-box backup) and #2514 both touch `zoe_offbox.py` and `zoe_restore_drill.py`. Build this
  after them.

### 4.8 Night window

- `scripts/night/night_window.py:71-74` stops zoe-data, the router, Kokoro and the brain, and restores them in reverse.
  `presleep_units` (`:503`) runs the backup.
- **Decision to record** [inferred recommendation]: the kernel is **not stopped** by the window. It is about 20 MB resident (K-auth
  unit comment `:15`), and zoe-data is stopped anyway.
- The retention sweep runs as a presleep step after the backup, so the backup always holds the pre-sweep record. Rehearse it by day
  first (D5 and plan rule 8).
- Add the kernel to `scripts/landing/night_busy.sh` only if the sweep can run long.

### 4.9 Forget-reach and household-text inventories

- `zd/forget_reach.py:208-262` lists side stores. K-shadow and K-prod add the kernel's store. The drift test
  `zd/tests/test_forget_reach_inventory.py` scans only `services/zoe-data` (`:45-47,196-203`), so a kernel store under
  `services/zoe-os/` is invisible to it.
- Widen the scan roots, and give each kernel store its own entry with its eraser: events, receipts, list rows, reminder notes,
  alarm labels, the shield, the forget ledger.
- The household-text inventory (`zd/household_text.py`, #2513) is not on `main`. Once it lands, add the kernel stores to its
  `EXTRA` / `WRITE_SITES`.

### 4.10 The flag registry

- Flags are added by the open PRs:
  - `ZOE_OS_LIST_SHADOW`, `ZOE_OS_SHADOW_VERBS`, `ZOE_OS_SHADOW_DIR` (K-shadow);
  - `ZOE_OS_LIST_ADD_AUTHORITY`, `ZOE_OS_KERNEL_DB` (K-prod).
- Each later verb adds `ZOE_OS_<VERB>_AUTHORITY`, off by default, registered with `python3 tools/audit/flag_inventory.py --sync-registry`.
- [inferred] Prefer one `ZOE_OS_AUTHORITY_VERBS` list over one flag per verb, mirroring `ZOE_OS_SHADOW_VERBS`, so the rollback stays
  one line. That is a naming choice; the plan's "its own flag" holds either way.
- Moving the kernel out of `labs/` moves its reads from the LAB section to production (`tools/audit/flag_inventory.py:36-38,871-873`).

### 4.11 Felt scorecard

`labs/felt-scorecard/scorecard.py:67-74` reads log markers. Needed rows:

- shadow agreement per verb (`OS_SHADOW_ACT`, K-shadow `zd/os_shadow.py:327-330`);
- authority fallbacks by reason;
- refusals honoured;
- why and undo answered;
- kernel forgets that verified.

#2519 also edits `scorecard.py`; build after it.

### 4.12 Review cost controller

`.github/workflows/greptile-gate.yml:343-348` (`LOAD_BEARING`) has no kernel path. Add `^services/zoe-os/` (or the lab path until
the move). A new unit file is already caught by `^scripts/setup/systemd/`.

### 4.13 First boot and the sold box

Nothing creates a kernel user, database, roster or socket directory (`scripts/setup/install-jetson.sh:203-290`; grep). Add an
offline step that:

- creates the state directory (mode 0700) and the database;
- writes the roster from the member source (WP-23), not by hand;
- installs the unit as optional.

#2617's checklist (`zd/first_boot.py`) gets a "kernel ready" item. D9: every part of this is offline.

### 4.14 The de-root programme

`docs/architecture/p0-deroot.md:121-131` names target users per service. Add a `zoe-os` user, separate from `zoe-svc`. That
separation is the point: zoe-data must reach the socket but not the file (G15). Add with it:

- a `scripts/setup/p0-deroot/systemd/zoe-os.service.d/50-p0-deroot.conf`;
- tmpfiles entries for the state and socket directories (`scripts/setup/p0-deroot/tmpfiles.d/zoe-p0-deroot.conf`);
- lab cells in `labs/p0-deroot/`.

The socket directory must be group-accessible to zoe-data's user and nothing else [inferred]. #2494 and #2627 to #2629 are the open
de-root PRs.

### 4.15 `docs/CANONICAL.md` and its invariants test

The kernel is not a rock and should not enter the `LOCKED-ROCKS` block (`docs/CANONICAL.md:28-60`). Add it to the spine list
(`:70-131`) only when the first authority flag is live, because CANONICAL lists what *is* live. Add a pin in
`zd/tests/test_canonical_invariants.py` on the router pattern (`:290-354`): the unit file exists, the authority flags default to off
in code, and the zoe-data client never opens the kernel database (K-prod `zd/os_authority.py:77-91` already promises this).

### 4.16 Panel and UI surfaces for why, undo and receipts

- **Voice is the first surface.** "Why did you add that?" and "undo that" must reach the kernel's `explain` and `undo`. Both are
  surface-only ops (K-lab `server.py:257-300`), so zoe-data must hold the surface credential.
- **Today's competing answers:**
  - "undo / put back" is a skybridge list action (`zd/skybridge_service.py:1479`);
  - "why" questions go to `zd/provenance_answers.py`.

  Under list-add authority, an undo of a kernel-decided add must call the kernel's `undo` and then remove the read-model row. Doing
  both in one transaction is the drift risk (R3). Route by receipt: a row with a kernel receipt is undone through the kernel, any
  other row the old way.
- **Touch.** A receipt line on the list card ("added by voice, 2 min ago — undo") belongs in `ui/touch/` only.
- **Desktop** gets its own version later. No links between the two (repo rule).

## 5. Open pull requests

The open-PR list was read 2026-11-15 (102 open). Every PR that touches a seam above:

| PR | What | Verdict | Why |
|---|---|---|---|
| #2630 | the lab kernel | **land first** | Phase 0. Note: +13,459 lines, far over the size rule; it is one reviewed lab drop |
| #2634, #2635, #2633 | harness, speed-up, forget decisions | land in order | Phase 0 stack |
| #2632 | the shadow kernel serves four verbs | land | Phase 1 |
| #2622 to #2625, #2631 | the shadow stack | **must add**: remove hooks at the converged remove seam | #2631 hooks only the skybridge remove (K-shadow `zd/skybridge_service.py:5108`), so the intent and MCP "remove" (which tick) are never shadowed and agreement for `list_remove` measures one path of four |
| #2637 | authority kernel | **rebuild on #2630's head** | built on an older lab commit, missing the privacy and forget fixes (G1) |
| #2638 | list add decided by the kernel | **reshape** | covers intents and MCP only; the panel's skybridge add, REST add and `pending_suggestions` bypass it. Move the hook to the converged insert (WP-20/WP-22). It also edits `memory_service.delete_user` alongside #2598 (merge order) |
| #2639 | reconcile | land after #2638 | the Phase 3 drift report; add uncertain-receipt settlement (G18) |
| #2642 | synthetic day | land | Phase 2. Add guest, child, panel-household and two-alarm shapes per the plan |
| #2636 | the cut-over plan | land with this map | |
| #2598 | member delete deletes everything | **must add** a kernel erase beside its erase, failing closed; agree list-item authorship | it decides a family list's items stay because they have no author; the kernel knows the author. One rule must win (R4, Q2) |
| #2522, #2523, #2607, #2614 | routines | **must add** kernel publish | a new store of household actions: publish receipts from day one; tick, skip and snooze are kernel actions with undo |
| #2588 to #2592 | anticipation engine | **rebuild kernel-first** | still dark; offers, answers and cooldown should be kernel offers with the attention budget (G16) |
| #2593, #2594, #2596 | presence fusion | **must add** publish to the kernel | it adds a third presence notion (2.10) |
| #2512 | web push opt-in for reminders | **must add** | must go through the one "may Zoe send now" gate (WP-61), not a fourth quiet-hours copy |
| #2537, #2534 | named guests | **blocked for kernel cases** until G4 | guest persons do not exist in the kernel; fine to land on the old path |
| #2527 to #2530 | web lookup | **must add** a kernel grant | the one door out is a capability; its grant belongs in the consent model |
| #2513, #2514 | household-text inventory, two forget reaches | land; then add kernel stores | 4.9 |
| #2519, #2520, #2521 | measure gaps, scorecard | land; then add kernel rows | 4.11 |
| #2516 | off-box backup | land before WP-09 | same files |
| #2617 to #2621 | first boot | **must add** a kernel-ready item | 4.13 |
| #2494, #2627 to #2629 | de-root | **must add** a zoe-os user | 4.14 |
| #2575, #2577, #2578, #2502, #2503 | calendar feed and fetcher | unaffected now | calendar is Phase 5.7; the feed writes through `create_event_record` |
| #2601, #2600, #2574 | memory authority and scrubber | unaffected | memory is last and observe-only |
| #2509 to #2511 | plan loop | **must add** when wired | a bounded plan-act-verify executor that acts should act through kernel `act` so each step has a receipt [inferred] |
| #2582, #2585 to #2587 | self-heal | unaffected | 4.6 |
| #2615, #2568 | child and medical floors | unaffected | the plan lists the floors as not affected |
| #2466, #2610 to #2613, #2640, #2572, #2573, #2597, #2599, #2603 to #2606 | voice feel, reflex, listen pace | unaffected | they do not touch an action seam; they share the replay gate's capacity, which is the real contention |
| #2626, #2616, #2475, #2517, #2518, #2536, #2538 to #2542, #2550, #2562, #2563, #2531 to #2533, #2500, #2501, #2602 | tests, docs, labs, chat UI | unaffected | |

## 6. Work packages

### 6.1 Conventions

- **Owner** says who builds: K is the kernel builder, G a general builder, C Codex (narrow change or review), L the landing session
  (workflow, unit and deploy files that builders may not edit).
- **Size** [inferred]: S is up to about 250 changed lines, M up to about 600, L up to about 800.
- **Voice** "yes" means it touches a voice-gated path and needs a replay.
- **Phase** is the plan's phase.
- **Proofs:** T = focused tests with a negative control; KS = the kernel suite (heavy, so it runs in a coordinator test window);
  SA = shadow agreement; R = voice replay; LV = live check after landing.

### 6.2 Foundation and infrastructure

| # | Goal | Files | Depends on | Parallel with | Proof | Voice | Size | Phase | Owner |
|---|---|---|---|---|---|---|---|---|---|
| WP-01 | Rebase the authority kernel onto #2630's head; re-run erase and privacy tests | `labs/zoe-os/authority.py`, `shadow_daemon.py`, `zoe_os/spine.py`, `wire.py`, tests | #2630 landed (or its head) | all G work | KS | no | M | 0/3 | K |
| WP-02 | Kernel test step in the `checks` job; budgets; LANES; skip allowlist | `.github/workflows/validate.yml`, `tests/unit/test_required_gate_workflows.py`, `scripts/maintenance/affected_tests.py`, kernel README | WP-03 (paths) or lab paths | everything | CI green with the step, and red when one kernel test is broken | no | S | before 1 | L (+G prepares) |
| WP-03 | Move the kernel package and tests to `services/zoe-os/`; child AGENTS.md; DOX index; labs keep the twin and benches | `services/zoe-os/**`, `services/AGENTS.md`, `labs/AGENTS.md`, `labs/zoe-os/**` imports | #2630, WP-01 | G work | KS; nothing under `services/zoe-data` imports it | no | M (mostly renames) | before 1 | K |
| WP-04 | One `zoe-os.service` (mode shadow or authority), memory protection, README row, installer optional unit | `scripts/setup/systemd/zoe-os.service`, `tests/unit/test_systemd_memory_protection.py`, `scripts/setup/systemd/README.md`, `scripts/setup/install-jetson.sh` | WP-03 | WP-05..09 | T; owner install; `systemctl --user status` | no | S | 1 | L |
| WP-05 | Deploy step: restart the kernel if changed, before zoe-data; health probe; manual twin | `.github/workflows/deploy.yml`, `scripts/maintenance/deploy_live.sh` | WP-04 | WP-06..09 | a no-op deploy and a kernel-diff deploy on the box | no | S | 1 | L |
| WP-06 | Voice-gate and Greptile patterns for the kernel tree and unit; drift pin | `scripts/maintenance/voice_gate_check.py`, `.github/workflows/greptile-gate.yml`, `tests/unit/test_greptile_gate_workflow.py` | WP-03 (path) | all | T; must **deploy one landing before** shadow is on | no (it is the gate) | S | 1 | C + L |
| WP-07 | A scratch kernel in the regression probe; off/kernel run pair; said-vs-did row for kernel-decided adds | `scripts/maintenance/voice_regression_probe.py`, `zd/tests/replay_samples.py`, `scripts/perf/measure_voice.py` | #2632, WP-01 | WP-20 | a probe run with the kernel present (harness lock) | yes (harness) | M | before 3 | G |
| WP-08 | Ground truth, health spine and crash-loop watch know the kernel | `scripts/maintenance/zoe_ground_truth.sh`, `zoe_health.py`, `zoe_crash_loop_watch.py` | WP-04 | all | T; one ground-truth run on the box | no | S | 1 | C |
| WP-09 | Kernel `backup` op (SQLite online backup); add it to the host archive; restore-drill check | `services/zoe-os/...` (op), `scripts/maintenance/zoe_offbox.py`, `zoe_restore_drill.py`, `tests/unit/test_zoe_offbox.py`, `test_restore_drill.py` | WP-03, #2516, WP-09b | WP-07, WP-20 | T; a drill run | no | M | 1 | K (op) + G |
| WP-09a | **Pin: the kernel state directory is in no backup** (host archive sources, Postgres and MemPalace dumps, off-box copy) until WP-09b lands. Required before the shadow flag is switched on | a test over `scripts/maintenance/zoe_offbox.py` sources and `zoe-backup.service` | none | shadow activation | T | no | S | 1 | G |
| WP-09b | **Backup-safe forget** (owner picks: per-member backup keys a forget destroys, or a backup-aware forget), for Postgres backups and the kernel's alike. Erase is not complete without it | `scripts/maintenance/zoe_offbox.py`, the backup units, `zd/forget_reach.py` | owner choice | WP-09, Phase 3 | T; restore drill proves a forgotten member's words cannot be read back | no | L | 1 | G |
| WP-10 | Forget-reach entries per kernel store; widen the drift scan roots; household-text entries | `zd/forget_reach.py`, `zd/tests/test_forget_reach_inventory.py`, `zd/household_text.py` | #2513, WP-03 | all | T | no | S | 1 | G |
| WP-11 | Scorecard rows for agreement, fallback, refusal, why, undo, forget | `labs/felt-scorecard/scorecard.py`, its test | #2519, #2631 | all | T | no | S | 1 | C |
| WP-12 | CANONICAL spine bullet and invariants pin (unit exists, authority flags default off, client never opens the DB) | `docs/CANONICAL.md`, `zd/tests/test_canonical_invariants.py` | first authority flag live | — | T | no | S | 3 | G |
| WP-13 | First boot: kernel state dir, DB, roster from the member source, optional unit; checklist item | `scripts/setup/install-jetson.sh`, `zd/first_boot.py` | WP-04, WP-23b, #2617 | — | an offline dry run in a scratch HOME | no | M | before sale | G |
| WP-14 | De-root: zoe-os user, drop-in, tmpfiles, lab cells; the two root-only file tests on the box | `docs/architecture/p0-deroot.md`, `scripts/setup/p0-deroot/**`, `labs/p0-deroot/**` | #2494, WP-04 | — | lab cells; root test run by the owner | no | M | 4 | G |
| WP-15 | Retention: list rows survive the sweep; `sweep` op; night presleep step after the backup; day rehearsal | kernel `retention.py`, `authority.py`; `scripts/night/night_window.py` | WP-01, WP-09 | — | KS; one daytime rehearsal | no | M | 4 | K + L |
| WP-16 | Kernel speed track (everyday call ≤ 0.7 ms, refusal ≤ 0.4 ms, forget p99 < 10 ms) | kernel `spine.py`, `receipts.py`, `explain.py` | #2635 | everything | `bench_kernel.py` before and after | no | M | track | K |

### 6.3 Lists

| # | Goal | Files | Depends on | Parallel with | Proof | Voice | Size | Phase | Owner |
|---|---|---|---|---|---|---|---|---|---|
| WP-20 | **Converge item insert:** skybridge `_add_spoken_items`, REST POST items and the `pending_suggestions` list add call one `list_service` insert inside `list_write_txn`; behaviour choices stay as parameters | `zd/list_service.py`, `zd/skybridge_service.py:4786-4870`, `zd/routers/lists.py:344-400`, `zd/pending_suggestions.py:859-891`, `services/AGENTS.md` | none | WP-01, WP-40 | T (existing list tests unchanged and green; a new test that every add path reaches the one function) and R | **yes** | M | 3 prereq | G1 |
| WP-21 | **Converge list create:** the 5 raw `INSERT INTO lists` go through `create_list_record`, keeping each caller's visibility rule as a parameter | `zd/skybridge_service.py:4455,4603,4758`, `zd/pending_suggestions.py:878` | WP-20 | WP-27 | T, R | yes | S | 3 prereq | G1 |
| WP-22 | **Reshape #2638:** the hook moves to WP-20's insert, so the panel add is covered. Children: `fallback_child` until D7. Measure the lock-hold time with a 150 ms kernel; optionally propose before taking the advisory lock | `zd/list_service.py`, `zd/os_authority.py`, tests | WP-20, WP-01 | WP-07 | T (hung, dead and slow kernel); R with the scratch kernel; SA | yes | M | 3 | G1 |
| WP-23a | Kernel roster ops `member_upsert` and `member_remove` (role, guardians); `member_remove` chains to `erase_member` | kernel `capability.py`, `server.py`, `wire.py`, `authority.py` | WP-01 | WP-20 | KS | no | M | 4 (D6) | K |
| WP-23b | A member-change source in zoe-data that pushes the roster (on change and at start); role from `auth_users` plus `member_modes`; guardians = adult admins | new `zd/os_roster.py`; callers in the member register and erase paths | WP-23a, Q3 | WP-40 | T; a live demo-member add and remove | no | M | 4 (D6) | G |
| WP-24 | **The UI member delete reaches zoe-data's erase** (and so the kernel), failing closed | `services/zoe-auth/api/admin.py:246-309` or `ui/settings.html:1783` | Q1 | all | T; a live demo-member delete | no | S | 1 (rule 5) | G |
| WP-25 | Kernel list model: list id, type, visibility, household owner, the `__household__` actor with the fridge rule, guests | kernel `brain/drivers.py` list driver, `authority.py` | WP-01 | WP-23a | KS | no | L | 4/5.1 | K |
| WP-26 | `created_by` on `list_items` (migration, the next free number after 0083); every insert path sets it | `zd/alembic/versions/`, `zd/list_service.py` (after WP-20) | WP-20 | WP-22 | T; migration up/down | yes | S | 3 | G |
| WP-27 | **Converge remove, tick, untick and delete item** into `list_service` functions. The meaning is settled: spoken "remove" deletes (owner decision Q4, answered 2026-11-15); the tick paths change to delete. Record `list_removed` receipts at the one site | `zd/skybridge_service.py:5023-5110,5184-5250`, `zd/intent_router.py:3128-3210`, `zd/mcp_server.py:1555-1590`, `zd/routers/lists.py:438-560`, `zd/list_service.py` | WP-20, Q4 | WP-40 | T, R | yes | L | 5.1 prereq | G2 |
| WP-28 | Shadow `list_remove` at WP-27's seam (replaces #2631's skybridge-only hook) | `zd/os_shadow.py`, `zd/list_service.py` | WP-27, #2631 | WP-41 | SA | yes | S | 5.1 | G2 |
| WP-29 | REST visibility goes through `set_list_visibility` (owner rule, heads-up); REST rename and delete get one store function | `zd/routers/lists.py:239-340`, `zd/list_sharing.py` | none | all | T | no | S | 5.1 prereq | C |
| WP-30 | `write_receipts.record` publishes to the kernel in shadow; write the three declared-but-unwritten removal kinds at their sites | `zd/write_receipts.py`, `zd/os_shadow.py`, removal sites | #2631 | all | T; SA (receipts agree) | yes | M | 5.4 | G |

### 6.4 Reminders, alarms and timers

| # | Goal | Files | Depends on | Parallel with | Proof | Voice | Size | Phase | Owner |
|---|---|---|---|---|---|---|---|---|---|
| WP-40 | **Reminder store functions:** update, snooze, acknowledge and delete in `reminder_service`; REST and MCP call them. Fixes: MCP snooze ownership, the hard-coded UTC offset, no reschedule on MCP edit; route the `pending_suggestions` reminder create through `create_reminder_record`; add a `reminder_cancel` handler or drop the intent | `zd/reminder_service.py`, `zd/routers/reminders.py:192-443`, `zd/mcp_server.py:2357-2432`, `zd/pending_suggestions.py:894-905`, `zd/intent_router.py` | none | WP-20 | T (each fix has a negative control); R | yes | L | 5.2 prereq | G2 |
| WP-41a | Kernel reminders: durable store; absolute wall time with timezone; date-only plus default time; no 7-day cap; visibility | kernel reminder driver and skill, `spine.py` table | WP-01 | WP-20 | KS (including kill-mid-write) | no | M | 4 | K |
| WP-41b | Kernel recurrence, the same RRULE subset; one golden table shared with `zd/reminder_recurrence.py` | kernel, `zd/tests/` golden | WP-41a | WP-50a | KS + T (both sides read the same table) | no | M | 4 | K |
| WP-41c | Kernel reminder edit, snooze, done (one occurrence) and delete with today's owner and family rules | kernel | WP-41a | WP-40 | KS | no | M | 4 | K |
| WP-42 | Shadow the reminder edit, snooze, done and delete at WP-40's functions; `os_shadow.kernel_args` stops refusing recurrence, date-only and range | `zd/os_shadow.py`, `zd/reminder_service.py` | WP-40, WP-41a-c | WP-51 | SA, R | yes | M | 5.2 | G |
| WP-43 | Reminder create under kernel authority (the list-add pattern) | `zd/reminder_service.py`, `zd/os_authority.py` | WP-42, D1 pass mark | — | T, R, SA, LV | yes | M | 5.2 | G |
| WP-44 | **One quiet-hours function:** Q1 (engine) and Q2 (comm_prefs) become one, with per-member override, read per call; tests that every consumer agrees | `zd/proactive/engine.py:34-77`, `zd/comm_prefs.py:160-185`, the consumers in 2.6 | none | everything | T | yes (timer-done speech) | M | 5.2/5.6 prereq | G3 |
| WP-45 | Firing asks the kernel "deliver now?" in shadow (`attention.route`), compared with the engine's decision | `zd/proactive/engine.py:80-215`, kernel attention | WP-44, WP-60 | — | SA | no | M | 5.2 | G |
| WP-50a | Kernel alarms: durable store, several per person (cap 30), labels, repeat rules, once-date, enabled, household owner | kernel alarm driver | WP-01 | WP-41 | KS | no | M | 4 | K |
| WP-50b | Kernel alarm edit, delete, snooze (1-30 min, 8 max), dismiss, overrides; "the panel may silence" | kernel | WP-50a | WP-41c | KS | no | M | 4 | K |
| WP-51 | Alarm receipts inside `alarms.py` functions, not callers (REST, anticipation, delete, snooze and dismiss today record nothing) | `zd/alarms.py`, `zd/alarm_voice.py` | none | all | T | yes | S | 5.3/5.4 | C |
| WP-52 | Shadow the alarm edit, delete, snooze and dismiss in `alarms.py`; drop `os_shadow`'s one-alarm and HH:MM-only limits | `zd/os_shadow.py`, `zd/alarms.py` | WP-50a-b, #2631 | WP-42 | SA, R | yes | S | 5.3 | G |
| WP-53 | Timers: inventory the JSON snapshot in forget reach and erase labels on member delete; one guest owner id | `zd/skybridge_service.py:106-195,591,684`, `zd/routers/skybridge.py:170,180`, `zd/forget_reach.py` | none | all | T | yes | S | 5.3 | C |

### 6.5 Offers, notifications, the rest

| # | Goal | Files | Depends on | Parallel with | Proof | Voice | Size | Phase | Owner |
|---|---|---|---|---|---|---|---|---|---|
| WP-60 | Kernel attention service: durable settings, decline back-off, cooldown, channels, urgency override, server op wired | kernel `attention.py`, `server.py` | WP-01 | WP-41 | KS | no | L | 5.5/5.6 | K |
| WP-61 | One "may Zoe speak or send now" function wrapping `enqueue_announcement`, `send_push_to_user` and the notification inserts; shadow-asks the kernel | new `zd/speak_gate.py`; `zd/voice_announce.py`, `zd/routers/push.py`, the 12 insert sites | WP-44 | WP-40 | T; SA | yes | L | 5.6 | G3 |
| WP-62 | Anticipation offers as kernel offers (reshapes #2588 to #2592) | `zd/anticipation*.py` | WP-60 | — | T; lab numbers | no | L | 5.5 | G |
| WP-63 | Note and journal writers converge (4 and 3 modules); receipt kinds `journal` and `transaction` | `zd/routers/notes.py`, `journal.py`, `transactions.py`, `zd/intent_router.py`, `zd/mcp_server.py` | none | anything | T, R | yes | M | 5.7 | G |
| WP-64 | Forget entry points 4, 7 and 8 (guardian, "forget that", MCP) reach the kernel | `zd/memory_space.py`, `zd/turn_forget.py`, `zd/mcp_server.py`, `zd/os_shadow.py` | #2624 | all | T | yes | S | 1 (rule 5) | G |
| WP-65 | Voice "why did you add that" and "undo that" routed by receipt (kernel row → kernel; else old path) | `zd/skybridge_service.py:1479`, `zd/provenance_answers.py`, `zd/os_authority.py` | WP-22 | WP-27 | T, R | yes | M | 3 | G |
| WP-66 | Touch receipt line and undo on the list card | `ui/touch/` list card, `sw.js` | WP-65 | — | UI harness | no | S | 3 | G |

Packages are cut at about 800 changed lines. WP-27, WP-40, WP-61 and WP-62 are the ones most likely to need a stacked split; split by
entry layer (REST / MCP / voice).

### 6.6 Critical path to "list add under kernel authority, live"

1. **Land Phase 0:** #2630, then #2634 → #2635 → #2633. Review-bound, not build-bound.
2. **In parallel:**
   - WP-01 (K) → WP-03 (K);
   - WP-20 (G1) → WP-22 (G1);
   - WP-24 (G) and WP-64 (G): built in parallel, but both are **activation prerequisites** (step 4), not fill-ins.
3. **Infrastructure:** WP-06 lands and **deploys first** (one landing ahead). Then WP-04 and WP-05 (L), WP-02 (L), WP-08, WP-09a,
   WP-10 and WP-11.
4. **Shadow:** land the stack (#2622 to #2625, #2631, #2632). **Activation gate** (plan rule 5: every forget and
   member delete reaches the kernel before it holds real content): WP-09a is green (the kernel's state is in no backup), **WP-24
   has landed** (the Settings delete button runs the full erase) and **WP-64 has landed** (guardian forget, "forget that" and the
   MCP erase reach the kernel). Only then does the owner install the unit and turn shadow on (Phase 1). Landing the code
   flag-dark does not wait for this.
5. **Prove agreement:** WP-07 with #2642, the synthetic day and the corpus replay with shadow on, until the D1 pass mark is met for
   list add (Phase 2).
6. **Authority:** WP-09b (backup-safe forget) then WP-09 (the kernel joins the backup); #2637 (rebased by WP-01), WP-22 (reshaped #2638), #2639 reconcile, WP-26 `created_by`, WP-65 why/undo. Then a
   replay with the scratch kernel, and the owner flips `ZOE_OS_LIST_ADD_AUTHORITY=kernel` (Phase 3).

**Longest chain:**

> #2630 → WP-01 → WP-03 → WP-04 → shadow on → Phase 2 pass mark → WP-22 (needs WP-20) → replay → flip

The pass mark is the long pole. The plan's synthetic day compresses it from weeks to about a day of runs [inferred]. The other long
pole is the test lane: the kernel suite is heavy and runs only in coordinator windows.

### 6.7 Critical path to "reminders and alarms under kernel authority, live"

1. **Production convergence, which can start today:** WP-40 (G2), WP-44 (G3), WP-51 (C), WP-53 (C).
2. **Kernel, in sequence on one builder:**

   > WP-41a → WP-41b → WP-41c → WP-50a → WP-50b

   Each one is KS-proven. WP-23a/WP-23b and WP-15 run beside them; Phase 4 needs the roster push and retention before more verbs go
   authoritative.
3. **Shadow:** WP-42 and WP-52; then Phase 2 for each verb.
4. **Authority:** WP-43 for reminder create first, then snooze and done; firing last through WP-45 and WP-61; then alarms on the
   same pattern.
5. **Gate:** each step is replay-gated, and list add must have run a clean week first (plan Phase 3 → 5).

**Longest chain:**

> list add live → a week clean → WP-41a..c (K, about five packages in sequence) → WP-42 → pass mark → WP-43

The kernel builder is the bottleneck. Everything production-side (WP-40, WP-44, WP-51) should be done before the kernel side
finishes, so it is never what waits.

### 6.8 Parallelism

The constraints: one test lane, tests only in memory windows, three general builders, one kernel builder, Codex.

- **Kernel builder:** strictly serial. WP-01 → WP-03 → WP-23a → WP-41a → WP-41b → WP-41c → WP-50a → WP-50b → WP-60, with WP-16 and
  WP-25 when the lane is busy. Every KS run is heavy, so it is batched into coordinator windows: about one KS run per window [inferred].
- **G1:** WP-20 → WP-21 → WP-22 → WP-26 → WP-65.
- **G2:** WP-40 → WP-27 → WP-28 → WP-42.
- **G3:** WP-44 → WP-07 → WP-61, with WP-24 and WP-64 as fill-ins.
- **Codex:** WP-06, WP-08, WP-11, WP-29, WP-51, WP-53 (all S, narrow), plus the cross-vendor review of every Claude-built package.
- **Landing session:** WP-02, WP-04, WP-05 and the workflow half of WP-06.
- **Test lane:** about 4 to 6 focused runs a day and one KS window [inferred]. That supports about two packages landing a day. Five
  coding at once is fine; five *testing* at once is not.
- **Replay:** every voice-path package needs a replay. The lander runs those one at a time, so voice packages (WP-20, WP-22, WP-27,
  WP-40, WP-44) should be staggered, never pushed on the same evening.

## 7. Risks and open problems the plan does not mention

| # | Risk | Evidence | Answer |
|---|---|---|---|
| R1 | "List add under authority" would not cover the main spoken path | #2638's hook runs only from `add_items_to_list` (K-prod `zd/list_service.py:703`); the panel's skybridge add has its own INSERT (`zd/skybridge_service.py:4839`) | WP-20 then WP-22. Until then the Phase 3 proof would measure a minority of adds |
| R2 | The kernel round-trip runs while the per-list advisory lock and the DB transaction are held | K-prod `zd/list_service.py:582,601,628`; timeout 150 ms (`zd/os_authority.py:37`) | measure lock-hold under a slow kernel; consider proposing before the lock and re-checking dedup after [inferred] |
| R3 | The kernel can record a verified receipt for an add whose read-model INSERT then fails or rolls back | the receipt is decided before the row (`:601`); the transaction can still fail after | #2639 reconcile catches `kernel_only`. Add a compensating `undo`, or an uncertain-receipt settlement (G18) |
| R4 | After a member delete, kernel and read model disagree about family-list items | #2598 keeps family-list items (no author); the kernel's `erase_member` tombstones the member's events | one rule (Q2); WP-26 `created_by` makes them agree |
| R5 | The Settings UI's member delete never reaches zoe-data or the kernel | `ui/settings.html:1783` → `services/zoe-auth/api/admin.py:246-309`, no zoe-data call | WP-24 (Q1 is answered: the button runs the full erase) |
| R6 | D6 has no source to push from | members change in zoe-auth; child status lives in `member_modes`; zoe-data's `users` is a lazy mirror (`zd/database.py:91`) | WP-23b (Q3) |
| R7 | Today's child rule is not one rule | `zd/guest_policy.py:211-216` turns an unknown role (incl. `child`, `family_member`) into `guest` for REST checks [inferred, verify]; `zd/manner_block.py:111-120` uses `member_modes` + role; `zd/household_authority.py:282-299` treats a lookup failure as child | D7's "fall back to today's rule" needs today's rule written down per verb first; a kernel must not copy a fifth version |
| R8 | "Remove" means two different things today | delete in skybridge and REST, tick in intents and MCP (2.1) | WP-27 (Q4 is answered: remove deletes); shadow agreement on `list_remove` is meaningless until WP-27 lands |
| R9 | The shadow remove hook watches one remove path of four | K-shadow `zd/skybridge_service.py:5108` only | WP-28 |
| R10 | Gate lists deploy one landing late | `voice_gate_check.py:604-605` runs the deployed copy | WP-06 lands and deploys before shadow goes on |
| R11 | A replay with authority on would measure only the fallback | no kernel in the probe (4.5) | WP-07 |
| R12 | A WAL database copied as files is not a backup | `spine.py:215-218` | WP-09's online-backup op |
| R13 | Two kernel unit templates that conflict | K-auth `zoe-os-shadow-kernel.service` and `zoe-os-authority-kernel.service` (`Conflicts=`) | one unit with a mode (WP-04) |
| R14 | Credentials are rewritten on every kernel start | integration doc item 12 | a kernel restart by deploy must not break zoe-data's next call: stable credentials (G20) before WP-05 runs on a live kernel |
| R15 | The production reminder paths already have bugs a shadow would report as "disagreement" | MCP snooze with no owner check and a fixed offset (`zd/mcp_server.py:2399,2406-2416`); unfireable `pending_suggestions` reminders (`zd/pending_suggestions.py:894-905`); `reminder_cancel` with no handler | WP-40 before WP-42; otherwise every disagreement must be explained by hand (D1) |
| R16 | Removal claims are always judged unbacked | `_removed` kinds never recorded (`zd/write_receipts.py:41`) | WP-30 |
| R17 | A once-a-day trigger may fire every tick in its hour | `zd/proactive/triggers/people_health.py:42-43` checks only the hour; no dedupe found [inferred] | not a kernel item; fix it or note it (open-problems) before "one gate for unprompted speech" counts its traffic |
| R18 | Two unprompted-speech paths have no quiet-hours gate | MCP `panel_announce` (`zd/mcp_server.py:2716-2728`); `send_push_to_user` (`zd/routers/push.py:132-200`) | WP-61 |
| R19 | `do_not_disturb` and `proactive_enabled` are dead switches | stored `zd/routers/stubs.py:74,80`, read by nothing | WP-61 reads them or removes them |
| R20 | The kernel's home clashes with a services rule | `services/AGENTS.md:25` | Q5 |
| R21 | The lab kernel's latest privacy fixes are not yet validated | K-lab `README.md:3-8` | KS in a coordinator window before WP-01 is merged |
| R22 | PR size | #2630 is +13,459; #2514 is +5,431; the repo's hard limit is 1,000 (`oversized-ok` label) | accepted for the lab drop; kernel follow-ups use the 800-line packages above |

## 8. Questions for the owner

1. **Q1, the member delete button. ANSWERED (2026-11-15, cut-over plan "Further owner decisions"):** yes. The Settings delete
   button runs the full erase (zoe-data's `delete_user`, and so the kernel), not only the account deactivation; items shared
   with other members and accepted into their data stay with those members. WP-24 builds it; do not reopen.
2. **Q2, family-list items after a member delete.** #2598 keeps them because they record no author. The kernel knows the author.
   Recommendation: add `created_by` (WP-26). Then the member's items on a family list go too, or stay by choice; but one rule for
   both.
3. **Q3, the roster source.** D6 says zoe-data pushes members, but accounts live in zoe-auth. Options: zoe-auth calls zoe-data on
   every change, or zoe-data reconciles `auth_users` and `member_modes` on a short timer. Recommendation: the timer reconcile plus a
   push on the paths zoe-data owns. It is offline and has no new cross-service call.
4. **Q4, spoken "remove". ANSWERED (2026-11-15, same table):** it deletes the item. The two paths that tick it are wrong for
   that request. WP-27 converges on delete; do not reopen.
5. **Q5, the kernel's home.** `services/zoe-os/`, with an `AGENTS.md` that says the household-concepts rule does not apply to it?
6. **Q6, timers.** Do timers move to the kernel at all, or do they stay as ephemeral objects with receipts only?
