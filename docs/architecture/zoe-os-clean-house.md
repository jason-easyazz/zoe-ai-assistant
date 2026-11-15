---
type: architecture
title: Zoe OS - the clean-house register
date: 2026-11-15
status: a reading of the code and the records, nothing built, nothing run, nothing live read. Companion to the cut-over plan, the
  implementation map, the architecture study and the prior-art record (all on open PRs, see "Sources").
related: zoe-os-cutover-plan.md, zoe-os-implementation-map.md, zoe-os-architecture.md, ../research/zoe-os-prior-art-2026-11-15.md,
  ../knowledge/open-problems.md
---

# Zoe OS: the clean-house register

Owner request, 2026-11-15: use the kernel migration to clean up comprehensively. Every household action is moving onto the Zoe OS kernel one at a time, so every one of those code paths is being
read anyway. This page is the ONE register of what should be cleaned, fixed, removed or decided while that happens, and the
coverage audit that shows what was looked at.

It does not set the order of work: `docs/ROADMAP.md` owns that, and the cut-over plan owns the order inside Zoe OS. It does not
replace `docs/knowledge/open-problems.md`: problems that have nothing to do with a kernel seam stay there. Where a row here repeats
a ledger line, it says so.

## How to read this page

- **Tree read:** `origin/main` at `3826afa24`. The implementation map was read at `fec650907`; `git diff fec650907 3826afa24 --
  services/zoe-data` is empty, so the map's `services/zoe-data` line numbers hold on `main`.
- **Where:** a `path:line` was read on `main` in this audit (by me, or by one of four read-only sub-readers whose reports I
  spot-checked). `(r)` means reported, unverified: taken from a builder report, a PR body or a review thread, often on a branch that
  is not on `main`. `(PR #n)` means the line is on that PR's branch.
- **Shorthand:** `zd/` = `services/zoe-data/`, `ui/` = `services/zoe-ui/dist/`, `brain/` = `labs/flue-zoe-brain-2x/`,
  `tg/` = `labs/flue-zoe-telegram-2x/` (messaging integration), `auth/` = `services/zoe-auth/`.
- **Kind:** BUG live bug · DUP duplicate rule · DEAD dead or unreachable code · MEM in-memory state that should be durable ·
  FGT missing forget reach · PRIV privacy or safety gap · DOC stale or contradictory doc · TEST test that cannot fail or is
  missing · FLAG config or flag debt · NAME naming or structure debt · REV deferred review item · VER unproven claim to verify ·
  OWN owner decision to implement.
- **Src:** where the finding came from. M the implementation map; S the architecture study; P the prior-art record; H the
  builder hand-off notes and review threads of 2026-11-15; L the open-problems ledger; C new in this audit (the coverage pass).
- **WP:** the map's work package, or a new one (NEW WP-xx, defined in the table after the register).
- **By:** the phase that must not start until the row is done. `now` = independent of the kernel and should not wait; `P1`..`P6`
  = the cut-over plan's phases (`5.2` = Phase 5 step 2); `sale` = before a box is sold.
- **Sev:** for the household. Reasons for generalised high-severity findings are held privately.
- **Status:** open · in flight (PR number) · local (built on a named branch, not pushed) · done. Read from the open-PR list of
  2026-11-15 (104 open) and the hand-off notes.

## What is held privately and why

135 rows have their location and detail in the private security review. They are summarised here by class to avoid publishing an exposure map. The private copy is the authority for those rows. This also covers uncertain, dormant and proposed security-sensitive findings; inclusion does not establish live reachability. Required row metadata is unchanged.

The fixed class vocabulary below is used in both detail cells of each generalised row. It names the problem class; completion requires the private acceptance criteria. Coverage lists describe scope and kernel routing, not security guarantees. Coverage references to generalised findings are withheld so they cannot reconstruct locations.

| class | rows |
|---|---|
| authorisation gap | 21 |
| identity-linking gap | 3 |
| forget does not reach a store | 45 |
| member delete does not reach a store | 5 |
| outbound request not constrained | 6 |
| physical device control lacks confirmation | 3 |
| biometric consent not enforced | 2 |
| action leaves no record | 7 |
| test can touch real data | 2 |
| unused privileged code to delete | 5 |
| consent or disclosure gap | 25 |
| privileged process isolation gap | 5 |
| security or privacy assurance gap | 6 |

## 1. The register

