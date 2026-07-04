---
type: executable-plan
audience: all agents + Jason
status: planned
source: senior-dev architecture review, 2026-07-04 (read-only)
verification: every claim below re-checked against the LIVE checkout (/home/zoe/assistant
  @ flue-recall-reliable) + live host, 2026-07-04, via 3 read-only sub-agents. Findings
  that did NOT survive verification are struck and corrected inline.
---
# Tech-debt remediation plan 🔧

Sequenced, small-PR remediation of the 2026-07-04 architecture review, **corrected by
live verification**. Ordered by leverage-per-risk. Each wave = one small mergeable PR
(or a couple). Nothing big-bang.

> ⚠️ **Execution is delicate — triple-check at execution time too.** Several of the
> review's *premises* were wrong (esp. Wave 3 root cause, and which brain is live).
> Verify against the live tree/host again before each PR; do not trust this doc's prose
> as ground truth without re-confirming the cited `file:line`.

**Guardrails:** small PRs + Greptile loop to merge (no `--admin`/`--force`); DOX pass on
every change; **any voice-path-adjacent change replay-gated** against `~/.zoe-voice-samples`
under `flock /tmp/zoe-voice-harness.lock`; new test files must be reachable by CI (Wave 2);
**never `git add docs/archive/`** (see live-issues below).

**Status key:** 📝 planned · 🔨 active · ✅ done · 🗄️ parked

