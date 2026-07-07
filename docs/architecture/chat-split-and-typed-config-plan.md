---
type: executable-plan
audience: all agents (cold-startable by Sonnet/Codex) + Jason
status: planned
parent: tech-debt-remediation-plan.md (Wave 4)
verification: every symbol/line below re-checked against this checkout 2026-07-06;
  line numbers WILL drift — re-verify each cited symbol with grep before editing.
---
# Wave 4 packet — `chat.py` split (SSE-protocol vs routing-policy) + typed config

Execution packet for two Wave 4 items from
[`tech-debt-remediation-plan.md`](tech-debt-remediation-plan.md):

1. **`services/zoe-data/routers/chat.py` (4035 lines) → SSE-protocol helper modules + routing
   policy left in the one router** — including killing the duplicate `use_flue_brain()` at
   `chat.py:621` as step 1.
2. **Typed config module** — extend the `runtime_env.py`/`gemma_endpoint.py` pattern with a small
   typed env-read helper; migrate call sites incrementally by module, highest count first.

Each PR below is small, independently mergeable, and drives through the Greptile loop
(reply + **resolve** every thread via GraphQL `resolveReviewThread`; squash; never
`--admin`/`--force`). Merging ≠ live: deploy is an operator
`systemctl --user restart zoe-data.service` from the live checkout.

## Binding contracts (read before executing)

- **Exactly ONE production chat router** — `routers/chat.py`
  (`services/AGENTS.md`, `services/zoe-data/routers/AGENTS.md`). The split extracts **helper
  modules that chat.py imports**; never a `chat_v2.py`/parallel router. Helpers live at
  **`services/zoe-data/` top level** (siblings of `ag_ui_stream.py`), NOT under `routers/`.
- **Precedent to mirror:** the `voice_tts.py` split (`tts_waterfall.py` + `stt_wake_strip.py`) —
  the router **re-exports** the moved symbols so existing importers and monkeypatching tests keep
  targeting the router module (`services/zoe-data/AGENTS.md`).
- **Monkeypatch-seam rule (load-bearing):** tests patch attributes ON the chat module
  (`monkeypatch.setattr(chat_router, "_iter_hermes_stream_events", ...)` etc. in
  `tests/test_hermes_routing.py`, `test_chat_stream_lifecycle.py`). Therefore:
  (a) chat.py imports moved names INTO its namespace (`from chat_hermes_stream import x`);
  (b) call sites inside chat.py keep referencing the **bare module-global name** — never convert
  to `module.attr` calls; (c) a moved function must not call BACK into a chat-module seam —
  move whole cohesive clusters, and keep heavily-patched seams in chat.py (list per PR below).
- **`/api/chat` is voice-path-adjacent**: voice clients hit it with `X-Voice-Mode`
  (`chat.py:3413`), and the non-stream path runs `_brain_oneshot(..., voice_mode=is_voice_mode)`
  (`chat.py:3661`). Every PR here takes the **replay gate** (below), not just the chat tests.
- **Rocks fixed:** Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro. Nothing here touches model
  choice; `GEMMA_SERVER_URL` handling stays owned by `gemma_endpoint.gemma_base()`.
- Retire by **removing** (git keeps history) — dead code found below is deleted, not archived.

## Per-PR gate (every PR in this packet)

