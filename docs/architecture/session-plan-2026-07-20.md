# Session plan & handoff — 2026-07-20

> Everything found, decided, fixed and left open in one place, so the next
> session starts from evidence rather than re-deriving it. Work items are
> ordered by what unblocks what.

---

## 0. The one lesson worth carrying

**Every error this session came from trusting a description instead of checking
execution.** Skills that looked live and had never run. A service documented as
paused that was active. A harness read as failing that had succeeded. A caller
count repeated from a review comment instead of counted. A fix whose premise
collapsed under its own test.

The two bugs that hid longest were both **a swallowed exception written to a dead
logger**. Verify execution, not presence.

---

## 1. Decisions of record

| Decision | Basis |
|---|---|
| **KEEP Multica** — it is valuable *software*, not valuable *data* | Third-party product running on Zoe with its own DB; gives the human steering UI, a working integration, and agent-native primitives. Jason: the issues themselves "could all be erased". Scope: [`multica-executor-migration.md`](multica-executor-migration.md) (lands with #1484) |
| **KEEP the two-stage router; lock architecture, not checkpoint** | FunctionGemma-270M returns `shopping_list_add` at 0.9996 where the 4B brain is ~14% wrong. Self-train ratchet owns the weights. Locked in `CANONICAL.md` + `test_canonical_invariants.py` (PR #1479) |
| **Do NOT retire Multica/the harness** | It reached true 100% hands-off idea→merged-PR autonomy 2026-06-17 (ZOE-5834 → PR #682, `merge_sha 2d3edaa9`). It is PAUSED, not broken |
| **Retire the 101 skills; keep the runtime** | Skills never executed (two independent methods). The runtime couplings are separate and gated |

---

## 2. Merged this session (7)

`#1461` skill backups + 3 doc drifts · `#1468` **application logging restored** ·
`#1471` `skill_discovery` deleted (dead, 0 consumers) · `#1473` `zoe_repo_root`
freed from `hermes_http` · `#1475` plan stop-blocks (prevented the plan
instructing deletion of a working harness) · `#1478` stochastic-test docs ·
`#1479` router locked as an improvable rock

---

## 3. Open work, in dependency order

### 3.1 Finish the two open fix PRs  ← START HERE

**PR #1480 — nightly digest.** *The fix as written does not work.* Greptile
caught it: the discovery query was widened to a 30h rolling window, but
`_load_todays_messages` (~`memory_digest.py:710`) still carries the calendar-day
clause, so the job selects a user and then extracts nothing.

- [ ] Apply the same rolling clause in `_load_todays_messages`;
      `params = (user_id, _DIGEST_LOOKBACK_HOURS)`
- [ ] Replace bare `int()` on `ZOE_MEMORY_DIGEST_LOOKBACK_HOURS` with a validated
      parser: a typo (`"30h"`) currently raises at import and takes the module
      down when the scheduled loop reaches it; `0` silently recreates the empty
      window. Floor at **27h** (a 03:00 run needs the whole previous day)
- [ ] Add a test asserting the EXTRACTION path's SQL, not just discovery — the
      existing tests all passed with this bug present

**PR #1482 — scheduler closures.** Greptile: `create_subprocess_exec` runs on the
main uvicorn event loop and can freeze the API now that the job actually
registers.

- [ ] Use the off-loop subprocess helper the selftrain job already uses

### 3.2 Deploy  ← operator

Live checkout is now on `main`, **0 commits behind** (restored this session).
Nothing is live until `systemctl --user restart zoe-data`. Until then:
`music_discovery_weekly` stays unregistered and the logging fix is inert.

**Caution:** restart under memory pressure has hung >90s before
(`incident-runbook.md`). Headroom was 359Mi at session end.

### 3.3 Voice replay gate — currently RED

`/home/zoe/.cache/zoe/voice_regression_last.json`: `status: fail`,
`OK rate 0.850 vs baseline 0.950`, 3 of 20 EMPTY.

**Probably corpus contamination, not a regression** — another agent quarantined
5 TV false-wake samples (`~/.zoe-voice-samples/quarantine-tv-falsewakes-20260719/`,
corpus now 916) and is waiting on 2GB to re-run. Confirm with one clean run under
`flock /tmp/zoe-voice-harness.lock` before treating it as a bug.

### 3.4 Multica → Pi/Flue executor migration

Full scope in [`multica-executor-migration.md`](multica-executor-migration.md)
**(lands with PR #1484 — if that link 404s, #1484 has not merged yet).**
Phase 1 (the Zoe-native executor) is the only hard part and is deliberately
uncosted — three unknowns must be answered first. **Do not rebuild
`kanban_adapter`**: it encodes twelve PRs of discovered failure modes.

### 3.5 Smaller, unblocked

- [ ] **Pin the Multica image digests.** Both containers run `:latest` — a bad
      upstream push lands silently on the next pull
- [ ] **`expert_dispatch.py:666`** bypasses `brain_dispatch`, so expert turns
      ignore `ZOE_BRAIN_BACKEND=flue`. **Naive reroute is unsafe** — `zoe_flue_client`
      explicitly ignores `db_memory_context`, which `expert_dispatch` assembles.
      Order: teach the Flue client to honour a supplied packet, *then* reroute.
      Voice path ⇒ replay-gated
- [ ] **Hermes primary Codex auth is failing**, silently degrading to
      `openrouter/free` on every turn
- [ ] **`zoe-voice-regression.service` is in `failed` state** — boot race, it
      starts before Postgres accepts connections. Add a docker/Postgres ordering
      dependency. Modest impact, but a permanently-red unit masks real failures
- [ ] **`panel_presence_events`** has no writer anywhere and 0 rows; a daily
      purge timer runs against it. Cosmetic dead weight

### 3.6 Known-good, do not "fix"

- `ZOE_ROUTER_SELFTRAIN` unset ⇒ job correctly absent. **Deliberately off**
- `ZOE_YTMUSIC_REFRESH_ENABLED` unset ⇒ correctly absent
- Multica dispatch paused via `~/.zoe/multica_dispatch_paused` — **deliberate**
- `memory_consolidation` — healthy (23–24 users, 585/1078 merges). The working
  control that made the digest diagnosis possible

---

## 4. Regressions found and fixed

| Bug | How it hid |
|---|---|
| **Root logger had no handler** — every `logger.info()` in zoe-data discarded, WARNING+ unformatted and undatable | Silent by construction. Fixed #1468 |
| **Nightly digest processed 0 users for 10 nights** — asked for calendar-"today" at 03:00 | Logged `nightly run complete` every night. #1480, **incomplete** |
| **`music_discovery_weekly` never registered once** — `add_job` cannot pickle a closure nested in `lifespan` | Exception swallowed into the dead logger. #1482 |
| **`router_selftrain_weekly` identical latent defect** | Looked like the flag being off. Would fail on first enable. #1482 |

---

## 5. Open questions for the operator

1. **Self-evolution target model size** — LoRA on ≤8B points at a used 3090;
   only 27B+ makes a case for DGX Spark. This single answer picks the hardware.
2. **OpenClaw config** — ~3 lines (`agents.list[0].workspace`,
   `skills.load.allowSymlinkTargets`, restore `zoe-verify`) would light up 31
   skills that have never run. Behaviour change on a live system.
3. **Phase 1 substrate** — Flue workflow, or a plain Python loop reusing
   `worktree_bootstrap`? Flue is strategic; Python is faster to prove.