## Delivery status (2026-07-04) — PRs open, verified, NOT merged (await review)
| Item | PR | State |
|---|---|---|
| Graphify tooling retirement (+ host cleanup) | [#1007](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1007) | ✅ delivered |
| Wave 1 PR A — brain docs → 3-way dispatch | [#1008](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1008) | ✅ delivered (rocks block intact, invariants pass) |
| Wave 1 PR B — drift sweep (HW model, agent-zero, CHANGELOG, db archive ptr) | [#1009](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1009) | ✅ delivered |
| Wave 1 PR C — untrack runtime artifacts + tracked pre-commit | [#1010](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1010) | ✅ delivered |
| Wave 2 PR D — CI: de-dup + governance-validator reconciliation | [#1013](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1013) | ✅ partial — silent-skip fix DEFERRED (see below) |
| Wave 2 PR E — reporting-only self-hosted pytest | [#1011](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1011) | ✅ delivered (additive, non-gating) |
| This plan doc | [#1012](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1012) | ✅ delivered |

**Merge notes:** all branches are behind main by #1006 (branched off #1005) → each needs a branch-update before merge (re-triggers Greptile); no revert risk (merge-base diff is clean). No cross-PR file collisions (#1013 *reads* the manifest, #1010 *writes* it — disjoint keys).

**Deferred / needs a decision (NOT auto-shipped — design or live-voice risk):**
- **Wave 2 silent-skip fix** — directory-wide CI selection collects clean (30→3763) but a real run has 22 failures that are NOT marker-gated (incl. a Starlette version mismatch), so it can't be proven CI-green off-box. Follow-up = add opt-in `@pytest.mark.ci_safe` + select `-m ci_safe`.
- **Wave 3 `people_relate`** — advertised across the brain surface but has **no storage backend anywhere** (no tool, no table). Making it work = a schema/memory design decision (person↔person relationships): people-table edge, a relationships table, or a MemPalace relationship memory. NOT a mirror-the-executor fix.
- **Wave 3 Tier 2 (extract shared calendar/list/people services)** — real anti-drift value, but rewrites live-voice-path SQL → must be one-aggregate-per-PR behind the `~/.zoe-voice-samples` replay gate.
- **Wave 3 memory-router graphify** — `MemoryBackend.GRAPHIFY` is the *primary* route for code/graph queries with asserting tests; removing it changes routing (memory workstream).
- **Wave 4** — god-file splits (`voice_tts.py` 4.8k, `chat.py` 4.0k), typed config module, fence the engineering harness out of the prod FastAPI process. Incremental, replay-gated, do-last.

---

## Live brain topology (verified — the basis for PR A)
3-way dispatch, `services/zoe-data/brain_dispatch.py`, priority **flue > core > legacy**.
Code default is `core`; **the live host `.env:262` sets `ZOE_BRAIN_BACKEND=flue`**, so:
- **flue = LIVE.** `zoe_flue_client.py` → Flue Pi-`Agent` sidecar `labs/flue-zoe-brain` on
  `:3578` (systemd user unit `enabled+active`, token auth). Standalone reimpl: persona is a
  *verbatim copy* of `services/zoe-core/SOUL.md`, ability slot-shapes *mirror*
  `services/zoe-core/abilities/*.ts`, and it calls **back** into zoe-data via
  `POST /api/system/intent-dispatch` → `intent_router.execute_intent` (same path as live chat).
- **core = default fallback (dormant).** `zoe_core_client.py` spawns `pi --mode rpc` loading
  `services/zoe-core/extensions/*`. Wired + tested; never invoked while flue is on (no
  `pi --mode rpc` process on the host). **Not retired.**
- **legacy = last fallback.** `zoe_agent.py`, only if `ZOE_USE_CORE_BRAIN` is off.
- All three share the model rock: **Gemma 4 E4B-QAT + MTP, host-native `llama-server` :11434**.

---

## Wave 1 — Docs + hygiene (cheap, high-value, zero runtime risk) 📝

### PR A — Reconcile the brain docs to reality *(review #2, do first)*
The contradiction is real and worse than the review said — **the service README the review
called "authoritative" is itself stale.** Per-doc verdict (verified):
- `docs/architecture/zoe-flue-integration.md` + `docs/PLANS.md` — **ACCURATE** (state flue cut
  over 2026-07-03). Use these as the canonical anchor.
- `docs/CANONICAL.md:47` ("zoe-core = Zoe's brain") — **STALE**: it's the *fallback* now, and
  the line omits flue. Safe to edit (prose, NOT CI-parsed).
- `services/zoe-core/README.md` — **STALE**: claims "lab-only, nothing wired, zoe_agent is prod."
  All three false now.
- `.cursorrules:71/79/274` + `.cursor/rules/project-context.mdc:8` ("RETIRED, do not extend") —
  **CONTRADICTORY/dangerous**: tells agents to ignore the wired fallback brain. Must distinguish
  the *truly-retired legacy Docker/RouteLLM monolith* from *current Pi-agent zoe-core*.
- root `README.md:34` ("archived under docs/archive/") — **WRONG**.
- `labs/flue-zoe-brain/README.md:1` ("LAB ONLY / Not wired into production") + `labs/AGENTS.md`
  "default-OFF seam / never live" framing — **STALE**: live on this host. But keep the honest
  nuance: *shipped repo default is OFF; live-ON on this deployment since 2026-07-03* — do NOT
  over-correct the systemd unit templates in `scripts/setup/systemd/` (those correctly describe
  the shipped default).
- **Do:** write ONE canonical 3-way-dispatch description (anchor in `CANONICAL.md`), point every
  doc above at it, stop the contradictions.
- **Risk:** none (docs). **Do NOT touch** `CANONICAL.md`'s ```yaml rocks:``` block
  (`test_canonical_invariants.py` parses it). **Gate:** `validate_structure.py`.

### PR B — Drift sweep *(review #5 — all confirmed; removal caveats noted)*
- `HARDWARE_COMPATIBILITY.md:19,188` — llama-3.2-3b listed as Primary; fix content to Gemma 4.
  **Do NOT delete the file** — `tools/audit/enforce_structure.py:83` requires its presence.
- graphify **tooling** — ✅ **DONE via PR #1007** (`fix/retire-graphify`): deleted 6 probe/shard
  scripts + 5 tests + refresh-evidence doc + `.graphifyignore`; updated `scripts/AGENTS.md`
  contract + `.zoe/manifest` + cursor rule; host orphan `zoe-graphify-refresh.service` + 99 MB
  stale `graphify-out/` removed. **Follow-up (NOT done, separate/behavioral):** graphify is still
  woven into **live memory code** — `MemoryBackend.GRAPHIFY` is the *primary* route for code/graph
  queries (`zoe_memory_router.py:190`, with tests asserting it), plus `TRACE_SURFACES`,
  `zoe_capability_profile.py`, and the `mempalace_baseline` case. Removing those changes memory
  routing + the test contract → belongs to the memory workstream, done deliberately.
- `skills/agent-zero-research/` — **safe to remove**: the live skill router
  (`skill_discovery.py:26-27`) scans only `~/.openclaw/.../skills` + `~/.hermes/skills`, NOT the
  repo `skills/` dir, and no `agent-zero` endpoint exists. (Review's "router still routes to it"
  is FALSE — it's simply orphaned.)
- `database.py` archive pointer + `CHANGELOG.md` (Nov 2025) — confirm/refresh.

### PR C — Untrack committed runtime artifacts + tracked pre-commit *(review nits — verified)*
Untrack (none read by live code):
- `homeassistant/.ha_run.lock` — **SAFE**.
- `data/skills.lock` — **SAFE** (only reader is dead retired-zoe-core in worktrees).
- `data/music-assistant/*.log.1` — **SAFE**; contains LAN IPs + a "generated JWT secret" *log
  line* (no actual secret value) — mild topology leak, worth untracking.
- `data/autoresearch/jun11-*` — **VERIFY FIRST**: the autoresearch *feature is live*
  (`main.py:1849`) but reads runtime run-dirs, not these dated samples. Confirm no fixture points
  at `data/autoresearch/jun11-*` before untracking.
- Add tracked `.pre-commit-config.yaml`. Duplicate cleanup-script families
  (`scripts/maintenance/` vs `tools/cleanup/`, two `comprehensive_cleanup.py`) — nothing runs
  them; flag for later consolidation, not this PR.

## Wave 2 — CI gate (structural) 📝

### PR D — Marker-based test selection *(confirmed — with a real trap)*
`validate.yml:94-134` enumerates **31 unique** test files (35 refs, **4 dups**:
`test_pipeline_store`/`test_pipeline_evidence`/`test_multica_poll_dispatch`/`test_main_multica_poll`
at :108-118) of **334** total → new tests silently never run. Also collapse the 3 drifting
governance validators to one source: `.zoe/manifest.json:149` (12) vs `validate_structure.py:146`
(hardcoded 10) vs `validate_critical_files.py:58-68` (own critical-file list missing
`music.html`/`people.html` that manifest lists) — latent, not currently CI-breaking.
> ⚠️ **THE TRAP:** a broad `pytest` *collects* fine (no heavy top-level imports — torch/onnx/
> moonshine/etc. are all mock/string refs), but ~25 tests **fail, not skip**, without the host
> stack: `tests/e2e/*` (hit `localhost:8000`), `tests/integration/*` (23 files),
> `test_pi_*_http_probe.py`, `test_zoe_agent_skills.py`. Only `samantha_live/` self-skips. So use
> an **opt-in `@pytest.mark.ci_safe` marker** (or an explicit exclude list), NOT a blanket
> `testpaths` widening. Root `tests/` = **53** files invisible to bare `pytest` (`pytest.ini:11`).

### PR E — Post-deploy self-hosted pytest job *(confirmed gap)*
`deploy.yml:14` is self-hosted but does **only** health curls (`:84-168`), never pytest — so
chat/voice/memory integration tests (the ones that can *only* run on the Jetson) never gate. Add
a post-merge self-hosted pytest job scoped to the host-only suites excluded from PR D.

## Wave 3 — Fix the write-path bug class, THEN dedup *(review #1 — root cause CORRECTED)*
**The review's premise was wrong.** Writes *are* triplicated (up to 4 copies/aggregate, already
drifted — people written with 11/11/16 columns), but that is **not** what causes the `ok:false`
bugs. The entry points **already share one funnel** (`intent-dispatch` → `execute_intent`); only
the *fulfillment bodies* are copied. The bug class (#960/#993/#995) is a **broken out-of-process
`mcporter` fallback**: a dispatchable write with no in-process direct executor spawns a second
`mcp_server.py` that dies on DB-pool init → returns `None` → mapped to `ok:false` while the user
hears "done." So extract-a-service does **not** close the bug class on its own. Re-split:

- **Tier 1 — the actual fix (lower risk, closes the class):** stop depending on the dead mcporter
  fallback for dispatchable writes — add the missing in-process direct executors and/or make
  `execute_intent` fail loudly instead of spawning the broken subprocess. **Known live gap:**
  `people_relate` (`intent_router.py:3905`) is a dispatchable write with no direct executor →
  returns `ok:false` today. *(also a live-issue below)*
- **Tier 2 — the dedup the review described (cleanup, not bug fix):** extract one
  `<aggregate>_service.py` (calendar/list/people; `reminder_service.py` already exists as the
  precedent — though `mcp_server.reminder_create:1577` still bypasses it, a latent drift) and
  route the direct executor + MCP tool + router through it. Bill honestly as anti-drift.
- **Change surface + risks** (all live voice+chat write path, **replay-gate mandatory**):
  divergent SQL is sometimes intentional (people router writes 16 cols + `preferences`/`is_partial`
  + explicit `commit()`); differing transaction atomicity (list add wraps in `db.transaction()`,
  MCP tool doesn't → orphaned-empty-list risk); timezone normalization differences (calendar
  requires resolvable date; reminders convert AWST→UTC downstream — don't double-convert);
  `user_id` binding (MCP enforces admin-only override, in-process path trusts identity-bound
  caller — keep both); per-aggregate MemPalace mirror + UI-notify channel differences. One
  aggregate per PR, calendar first.

## Wave 4 — Opportunistic, larger, do last 📝
- **God-file splits:** `voice_tts.py` (4812) → router/session/STT; `chat.py` (4035) →
  SSE-protocol/routing-policy. Incremental, replay-gated. Note `chat.py:629` carries a duplicate
  `use_flue_brain()`.
- **Typed config module:** verified **533** `os.getenv/environ` reads across **112** files (worse
  than the review's ~400/~110); rocks test guards only ~3. Extend `runtime_env.py`/`gemma_endpoint.py`.
- **Fence the engineering harness:** `greploop_guard.py`, `pipeline_store.py`,
  `executors/kanban_adapter.py`, Multica poll loop run inside the prod chat/voice FastAPI process.
  Separate process or walled subpackage before the Flue-recreation phase grows it.

## Live issues found during verification (separate from the plan — surface to Jason)
1. **`people_relate` silently fails on the live Flue path today** — dispatchable write, no direct
   executor → `ok:false`. Small standalone fix (Wave 3 Tier 1, first target).
2. **`docs/archive/` exists on disk with 241 UNTRACKED files.**
   `test_canonical_invariants.py::test_no_docs_archive_graveyard` asserts it's gone — CI passes
   only because they're untracked (clean checkout); it likely fails locally. Any branch that
   `git add`s them hard-fails CI + reintroduces the graveyard CANONICAL forbids. Clean off disk.
3. ~~**Orphaned `zoe-graphify-refresh.service`** on host with a missing ExecStart script.~~
   ✅ **RESOLVED 2026-07-04** — host unit + stale `graphify-out/` removed (with PR #1007).

## Not in scope (already tracked)
UI consolidation → owned by the "Touch / Chat UI" Active plan.

## Parallelization
Waves 1 and 2 are file-disjoint → safe to fan out to sub-agents in own worktrees. Wave 3 stays
serial, hands-on, replay-gated.