1. Focused tests on the self-hosted Jetson (these import service modules — GitHub-hosted
   runners can't run them): `cd services/zoe-data && python3 -m pytest tests/<files for the PR> -q`.
2. New test files: slim-dep-green tests get `pytestmark = pytest.mark.ci_safe` (validate.yml
   selects by marker now — no YAML enumeration needed; the self-hosted lane runs the full dir).
3. Live smoke after operator restart (`systemctl --user restart zoe-data.service`):
   - `curl -s http://localhost:8000/health`
   - SSE: `curl -sN -X POST http://localhost:8000/api/chat/ -H 'Content-Type: application/json' -d '{"message":"what time is it","session_id":"smoke_w4"}' | head -40`
     → expect `RUN_STARTED` … `TEXT_MESSAGE_*` … `RUN_FINISHED` frames.
   - Non-stream: same POST to `/api/chat/?stream=false` → JSON with a `response`.
4. **Replay gate (MANDATORY, all PRs here):**
   `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py`
   against `~/.zoe-voice-samples` — said-vs-did must not regress; per-stage speed must not drift
   vs baseline. (Harness numbers are relative; see `docs/knowledge/voice-pipeline.md`.)
5. DOX pass: update `services/zoe-data/AGENTS.md` (module ownership + re-export contract) and
   `services/zoe-data/routers/AGENTS.md` when a helper module is added.

---

# Part 1 — `chat.py` split

## Interpretation (so nobody builds the wrong thing)

"SSE-protocol vs routing-policy" means: **protocol mechanics move out; policy stays in the one
router.** `chat.py` keeps the endpoints, `chat_stream_generator`'s lane ordering (approval gate →
hermes-forced lane → Tier-1.5 `fast_tiers.resolve` → Pi-hybrid → Tier-0 intent → local-brain →
OpenClaw fallback), and the policy tables (`_FORM_INTENTS`, `_OPENCLAW_DELEGATION_INTENTS`,
`_MULTICA_BOARD_INTENTS`). What moves out is: (a) brain-lane **selection**, which already has a
single source of truth in `brain_dispatch.py` — chat.py's private copy is deleted; (b) AG-UI/SSE
**event adaptation** mechanics; (c) the Hermes **provider-call/stream** mechanics.

