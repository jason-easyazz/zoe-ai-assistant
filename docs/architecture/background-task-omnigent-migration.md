---
type: spec
status: draft
owner: platform
created: 2026-07-24
---

# Background-task migration: Hermes CLI → Omnigent

## Why this exists

`services/zoe-data/background_runner.py` is the last live Hermes dependency after
the 2026-07-24 Hermes retirement (gateway stopped, board rows removed, kanban
default flipped to the executor, voice escalation removed — PR #1531). It still
executes Zoe's "I'll work on that in the background and let you know" tasks by
shelling `hermes -p <profile> --accept-hooks -z <prompt>` (the OpenRouter worker
path — *not* the dead `:8642` gateway, which is why it still works).

This is a **live feature**, so it is a *migration*, not a deletion. This spec is
the plan to move the execution engine to Omnigent (the same lane the Multica
board runner is proven on) while keeping every surrounding contract byte-for-byte
identical.

## The current contract (what MUST be preserved)

`background_runner.py` (384 lines). The engine is one function; everything else is
orchestration that must survive unchanged.

- **`enqueue_background_task(task, user_id, session_id, panel_id, request_depth,
  multica_issue_id) -> int`** — inserts a `background_tasks` row (`status=pending`),
  fires `_run_task` as a fire-and-forget coroutine tracked in `_running`, returns
  the task id. Enforces `request_depth <= _MAX_REQUEST_DEPTH` (the A2A
  delegation-depth guard — must survive).
- **`_run_task`** — sets `running` → calls the engine → on success sets
  `done`+`result`, records a cost event, broadcasts `background_task_done` (+
  `panel:announce` when `panel_id` is set), and runs the **evolution-proposal
  auto-deploy** post-step (regex-matches `Implement evolution proposal <UUID>`,
  flips the proposal to `deployed`, syncs to Multica). On failure → `error` +
  `background_task_error` broadcast. All of this is engine-agnostic and stays.
- **`_run_hermes_background_task`** — **THE ONLY PART THAT CHANGES.** Shells the
  Hermes CLI with a 900 s timeout (`HERMES_BACKGROUND_TIMEOUT_S`), returns stdout
  as the text answer.
- **`get_pending_tasks(user_id)`** — completed-but-unseen delivery (polling
  fallback beside the WebSocket push). Unchanged.
- **`_watchdog_loop`** — marks tasks stuck in `running` past `ZOE_TASK_TIMEOUT_S`
  as `blocked`, prunes rows >30 days. Unchanged.

**Callers** (4): `routers/chat.py` ×2 (agent background escalation + the pending
tasks API), `routers/voice_tts.py` ×1 (voice escalation, post-#1531), and
`mcp_server.py` ×1 (the `hermes` agent MCP tool). None of them change — they call
`enqueue_background_task`, which keeps its signature.

**Delivery is text, not a PR.** This is the load-bearing difference from the
board runner: `_run_hermes_background_task` returns a **prose answer** that is
stored, pushed, and read back to the user. The board runner's output is a merged
PR. So the migration reuses Omnigent's *kick + session* mechanics but **not** its
PR/gate/merge path.

## The overloaded-runner problem (decide this first)

`background_runner` today serves two genuinely different task kinds down one pipe:

1. **Engineering tasks** — e.g. `Implement evolution proposal <UUID>: …`. Omnigent
   (polly, a *coding orchestrator* that delegates to sub-agents and opens PRs) is
   an excellent fit. This is the same lane the board runner already proves.
2. **General / research tasks** — the voice- and chat-escalation path: *"work on
   that"*, and exactly the kind Jason described for task #18: *"how much are
   tickets to Bali atm"*. Polly's coding-orchestrator framing is the **wrong tool**
   for a web-lookup/research answer.

**Recommendation:** do not lump both onto polly. Route by kind:

- Engineering/proposal tasks → **Omnigent** (this spec).
- General/research/web-lookup tasks → the **web-search/browsing** capability
  being designed in task #18 (`project_zoe_web_search_browsing`,
  `browser_broker.py` survives). A research question should hit a fast
  search/browse tier, not spin up a coding orchestrator.

Classification can be cheap and explicit: the evolution-proposal regex already
identifies engineering tasks; everything else defaults to the research tier.
This keeps latency and cost sane (a coding-orchestrator boot for "tickets to
Bali" is absurd) and means the two migrations proceed independently.

## Target design (engineering lane)

Swap only the engine, behind a flag, reusing the board runner's proven mechanics.

### Reuse, don't reinvent

`omnigent_issue_executor.py` already has the pieces:
- `kick_omnigent(issue) -> sid` — POST `/v1/sessions` + staged brief + runner +
  `docker exec -d … omnigent run -r <sid>` (the handoff recipe).
- `_session_text(sid) -> str` — pulls the session transcript text. This is the
  **text-result extractor** a background task needs (the board runner instead
  greps that text for a `PR_URL=`).
- `_omnigent_url/_omnigent_agent_id/_omnigent_container` — lazy env accessors.

Factor the shared kick+poll out of the PR-specific path so both consumers use it:

```
kick_omnigent(brief) -> sid
poll_session_until_settled(sid, timeout_s) -> final_text   # NEW, generalises poll_for_pr_url
```

The board runner keeps `poll_for_pr_url` (grep the text for a PR); the background
runner adds `poll_session_until_settled` (return the text itself).

### New engine

```python
def _run_omnigent_background_task(task, *, user_id, task_id) -> str:
    brief = _background_brief(task, user_id=user_id, task_id=task_id)  # untrusted-data framed
    sid = kick_omnigent({"number": task_id, "title": f"bg-{task_id}", "body": brief})
    text = poll_session_until_settled(sid, timeout_s=BACKGROUND_TIMEOUT_S)
    if not text:
        raise RuntimeError("omnigent background task returned no text")
    return text
```

### Completion signal (the real engineering risk)

Omnigent sessions **settle to `idle`, never `completed`** (measured; the board
runner works around this with a nonce-token/PR-URL grep). A background task needs
the *answer text*, so it needs a definite "done, here it is" signal — two options:

- **(A) Quiescent-idle + last assistant message.** Poll until the session is
  `idle` and the transcript has stopped growing for N polls, then take the last
  assistant turn. Simple, but risks truncating a still-thinking agent.
- **(B) Sentinel marker.** Brief the agent to end with `RESULT: <answer>` and
  extract after the marker (mirrors the board runner's `PR_URL=` contract, which
  is proven). More robust; **recommended.**

Use (B): reuse the prefixed-marker discipline (`_PR_URL_RE` is the template) so
completion is explicit, not inferred from quiescence.

### Cutover, flag-gated (mirror the kanban_adapter pattern)

- `ZOE_BACKGROUND_BACKEND = hermes | omnigent`, read per call, **default
  `hermes`** so landing the code changes nothing (the seam ships dark, exactly as
  the kanban seam did — see `_kanban_backend()`).
- `_run_task` dispatches on the flag to `_run_hermes_background_task` (unchanged)
  or `_run_omnigent_background_task` (new).
- Prove on the lab/dev box with ≥3 real background tasks, compare said-vs-did,
  then flip the default to `omnigent`. Only after the flip is Hermes deletable.

### Cost accounting

`_record_cost_event` currently logs `agent_name="hermes"`, `model=<profile>`,
tokens estimated from output length. For the Omnigent path, log
`agent_name="omnigent"` and the real model when the session API exposes it; keep
the length estimate as the fallback. Do not silently attribute Omnigent spend to
"hermes".

## Capability parity — VERIFY before flipping

These are the things that can make the Omnigent path *quietly worse*, not just
different. Each must be checked on the live container, not assumed:

1. **Browser / CloakBrowser tools.** The Hermes brief says *"Use CloakBrowser MCP
   tools when needed."* Does `zoe-omnigent` have those MCP tools connected? If not,
   any browsing background task regresses. (Overlaps task #18 — likely the reason
   research tasks should route there instead.) **OPEN — verify.**
2. **No docker/host access.** The board runner already proved Omnigent can't do
   infra tasks (it correctly returns `blocked`). Any background task needing host
   access will regress the same way — acceptable, but the failure must surface as
   a clear blocked/error, not a hang.
3. **Concurrency.** Background tasks are **user-initiated and interactive** —
   several can be in flight at once (each `enqueue` fires its own coroutine). The
   board runner is deliberately **single-lane** (the usage guard). These must not
   share one lane, or a user's "do this now" waits behind autonomous board work.
   Decide a **bounded** background concurrency (e.g. a small semaphore) separate
   from the board's single lane. **OPEN — decide.**
4. **Container Claude account.** Omnigent runs on the container's consumer OAuth /
   API key, which has expired before (2026-08-22 next) and can exhaust credits.
   The board runner surfaces this in seconds; background tasks must do the same
   (→ `error`, with the reason), never hang to the 900 s timeout.
5. **`HERMES_YOLO_MODE` / `--accept-hooks`.** The Hermes path runs
   auto-approving. The Omnigent session equivalent (auto permission mode) must be
   set so a background task doesn't stall on a permission prompt with no human.

## Verification plan

- Unit: dispatch-on-flag (both engines reachable), depth guard preserved,
  marker extraction, timeout → error (not hang), cost event attributes
  `omnigent`. Negative controls throughout (per `feedback_verify_your_instruments`).
- Lab: ≥3 real background tasks end-to-end through Omnigent (one engineering /
  evolution-proposal, one general, one that *should* fail — e.g. host access — to
  confirm it surfaces `error`/`blocked`, not a hang). Compare said-vs-did to the
  Hermes path.
- Only after the lab proof: flip `ZOE_BACKGROUND_BACKEND=omnigent`, watch, then
  in a **separate** PR delete `_run_hermes_background_task`, the Hermes CLI helper
  imports, and **revoke `HERMES_API_KEY`** (a live credential in
  `services/zoe-data/.env` — revoke, don't just delete the line).

## Open decisions for the operator

1. **Split by task kind?** (recommended) Engineering → Omnigent; research →
   task #18's web tier. Or force everything through Omnigent for now?
2. **Background concurrency** — bounded semaphore separate from the board's single
   lane; what bound? (usage vs. responsiveness)
3. **Completion signal** — sentinel marker (recommended) vs. quiescent-idle.

## Sequencing

This is stages 3–4 of the Hermes retirement (`project_multica_executor_migration`).
It is **independent of** and can run in parallel with task #18, but the
split-by-kind decision above couples them: if research tasks route to #18, that
capability should exist first so the escalation path has somewhere to go.

## Related

- `project_multica_executor_migration` — the Omnigent lane + kick recipe this reuses.
- `reference_omnigent_handoff_mechanics` — the session/kick mechanics.
- `project_zoe_web_search_browsing` / task #18 — where research tasks should go.
- `docs/architecture/multica-executor-migration.md` — the board-runner precedent.