| id | kind | src | where | what is wrong | done looks like | WP | by | sev | status |
|---|---|---|---|---|---|---|---|---|---|
| **Lists** | | | | | | | | | |
| L01 | DUP | M | `zd/routers/lists.py:76,147,213,258,324,415` | 6 of the 22 copies of the list visibility filter `(visibility='family' OR user_id=?)` | the one read function (WP-31) used; a test forbids a raw filter on list tables | NEW WP-31 | P6 | med | open |
| L02 | DUP | M | `zd/skybridge_service.py:4439,4476,4676,4721,4737,4972,4989,5008` | 8 more copies of the same filter | as L01 | NEW WP-31 | P6 | med | open |
| L03 | DUP | M | `zd/intent_router.py:2958,2967,3165` | 3 more copies | as L01 | NEW WP-31 | P6 | med | open |
| L04 | DUP | M | `zd/mcp_server.py:1479,1487,1566,1574` | 4 more copies (on the stdio surface) | removed with X03, or as L01 | WP-80 / WP-31 | P6 | low | open |
| L05 | DUP | M | `zd/pending_suggestions.py:866` | the 22nd copy | as L01 (falls out of WP-20) | WP-20 | P3 | low | open |
| L06 | DUP | M | `zd/list_service.py:414,451,479,799` | 4 list resolvers, each with its own rule | one resolver with explicit parameters; the kernel resolves names to ids itself (P 7.2) | WP-20 / WP-25 | P3 | med | open |
| L07 | DUP | M | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-32 | P3 | med | open |
| L08 | BUG | M | held privately | authorisation gap | authorisation gap | NEW WP-32 | P3 | med | open |
| L09 | DUP | M | `zd/intent_router.py:3980` | guest predicate 3 of 6 | as L07 | NEW WP-32 | P3 | med | open |
| L10 | DUP | M | `zd/guest_policy.py:269` | guest predicate 4 of 6 | as L07 | NEW WP-32 | P3 | med | open |
| L11 | DUP | M | `zd/user_filters.py:17,39` | guest predicate 5 of 6 (the sentinel list; the natural home for the one function) | the function every other site calls | NEW WP-32 | P3 | low | open |
| L12 | DUP | M | `zd/list_sharing.py:81-82` | guest predicate 6 of 6 | as L07 | NEW WP-32 | P3 | med | open |
| L13 | DUP | M | `zd/skybridge_service.py:4875,5024,5142,5187,5276` | 5 literal `{"guest","voice-guest"}` checks | as L07 | NEW WP-32 | P3 | med | open |
| L14 | DUP | M | `zd/routers/lists.py:49-53,86` | fridge rule copy 1 of 4 ("anyone at the panel may edit items on household lists") | the household principal's rule in the kernel (G3), matching `list_service.py:807-808`; three copies deleted at P6 | WP-25 | P6 | med | open |
| L15 | DUP | M | `zd/skybridge_service.py:2947-2950,3073` | fridge rule copy 2 of 4 | as L14 | WP-25 | P6 | med | open |
| L16 | DUP | M | `zd/intent_router.py:3977,4193-4203` | fridge rule copy 3 of 4 | as L14 | WP-25 | P6 | med | open |
| L17 | DUP | M | `zd/list_service.py:807-808` | fridge rule copy 4 of 4 (the reference copy) | the kernel rule is pinned to this one by a golden table | WP-25 | P3 | low | open |
| L18 | BUG | M H | `zd/intent_router.py:3200,3205`; `zd/mcp_server.py:1582`; `ui/touch/home.html:3706` | spoken and brain "remove X from the list" TICKS the item (`completed=1`) while the panel and REST DELETE it; the reply says removed and the receipt kind is `list_removed`. Owner decided: remove deletes | one delete function in `list_service`; "tick off / got / cross off" stays a tick; one phrase table tested both ways | WP-27 | 5.1 | med | open |
| L19 | BUG | C | held privately | unused privileged code to delete | unused privileged code to delete | WP-27 / WP-80 | 5.1 | low | open |
| L20 | DUP | M H | `zd/skybridge_service.py:4839`; `zd/routers/lists.py:344,363`; `zd/pending_suggestions.py:859-891` (`:878,887`) | item add has 8 entry points and 3 of them carry their own INSERT; the kernel hook would cover a minority of adds (map R1) | one insert in `list_service` inside `list_write_txn`; a test that every add path reaches it | WP-20 | P3 | med | local (`refactor/list-insert-one-path`, unpushed, untested) |
| L21 | BUG | M | `zd/routers/lists.py:344-363` | REST item add has no dedup and no lock | falls out of WP-20 | WP-20 | P3 | low | local (as L20) |
| L22 | DUP | M | `zd/skybridge_service.py:4455,4603,4758`; `zd/pending_suggestions.py:876,878`; `zd/anticipation.py:617`; `zd/routers/lists.py:157` | 5 raw `INSERT INTO lists` with three different default visibilities (forced personal, family, own rule) | all through `create_list_record`, the visibility choice a parameter | WP-21 | P3 | med | open |
| L23 | BUG | M | `zd/skybridge_service.py:4455` | `_ensure_default_lists` writes on every add, remove, complete and show, including read turns | default lists created once (first boot or first write) | WP-21 | P3 | low | open |
| L24 | PRIV | M | held privately | authorisation gap | authorisation gap | WP-29 | 5.1 | med | open |
| L25 | DUP | M | `zd/routers/lists.py:468` | REST item rename bypasses `rename_item_on_list` | REST calls the service function | WP-29 | 5.1 | low | open |
| L26 | DUP | M | `zd/routers/lists.py:239,291,306,335`; `scripts/maintenance/merge_duplicate_lists.py:110-123` | list rename and delete exist only as REST SQL and a maintenance script's raw writes | one store function each | WP-29 | 5.1 | low | open |
| L27 | DUP | M | `zd/skybridge_service.py:1479,5184,5249`; `zd/routers/lists.py:438,505` | tick, untick and "put back" have no seam | converged functions with receipts | WP-27 | 5.1 | low | open |
| L28 | NAME | M | `ui/js/widgets/core/shopping.js:796-823` | no server "clear list" verb; the UI loops per-item DELETEs (one receipt-less write each) | a bulk verb with one receipt | WP-27 | 5.1 | low | open |
| L29 | PRIV | M | held privately | authorisation gap | authorisation gap | WP-22 / WP-23a | P3 | low | open |
| L30 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-26R | P1 | high | in flight #2598 |
| L31 | OWN | M S | `zd/alembic/versions/` (no `created_by` anywhere) | items on a family list record no author, so a member delete must keep or drop them on a guess; owner rule: keep only what is shared and accepted | `created_by` on 4 tables, `share_acceptances`, the "keep it" action (study D.1) | NEW WP-26R | P3 | med | open |
| L32 | BUG | C | `zd/routers/ui_actions.py:788-792` | panel form confirm with an unknown panel type answers "Form confirmed" and writes nothing | refuse with a reason | WP-20 | P3 | low | open |
| L33 | DUP | C | `zd/routers/ui_actions.py:740-781` | panel form confirm is a ninth add entry point (loops `execute_intent` per item) | named in WP-20's every-path test | WP-20 | P3 | low | open |
| L34 | DUP | M | held privately | consent or disclosure gap | consent or disclosure gap | WP-22 | P3 | low | in flight #2638 (reshape) |
| **List add under authority, the kernel's list model** | | | | | | | | | |
| K01 | DUP | M H | held privately | consent or disclosure gap | consent or disclosure gap | WP-22 | P3 | med | in flight #2638 |
| K02 | VER | M | K-prod `zd/list_service.py:582,601,628`; `zd/os_authority.py:37` (PR #2638) | the kernel round trip (150 ms cap) runs while the per-list advisory lock and the transaction are held (map R2) | lock-hold measured with a 150 ms kernel; propose before the lock if it matters | WP-22 | P3 | med | open |
| K03 | BUG | M S | K-prod `:601` (PR #2638) | a verified receipt can exist for an add whose INSERT then rolls back (map R3, G18) | `settle` after commit; unsettled = uncertain, resolved by reconcile | NEW WP-01b | P3 | med | open |
| K04 | BUG | P | design (P 6.1, 6.2; R24) | after a 150 ms timeout the old path writes; a late kernel commit can write the same item twice | idempotency key on every proposal, carried by the fallback; a late-commit double-write test | WP-22 / NEW WP-01b | P3 | med | open |
| K05 | NAME | M | held privately | forget does not reach a store | forget does not reach a store | WP-01 | P3 | med | in flight #2637 |
| K06 | VER | H | PR #2637, #2638 (r) | owed on the authority stack: voice replay, mutation runs, `erase_member` re-joined with #2598, the shadow unit's `--db` | each done and reported | WP-01 / WP-22 | P3 | med | in flight #2637 |
| K07 | PRIV | S | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-01b | P3 | med | open |
| K08 | VER | M | PR #2639 | reconcile lands after #2638 and has no uncertain-receipt settlement (G18) | settlement added; drift report reads fingerprints | WP-22 | P3 | low | in flight #2639 |
| K09 | NAME | M | lab `drivers.py:21-31` | list rows keyed by person only, default list "shopping" (G2) | list id, name, type, visibility, owner; an actor separate from the owner | WP-25 | 5.1 | med | open |
| K10 | NAME | M S | lab (no household principal) | no "household at the panel" principal (G3) | the principal of study D.5 with the fridge rule and alarm silencing | WP-25 | 5.1 | med | open |
| K11 | NAME | M | lab `capability.py:20,101` | the guest role exists but is never created; no named guests (G4) | guest persons with expiry and their own forget | WP-25 / WP-23a | 5.1 | med | open (blocks #2534, #2537 for kernel cases) |
| K12 | PRIV | M | held privately | authorisation gap | authorisation gap | WP-22 / WP-23a | P3 | med | open |
| K13 | PRIV | P | held privately | authorisation gap | authorisation gap | WP-22 / WP-25 | P3 | med | open |
| K14 | PRIV | S P | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-74 | P3 | med | open |
| K15 | PRIV | S | held privately | privileged process isolation gap | privileged process isolation gap | NEW WP-01b | P3 | med | open |
| K16 | DUP | M | `zd/skybridge_service.py:1479`; `zd/provenance_answers.py` | "undo / put back" and "why" already have answers outside the kernel | routed by receipt: kernel rows through the kernel, others the old way | WP-65 | P3 | low | open |
| K17 | NAME | M | `ui/touch/` list card | no receipt line or undo on the card | "added by voice, 2 min ago - undo" on touch only | WP-66 | P3 | low | open |
| K18 | FLAG | M | K-shadow, K-prod flags | one flag per verb (`ZOE_OS_<VERB>_AUTHORITY`) makes rollback many lines | one `ZOE_OS_AUTHORITY_VERBS` list, registered | WP-22 | P3 | low | open |
| K19 | PRIV | C | held privately | authorisation gap | authorisation gap | WP-22 / WP-43 | P3 | med | open |
| **Shadow stack review items** | | | | | | | | | |
| SH01 | DUP | M | K-shadow `zd/skybridge_service.py:5108` (PR #2631) | the shadow remove hook watches 1 of the 4 remove paths, so `list_remove` agreement is meaningless (R9) | hook at WP-27's one function | WP-28 | 5.1 | med | in flight #2631 |
| SH02 | REV | H | `zd/os_shadow.py:402` (PR #2622) | `note_add()` still starts the worker thread on the turn | never start a thread on the turn; drop the job; an off-turn reviver | WP-28 | P1 | med | in flight #2622 (re-pass running) |
| SH03 | REV | H | `zd/pending_suggestions.py:859-892` (PR #2622, #2623) | a member-accepted suggestion add is a real add but is not shadowed | hook `_execute_action`; anticipation stays excluded | WP-28 | P1 | low | in flight #2623 |
| SH04 | REV | H | `zd/routers/lists.py:95,402` (PR #2623) | `_shadow_list_name()` adds a SELECT on the turn and reads the flag twice | flag read once; name resolved off-turn | WP-28 | P1 | low | in flight #2623 |
| SH05 | TEST | H | PR #2623 seam scan | the "every write is hooked" scan counts a never-called nested function as hooked and misses assignment aliases and f-string table names | scan tightened, each blind spot with a negative test | WP-28 | P1 | med | in flight #2623 |
| SH06 | FGT | H | held privately | forget does not reach a store | forget does not reach a store | WP-64 | P1 | high | in flight #2624 |
| SH07 | FGT | H | held privately | forget does not reach a store | forget does not reach a store | WP-64 | P1 | high | in flight #2624 |
| SH08 | NAME | M | K-shadow `zd/os_shadow.py:665-686` | the roster is a hand-written `people.json` read once at start; later members are never proposed (owner: never a second list) | the reconciler of study D.2 | WP-23b | P1 | med | open |
| SH09 | VER | M | plan Phase 1 | the hook's cost on a turn must be one flag read and one queue put; not re-measured | measured on a replay | WP-07 | P1 | low | open |
| SH10 | VER | M H | PR #2642 (+1020 lines) | the synthetic day is over the size limit and lacks guest, child, panel-household and two-alarm shapes | split or `oversized-ok`; shapes added | WP-07 | P2 | low | in flight #2642 |
| SH11 | NAME | M | K-shadow `os_shadow.kernel_args` | shadow refuses recurrence, date-only and range reminders | refuses nothing the production path accepts | WP-42 | 5.2 | low | open |
| SH12 | NAME | M | K-shadow `os_shadow` alarm limits | shadow models one alarm per person, `HH:MM` only | the limits dropped | WP-52 | 5.3 | low | open |
| **The kernel itself** | | | | | | | | | |
| KN01 | NAME | M | K-auth `shadow_daemon.py:56-78` | `list.remove` only; no tick, untick, put back, rename item, list create, rename or delete (G6) | the list verb set, matched to WP-27's seams | WP-27 / WP-25 | 5.1 | med | open |
| KN02 | NAME | M | lab `drivers.py:52-60` | `remind.set` takes relative minutes 1..10080 only (G7) | absolute wall time plus the household zone, date-only at the default time, no 7-day cap | WP-41a | 5.2 | med | open |
| KN03 | NAME | M | lab | no recurrence (G8) | the production RRULE subset; one golden table read by both sides | WP-41b | 5.2 | med | open |
| KN04 | NAME | M | lab `zoe_os/skills/reminders.py:26` | no edit, snooze, done (one occurrence) or delete on the brain path (G9) | the verbs with today's owner and family rules | WP-41c | 5.2 | med | open |
| KN05 | NAME | M | lab `drivers.py:80-92` | one alarm per person, `HH:MM` only (G10) | up to 30, labels, repeat rules, once-date, overrides, enabled, snooze limits, household owner | WP-50a / WP-50b | 5.3 | med | open |
| KN06 | OWN | S | `zd/skybridge_service.py:241,304` | timers are decided by nobody and cancel records no receipt; study: timers are recorded, not decided (drops G11) | a create and cancel receipt; labels in forget reach (WP-53) | WP-53 | 5.3 | low | open |
| KN07 | MEM | M | lab `capability.py:86`, `attention.py:30-32`, `twin/unusual.py:63` | grants, manifests, attention settings, sharing, reminders and alarms live in memory (G12) | durable stores, crash-tested like the record | WP-41a / WP-50a / WP-60 | P4 | med | open |
| KN08 | NAME | M | lab `kernel.py:41`, `capability.py:82-84` | the roster is fixed at construction; no `member_upsert` / `member_remove` (G13) | wire ops; `member_remove` chains to `erase_member` | WP-23a | P4 | med | open |
| KN09 | OWN | M | lab `retention.py:27`; K-auth README `:199` | nothing calls the retention sweep, and a sweep would drop list rows (G14); owner D5: the night window after a day rehearsal | rows survive the sweep; a `sweep` op the night job calls; rehearsed by day | WP-15 | P4 | med | open |
| KN10 | PRIV | M S | held privately | privileged process isolation gap | privileged process isolation gap | WP-14 | P3 | med | open |
| KN11 | NAME | M | lab `attention.py:49-70`, `replay.py:184` | `attention.route` is called by nothing on the server; no cooldown, back-off or channels (G16) | the attention service | WP-60 | 5.5 | med | open |
| KN12 | NAME | M | lab | no removal receipts, no turn field (G17) | removals as ordinary `act` receipts; a `turn` field | WP-30 | 5.4 | med | open |
| KN13 | NAME | M S | lab (`zoe-os.md` "missing") | no schema version, migration runner or online backup (G19) | `meta.schema`, expand-then-contract, backup before migrate (study E.2) | WP-09 | P1 | med | open |
| KN14 | BUG | M P H | integration doc item 12 (r) | kernel credentials are rewritten on every start, so a deploy restart breaks zoe-data's next call (R14) | stable per-install credentials, mode 0600, a rotation op | NEW WP-01b | P1 | med | open |
| KN15 | VER | M H | lab bench (r) | everyday call ~1.1 ms vs 0.7 target; refusal 0.85 vs 0.4; forget p99 ~73 ms; `explain` scans every refusal; forget and the checkpoint run under the global lock (G21) | the speed track's numbers met | WP-16 | track | low | open |
| KN16 | VER | H P | lab `spine.py` forget path (r) | forget p99 is probably the truncating WAL checkpoint; the prior art says keep the checkpoint and measure the fsync floor first | the fsync floor measured; the p99 target set from it | WP-16 | track | low | open |
| KN17 | VER | M | K-lab README `:165` | power loss untested; one unexplained failure in about 50 full runs (G22) | a power-cut test and the flake found | WP-16 | P3 | med | open |
| KN18 | BUG | P | lab `synchronous=NORMAL` (P 4.1, 4.3; R25) | the kernel calls NORMAL-mode writes "durable", but a committed write can roll back on power loss | two tiers: FULL for grants, forgets, consequential receipts, member removal | WP-16 | P3 | med | open |
| KN19 | TEST | P | held privately | forget does not reach a store | forget does not reach a store | WP-16 | P1 | med | open |
| KN20 | NAME | P S | lab event record | stored events carry no schema version or origin (box id) | both fields, an upcast on read, in the same change as the move | WP-03 | P1 | low | open |
| KN21 | TEST | P | held privately | test can touch real data | test can touch real data | WP-01b | P2 | med | open |
| KN22 | FGT | P | held privately | forget does not reach a store | forget does not reach a store | NEW WP-09a | P1 | med | open |
| KN23 | TEST | P | held privately | forget does not reach a store | forget does not reach a store | NEW WP-09a | P1 | med | open |
| KN24 | TEST | P | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | med | open |
| KN25 | OWN | P | design (P 5.4, Q7) | no stated backup retention window; person-facing text could promise "forever" over an unreachable copy | a window chosen by the owner and the honest sentence | WP-09a | P1 | low | open |
| KN26 | NAME | P | design (P 6.3, 6.4) | drivers declare nothing about repeat safety; relative verbs are allowed | every driver idempotent-by-key, readable or fire-and-forget; absolute verbs only | NEW WP-78 | P4 | low | open |
| KN27 | NAME | P | design (P 6.5) | an uncertain receipt is never resolved | a bounded resolver pass on start and on a timer | NEW WP-78 | P4 | low | open |
| KN28 | BUG | P | held privately | security or privacy assurance gap | security or privacy assurance gap | NEW WP-01b | P3 | low | open |
| KN29 | PRIV | P | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-01b | P3 | low | open |
| KN30 | DOC | P | kernel AGENTS.md (PR #2643) | "the kernel never forwards a credential onward" is not written down | the rule in the kernel's local contracts | NEW WP-75 | P3 | low | in flight #2643 |
| KN31 | NAME | P | held privately | authorisation gap | authorisation gap | WP-23a | P4 | med | open |
| KN32 | NAME | P | design (P 3.1-3.4, 3.7) | no "consent ask" outcome, lifetimes, remembered refusal or ask budget | the consent-ask package | NEW WP-77 | 5.5 | med | open |
| KN33 | OWN | P | kernel AGENTS.md (PR #2643) | "no exempt principal", "unknown identity is least privileged" and "deny, ask, allow" are not guarantee text; wording is the owner's | the three lines approved and pinned | NEW WP-75 | P3 | low | in flight #2643 |
| KN34 | OWN | P | held privately | forget does not reach a store | forget does not reach a store | NEW WP-79 | later | low | open |
| KN35 | FLAG | P | held privately | privileged process isolation gap | privileged process isolation gap | WP-04 | P1 | low | open |
| KN36 | TEST | P | lab wire (23 fixed malformed frames) | a fixed frame list is a regression suite, not a fuzzer | a property fuzz test on the decoder | WP-01b | P4 | low | open |
| KN37 | NAME | P | held privately | authorisation gap | authorisation gap | WP-65 | P3 | low | open |
| KN38 | OWN | H | owner D8 | "learn this again" is blocked in code; the owner allows a deliberate, recognised, same-person relearn for phrases | built in the lab on #2633, never automatic | NEW WP-01b | P4 | low | open |
| KN39 | FGT | H | held privately | forget does not reach a store | forget does not reach a store | WP-01 | P1 | med | in flight #2633 |
| KN40 | REV | H | PR #2630 | the replay discriminator was deferred out of the lab drop | built or written off in a follow-up | WP-01 | P2 | low | open |
| KN41 | VER | H | kernel fast-path work (r, uncommitted) | the fast path passed its tests on an older base and is not fitted to the current stack | re-fitted, tested on the stack, committed | WP-16 | track | low | local (uncommitted) |
| KN42 | VER | H | merge-ups of #2634 and #2635 into #2633 (r) | the merges are local and untested | the lab suite run on each merged head | WP-01 | P0 | low | local (unpushed) |
| KN43 | TEST | H | `test_history_cost.py` (PR #2634) (r) | the `history=200` negative-control parameters `return` instead of `pytest.skip`, so they pass silently | `pytest.skip` with a reason, or a real assertion | WP-16 | P0 | low | in flight #2634 |
| KN44 | VER | H | lab env names `ZOE_OS_*` renamed `OSLAB_*` (r) | the lab brain bundle was not rebuilt after the rename | the lab prep script run before the next real-brain run | WP-07 | P2 | low | open |
| KN45 | NAME | H | branches `feature/os-kernel-2-reminders-alarms`, `-3-reminder-rows`, `-4-alarms`; `feature/os-kernel-3-production` (r) | the kernel reminders and alarms stack was built with nothing run, and two branches share the `-3-` number | renamed; the light and heavy test runs done | WP-41a / WP-50a | P4 | low | local (unpushed) |
| KN46 | VER | H | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | med | open |
| KN47 | VER | M | K-lab `README.md:3-8` | the lab's latest privacy fixes are not validated in a kernel-suite window (R21) | the suite run before WP-01 merges | WP-01 | P3 | low | open |
| KN48 | NAME | M | PR #2630 (+13,459 lines), #2514 (+5,431) | far over the 1,000-line rule (R22) | accepted for the lab drop; follow-ups in 800-line packages | n/a | P0 | low | in flight #2630 |
| KN49 | DOC | H M S | map 4.4; study E.5; the landing session's criteria (r) | the map and the study say the kernel unit gets `MemorySwapMax=0`; the landing session's criterion says it must NOT (it may swap rather than crowd the voice stack) and the drafted unit dropped it | one answer written into the unit contract and both docs | WP-04 | P1 | med | open |
| KN50 | DOC | M H | lab `zoe-os-shadow-kernel.service`, `zoe-os-authority-kernel.service` (R13) | two unit templates that conflict with each other | one `zoe-os.service` with a mode argument | WP-04 | P1 | low | open |
| KN51 | DOC | S H | kernel production draft (r, uncommitted) | the deploy draft keeps the kernel under `labs/` with a source-folder setting; the study decides `services/zoe-os/` before any authority | the setting kept, pointed at `services/zoe-os/` | WP-03 | P3 | med | local (uncommitted) |
| KN52 | DOC | M H | branch `ci/zoe-os-tests-in-validate` (r) | the CI branch puts the lab tests into the `unit-shard` lanes; the map recommends a separate step in `checks` because `tests/AGENTS.md:20` forbids two service trees in one invocation; its shard pins never ran | one approach chosen; the pins run | WP-02 | P1 | med | local (unpushed) |
| KN53 | OWN | H | proposed kernel DB path (r) | D3 decided "own file beside Zoe's data" but the exact path waits on the unit | the path confirmed and written in the unit and store entry | WP-04 | P1 | low | open |
| KN54 | FLAG | M | `tools/audit/flag_inventory.py:36-38,871-873` | moving the kernel out of `labs/` moves its flag reads from the LAB section to production | the registry entries updated in the move | WP-03 | P3 | low | open |
| KN55 | DOC | M S | `services/AGENTS.md:25` | "no household concepts in kernel code" reads as a ban on a kernel that models household lists (Q5, now closed) | the rule scoped to the main service's router, memory schema, auth and tools | WP-03 | P3 | low | open |
| KN56 | NAME | S | design (study E.4) | the "what the kernel owns" table is prose; nothing fails when a write slips past a seam | a machine-readable block; a test per owned action that fails on any other write to its table | NEW WP-75 | P3 | med | open |
| KN57 | TEST | S | design (study E.5) | no size or dependency budget for the trusted core | 6,000 non-blank lines, stdlib-only imports, no free-text columns, 64/96 MB, each pinned | NEW WP-75 | P3 | low | open |
| KN58 | TEST | S | design (study E.3) | nothing replays recent decisions through an upgraded kernel | decision replay on a copy of the live record, every difference explained in the PR | NEW WP-75 | P3 | med | open |
| KN59 | OWN | S | design (study H4) | kernel code changes land like any other | the owner's one-tap approval for kernel code, its owns table and budgets | NEW WP-75 | P3 | low | open |
| KN60 | NAME | S | design (study E.1) | the client cannot ask which operations a kernel supports | a `hello` reply listing ops; the client turns off what is missing | NEW WP-01b | P3 | low | open |
| KN61 | NAME | S | held privately | privileged process isolation gap | privileged process isolation gap | WP-14 | P6 | med | open |
| KN62 | NAME | S | held privately | privileged process isolation gap | privileged process isolation gap | WP-09 / WP-15 | P1 | low | open |
| KN63 | NAME | S | design (study E.3) | the drafted unit restarts after 10 s and nothing restarts a hung but alive kernel (map 4.6) | `RestartSec=1` and a watchdog ping | WP-04 / WP-08 | P3 | low | open |
| **Infrastructure around the kernel** | | | | | | | | | |
| I01 | TEST | M | `pytest.ini:1-17`; no kernel test carries `ci_safe` | the kernel's tests run only by hand | a CI step that is green, and red when one kernel test is broken | WP-02 | P1 | med | local (`ci/zoe-os-tests-in-validate`) |
| I02 | TEST | C | held privately | security or privacy assurance gap | security or privacy assurance gap | NEW WP-02b | now | med | open |
| I03 | TEST | H | kernel brain-bridge lab app (r) | the CI builder left the lab's Node test as a follow-up | in WP-02b's step | NEW WP-02b | P1 | low | open |
| I04 | TEST | H | `scripts/maintenance/affected_tests.py:88-110` | no `LANES` mapping for the kernel | the mapping | WP-02 | P1 | low | open |
| I05 | VER | M H | `.github/workflows/deploy.yml:544-759`; kernel deploy draft (r) | deploy neither restarts nor health-checks a kernel; the draft is uncommitted and untested against the landing criteria (never fail or delay the zoe-data deploy; a missing kernel skips; base-ref code only; user unit) | each criterion shown on a no-op and a kernel-diff deploy | WP-05 | P1 | med | local (uncommitted) |
| I06 | NAME | M | `scripts/maintenance/voice_gate_check.py:232-242,549,604-605,652-709` | kernel paths take the voice gate's no-op pass, and the deployed checker is one landing behind (R10) | patterns added and deployed one landing before shadow | WP-06 | P1 | med | open |
| I07 | TEST | M H | `zd/tests/replay_samples.py:64-96`; `scripts/maintenance/voice_regression_probe.py` | the voice replay has no kernel, so with authority on it measures only the fallback (R11) | a scratch kernel per probe run; off/kernel pairs; a said-vs-did row for kernel adds | WP-07 | P3 | med | open |
| I08 | NAME | M | `scripts/maintenance/zoe_ground_truth.sh:73-112`; `zoe_health.py:57-63`; `zoe_crash_loop_watch.py:688-689` | ground truth, health and crash-loop watch know nothing of the kernel | unit, flags (names), socket health, last agreement | WP-08 | P1 | low | open |
| I09 | NAME | M | `scripts/maintenance/zoe_offbox.py:199-222`; `zoe_restore_drill.py:584-620` | the kernel DB is in no backup; a WAL file copy is not a backup (R12) | an online backup op, an archive source, a drill check | WP-09 | P1 | med | open (after #2516) |
| I10 | TEST | M | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | med | open |
| I11 | NAME | M | `zd/household_text.py` (PR #2513) | the household-text inventory has no kernel stores | kernel stores in `EXTRA` / `WRITE_SITES` | WP-10 | P1 | low | in flight #2513 |
| I12 | NAME | M P | `labs/felt-scorecard/scorecard.py:67-74` | no rows for agreement, fallbacks, refusals, why, undo, forget; nor duplicates removed per verb, asks per day, idempotent repeats | the rows | WP-11 | P1 | low | open (after #2519) |
| I13 | NAME | M | `.github/workflows/greptile-gate.yml:343-348` | kernel paths are not on the load-bearing list | `^services/zoe-os/` added | WP-06 | P3 | low | open |
| I14 | NAME | M | `scripts/setup/install-jetson.sh:203-290`; `zd/first_boot.py` (PR #2617) | nothing creates the kernel's user, DB, roster or socket dir; no "kernel ready" item | an offline first-boot step and the checklist item | WP-13 | sale | med | in flight #2617 (must add) |
| I15 | NAME | M | `docs/architecture/p0-deroot.md:121-131` | no `zoe-os` user in the de-root programme | user, drop-in, tmpfiles, lab cells | WP-14 | P3 | med | in flight #2494, #2627-#2629 (must add) |
| I16 | TEST | M | held privately | security or privacy assurance gap | security or privacy assurance gap | WP-12 | P3 | low | open |
| I17 | OWN | M | `scripts/night/night_window.py:71-74,503` | not recorded: the kernel is not stopped by the night window; the sweep runs presleep after the backup | the decision in the night-window contract | WP-15 | P4 | low | open |
| I18 | TEST | M | `tests/unit/test_systemd_memory_protection.py:53-134`; `scripts/setup/systemd/README.md:11-32`; `install-jetson.sh:45,54` | the memory-protection pins, the unit table and the installer lists do not know the unit | rows added (with KN49's swap answer) | WP-04 | P1 | low | open |
| **Reminders** | | | | | | | | | |
| R01 | BUG | M H | `zd/pending_suggestions.py:894-905` (offers made by `zd/latent_intent_detector.py:23,227`) | accepting a reminder suggestion stores it with NULL date and time, so it can never fire (high: Zoe confirms a reminder that never rings) | routed through `create_reminder_record` with the offer's time, or the offer asks for a time | WP-40 | now | high | local (`fix/reminders-one-path`, unpushed) |
| R02 | BUG | M H | `zd/conversation_context.py:83-113`; `zd/intent_router.py:1572,1723` | "cancel that" after a reminder becomes the intent `reminder_cancel`, which has no executor; the turn falls to the brain, which may claim it cancelled | a handler through WP-40's delete, or the branch removed | WP-40 | 5.2 | med | local (as R01) |
| R03 | DEAD | C | `zd/conversation_context.py:84,97,100,102,106,121,123` | the coreference resolver also emits `calendar_add`, `calendar_delete_event`, `calendar_list_events`, `list_remove_item`, `list_get_items`, `music_stop` and reads `timer_set`: none matches an executor | mapped to the real intent names or deleted, with a test that every emitted name has an executor | WP-40 | 5.2 | med | open |
| R04 | BUG | M H | held privately | unused privileged code to delete | unused privileged code to delete | WP-40 / WP-80 | 5.2 | med | local (as R01) |
| R05 | BUG | M H | `zd/mcp_server.py:2408` | MCP snooze hard-codes an 8-hour clock offset instead of the household zone | the household zone (DST-aware) | WP-40 | 5.2 | med | local (as R01) |
| R06 | BUG | M | `zd/mcp_server.py:2357-2373` | MCP edit neither normalises the date, reschedules the job nor bumps the generation, so the old job fires at the old time (latent) | WP-40's update function | WP-40 | 5.2 | med | open |
| R07 | DUP | M | `zd/routers/reminders.py:192-281`; `zd/mcp_server.py:2357` | two SQL copies of reminder edit | one `reminder_service` function | WP-40 | 5.2 | low | open |
| R08 | DUP | M | `zd/routers/reminders.py:316-378`; `zd/mcp_server.py:2396` | two copies of snooze | one function | WP-40 | 5.2 | low | open |
| R09 | DUP | M | `zd/routers/reminders.py:284-313`; `zd/mcp_server.py:2375-2394` | two copies of delete; neither records a receipt | one function recording `reminder_removed` | WP-40 / WP-30 | 5.2 | low | open |
| R10 | DUP | M | `zd/routers/reminders.py:88-90,156`; `zd/mcp_server.py:1620,1627` | reminder rule 1 of 5: family-or-own read | the one read function | NEW WP-31 | P6 | low | open |
| R11 | DUP | M | `zd/mcp_server.py:2359,2377`; `zd/omi_review_apply.py:183` | reminder rule 2 of 5: own only | the kernel's reminder rules (G9) | WP-41c | P6 | low | open |
| R12 | DUP | M | `zd/routers/reminders.py:210-222,301,334,398` | reminder rule 3 of 5: owner-only snooze, done and visibility, while any member may edit or delete a family reminder | written once as the kernel's rule, matched exactly | WP-41c | P6 | low | open |
| R13 | DUP | M | held privately | unused privileged code to delete | unused privileged code to delete | WP-40 | 5.2 | low | open |
| R14 | DUP | M | `zd/reminder_delivery.py:43-44,100-118` | reminder rule 5 of 5: sensitivity decided by visibility | an input to the speak gate | WP-61 | 5.6 | low | open |
| R15 | DUP | M | `zd/reminder_service.py:223`; `zd/guest_policy.py:171-174`; `zd/household_authority.py:79` | three more role gates stacked on the reminder rules (feature access, guests read-only, log-only `personal_write`) | folded into the kernel's rule set | WP-41c | P6 | low | open |
| R16 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-40 | P1 | high | in flight #2598 |
| R17 | VER | M | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | med | open |
| R18 | DOC | M S | `zd/reminder_service.py:57-63` vs study D.5(a) | the map says past dates are refused; the study says production stores one that never fires | the code read and one doc corrected; the spoken reply "that time has passed" pinned | WP-40 | 5.2 | low | open |
| R19 | BUG | H | `_fire_reminder` (PR #2598) (r) | the claim and lock must span check and deliver, and delete must wait for an in-flight fire; mostly untested on the latest commit | the four focused runs from the PR done | WP-40 | P1 | med | in flight #2598 |
| R20 | OWN | S | design (study D.5(b)) | a guardian may snooze or complete a child's reminder (owner-accepted; it adds an ability) | in the kernel's reminder rules | WP-41c | 5.2 | low | open |
| R21 | OWN | S | design (study D.5) | snooze length: the kernel allows 1..1440 minutes, production any number | the main service clamps and says so | WP-41c | 5.2 | low | open |
| R22 | OWN | S | design (study D.5) | reminder priority, category and type: only priority is a rule (urgent may break quiet hours) | priority read by the speak gate only | WP-61 | 5.6 | low | open |
| R23 | PRIV | C | held privately | action leaves no record | action leaves no record | WP-61 / NEW WP-81 | 5.6 | med | open |
| R24 | BUG | C | held privately | authorisation gap | authorisation gap | NEW WP-81 | P6 | low | open |
| R25 | NAME | M | PR #2512 | browser push for reminders would add a fourth quiet-hours copy | it calls the one speak gate | WP-61 | 5.6 | low | in flight #2512 (must add) |
| **Alarms and timers** | | | | | | | | | |
| A01 | DUP | M | `zd/routers/alarms.py:37-42`; `zd/alarm_voice.py:477-483` | two alarm viewer builders over one model (`zd/alarms.py:203-226`, about 8 call sites) | one builder | WP-50b | P6 | low | open |
| A02 | DUP | M | `zd/routers/alarms.py:71-76` | the ringing route re-masks alarms on its own | uses the one builder | WP-50b | P6 | low | open |
| A03 | NAME | M | `zd/alarms.py` (REST, anticipation, delete, snooze, dismiss) | alarm receipts are written by voice callers only | receipts inside the `alarms.py` functions | WP-51 | 5.3 | low | open |
| A04 | NAME | C | `zd/alarm_voice.py:532,653,673` | alarm writes record the receipt kind `timer` | an `alarm` kind, or the reason written down | WP-51 | 5.3 | low | open |
| A05 | FGT | M S | held privately | forget does not reach a store | forget does not reach a store | WP-53 | P1 | low | open |
| A06 | BUG | M C | `zd/skybridge_service.py:591,684`; `zd/routers/skybridge.py:170,180` | the timer store buckets by the raw owner string: `"guest"` on create, `"voice-guest"` on the panel's read and cancel, so a guest timer may be invisible to the panel (the ring path was not read) | one guest owner id; a test that a guest timer is listed and cancellable | WP-53 | 5.3 | med | open |
| A07 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | WP-25 | 5.3 | low | open |
| A08 | VER | M | `zd/alarms.py:5,35` | `ZOE_ALARMS` defaults off; the live value was not read | ground truth reports it | WP-08 | P1 | low | open |
| A09 | OWN | S | design (study D.5) | one-day alarm exceptions belong to the kernel; sound and ramp do not | in WP-50b's scope | WP-50b | 5.3 | low | open |
| A10 | TEST | H | PR #2521 | the alarm lateness instrument counts delete and snooze-dismiss as ringing-set changes; `seen_by_panel()` can acknowledge a transition the snapshot missed | both fixed | WP-11 | P1 | low | in flight #2521 |
| **Write receipts and the claim guard** | | | | | | | | | |
| W01 | MEM | M | `zd/write_receipts.py:49-50,53-62` | the receipt store is in process memory (64 per user, 1 hour), so "did I really do that?" forgets across a restart | each `record()` also publishes to the kernel in shadow; after a clean week the guard reads durable receipts | WP-30 | 5.4 | med | open |
| W02 | MEM | M | `zd/list_service.py:605,650,683,887`; `zd/skybridge_service.py:4702,4863`; `zd/anticipation.py:622` | 7 `list` receipt sites | publish through the one `record()` call | WP-30 | 5.4 | low | open |
| W03 | MEM | M | `zd/calendar_service.py:141`; `zd/skybridge_service.py:3916` | 2 `calendar` sites | as W02 | WP-30 | 5.4 | low | open |
| W04 | MEM | M | `zd/reminder_service.py:264` | 1 `reminder` site | as W02 | WP-30 | 5.4 | low | open |
| W05 | MEM | M | `zd/intent_router.py:3482` | 1 `note` site | as W02 | WP-30 | 5.4 | low | open |
| W06 | MEM | M | `zd/alarm_voice.py:532,653,673`; `zd/skybridge_service.py:241,304` | 5 `timer` sites | as W02 | WP-30 | 5.4 | low | open |
| W07 | MEM | M | `zd/intent_router.py:4761`; `zd/write_claim_guard.py:771` | 2 `memory` sites | as W02 | WP-30 | 5.4 | low | open |
| W08 | MEM | M | `zd/memory_forgotten.py:421`; `zd/memory_retire.py:400-402` | 2 `forget` sites | as W02 | WP-30 | 5.4 | low | open |
| W09 | MEM | M | `zd/intent_router.py:3205` | 1 `list_removed` site (recorded for a tick, L18) | as W02, at WP-27's delete | WP-30 | 5.4 | low | open |
| W10 | MEM | M | `zd/smart_home_service.py:667-678` | 1 `device` site | as W02 | WP-30 | 5.4 | low | open |
| W11 | BUG | M | `zd/write_receipts.py:41`; `zd/lexicons_data/write_claims.json:82,148-154` | `calendar_removed` is declared and its claim is detected but nothing records it, so a true "I took that off your calendar" is always rewritten as unbacked | recorded at every calendar delete | WP-30 | 5.4 | med | open |
| W12 | BUG | M | as W11 | `reminder_removed` declared, never recorded | recorded at WP-40's delete | WP-30 | 5.4 | med | open |
| W13 | BUG | M | as W11 | `timer_removed` declared, never recorded | recorded at timer cancel | WP-30 | 5.4 | med | open |
| W14 | NAME | M | `zd/write_claim_guard.py:710-729` | receipts with no turn stamp count for every turn; the kernel has no counterpart | a `turn` field (G17) and one receipt per claim | WP-30 | 5.4 | low | open |
| W15 | VER | M | `zd/brain_dispatch.py:935-958` [inferred] | deterministic fast-path replies may not pass through the claim guard | read; if so, guarded or written down | WP-30 | 5.4 | med | open |
| W16 | NAME | C | grep: zero `write_receipts` in `zd/mcp_server.py`; `zd/intent_router.py:5341` | no receipt kind for journal, transaction, people, contact or music writes | kinds added with their seams | WP-63 / NEW WP-84 / NEW WP-68 | 5.4 | low | open |
| W17 | NAME | C | `zd/write_receipts.py:43` | `device` is `NOT_A_WRITE`: it backs only its own claim and has no why or undo | a real kind once the device door exists | NEW WP-67 | 5.4 | low | open |
| W18 | BUG | C | `zd/intent_router.py:4614` (`memory_retire` executor) | returns "Noted." when the retire throws: a false success | an honest failure line | WP-30 | 5.4 | low | open |
| **Offers and anticipation** | | | | | | | | | |
| O01 | DUP | M | `zd/pending_suggestions.py`; `zd/contact_offer_policy.py:1-12` | two offer stores and several gates, no single offer seam | kernel offers with a yes and a receipt | WP-62 | 5.5 | med | open |
| O02 | MEM | M | `zd/contact_offer_policy.py:1-12` | contact-offer state is in process memory | durable or a kernel offer | WP-62 | 5.5 | low | open |
| O03 | NAME | M | `zd/proactive/policy_log.py:21-30` | the nearest thing to an attention budget is log-only | the kernel attention service | WP-60 | 5.5 | low | open |
| O04 | PRIV | M | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 | 5.6 | med | open |
| O05 | BUG | C | `zd/intent_router.py:4287-4330` | dismissing an offer says "won't save" whatever the offer was; the accept path hard-codes the word "contacts" | wording from the offer type | WP-62 | 5.5 | low | open |
| O06 | NAME | M | PR #2588-#2592 | the anticipation engine (dark) has its own store and cooldown | rebuilt as kernel offers with the attention budget | WP-62 | 5.5 | med | in flight #2588-#2592 (rebuild kernel-first) |
| O07 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-74 | 5.5 | low | open |
| O08 | NAME | M | PR #2522, #2523, #2607, #2614 | routines are a new store of household actions on plain tables | receipts from day one; tick, skip, snooze as kernel actions with undo | WP-30 | 5.4 | med | in flight (must add) |
| **Notifications, unprompted speech, the proactive engine** | | | | | | | | | |
| N01 | DUP | M | `zd/proactive/engine.py:34-35,70-77` | quiet hours 1 of 3: hour granularity, read at import, no member preference | one function, per member, read per call | WP-44 | 5.2 | med | open |
| N02 | DUP | M | `zd/comm_prefs.py:160-185` | quiet hours 2 of 3: minute granularity, member override | becomes the one function | WP-44 | 5.2 | med | open |
| N03 | DUP | M | `zd/voice_delivery.py:112-119` | quiet hours 3 of 3: own flags, tone only | reads the one function | WP-44 | 5.2 | low | open |
| N04 | DUP | M | `zd/proactive/engine.py:99,428`; `arrival.py:433`; `pull.py:277-283`; `zd/voice_enrollment.py:313-317`; `zd/mcp_server.py:3307-3310`; `zd/skybridge_service.py:355-362`; `zd/intercom.py:327`; `zd/reminder_delivery.py:176-191`; `zd/change_notices.py:818,864,878` | the consumers and wrappers of the three copies | all call the one function | WP-44 | 5.2 | low | open |
| N05 | TEST | M | grep | no test asserts that the copies agree | a test that every consumer gets the same answer | WP-44 | 5.2 | low | open |
| N06 | DEAD | M C | held privately | unused privileged code to delete | unused privileged code to delete | WP-80 | now | low | open |
| N07 | PRIV | M | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 | 5.6 | med | open |
| N08 | PRIV | M C | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 / WP-80 | 5.6 | med | open |
| N09 | PRIV | M | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 | 5.6 | med | open |
| N10 | DUP | M | `zd/distress_handoff.py:273-277`; `zd/mcp_server.py:2494`; `zd/routers/notifications.py:91`; `zd/proactive/engine.py:185`; 8 more (12 in all) | direct `INSERT INTO notifications` beside `notifications_store.create` | one creator behind the gate | WP-61 | 5.6 | low | open |
| N11 | BUG | M C | `zd/proactive/triggers/people_health.py:42-56` | the once-a-day nudge checks only the hour and has no dedup, so it fires on every 5-minute tick for an hour (about 12 times per person, up to 10 people) with push, speech and a pending row; its `< NOW()::TEXT` guard is always true (map R17) | a per-day dedup like `people_birthday.py`; a test of two ticks in one hour | NEW WP-61a | now | med | open |
| N12 | MEM | C | `zd/proactive/triggers/evolution_weekly_digest.py:37-47` | the weekly admin digest's once-a-week guard is in memory; a restart inside the hour re-sends it | a durable dedup | NEW WP-61a | now | low | open |
| N13 | VER | C | `zd/proactive/triggers/evening_windown.py:18-62` (r) | dedups on the database's `CURRENT_DATE`, which may not be the household's date | read; the household date used | NEW WP-61a | now | low | open |
| N14 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 / WP-60 | 5.6 | med | open |
| N15 | PRIV | C | held privately | outbound request not constrained | outbound request not constrained | NEW WP-72 | now | med | open |
| N16 | VER | C | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 | 5.6 | low | open |
| N17 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-61 | P1 | med | in flight #2598 |
| N18 | NAME | C | `zd/omi_health.py:506`; `zd/panel_speaker_health.py:194-210`; `zd/system_updates.py:856-870` | pendant warnings, speaker-outage alerts and the update notice are unprompted sends outside the engine | through the speak gate (admin channel) | WP-61 | 5.6 | low | open |
| **Notes, journal, transactions** | | | | | | | | | |
| NJ01 | DUP | M | `zd/routers/notes.py:118,178,243`; `zd/intent_router.py:3445,3464`; `zd/mcp_server.py:1667,2435,2453`; `zd/pending_suggestions.py:926` | note writes in 4 modules, no seam | one note writer with receipts | WP-63 | 5.7 | low | open |
| NJ02 | DUP | M | `zd/routers/journal.py:161,360,410`; `zd/intent_router.py:3492`; `zd/mcp_server.py:2111` | journal writes in 3 modules | one writer, a `journal` receipt kind | WP-63 | 5.7 | low | open |
| NJ03 | PRIV | M C | held privately | consent or disclosure gap | consent or disclosure gap | WP-63 | 5.7 | med | open |
| NJ04 | VER | M | held privately | forget does not reach a store | forget does not reach a store | WP-63 | 5.7 | med | open |
| NJ05 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-63 | P1 | high | in flight #2598 |
| **Calendar** | | | | | | | | | |
| CA01 | DUP | M | `zd/skybridge_service.py:3894`; `zd/pending_suggestions.py:912` | two event creates bypass `create_event_record` | both call it | NEW WP-83 | 5.7 | med | open |
| CA02 | DUP | M | `zd/skybridge_service.py:4060,4205`; `zd/mcp_server.py:2336,2351`; `zd/routers/calendar.py:424,458` | event edits and deletes have no service function; none records a receipt | update and delete functions with receipts | NEW WP-83 | 5.7 | low | open |
| CA03 | DOC | C | `services/AGENTS.md:26` | says "Exactly ONE calendar-event writer ... never open a second INSERT INTO events"; two other INSERTs exist (CA01) | the contract made true (CA01) or corrected | NEW WP-83 | now | low | open |
| CA04 | DUP | M | `zd/routers/calendar.py:53-55,171-187`; `zd/calendar_service.py:445`; `zd/calendar_read.py:248-253` | four places decide who may see or write an event | the one read function and the kernel's write rule | NEW WP-31 | P6 | low | open |
| CA05 | DEAD | M C | `zd/calendar_ical.py:324` | `to_ics` has no production caller (calendar publishing does not exist) | deleted, unless publishing is planned | WP-80 | now | low | open |
| CA06 | OWN | S | held privately | member delete does not reach a store | member delete does not reach a store | NEW WP-26R | P3 | med | open |
| CA07 | NAME | M | `zd/calendar_feed.py:4,449` | feed import is dark and nothing schedules `sync_account`; the feed engine PRs extend it | lands through `create_event_record`; untrusted cause (K14) | WP-74 | 5.7 | low | in flight #2575, #2577, #2578 |
| **People and contacts** | | | | | | | | | |
| P01 | DUP | M | 9 modules insert `people` (contacts_store, pending_suggestions, intent_router, mcp_server, named_relations, person_extractor, routers/people, routers/voice_tts, skybridge_service) | no person write seam | one writer in `people_graph` | NEW WP-84 | 5.7 | med | open |
| P02 | DUP | M | `zd/person_merge.py:412-426`; `zd/memory_forget_cascade.py:460,587` | raw UPDATEs around the graph's functions | through `people_graph` | NEW WP-84 | 5.7 | low | open |
| P03 | BUG | C | held privately | identity-linking gap | identity-linking gap | NEW WP-84 | 5.7 | med | open |
| P04 | PRIV | C | held privately | authorisation gap | authorisation gap | NEW WP-84 | 5.7 | med | open |
| P05 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | NEW WP-26R | P1 | med | in flight #2598 |
| P06 | DEAD | C | `zd/intent_router.py` `execute_intent` | `people_introduce`, `panel_confirm_code`, `panel_setup`, `extend_capability` have no executor (handled only in `routers/chat.py` and `routers/voice_tts.py:4829-4859`), so a brain or dispatch caller gets nothing | executors, or the names kept out of the dispatchable set by a test | NEW WP-84 | P6 | low | open |
| P07 | NAME | C | held privately | forget does not reach a store | forget does not reach a store | WP-80 | now | low | open |
| **Presence** | | | | | | | | | |
| PR01 | NAME | M | `zd/presence/ha_presence.py:103-105`; `zd/proactive/presence.py`; PR #2593 | two unrelated presence notions, and #2593 adds a third | one presence source the kernel can read | n/a (5.8) | 5.8 | low | in flight #2593-#2596 (must publish) |
| PR02 | DUP | M | `zd/routers/ui_actions.py:185,190,449`; `zd/routers/panel_auth.py:1007`; `zd/routers/voice_tts.py:2162` | five writers of panel presence | one writer | n/a (5.8) | 5.8 | low | open |
| **Memory and every forget path** | | | | | | | | | |
| F01 | PRIV | M S H | held privately | member delete does not reach a store | member delete does not reach a store | NEW WP-24R | now | high | open |
| F02 | PRIV | M | held privately | member delete does not reach a store | member delete does not reach a store | NEW WP-24R | now | high | open (same reason as F01) |
| F03 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-64 / NEW WP-81 | P1 | med | open |
| F04 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-64 | P1 | med | open |
| F05 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-64 / WP-80 | P1 | low | open |
| F06 | FGT | M | held privately | forget does not reach a store | forget does not reach a store | WP-64 / WP-10 | P3 | low | open |
| F07 | OWN | M H | held privately | consent or disclosure gap | consent or disclosure gap | WP-64 | P4 | low | open |
| F08 | FGT | C H | held privately | member delete does not reach a store | member delete does not reach a store | NEW WP-76 | P1 | high | in flight #2598 (landing asked for it in the same PR; the panel copy may be an owner step) |
| F09 | FGT | C | held privately | forget does not reach a store | forget does not reach a store | NEW WP-76 | P1 | med | open |
| F10 | FGT | C | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | high | in flight #2598 (most), #2514 (two speech stores) |
| F11 | FGT | C | held privately | forget does not reach a store | forget does not reach a store | NEW WP-68 | P1 | med | open |
| F12 | FGT | C | held privately | forget does not reach a store | forget does not reach a store | NEW WP-09a | P1 | med | open |
| F13 | FGT | H C | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | med | in flight #2513 |
| F14 | FGT | C | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | low | in flight #2514 |
| F15 | TEST | H | held privately | test can touch real data | test can touch real data | WP-10 | now | high | in flight #2514 |
| F16 | FGT | H | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | med | in flight #2514 |
| F17 | FGT | H | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | low | in flight #2514 |
| F18 | TEST | H | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | low | in flight #2513 |
| F19 | TEST | H | held privately | forget does not reach a store | forget does not reach a store | WP-11 | P1 | low | in flight #2519 |
| F20 | FGT | L H | held privately | forget does not reach a store | forget does not reach a store | WP-64 | P3 | low | open (ledger 2026-11-15) |
| F21 | VER | H S | held privately | forget does not reach a store | forget does not reach a store | NEW WP-26R | P3 | med | open |
| F22 | REV | H | held privately | forget does not reach a store | forget does not reach a store | WP-64 | P3 | low | in flight #2574 |
| F23 | OWN | L | held privately | forget does not reach a store | forget does not reach a store | WP-24R | P3 | med | open (ledger) |
| F24 | FGT | L C | held privately | forget does not reach a store | forget does not reach a store | NEW WP-73 | P4 | med | open (ledger) |
| F25 | VER | C | held privately | forget does not reach a store | forget does not reach a store | NEW WP-73 | P4 | med | open |
| F26 | FGT | L | held privately | forget does not reach a store | forget does not reach a store | WP-64 | P3 | med | open (ledger) |
| F27 | DEAD | C | held privately | forget does not reach a store | forget does not reach a store | WP-80 | now | low | open |
| F28 | FGT | C | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | low | open |
| F29 | PRIV | C | held privately | action leaves no record | action leaves no record | NEW WP-81 | 5.7 | med | open |
| F30 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-81 / NEW WP-31 | P6 | med | open |
| F31 | FGT | C | held privately | member delete does not reach a store | member delete does not reach a store | NEW WP-69 | P1 | low | open |
| **Members, children, guests, identity** | | | | | | | | | |
| M01 | VER | M | held privately | authorisation gap | authorisation gap | WP-23b | P3 | med | open |
| M02 | DUP | M | held privately | consent or disclosure gap | consent or disclosure gap | WP-23b | P3 | med | open |
| M03 | DUP | M | `zd/household_authority.py:282-299` | child rule copy 3 of 4: a lookup failure counts as a child (a safe default to keep as "unknown = least privilege") | the kernel rule | WP-23b | P3 | low | open |
| M04 | DUP | M | held privately | consent or disclosure gap | consent or disclosure gap | WP-80 | P6 | low | open |
| M05 | OWN | M S | `zd/memory_curation.py:5-8`; `zd/memory_space.py:298-333` | guardians are implicit (every adult admin); no guardian table; owner D7 | explicit guardian links written by the reconciler | WP-23a / WP-23b | P4 | med | open |
| M06 | DUP | M | `zd/database.py:91`; `zd/routers/chat.py:1156,2746`; `zd/intent_router.py:3583`; `zd/pending_suggestions.py:279` | five lazy `INSERT ... ON CONFLICT DO NOTHING` sites for zoe-data's users mirror | one upsert, owned by the reconciler | WP-23b | P4 | low | open |
| M07 | OWN | M S | no member-change source (R6); owner: automatic from existing accounts | D6 has nothing to push from | the reconciler: accounts plus child settings, every 60 s and on own changes; an absence suspends, never erases | WP-23b | P4 | med | open |
| M08 | BUG | C | held privately | authorisation gap | authorisation gap | WP-23b | P4 | med | open |
| M09 | PRIV | C | held privately | action leaves no record | action leaves no record | NEW WP-70 | 5.7 | med | open |
| M10 | PRIV | C | held privately | identity-linking gap | identity-linking gap | NEW WP-69 | now | high | open |
| M11 | PRIV | C | held privately | authorisation gap | authorisation gap | NEW WP-71 | now | med | open |
| M12 | PRIV | C | held privately | authorisation gap | authorisation gap | NEW WP-71 | now | low | open |
| M13 | PRIV | C | held privately | authorisation gap | authorisation gap | NEW WP-71 | 5.7 | low | open |
| M14 | PRIV | C | held privately | biometric consent not enforced | biometric consent not enforced | NEW WP-76 | 5.7 | med | open |
| M15 | PRIV | C | held privately | biometric consent not enforced | biometric consent not enforced | NEW WP-76 | 5.7 | med | open |
| M16 | NAME | C | held privately | action leaves no record | action leaves no record | NEW WP-76 | 5.7 | low | open |
| M17 | PRIV | C | held privately | authorisation gap | authorisation gap | NEW WP-71 | now | med | open |
| M18 | VER | C | held privately | authorisation gap | authorisation gap | NEW WP-70 | 5.7 | low | open |
| M19 | NAME | M | `zd/named_guests.py:7-24`; PR #2534, #2537 | named guests are dark and have no kernel principal | guest persons in the kernel (K11) | WP-25 | 5.1 | low | in flight #2534, #2537 |
| **Devices, smart home, music** | | | | | | | | | |
| D01 | PRIV | C | held privately | physical device control lacks confirmation | physical device control lacks confirmation | NEW WP-67 | 5.3 | high | open (owner choice H2) |
| D02 | VER | C | held privately | physical device control lacks confirmation | physical device control lacks confirmation | NEW WP-67 | 5.3 | med | open |
| D03 | NAME | C | held privately | action leaves no record | action leaves no record | NEW WP-67 | 5.4 | med | open |
| D04 | PRIV | C | held privately | authorisation gap | authorisation gap | NEW WP-71 | now | med | open |
| D05 | NAME | C | held privately | authorisation gap | authorisation gap | NEW WP-70 | 5.7 | low | open |
| D06 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-68 | 5.3 | low | open |
| D07 | NAME | C | held privately | authorisation gap | authorisation gap | NEW WP-68 | 5.4 | low | open |
| D08 | BUG | C | held privately | identity-linking gap | identity-linking gap | NEW WP-68 | 5.7 | low | open |
| D09 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-68 | 5.7 | low | open |
| D10 | NAME | C | `zd/music_service.py:321-336` | the preferred speaker is a JSON file inside the checkout (`data/music_prefs.json`) | a household setting in the database | NEW WP-68 | P6 | low | open |
| **Things that leave the house** | | | | | | | | | |
| E01 | PRIV | C | held privately | outbound request not constrained | outbound request not constrained | NEW WP-72 | 5.7 | med | open (scrubber in #2527) |
| E02 | VER | C | held privately | outbound request not constrained | outbound request not constrained | NEW WP-82 | P3 | med | open |
| E03 | PRIV | C | held privately | outbound request not constrained | outbound request not constrained | NEW WP-72 | 5.7 | low | open |
| E04 | PRIV | C | held privately | outbound request not constrained | outbound request not constrained | NEW WP-72 | 5.7 | low | open |
| E05 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-69 | 5.6 | low | open |
| E06 | NAME | S | held privately | outbound request not constrained | outbound request not constrained | NEW WP-74 | 5.7 | med | in flight #2527-#2530 (must add) |
| E07 | VER | C | held privately | authorisation gap | authorisation gap | NEW WP-81 | now | low | open |
| E08 | NAME | C | `scripts/perf/self_improver.py:457,588` | (template, not installed) the self-improver sends scrubbed prompts to a cloud API | listed as an egress with its scrubber | NEW WP-72 | later | low | open |
| **Messaging, pendant, self-evolution, settings** | | | | | | | | | |
| T01 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-74 | P3 | med | open |
| T02 | VER | C | held privately | physical device control lacks confirmation | physical device control lacks confirmation | NEW WP-67 | 5.3 | med | open |
| T03 | VER | C | held privately | forget does not reach a store | forget does not reach a store | WP-10 | P1 | low | open |
| T04 | NAME | C | `labs/AGENTS.md:10-15`; `tg/` | the live Telegram bot is deployed from `labs/` under an exception named temporary | promoted to `services/` (deep dive task B1) | n/a | P6 | low | open |
| OM01 | PRIV | C S | held privately | consent or disclosure gap | consent or disclosure gap | NEW WP-74 | 5.7 | med | open (owner choice H5) |
| OM02 | FGT | L | held privately | forget does not reach a store | forget does not reach a store | WP-64 | 5.7 | med | open (ledger) |
| OM03 | MEM | C | `zd/omi_linking.py:20`; `zd/routers/omi_recordings.py:~170`; `zd/auth_handoff.py:152`; `zd/setup_qr.py:29`; `zd/routers/panel_provision.py:54` | short-lived handles, cooldowns and rate limits live in memory | accepted as short-lived by design; listed | n/a | P6 | low | open |
| SE01 | PRIV | C | held privately | security or privacy assurance gap | security or privacy assurance gap | NEW WP-75 | sale | med | in flight #2582-#2587 |
| SE02 | NAME | C | `zd/main.py:1535`; `scripts/maintenance/router_selftrain.py` | (off by default) router self-training swaps a model file and restarts a unit | recorded as a switch change | NEW WP-75 | later | low | open |
| SE03 | VER | C | `zd/evolution_autonomy.py` (header) | some proposal types are "execute on admin approval"; today approval only records (`evolution_inbox.py:49`) | read; pinned as record-only until reviewed | NEW WP-75 | later | low | open |
| S01 | NAME | C | held privately | action leaves no record | action leaves no record | NEW WP-70 | 5.7 | low | open |
| S02 | NAME | C | held privately | action leaves no record | action leaves no record | NEW WP-70 | 5.7 | low | open |
| S03 | NAME | S | design (study A.3) | the kernel does not note the values of its own switches at start | it records them; it never controls its own off switch | NEW WP-75 | P1 | low | open |
| **Dead code, structure, docs** | | | | | | | | | |
| X01 | FLAG | H C | held privately | security or privacy assurance gap | security or privacy assurance gap | WP-80 | now | med | open |
| X02 | FLAG | C | `zd/flag_registry.py:90` | `ZOE_A2A_CLIENT_TIMEOUT_S` is registered and read by nothing | the entry deleted | WP-80 | now | low | open |
| X03 | DEAD | C | held privately | unused privileged code to delete | unused privileged code to delete | WP-80 | now | med | open |
| X04 | NAME | C | `zd/chat_stream_protocol.py:150,363`; `zd/routers/chat.py:395,404,519,1936,1949`; `zd/greploop_guard.py:2334-2361`; `zd/executors/kanban_adapter.py`; `zd/executors/executor_queue_backend.py`; `services/zoe-core/abilities/delegate.ts:2-5`; `docker-compose.modules.yml:210`; `RESTART_SERVICES.sh:16`; `scripts/setup/jetson/zoe-self-review.service:2`; `.greptile/rules.md`; `.cursorrules` | live code and config still named for, or calling, the retired agents | renamed or deleted; `delegate.ts`'s exposure checked first | WP-80 | P6 | low | open |
| X05 | DEAD | C | `zd/zoe_capability_profile.py:19,178-187`; `zd/zoe_memory_router.py:16,82`; `zd/zoe_observation_trace.py:48`; `zd/mempalace_baseline.py:68` | graphify residue, although graphify is retired (CLAUDE.md) | removed | WP-80 | now | low | open |
| X06 | DEAD | C | section 3 list | retired-agent scripts with no caller | deleted | WP-80 | now | low | open |
| X07 | DOC | C | `docs/guides/HERMES_OPTIMIZATION_PLAYBOOK.md` and 6 more (section 3) | guides that present retired agents as live, with no banner | deleted | WP-80 | now | low | open |
| X08 | DEAD | C | `zd/routers/stubs.py:24` and the `/api/projects`, `/api/collections`, `/api/user/layout` stubs | stub routes no page calls (one always answers 501) | deleted with their pin in `tests/test_mutating_routes_declare_auth.py:79` | WP-80 | now | low | open |
| X09 | DEAD | C | `zd/autoresearch_bridge.py`; `zd/zoe_capability_profile_promotion_handoff.py` | no importer outside their tests | deleted with their tests | WP-80 | now | low | open |
| X10 | OWN | C | `zd/zoe_capability_*` (12 modules), `zoe_evolution_outcome_*` (3), `zoe_memory_admission`, `zoe_memory_contract`, `hindsight_*` (3) | about 21 modules reached only by two hand-run scripts | the owner keeps them as a tool or they go | WP-80 | P6 | low | open |
| X11 | DOC | H C | `tests/unit/test_research_index.py:20`; `docs/research/index.md` | the research index is at its cap (25 open), so new records (the prior-art record among them) cannot register; 40 absorbed rows are due to retire on 2026-11-17 | the 40 retired on the day; the prior-art record absorbed into this page and the plan | WP-80 | now | low | open |
| X12 | TEST | C | `zd/tests/test_zoe_agent_skills.py:188`; `tests/unit/test_samantha_person.py:606` | `assert ... or True`: cannot fail | a real assertion or deleted | WP-80 | now | low | open |
| X13 | TEST | C | `zd/tests/test_phone_app.py:290-292`; `zd/tests/test_memory_space.py:382`; `zd/tests/test_calendar_feed.py:505` | a parametrised case skipped with no reason; a race masked by a runtime skip; a silent return when an optional package is missing | reasons, or the case moved | WP-80 | now | low | open |
| X14 | DOC | C | `docs/knowledge/voice-pipeline.md:19` vs `docs/CANONICAL.md:127`; `docs/developer/continuation-prompt.txt`; `docs/PLATFORM_OVERRIDES.md:52,57` | docs that contradict CANONICAL (the STT engines; retired containers) | corrected or deleted | WP-80 | now | low | open |
| X15 | DOC | C | `docs/ROADMAP.md:92,121-133`; `docs/PLANS.md:28-31` | the roadmap and plans still describe Zoe OS as "#2504 to #2507, 84 lab tests", "103 stores by hand", the record "arriving with #2507" | updated to #2630 and its stack, the fast track, the map's 134-store count | WP-80 | now | low | open |
| X16 | DOC | C | `docs/AGENTS.md` (21-day ledger rule); `docs/knowledge/open-problems.md` | many ledger lines are older than 21 days, which the docs contract says become a roadmap item or are deleted | a ledger pass | n/a | now | low | open |
| X17 | DOC | C | held privately | security or privacy assurance gap | security or privacy assurance gap | WP-80 | now | low | open |
| X18 | DUP | C | `brain/src/tools/zoe-tools.ts:576-615` | `set_timer` is the one brain write that copies `runWrite`'s gates inline instead of calling it | through `runWrite` | NEW WP-02b | P3 | low | open |
| X19 | PRIV | C | held privately | consent or disclosure gap | consent or disclosure gap | WP-61 / NEW WP-74 | 5.6 | low | open |
| X20 | NAME | C | `scripts/maintenance/multica_board_runner.py` chain (`zd/multica_board_runner.py`, `omnigent_issue_executor.py`, `pi_executor.py`) | hand-run only | kept as an operator tool or deleted | WP-80 | P6 | low | open |
| **Safety floors (noted, not kernel seams)** | | | | | | | | | |
| SF01 | BUG | H L | `zd/medical_safety.py`; distress hand-off cues | a question-form overdose is answered by the distress gentle line before the medical floor; a joking overdose sentence gets the crisis line (r) | routing fixed with must-not-fire sets | outside (ledger) | now | med | open (parked builder) |


### New work packages proposed by this register

The map's packages run WP-01 to WP-66. These are new; sizes are [inferred].

| WP | Goal | Size | Phase | Owner |
|---|---|---|---|---|
| WP-01b | Authority kernel integration, privacy safeguards and reliable settlement | L | P3 | K |
| WP-02b | The brain's Node suite (and the kernel lab app's) in CI, offline; `set_timer` through `runWrite` | S | now | L + G |
| WP-09a | Forget and restore consistency, with stated backup retention | S | P1 | K + G |
| WP-24R | Complete, durable and resumable member removal; replaces WP-24 | M | now | G |
| WP-26R | Authorship and accepted-sharing migration, including an explicit old-row policy | M | P3 | G |
| WP-31 | Read side: one visibility function for lists, reminders and calendar; the kernel's sharing written as the `visibility` projection; a test forbidding raw filters (study G.9) | M | starts now, finishes P6 | G |
| WP-32 | One consistent guest predicate, pinned by a test | S | P3 | C |
| WP-61a | Unprompted-trigger hygiene: per-day dedup for `people_health`, durable weekly guard, household date | S | now | C |
| WP-67 | Device consequence policy, confirmation and receipts | M | 5.3 | G |
| WP-68 | Music actions, privacy, attribution and durable preferences | M | 5.7 | G |
| WP-69 | Identity linking hardened for the messaging integration | S | now | G |
| WP-70 | Household settings as decided and recorded actions | M | 5.7 | G |
| WP-71 | Panel and administrative access policy hardened | S | now | C |
| WP-72 | Constrained outbound requests and private disclosure | M | 5.7 (push: now) | G |
| WP-73 | Forget consistency across night processes | M | P4 | G |
| WP-74 | Cause classes and consent for actions derived from untrusted text | M | P3 | K + G |
| WP-75 | Kernel ownership, budgets, replay evidence and owner approval | M | P3 | K |
| WP-76 | Biometric consent and complete identity-data lifecycle | M | 5.7 (forget: P1) | G |
| WP-77 | Consent ask (P 3.x) | S | 5.5 | K |
| WP-78 | Driver classes, absolute verbs, the uncertain resolver (P 6.3-6.5) | S | P4 | K |
| WP-79 | Encrypted mode, lab only (P 12) | L | later | K |
| WP-80 | Retire by removing: section 3, now | M (many small commits) | now | C |
| WP-81 | Consistent authorisation and records for administrative actions | S | 5.7 | G |
| WP-82 | Verify legacy lane use; retire or align its policies | S | P3 | G |
| WP-83 | Calendar convergence: creates through `create_event_record`, update and delete functions with receipts, the services contract made true | M | 5.7 | G |
| WP-84 | People convergence: one writer, reliable matching and consistent policy | M | 5.7 | G |



## 2. The coverage audit

Inventory from the private reading, with every listed router, intent, tool, handler, job and module retained. D = decide, R = record, N = not through the kernel. UNCOVERED means the implementation map has no seam; it does not identify a missing security check. Source offsets and security-finding links are withheld. An identifier containing prohibited private terminology is withheld in the retired-tool inventory.


### 2.1 Every router (`zd/routers/`, 65 modules)

| router | what it does | seam | class | rows |
| --- | --- | --- | --- | --- |
| alarms | alarm CRUD, snooze, dismiss, overrides | 2.3 | D (R snooze/dismiss) | detail held privately |
| auth | sign-out proxy to zoe-auth | 2.12 | R | none |
| autoresearch | run status read | UNCOVERED | N | detail held privately |
| bluetooth | panel Bluetooth jobs, admin only | UNCOVERED | D (device) | none (sound) |
| board | engineering summary read | UNCOVERED | N | none |
| calendar | event CRUD | 2.8 | D | CA02, CA04 |
| capability_matrix | per-role permission rows | UNCOVERED | D (consent) | detail held privately |
| change_notices | owner notice prefs, kill file | UNCOVERED | R | detail held privately |
| chat | the chat turn, sessions, feedback | 2.4 (turn) | gateway; R sessions | detail held privately |
| contacts | contact fields, import, merges | 2.9 | D | none |
| dashboard | per-user widget layout | UNCOVERED | R | none |
| enrollment | biometric setup state, enrol result | UNCOVERED | R | detail held privately |
| evolution_inbox | approve or reject proposals | UNCOVERED | R | SE03 |
| face_id | face enrol, sync, delete | UNCOVERED | D | detail held privately |
| geo | geocoder proxy | UNCOVERED | N (egress) | detail held privately |
| ha_control | Home Assistant service calls | UNCOVERED | D | detail held privately |
| handoff | connect-an-app flow, send to phone | UNCOVERED | D (send) | detail held privately |
| ha_ui | cookie gate for the HA UI | UNCOVERED | N | detail held privately |
| journal | journal entries | 2.7 | D | detail held privately |
| lists | lists and items | 2.1 | D | detail held privately |
| member_register | per-member style register | 2.11 | R | detail held privately |
| memories_card | panel memory card taps (forget) | 2.11 | D (forget) | detail held privately |
| memories_curation | guardian edits of a child's memory | 2.11 | D | detail held privately |
| memories | proposals, review, forget, export | 2.11 | R; D forget | detail held privately |
| multica_ui | cookie gate, engineering board | UNCOVERED | N | detail held privately |
| music_ma_ui | cookie gate, Music Assistant UI | UNCOVERED | N | detail held privately |
| music | play, queue, transfer, group, favourites | UNCOVERED | D (other rooms) | detail held privately |
| music_setup | add a music source by phone | UNCOVERED | D (account) | none (token-only by design) |
| notes | notes CRUD | 2.7 | D | detail held privately |
| notifications | bell notifications | 2.6 | R | detail held privately |
| omi_day_review | approve pendant candidates | UNCOVERED | D | detail held privately |
| omi_recordings | pendant upload, assign, delete | UNCOVERED | R; D assign | OM03 |
| omi_voices | tag voices, voiceprints | UNCOVERED | D | detail held privately |
| panel_auth | panels, bindings, device tokens, PIN | 2.12 | D | detail held privately |
| panel_config | per-panel settings | UNCOVERED | R | detail held privately |
| panel_kiosk | kiosk ticket | UNCOVERED | N | none |
| panel_provision | first-boot pairing | 2.12 | D | detail held privately |
| panel_relay | remote-access certificates | UNCOVERED | D (leaves house) | none (admin only) |
| panel_review_cards | review card taps | UNCOVERED | D (apply) | detail held privately |
| people | people, fields, relationships | 2.9 | D | detail held privately |
| persona | household persona, member modes | UNCOVERED | R (setting) | detail held privately |
| phone_app | phone pairing, push tokens | UNCOVERED | R | none (flag-dark) |
| pi_intent_lab | admin intent lab | UNCOVERED | N | none |
| portrait | portrait, emotional moments | 2.11 | R | detail held privately |
| presence | presence read | 2.10 | N | PR01 |
| proactive | schedule nudges, pending, suggestions | 2.6, 2.5 | D | detail held privately |
| push | web push subscribe and send | 2.6 | R; send = gate | detail held privately |
| reminders | reminders CRUD, snooze, done | 2.2 | D | detail held privately |
| rooms | rooms and device placement | UNCOVERED | D (devices) | detail held privately |
| skybridge | typed command resolver, timers | 2.3, 2.1 | gateway; D cancel | detail held privately |
| smart_home_setup | device setup guide | UNCOVERED | N | none |
| struggles | struggle reports, labels | UNCOVERED | R | none |
| stubs | stubs, settings, dream window, intelligence flags | 2.6 | R | detail held privately |
| system | system operations | partial | mixed | detail held privately |
| system_stats | host health read | UNCOVERED | N | none |
| telegram_media | Telegram voice notes | UNCOVERED | N | detail held privately |
| transactions | money records | 2.7 | D | detail held privately |
| transcript_check | morning transcript answers | UNCOVERED | R | none |
| ui_actions | panel action queue, form confirm | UNCOVERED (2.5 adj.) | D (confirm); R | detail held privately |
| user_profile | profile, Telegram link, delivery prefs | 2.6 adj. | R | detail held privately |
| voice_livekit | LiveKit audio | UNCOVERED | N | none |
| voice_settings | household voice | UNCOVERED | R | detail held privately |
| voice_speaker_adapt | voiceprint adaptation, reset | UNCOVERED | R | detail held privately |
| voice_tts | voice gateway | partial | mixed | detail held privately |
| weather | weather read, prefs | UNCOVERED | R; N reads | detail held privately |

### 2.2 Every intent (`zd/intent_router.py`)

| intents | executor | seam | class | rows |
| --- | --- | --- | --- | --- |
| list_add, list_show, list_rename, list_remove | ; remove ticks at | 2.1 | D | L18 |
| reminder_create, reminder_list | dispatch | 2.2 | D | none |
| calendar_create, calendar_show | dispatch | 2.8 | D | none |
| note_create, note_search | dispatch | 2.7 | D | NJ01 |
| journal_create, journal_prompt, journal_streak | ; in-process tools | 2.7 | R | NJ02 |
| transaction_create, transaction_summary | in-process dispatch | 2.7 | D | detail held privately |
| timer_create | dispatch | 2.3 | R | A06 |
| alarm_set, alarm_list | dispatch only (`routers/system.py`) via `alarm_voice` | 2.3 | D | none |
| people_create, people_search, people_same_person_reply | dispatch | 2.9 | D | P01 |
| people_introduce | none (voice lane only) | 2.9 | D | P06 |
| pending_offer_accept, pending_offer_dismiss | dispatch | 2.5 | D | O05 |
| memory_forget_entity, memory_forget_last | dispatch | 2.11 | D | detail held privately |
| memory_remember, memory_store, memory_retire | dispatch | 2.11 | R | W18 |
| portrait_refresh, portrait_reveal | dispatch | 2.11 | R | none |
| smart_home | dispatch | NEW WP-67 | D | detail held privately |
| music_play, music_control, music_volume, music_favorite, music_setup, set_volume | dispatch | NEW WP-68 | D | detail held privately |
| panel_confirm_code, panel_setup, panel_status, panel_list | none (chat only) | NEW WP-71 | D | P06 |
| extend_capability | none (chat only) | UNCOVERED | D | P06 |
| engineering and board intents (agent_tasks_status, board_status, board_heal, engineering_dispatch_pause/resume, engineering_task_create/status, engineering_ticket_list/move_todo/split, evolution_proposals_review, user_issue_report) | dispatch | UNCOVERED (not household) | none | none (not dispatchable) |
| greeting, good_morning, good_evening, lets_talk, acknowledgement, status_check, time_query, date_query, calculate, daily_briefing, weather, time_planning_clarification | dispatch | none | N | none |
| coreference names reminder_cancel, calendar_add, calendar_delete_event, calendar_list_events, list_remove_item, list_get_items, music_stop, timer_set | none | 2.2 | D | R02, R03 |

### 2.3 Every MCP tool (`zd/mcp_server.py`, 59 tools)

| tools | writes | seam | class | rows |
| --- | --- | --- | --- | --- |
| calendar_list_events, calendar_today, calendar_create_event, calendar_update_event, calendar_delete_event | service operations; unused: delete | 2.8 | D | CA02 |
| list_get_items, list_add_item, list_remove_item | service operations; unused: delete | 2.1 | D | detail held privately |
| reminder_create, reminder_list, reminder_update, reminder_delete, reminder_snooze | service operations; unused: delete | 2.2 | D | detail held privately |
| people_search, people_create, people_update, people_delete | service operations; unused: delete | 2.9 | D | detail held privately |
| note_create, note_search, note_update, note_delete | raw | 2.7 | D | NJ01 |
| journal_create_entry, journal_list_entries, journal_get_streak, journal_get_prompts, journal_on_this_day | raw | 2.7 | R | NJ02 |
| transaction_create, transaction_list, transaction_summary | transaction operations | 2.7 | D | detail held privately |
| memory_add, memory_search, memory_list, memory_review, memory_forget, user_portrait_get | via MemoryService | 2.11 | R; D forget | detail held privately |
| notification_create, proactive_schedule, panel_announce | service operations; unused: delete | 2.6 | D | detail held privately |
| panel_navigate, panel_clear, panel_show_fullscreen, panel_request_auth, panel_check_auth, panel_set_mode, panel_show_smart_home, panel_show_media | UI queue | UNCOVERED (screens) | N | none |
| retired privileged tool (identifier held privately) | unused: delete | UNCOVERED | delete | detail held privately |
| dashboard_get_layout, dashboard_save_layout, dashboard_add_widget, dashboard_available_widgets | layout | UNCOVERED | R | none |
| weather_current, weather_forecast, zoe_get_time, zoe_get_status, zoe_list_skills, zoe_self_capabilities, media_get_now_playing, ambient_search | none | none | N | none |
| web_search, web_browse, panel_browser_screenshot, browser_get_capabilities, browser_compare_backends, cloakbrowser_fetch, cloakbrowser_screenshot | service operations; unused: delete | NEW WP-72 | D | detail held privately |
| greptile_pr_status, greptile_pr_comments, greptile_trigger_review, list_board_issues, update_board_issue, create_evolution_proposal, flag_needs_human_review | service operations; unused: delete | UNCOVERED | none | detail held privately |

### 2.4 Every brain tool (`brain/src/tools/zoe-tools.ts`)

| tool | dispatches | class |
| --- | --- | --- |
| get_time, recall_memory, get_weather, list_reminders, show_calendar, show_list, note_search | reads | N |
| shopping_list_add, add_to_list | list_add | D (2.1) |
| list_remove | list_remove (ticks: L18) | D (2.1) |
| set_timer | timer_create | R (2.3) |
| add_reminder | reminder_create | D (2.2) |
| alarm | alarm_set / alarm_list | D (2.3) |
| add_calendar_event | calendar_create | D (2.8) |
| create_note | note_create | D (2.7) |
| journal | prompts, streak, journal_create | R (2.7) |
| people | people_search, people_create | D (2.9) |
| media | music_play, music_control, music_volume, set_volume, music_setup | D (WP-68) |
| home | smart_home (lights only) | D (WP-67) |
| remember_fact, remember_emotional_moment, memory_retire | memory_store, memory_retire | R (2.11) |
| activate_abilities | disclosure only | N |
| web_search (flag) | `/api/system/web-search`; taints the turn | D (WP-72) |

### 2.5 Every panel action handler

| surface | handlers | seam | rows |
| --- | --- | --- | --- |
| `zd/routers/ui_actions.py` `POST /actions` | 31 action types (`zd/ui_orchestrator.py`): navigate, open_panel, focus, fill, submit, create_record, update_record, delete_record, highlight, notify, refresh, click, panel_navigate, panel_navigate_fullscreen, panel_clear, panel_browser_frame, panel_show_fullscreen, panel_announce, panel_request_auth, panel_set_mode, panel_show_smart_home, panel_show_media, panel_show_research_report, panel_open_form, panel_stream_text, panel_dismiss_ambient, panel_close_action_form, panel_list_update, panel_show_action_form, panel_update_field, show_card | UNCOVERED (screens); writes D | detail held privately |
| `ui_actions.py` bind, pending, ack, sync, context, ledger, retry, requeue, form open/close | panel session state | 2.10 (presence writers) | PR02 |
| `ui_actions.py` form confirm | calendar_create, list_add per item, otherwise nothing | 2.8, 2.1 | L32, L33 |
| `zd/skybridge_service.py` resolver (dispatch DB ) | lists (add, remove, complete, rename, correct, edit, create, share, unshare, show), calendar (create, update time, edit, delete, show), alarm, timer, clock, music, voice settings, smart home, weather, people (identity, remember, offers, create, search), the delayed-command refusal | 2.1, 2.3, 2.8, 2.9, WP-67, WP-68 | L02, L20, CA01, P01 |
| touch `ui/touch/home.html` and `ui/js/touch-*.js` fetches | skybridge resolve and timers; chat; voice speak and settings; HA control (`home.html`, `touch-ui-executor.js`); rooms; music control, player, transfer, group, jukebox; list items GET/PUT tick/DELETE (`home.html`); reminders PUT/DELETE; calendar PUT/DELETE; people DELETE and relationships; alarms POST/PATCH/DELETE, snooze, dismiss, overrides; handoff; display, idle logout, panel config; review cards; transcript check; local kiosk helper on port 7777 | as each router | detail held privately |
| desktop widgets (`ui/js/widgets/core/shopping.js`, `personal.js`, `work.js`, `tasks.js`; `ui/chat.html`) | list REST calls | 2.1 | L28 |

### 2.6 Every background and night job

| job | started | cadence | does | seam | class | rows |
| --- | --- | --- | --- | --- | --- | --- |
| proactive slow loop | `zd/proactive/engine.py`, `main.py` | 300 s | all Tier-2 triggers, pending rows, push, speech | 2.6 | D | N01 |
| pending cleanup | `engine.py` | 1 h | deletes expired pending rows | 2.6 | none | none |
| reminder scan + APScheduler fires | `main.py`; `proactive/scheduler.py` | slow tick; per reminder | schedules and fires reminders | 2.2 | D | detail held privately |
| morning check-in | `main.py` | 07:30-08:30, DB dedup | brief | 2.6 | D | none |
| evening wind-down | `main.py` | 21:00, DB date dedup | nudge | 2.6 | D | N13 |
| people birthday | `main.py` | 08:00, day dedup | birthday reminder | 2.9 | D | none |
| people health | `main.py` | 09:00 hour | "you have not spoken to X" | 2.9 | D | N11 |
| emotional follow-up | `main.py` | once per moment, flag | follow-up on a worry | 2.5 | D | none |
| evolution weekly digest | `main.py` | Friday 18:00, memory guard | admin digest | UNCOVERED | D | N12 |
| commitments sweep | `engine.py` | every tick | commitments, reminders | 2.5, 2.2 | D | detail held privately |
| alarm ring engine and push beat | `main.py`; `alarms.py` | 1 s | rings, alarm music | 2.3 | D | none |
| HA presence poll | `main.py` | interval, flag | hashed presence rows | 2.10 | R | PR01 |
| music journal observer | `main.py` | 300 s, on | play history per member | NEW WP-68 | R | detail held privately |
| music discovery batch | `main.py` | Sunday 03:00, off | per-member playlists | NEW WP-68 | D | detail held privately |
| YouTube Music cookie refresh | `main.py` | 12 h, off | account cookie | UNCOVERED | none | none |
| router self-train | `main.py` | Saturday 01:00, off | model swap, unit restart | NEW WP-75 | R | SE02 |
| Multica autopilot sync and poll | `main.py` | crons, off | engineering | UNCOVERED | none | none |
| memory digest (03:00) | `main.py`, `routers/system.py` | nightly | memory writes | 2.11 | R | detail held privately |
| weekly consolidation | `main.py` | Sunday 04:00 | merge and archive | 2.11 | R | detail held privately |
| idle consolidation | `main.py` | loop, off | memory writes | 2.11 | R | none |
| memory capture retry | `main.py` | loop | retries writes | 2.11 | R | none |
| pendant transcription, retention, health, review digest, daytime review | `main.py` | loops | transcripts; expiry; owner warnings; Telegram count; candidates | NEW WP-74 | R; D sends | detail held privately |
| voice clip retention | `main.py` | flag | expiry | 2.11 | none | none |
| change notices | `main.py` | interval, flag | owner Telegram feed | 2.6 | none | none |
| release check | `main.py` | daily | update notice, GitHub API | UNCOVERED | none | N18 |
| runtime health, task watchdog, prefix keeper, lag monitor, warmups | `main.py` | loops | probes, stuck tasks | UNCOVERED | none | detail held privately |
| panel token sweep | `main.py` | interval | revokes unused kiosk tokens | 2.12 | none | none |
| speaker-outage alarm, distress notify | `panel_speaker_health.py`; `distress_handoff.py` | per event | admin and contact notifications | 2.6 | D | detail held privately |
| zoe-backup | `scripts/setup/systemd/zoe-backup.service` (night presleep) | nightly | dumps, palace, host snapshot | 4.7 | none | detail held privately |
| off-box backup and verify | `zoe-offbox-backup.timer`, `zoe-backup-verify.timer` | 05:10, 05:30 | encrypted copy out of the house; drill | 4.7 | D (one-time) | detail held privately |
| memory export | `zoe-memory-export.service` (night pre-phase) | nightly | 14 JSON exports, redacted | 2.11 | none | none |
| health, crash-loop watch, log rotate, MA idle reap, agent samplers, merge queue, reapers, opensrc refresh, Telegram watchdog, heartbeat | `scripts/setup/systemd/*.timer` | minutes | ops | UNCOVERED (ops) | none | none |
| voice regression | `zoe-voice-regression.timer` | 04:30 | corpus replay | 4.5 | none | I07 |
| router retrain, Samantha weekly, self-improver, self-heal | timers (templates) | weekly / daily | training, evidence, prompt tuning, fixes | NEW WP-75 | R | detail held privately |
| weekly self review | `scripts/setup/jetson/zoe-self-review.timer` | Saturday 03:00 | runs a home-dir script named for a retired agent | none | delete | X04 |
| night window (digest, extract prepass, dreaming, night mind, struggle proposals, self review, pendant re-pass, clean read, day review) | `scripts/night/systemd/zoe-night-window.timer` | 02:30 | all in separate processes, zoe-data stopped | 2.11; NEW WP-73 | R | detail held privately |
| presleep catch-up, restore retry, index compaction | night timers | after boot / wake | backup, restore | 4.8 | none | none |
| legacy cron installers | `scripts/setup/setup_overnight_training*.sh` | opt-in | standalone digest | none | delete | detail held privately |

### 2.7 Top-level modules that write, speak, send or leave the house, not in the map

`music_history`  · `phone_app` (pairing; flag-dark) · `persona_layer`  · `user_prefs`  · `voice_settings`
 · `ui_layouts`, `ui_orchestrator`  · `restraint` (reached) · `speaker_adaptation`  · `named_guest_learning`,
`named_guest_enrol`  · `enrollment_state`  · `auth_handoff`, `telegram_link`, `telegram_notify`  ·
`connected_accounts` (reached) · `turn_outcomes`, `struggle_detector`, `struggle_proposals` (reached) ·
`evolution_queue`, `evolution_inbox`, `evolution_notice`  · `conversation_feedback`  · `background_runner`  ·
`exact_words`, `now_card`, `user_portrait`, `user_model_card`, `memory_curation`, `member_register` (2.11) · `omi_*`  ·
`distress_handoff`  · `panel_speaker_health`, `system_updates`  · `memory_residue`  · `proposal_board_bridge`,
`multica_board_runner`  · `web_search_provider`, `research_evidence`, `ytmusic_signin`, `greptile_client`  ·
`zoe_agent` .

Modules without a stated seam remain uncovered by the map; this is a scope verdict only.

### 2.8 The areas the map's fourteen did not include

| area | verdict | rows |
| --- | --- | --- |
| Music and media | D for playing into another room; R for the journal | detail held privately |
| Smart-home commands | D, with a consequence class (owner H2) | detail held privately |
| Web lookup | D, one door; the drafts are kernel-first | detail held privately |
| Messaging (Telegram, live from `labs/`) | the link is a consent decision; forwards are untrusted | detail held privately |
| Pendant recordings and the day review | R for capture; D for every apply, offer-only by default | detail held privately |
| Voice enrolment and identity | D for enrol and delete; profiles reached by forget | detail held privately |
| Self-heal and self-evolution | not through at run time; kernel changes need approval | detail held privately |
| Skills | not through: `skills/` is documentation only, no runtime loader | none |
| Settings and flags changed at runtime | R for owner switches; D for household settings; no endpoint writes `.env` | detail held privately |
| Exports and backups | D for off-box; forget reaches them by ageing only | detail held privately |
| Writes to another member's data | D; records identify the actor | detail held privately |


## 3. What to remove

### 3.1 Deletable now

Each was checked with grep for callers across `services/`, `scripts/`, `labs/`, `tools/`, `tests/`, `.github/` and `ui/`.

| what | evidence | proof nothing calls it |
| --- | --- | --- |
| `zd/flag_registry.py:90` `ZOE_A2A_CLIENT_TIMEOUT_S` | no reader | `grep -rIl ZOE_A2A_CLIENT_TIMEOUT_S .` lists only the registry |
| private deployment defaults (locations held privately) | replace with patterns | private review acceptance criteria |
| `scripts/setup/openclaw-multica-wrapper.sh` | retired agent | `grep -rIl openclaw-multica-wrapper .` lists only itself |
| `zd/OPENCLAW_AG_UI_STREAMING.md` | retired design note | no inbound reference |
| `scripts/maintenance/retire_hermes_openclaw_board.py`, `migrate_hermes_env.py` (+ `tests/unit/test_migrate_hermes_env.py`, its line in `test_owner_scripts_help.py`, the ratchet entries), `multica_reassign_open_to_hermes.py`, `reset_engineering_boards.py`, `verify_kanban_github_auth.sh` | one-shot or retired-agent scripts | callers are only their own tests, a ratchet list and retired guides |
| `docs/guides/HERMES_OPTIMIZATION_PLAYBOOK.md`, `HERMES_ENGINEERING_SKILLS.md`, `MULTICA_HERMES_PR_LOOP.md`, `KANBAN_GITHUB_AUTH.md`, `ENGINEERING_HARNESS_LOOP.md`; `docs/governance/OPENCLAW_ECOSYSTEM_UPDATE.md` | retired agents presented as live | no code caller; re-point the four `doc=` fields in `flag_registry.py` (`:134,308,437,1086`) first |
| `skills/openclaw/` | `skills/AGENTS.md`: not wired | no runtime loader exists |
| unused privileged code (locations held privately) | unused: delete | private review acceptance criteria; retain required production operations |
| unused stub routes (security detail held privately) | unused: delete | retain coverage evidence in the private review |
| `zd/calendar_ical.py:324` `to_ics` (unless publishing is planned) | no production caller | tests only |
| the `reminder_cancel` branch and the other dead coreference names (R02, R03), unless WP-40 wires them | no executor | a new test: every name `resolve_coreference` can emit has an executor |
| `zd/autoresearch_bridge.py`, `zd/zoe_capability_profile_promotion_handoff.py` and their tests | no importer | `grep -rlE "(from\|import) autoresearch_bridge"` finds only its test |
| graphify residue (X05) | graphify retired | the enum values and capability id have no reader outside the cluster |
| `RESTART_SERVICES.sh:16` retired unit names; `scripts/setup/jetson/zoe-self-review.*` | units that no longer exist | no installer references them |
| retired scheduled jobs (locations held privately) | unused: delete | private review acceptance criteria |
| the 40 research records whose rows say "retire by 2026-11-17" | absorbed | on the date, per `docs/AGENTS.md` |
| `assert ... or True` in `zd/tests/test_zoe_agent_skills.py:188` and `tests/unit/test_samantha_person.py:606` | cannot fail | replace with a real check or delete |

Not deletable, checked: `household_authority.py` (log-only but called, `routers/system.py:2320`); `proactive/selector.py` and
`pull.py` (reachable); `memory_claims.py` and `memory_claims_validate.py` (a staged validator the writer stack will consume,
per the ledger); `calendar_feed.py` (the feed engine PRs build on it); `docs/knowledge/operator-skills/` (the root `AGENTS.md`
names it as the backup of the retired skills); `forget_reach.py` (a ledger run by its test); every router (all mounted).

### 3.2 Deletable at each phase (Phase 6 made concrete)

A copy is deleted only when its verb has run under the kernel for a clean release with no fault fallback, and (for P6) zoe-data
meets the private isolation acceptance criteria. The proof in every case is the WP-75 seam test for that verb plus a grep pin that the function has
no caller.

| phase | delete | condition | proving test |
| --- | --- | --- | --- |
| P3 (list add authority) | duplicate add implementations and policy copies | list add clean a full release | the every-add-path test; the owns-table seam test for `list_items` INSERT |
| 5.1 (list remove) | duplicate remove implementations and policy copies | remove under the kernel a release | one-delete-function and seam checks |
| 5.2 (reminders) | duplicate reminder implementations and policy copies | reminders a release | reminder seam and quiet-hours agreement checks |
| 5.3 (alarms, devices) | the second viewer builder (A01) and re-mask (A02); per-path device checks once WP-67's door decides | alarms a release | seam test on `alarms` writes |
| 5.4 (receipts) | the in-memory store's role as the guard's source (`write_receipts.py` keeps only the publish) | the guard reads durable receipts a clean week | the guard's tests against the durable source |
| 5.5-5.6 (offers, speak gate) | `contact_offer_policy` in-process state; direct notification INSERTs (N10); per-site quiet checks (N04) | one gate a release | a test that every unprompted send passes the gate |
| 5.7 | note, journal, transaction, calendar and people duplicate writers (NJ01-NJ03, CA01-CA02, P01-P02) | each a release | seam tests per table |
| P6 | the 22 visibility copies (L01-L05), reminder read rules, the calendar read checks (CA04), `authority_table.json`, `household_authority.py` | WP-31's function is the only reader and the kernel writes `visibility` | a test forbidding raw visibility filters |

Tracked as one number: duplicate rule copies removed per verb, starting from the map's 74 (scorecard row, I12).


## 4. Definition of done for a kernel work package

A builder ticks every line before asking for review; the reviewer ticks it again and refuses the PR on any blank.

- [ ] Every duplicate at the seam is removed, or has a register row with the phase that removes it.
- [ ] Every bug found at the seam is fixed with a negative control, or has a register row (here or in the ledger).
- [ ] The seam is ONE function, and a test fails on any write to its table from anywhere else (the owns-table test).
- [ ] The kernel call is behind its flag, default off, and the old path is the fallback for faults only (a policy refusal is honoured).
- [ ] The proposal carries the cause class, the turn id and an idempotency key; the fallback carries the same key.
- [ ] Forget reach is proven: member forget, member delete and every entry point that touches the stores this verb writes reach the kernel and fail closed; the byte-canary test passes for the verb's stores.
- [ ] Receipts are durable (in the kernel), carry no words, and back exactly one claim.
- [ ] The kernel stores no free text for this verb (fingerprints only).
- [ ] The voice replay ran with a scratch kernel (off and kernel) for any voice-path change, under the harness lock, and said-vs-did did not regress.
- [ ] The number the package is judged by is named, measured before and after, and written in the PR.
- [ ] Docs are current: the nearest `AGENTS.md`, `forget-reach.md`, the flag registry entry, and this register (rows closed, new rows added).
- [ ] The "what the kernel owns" table is updated in the same change.
- [ ] The PR is under 1,000 changed lines (or stacked), one workstream, cross-reviewed; kernel code has the owner's approval (H4).
- [ ] Nothing in the PR names a household member, place or utterance.

## 5. The counts

Counted from the register above (one row = one finding; rows that list several sites count once).

**By kind**

| kind | high | med | low | rows |
|---|---|---|---|---|
| BUG live bug | 1 | 19 | 9 | 29 |
| DUP duplicate rule | 0 | 22 | 34 | 56 |
| DEAD dead or unreachable | 0 | 2 | 8 | 10 |
| MEM should be durable | 0 | 2 | 12 | 14 |
| FGT missing forget reach | 7 | 14 | 8 | 29 |
| PRIV privacy or safety | 4 | 29 | 12 | 45 |
| DOC stale or contradictory | 0 | 3 | 11 | 14 |
| TEST cannot fail or missing | 1 | 10 | 13 | 24 |
| FLAG config or flag debt | 0 | 1 | 4 | 5 |
| NAME naming or structure | 0 | 26 | 41 | 67 |
| REV deferred review item | 0 | 1 | 4 | 5 |
| VER unproven claim | 0 | 14 | 16 | 30 |
| OWN owner decision to build | 0 | 6 | 14 | 20 |
| **total** | **13** | **149** | **186** | **348** |

**By source** (each row counted once, under the first source that applies): already in the implementation map 173 · from the
architecture study or the prior-art record, not in the map 45 · from the hand-off notes and review threads only 33 · from the
open-problems ledger only 4 · **new from this coverage pass 93**. So 175 rows (50%) were not in the map.

**By status:** 46 rows are in flight on an open PR; 13 are built on a local branch and not pushed; 289 are open; none is done.
Of the 13 high rows, 8 are in flight (#2598 holds five of them), 1 is local, and 4 are open: F01, F02, M10, D01.

The figures are the output of the recount below on this file as committed.

### 5.1 How to recount

`grep -E '^\| [A-Z]+[0-9]+ \|' docs/architecture/zoe-os-clean-house.md | grep -v '^| P6 |' | awk -F'|' '{print $3}' | sort | uniq -c`
gives the kinds; field 4 is the source, 10 the severity, 11 the status. Recount after every package and update this section.

### 5.2 The ten highest-severity rows not yet done, in household words

1. **F01 / F02:** removing a person in Settings does not yet erase their data (high).
2. **M10:** an identity-linking gap in the messaging integration (high).
3. **F08:** member removal does not yet reach all identity data (high).
4. **R01:** accepting a reminder offer can save a reminder that never rings (high).
5. **D01:** physical device control lacks confirmation (high).
6. **L30, R16, NJ05, F10:** forget does not reach every store (high; in flight #2598).
7. **F15:** test can touch real data (high; in flight #2514).
8. **SH06 / SH07:** forget completion and durability need correction (high; in flight #2624).
9. **N11:** a daily nudge can repeat several times in one hour.
10. **L18:** removing a list item ticks it while the reply says it is gone.

Six of these findings (F01, F02, M10, F08, R01, D01) are independent of kernel delivery.

### 5.3 What could not be audited, and why

- This was a reading only. No tests, probes, benchmarks or live-state checks were run. Severity is an editorial judgement.
- Unmerged kernel, deployment, CI and convergence branches were assessed from reports and PR descriptions; their implementation and integration still need verification.
- Panel-side components and independent home-automation behaviour were outside the reading's reach.
- End-to-end integration policy and legacy-lane reachability remain unverified. Security-specific disagreements and locations are held privately.
- Live flags were not read. A default in the register means a code default.
- Job registration and UI-call enumeration can miss dynamic registrations and dynamically assembled calls.
- Brain and core behaviour beyond the enumerated registries was not fully read.
- Voice reflexes, performance work, safety floors and unrelated ledger items remain outside the kernel scope.

## Sources

- Cut-over plan and implementation map: `docs/architecture/zoe-os-cutover-plan.md`, `zoe-os-implementation-map.md` (PR #2636).
- Architecture study: `docs/architecture/zoe-os-architecture.md` (PR #2644).
- Prior art: `docs/research/zoe-os-prior-art-2026-11-15.md` (not yet on a PR; the research index is at its cap, X11).
- Kernel rules draft: PR #2643. The open-PR list of 2026-11-15. The builder hand-off notes and review threads of 2026-11-15
  (private; findings taken, wording not copied).
- `docs/knowledge/open-problems.md`, `docs/knowledge/forget-reach.md`, `services/AGENTS.md`, `services/zoe-data/AGENTS.md`.