Verified duplicate (the plan's `chat.py:629` note — the def is at 621, body line 629):
`routers/chat.py:621 _use_flue_brain()` duplicates `brain_dispatch.py:36 use_flue_brain()`;
`chat.py:632 _brain_streaming` / `chat.py:648 _brain_oneshot` duplicate
`brain_dispatch.py:48 brain_streaming` / `brain_dispatch.py:63 brain_oneshot`. The
`brain_dispatch.py` module docstring (lines 6–8) even records the duplication ("chat.py keeps its
own equivalent helpers"). All voice paths (`routers/voice_tts.py:3391`,
`routers/voice_livekit.py:515/575/1174`, `main.py:2589/2645`) already use `brain_dispatch`.

## PR W4-C1 — kill the duplicate brain dispatch (step 1)

**Edit `services/zoe-data/routers/chat.py`:**
- Delete `_use_flue_brain` (def at :621), `_brain_streaming` (:632), `_brain_oneshot` (:648).
- Add near the other top imports:
  `from brain_dispatch import use_flue_brain as _use_flue_brain, brain_streaming as _brain_streaming, brain_oneshot as _brain_oneshot`
  (alias form keeps every internal call site and every test target name unchanged).
- Remove now-unused imports: the whole `from zoe_core_client import run_zoe_core, run_zoe_core_streaming`
  (:343) and the `run_zoe_agent, run_zoe_agent_streaming` names from the `zoe_agent` import
  (:339) — KEEP `_mempalace_load_user_facts, _mempalace_add, _fire_memory_capture,
  _build_memory_context` from that import. Verify with grep that no other use of the four
  removed names remains in chat.py before deleting.
- `_USE_ZOE_CORE` (:497) **stays a module constant** — it feeds the lane-entry gate
  `_USE_LOCAL_BRAIN` (:500), which is checked at :2467, :2546 and :3657 and is monkeypatched as a
  constant by `test_hermes_routing.py:383-384` and `test_chat_stream_lifecycle.py:62/124/185`, so
  neither may become a function. To keep the lane-entry gate and the dispatch functions agreeing
  on the SAME parse, redefine it as an import-time snapshot of the canonical parser:
  `_USE_ZOE_CORE = use_core_brain()` (add `use_core_brain` to the `brain_dispatch` import).

**Edit `services/zoe-data/brain_dispatch.py`:** update the docstring (lines 5–8) — chat.py now
consumes this module; the "keeps its own equivalent helpers" sentence is stale after this PR.

**Declared behavior deltas (put verbatim in the PR body — there are TWO):**
1. Dispatch timing: chat's old `_brain_streaming`/`_brain_oneshot` chose core-vs-legacy via the
   **import-time** constant `_USE_ZOE_CORE`; `brain_dispatch.use_core_brain()` reads
   `ZOE_USE_CORE_BRAIN` at **call time**. Call-time is what every voice path already does and
   honors the `runtime_env` bootstrap.
2. Parse: both the dispatch functions AND `_USE_ZOE_CORE`/`_USE_LOCAL_BRAIN` now use
   `use_core_brain()`'s parse (only `{"0","false","no","off"}` → false) instead of
   `.lower() == "true"` — so a value like `"1"`/`"yes"` no longer disables the brain lane in chat
   while enabling it in voice. Lane gate and dispatch cannot disagree on the parse; the only
   residual divergence is import-time snapshot (gate) vs call-time re-read (dispatch), which
   matters only if the env changes mid-process — same as today across chat-vs-voice.

With the env unset/`true` (the default and the live value) all of this is byte-identical.
These are the ONLY intended deltas.

**Tests (existing, must stay green):** `test_brain_flue_backend.py` (asserts
`chat._use_flue_brain()` toggles by env, :234–238), `test_brain_dispatch.py`,
`test_chat_stream_lifecycle.py` (monkeypatches `chat_router._brain_streaming` — still works: the
generator resolves the module global), `test_hermes_routing.py`, `test_ag_ui_chat_contract.py`,
`test_chat_persistence_contract.py`. Then the common gate incl. replay.

## PR W4-C2 — extract SSE/AG-UI protocol mechanics → `services/zoe-data/chat_stream_protocol.py`

New module (top-level, NOT in `routers/`): chat-router AG-UI/SSE **adapters** — pure
"turn X into wire events" mechanics with no lane decisions. **Move** (cut from chat.py, paste,
fix imports; symbol-for-symbol, no rewrites):

| Symbol (def at, 2026-07-06) | What it is |
|---|---|
| `brain_tool_sentinel_events` (:508) | brain `__TOOL__:` sentinel → AG-UI tool/step events |
| `brain_tool_card_events` (:577) | sentinel → `zoe.ui_component` card events (lazy `skybridge_service` import comes along) |
| `_iter_openclaw_heartbeats` (:1429) | 4s heartbeat run_log/STATE_SNAPSHOT while an agent task runs |
| `_cancel_if_pending` (:1462) | cancel orphaned agent task on SSE disconnect |
| `_BUILDER_INTENTS` (:1515) | frozenset used only by the builder-reply cluster below |
| `_detect_preview_urls` (:1600) | preview-URL sniffing for builder replies |
| `_synthesize_builder_actions` (:1610) | synthesize navigate/orb_prompt UI actions |
| `_sanitize_builder_reply` (:1652) | strip fenced code from builder bubbles |
| `_extract_ui_actions` (:1690) | parse `:::zoe-ui:::` blocks out of reply text |
| `_map_ui_payload_to_action` (:1703) | UI payload → action dict |
| `_queue_ui_actions_background` (:1721) | queue parsed actions via `ui_orchestrator.enqueue_ui_action` |
| `_stream_openclaw_assistant_ag` (:1805) | assistant reply → TEXT_MESSAGE_* + zoe.ui_* event stream |

**Keep in chat.py (do NOT move — heavily monkeypatched seams / router state):**
`_record_run_state` (:1778), `_persist_ag_ui_run` (:1157), `_get_session_lock` +
`_SESSION_LOCKS`/`_SESSION_LOCK_TIMEOUT_S` (:1148–1155), `_extract_approval_token` (:1681,
tested by `test_risk_policy.py`), `chat_stream_generator`, all `_FORM_*`/`_MULTICA_*` policy
tables, `_check_frustration` cluster.

**Shim in chat.py:** one import block
`from chat_stream_protocol import (brain_tool_sentinel_events, brain_tool_card_events, _iter_openclaw_heartbeats, _cancel_if_pending, _BUILDER_INTENTS, _detect_preview_urls, _synthesize_builder_actions, _sanitize_builder_reply, _extract_ui_actions, _map_ui_payload_to_action, _queue_ui_actions_background, _stream_openclaw_assistant_ag)`
— existing importers (`tests/test_brain_tool_cards.py`, `tests/test_ag_ui_chat_contract.py`
import these **from `routers.chat`**) keep working unchanged. Note in the module docstring +
`AGENTS.md` that `routers/chat.py` re-exports these (the `voice_tts` contract, applied to chat).

**Intra-cluster check (why this set is safe):** `_stream_openclaw_assistant_ag` calls
`_extract_ui_actions`/`_synthesize_builder_actions`/`_sanitize_builder_reply`/`_BUILDER_INTENTS`
— all move together, so intra-module resolution is fine; no moved symbol calls a kept seam.
Its other deps (`iter_openclaw_text_chunks`, `AgRunRecorder`, `EventEncoder`,
`auto_extract_components`, ag_ui event classes) are already ordinary imports.

**Tests:** existing `test_brain_tool_cards.py`, `test_ag_ui_chat_contract.py`,
`test_chat_stream_lifecycle.py`, `test_hermes_routing.py`, `test_intent_router_safety.py`
(imports `chat_inject_background`, `run_openclaw_agent` — both stay in chat.py). Add ONE new
test `tests/test_chat_stream_protocol_shim.py` asserting the re-exports: `from routers import
chat` and `import chat_stream_protocol`; assert the twelve names above are the same objects in
both namespaces. **Do NOT mark it `ci_safe`** — it imports `routers.chat` (heavy service
imports), which per `tests/AGENTS.md` runs ONLY on the self-hosted Jetson lane; the full-dir
self-hosted job picks it up automatically, no marker or YAML edit needed. Then the common gate
incl. replay.

## PR W4-C3 — extract Hermes provider mechanics → `services/zoe-data/chat_hermes_stream.py` + delete dead code

**Delete (dead — verified 0 callers repo-wide 2026-07-06; RE-VERIFY with
`grep -rn "_hermes_stream_generator\|_iter_hermes_text_chunks" --include="*.py" .` before
deleting):** `_iter_hermes_text_chunks` (:3240) and `_hermes_stream_generator` (:3310). Retire by
removing — no `_old` copies.

**Move to the new module** (Hermes HTTP call + SSE/progress adaptation — provider mechanics):

| Symbol (def at) | Notes |
|---|---|
| `_HERMES_API_URL` (:3042), `_HERMES_MODEL` (:3043), `_HERMES_API_KEY` (:3044), `_ZOE_SOUL_HERMES` (:3050) | module constants — deliberately NOT re-exported; verified 2026-07-06 their only readers are inside the moved/deleted functions (:3067, :3079, :3139–3140, :3171, :3227, :3300) and no other repo module imports them. **Pre-move checklist:** re-run `grep -rn "_HERMES_API_URL\|_HERMES_MODEL\|_HERMES_API_KEY\|_ZOE_SOUL_HERMES" services/ --include="*.py"` and confirm every hit is inside the move/delete set before cutting — any surviving chat.py reader would be a `NameError` |
| `_build_hermes_payload` (:3058) | calls `_load_zoe_self_compact_for_chat` — moves with it |
| `_hermes_progress_message` (:3087), `_hermes_progress_events` (:3109) | progress event mapping |
| `_hermes_request_headers` (:3137) | auth headers |
| `_iter_hermes_stream_events` (:3146) | the SSE iterator chat's lanes consume |
| `_load_zoe_self_compact_for_chat` (:3261) | file read, only used by `_build_hermes_payload` |
| `_hermes_completion` (:3280) | non-stream completion |

**Keep in chat.py:** `_safe_load_portrait` (:3271) — memory helper, monkeypatched by
`test_hermes_routing.py:168` and imported by `routers/system.py:2467`; it is not Hermes-specific.

**Shims in chat.py:** `from chat_hermes_stream import (_build_hermes_payload, _hermes_progress_message, _hermes_progress_events, _hermes_request_headers, _iter_hermes_stream_events, _hermes_completion)`.
This preserves: `routers/system.py:2467` (`from routers.chat import _hermes_completion, ...`),
and `test_hermes_routing.py`'s `monkeypatch.setattr(chat_router, "_iter_hermes_stream_events", ...)`
(chat's lanes call the bare name at :2028 and :2768 — keep them bare). No moved function calls a
kept seam (`_hermes_completion` → `_build_hermes_payload`/`_hermes_request_headers`, intra-module).

**Consider (allowed, not required):** `hermes_http.py` already exists ("shared Hermes helpers");
if Greptile suggests folding, prefer keeping `chat_hermes_stream.py` separate — `hermes_http.py`
is CLI/gateway plumbing shared by non-chat callers, this module is the chat-turn protocol client.

**Tests:** `test_hermes_routing.py` (the main coverage), `test_chat_stream_lifecycle.py`, plus a
re-export assertion added to the shim test from W4-C2. Then the common gate incl. replay.

## PR W4-C4 (optional — only after C1–C3 are merged clean) — intent card/form payload builders

If further shrink is wanted: move the card/form **payload transform** cluster to
`services/zoe-data/chat_intent_cards.py`: `_normalized_list_items` (:79), `_intent_card_data`
(:98), `_intent_action_form_payload` (:193), `_broadcast_intent_nav` (:252), `_INTENT_PANEL_NAV`
(:54), `_ACTION_FORM_INTENTS` (:72), `_build_calendar_form_props`…`_build_timer_form_props`
(:416–470), `_FORM_COMPONENT_MAP` (:471), `_FORM_BLURB` (:486), `_build_panel_intent_card`
(:901). Same shim rules; covering tests `test_chat_calendar_cards.py`,
`test_chat_shopping_cards.py`, `test_chat_pi_hybrid_action_form.py` import from `routers.chat`.
The `_FORM_INTENTS`/`_OPENCLAW_DELEGATION_INTENTS`/`_MULTICA_BOARD_INTENTS` **policy** frozensets
stay in chat.py. Park this PR if C1–C3 produced any replay drift.

**End state:** chat.py ≈ 4035 → ~3350 after C1–C3 (~690 lines moved/deleted), ~2950 with C4 —
still the single router, now importing protocol mechanics the way `voice_tts.py` imports
`tts_waterfall`.

---

# Part 2 — Typed config module

## Measured baseline (2026-07-06, this checkout)

Command:
`grep -rlE "os\.(getenv|environ\.get|environ\[)" --include="*.py" services scripts tools modules | grep -vE "/tests/|/test_"`
→ **138 non-test files, 629 raw env reads** (the plan doc's 533/112 from 2026-07-04 was the same
order of magnitude with a narrower regex). Top runtime modules in `services/zoe-data`:

| Reads | Module | Note |
|---|---|---|
| 38 | `routers/voice_tts.py` | replay-gated (voice path) |
| 31 | `zoe_agent.py` | legacy-fallback brain, still wired |
| 24 | `greploop_guard.py` | **EXCLUDED** — Wave 4 fences the harness out of the prod process; don't migrate |
| 23 | `mcp_server.py` | |
| 23 | `main.py` | |
| 19 | `routers/chat.py` | shrinks anyway via Part 1 |

## The real bug class this fixes (verified example)

The same flag is parsed two incompatible ways today: `chat.py:497` reads `ZOE_USE_CORE_BRAIN`
as `.lower() == "true"` (so `"1"`/`"yes"` → **False**) while `brain_dispatch.py:28` reads the
SAME variable as `not in {"0","false","no","off"}` (so `"1"`/`"yes"` → **True**). A third style
exists at `chat.py:517` (`in ("1","true","yes","on")`). One typed parser ends this divergence.

## Design — `services/zoe-data/typed_env.py` (new, ~60 lines, stdlib-only)

Extends the existing pattern (`runtime_env.py` = bootstrap `.env` → `os.environ`;
`gemma_endpoint.py` = one lazy, normalized accessor). **NOT** pydantic-settings, **NOT** a
Settings singleton, **NOT** an import-time snapshot:

```python
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY  = frozenset({"0", "false", "no", "off"})

def env_str(key: str, default: str = "") -> str: ...          # strip()ed
def env_bool(key: str, default: bool = False) -> bool: ...    # canonical sets; PRESENT-BUT-EMPTY -> False (the live .env uses KEY= as "cleared", and every legacy truthy-set parse yields False for "" — returning the default would silently flip default-true flags during migration); unrecognized non-empty value -> default + one warning log
def env_int(key: str, default: int) -> int: ...               # invalid -> default + one warning log
def env_float(key: str, default: float) -> float: ...
def env_list(key: str, default: tuple[str, ...] = (), sep: str = ",") -> tuple[str, ...]: ...
```

Rules:
- Every accessor reads `os.environ` **at call time** (tests monkeypatch env; `runtime_env`
  bootstraps after import). Callers that want an import-time constant keep writing
  `X = env_bool("FLAG", default=True)` at module top — **read-time semantics remain the call
  site's choice**, so each migration is mechanical.
- `gemma_endpoint.gemma_base()` remains the sole `GEMMA_SERVER_URL` accessor (the `/v1/v1` 404
  trap); `typed_env` does not grow URL logic.
- No registry/schema of all keys. Just parsers.

## PR sequence

**PR W4-T1 — add the module + tests, zero call-site changes.**
`services/zoe-data/typed_env.py` + `tests/test_typed_env.py` (pure-stdlib,
`pytestmark = pytest.mark.ci_safe`): truthy/falsy table, unrecognized-value → default,
invalid int/float → default, call-time re-read after `monkeypatch.setenv`. No runtime risk;
common gate steps 1–3 only (no replay needed — nothing live changes; run it anyway if cheap).

**PR W4-T2 — migrate `routers/voice_tts.py` (38 reads).** Highest count. Replay gate is
non-negotiable. **Byte-equivalence rule for every site:** same key, same default, same
truthy/falsy outcome for every value that can actually appear, same read time (a module-level
constant stays a module-level constant, now built via `typed_env`). Any site whose current parse
differs from the canonical sets for reachable values (e.g. `== "true"` sites) is either left
untouched or changed **with an explicit line in the PR body + a test** — never silently. PR body
must include the before/after grep table (`grep -n "os.getenv\|os.environ" routers/voice_tts.py`
pre/post).

**PR W4-T3 — migrate `zoe_agent.py` (31 reads).** Same rules; it's the legacy brain fallback —
replay gate + `test_zoe_agent_skills.py`-adjacent focused tests.

> **T-series status + calibration (2026-07-07).** T1 ✅ #1127 (module) + #1130 (env_bool
> present-but-empty → False — REQUIRED for byte-equivalence, see that PR). T2 ✅ #1132
> (voice_tts: 18/41 reads migrated; rock-guard `_env_default` extended to parse typed_env
> shapes). T3 ✅ #1136 — and the honest finding: `zoe_agent.py` yielded exactly **1** of 31
> reads; the rest are NON-canonical shapes (odd truthy sets like `== "true"`/`!= "false"`,
> 16 unguarded `int()`/`float()` where crash→default is a behavior change, nested key
> fallbacks, meaningful empty edges). **Byte-equivalent migration has hit diminishing
> returns.** The remaining value is a *deliberate-delta normalization* pass — per site:
> adopt canonical parse + declare the flip cases, convert crash→default+warn with a test —
> which changes behavior and therefore needs sign-off, not mechanical execution. T4
> (`main.py`+`mcp_server.py`) executors: expect the same distribution; migrate the exact
> class, document the rest, don't force-fit.

**PR W4-T4 — migrate `main.py` + `mcp_server.py` (23 each).** May be two PRs if the diff exceeds
~300 lines. `mcp_server.py` sites feed subprocess workers — confirm `runtime_env.bootstrap_runtime_env()`
ordering is unchanged (bootstrap must still run before the first read of a bootstrapped key).

**The `ZOE_USE_CORE_BRAIN` divergence** (chat.py:497 vs brain_dispatch.py:28) is fixed by Part 1
W4-C1 (`_USE_ZOE_CORE = use_core_brain()`, delta declared there) — do NOT re-fix or re-parse it
in a typed_env migration PR; those PRs stay byte-equivalent.

Later modules (system.py 18, intent_router.py 14, voice_livekit.py 14, …) follow the same
per-module recipe; stop when marginal value drops — 100% conversion is NOT a goal.

---

# Do-NOT list (both parts)

- Do NOT create `chat_v2.py`/`chat_new.py`/any parallel chat router, and do NOT put helper
  modules under `routers/` — helpers are top-level `services/zoe-data/` modules the one router imports.
- Do NOT change lane ordering, policy tables, prompts, or event shapes in a move PR. Moves are
  byte-identical relocations; the only declared behavior deltas in this packet are W4-C1's two
  (call-time dispatch + the `ZOE_USE_CORE_BRAIN` parse alignment).
- Do NOT break monkeypatch seams: keep re-exports in chat.py, keep bare-name call sites, never
  move `_record_run_state` / `_persist_ag_ui_run` / `_safe_load_portrait` /
  `_save_chat_message` / `_ensure_user_and_chat_session` / `_check_frustration` /
  `_persist_memory_candidates` / `_mempalace_load_user_facts` / `_build_memory_context` out of
  chat.py's namespace.
- Do NOT adopt pydantic-settings / a global Settings object / import-time env freezing; do NOT
  rename any env var or change any default while migrating.
- Do NOT bypass `gemma_endpoint.gemma_base()` for `GEMMA_SERVER_URL` (the `/v1/v1` 404 trap).
- Do NOT migrate `greploop_guard.py`/harness modules (Wave 4 fences them out of the process).
- Do NOT touch the rocks or `docs/CANONICAL.md`'s ```yaml rocks:``` block
  (`test_canonical_invariants.py` parses it).
- Do NOT skip the replay gate on any chat.py/voice-adjacent PR; always under
  `flock /tmp/zoe-voice-harness.lock`.
- Do NOT leave `_old`/`_v2`/commented-out copies; retire by removing.
- Do NOT merge with unresolved Greptile threads; arm `gh pr merge <n> --squash --auto`; never
  `--admin`/`--force`; verify merge via REST (`pulls/N` → `merged: true`), not GraphQL pr-view.
- Do NOT `git add docs/archive/` (untracked graveyard on disk fails canonical invariants).

# Execution order

W4-C1 → W4-C2 → W4-C3 → W4-T1 → W4-T2 → W4-T3 → W4-T4 → (optional W4-C4).
C-track and T-track touch disjoint files after C1, so T1 may run in parallel with C2/C3 from
separate worktrees; the voice replay gate serializes T2+ (one harness lock).
