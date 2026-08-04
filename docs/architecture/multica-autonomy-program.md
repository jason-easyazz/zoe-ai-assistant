# Multica full-autonomy program — backlog → warden → dispatch → merged PR

> **Status:** PLAN. Docs only — nothing in this document is built. It is the
> forward-looking SSOT for Multica autonomy and **supersedes the forward-looking
> half of [`multica-executor-migration.md`](multica-executor-migration.md)**
> (its Phase-2-remaining / Phase-3 / Phase-4 sections and its §4 kill-switch
> non-negotiable). That document remains the historical record of how the
> executor got here, and its §1/§2/§5 decisions of record are unchanged and
> still binding.
>
> **Grounded in three read-only dives dated 2026-08-04** (Multica lifecycle +
> live DB/systemd state; Omnigent 0.7.0 as installed; Flue 2.0.1 as the rebuild
> target). Every mechanism named below is anchored to `file:line` in those dives
> or to live command output. Where a doc and live reality disagreed, live
> reality won and the divergence is recorded.

---

## 0. What this program is, in one paragraph

Today a Multica ticket cannot move from `backlog` to a merged PR without a human
at four separate points: someone must **assign** it to the engineering agent,
someone must **hand-write** its `dispatch_approved` contract block, someone must
**start** an executor process, and someone must **unwedge** it from a REPL when
an evidence gate blocks the chain. This program removes those four human steps,
in that order, replacing the judgement one — "is this ticket still worth doing?"
— with an **Omnigent-backed relevance gate (the *warden*)** that sits exactly
where the deterministic admission predicate already sits, and replacing the
mechanical ones with bounded, reason-recording, fail-closed automation. The
executor those tickets flow into is **rebuilt on Flue 2** as part of the
whole-estate Flue-2 migration. The one human step that stays is the merge
button, and even that is narrowed to the load-bearing tier.

**The governing asymmetry:** every gate in this program fails toward *not
spending money and not moving the ticket*. A judge that cannot judge does not
promote. An executor that cannot claim does not dispatch. A chain that cannot
prove its evidence does not advance. Silence is never a pass.

---

## 1. Target state

```
                     ┌──────────────────────────────────────────────────────────┐
  HUMAN / chat       │ multica_operator.create_ticket()  →  status = backlog     │
  / proposal bridge  │ (unchanged; the ONLY ticket-creation path)                │
                     └────────────────────────┬─────────────────────────────────┘
                                              v
 ┌──────────────────────────── B A C K L O G ────────────────────────────────────┐
 │                                                                               │
 │  ①  DETERMINISTIC PRE-FILTER            multica_admission.py:39-70            │
 │      ticket_is_dispatch_approved(): schema-1 block, no parse_error,           │
 │      no live-checkout path, no smoke/e2e source, criteria + evidence          │
 │      present, evolution-proposal contract matches, zoe_kind != parent         │
 │      → structural safety. NEVER delegated to a model. Unchanged.              │
 │                                                                               │
 │  ②  THE WARDEN — Omnigent relevance gate            ← THE CENTREPIECE         │
 │      one claude-sdk session per ticket, read-only, nonce-terminated           │
 │      "still relevant / won't break / won't regress", judged against a         │
 │      PINNED base_sha. Verdict is STRUCTURED, never prose.                     │
 │      → TT1 multica_triage_judge.judge_ticket(ticket, classifier=warden)       │
 │      → TT2 applies the computed DispositionAction to the board                │
 │      FAIL CLOSED: any judge failure = the ticket stays in backlog.            │
 │                                                                               │
 │  ③  PROMOTION      client.update_issue(id, status='todo', description=…,      │
 │                                        assignee_id=<engineering agent>)       │
 │      Mode VETO   (waves 3-5): the human wrote the contract; warden vetoes.    │
 │      Mode AUTHOR (wave 6+):   the warden writes the contract; ① still lints.  │
 │      Ordering + holds unchanged (queue_order → priority → identifier;         │
 │      phased titles held until predecessors are done).                         │
 └────────────────────────────────────┬──────────────────────────────────────────┘
                                      v
                                T O D O
                                      │  poll loop, 30 s active / 300 s paused
                                      │  cap: ZOE_MULTICA_POLL_DISPATCH_LIMIT = 1
                                      v
              LANE ARBITER  (§4 — a Jason decision; written at promotion time)
                                      v
              executor_registry.dispatch_issue → KanbanAdapter.dispatch
                                      v
              executor_queue_backend.run_kanban_command  (Python, already live)
                INSERT agent_task_queue (queued) + activity_log reason,
                same transaction. Reason is MANDATORY — an empty one throws.
                                      v
 ┌───────────── agent_task_queue ─────────────────────────────────────────────┐
 │  CONSUMER = labs/flue-executor-2x   live-runner.ts   (§5 rebuild)          │
 │    gates, all ON by construction:                                          │
 │      kill switch present            → idle, claim nothing                  │
 │      ZOE_EXECUTOR_DISPATCH=dry      → poll + log, mutate nothing           │
 │      single-lane claim              → advisory lock + SKIP LOCKED + NOT EXISTS│
 │      DB-name allowlist              → refuses any DB outside the set       │
 │    routing: heavy / has-brief → Omnigent lane; light → local phase worker  │
 └───────────────────────────────┬────────────────────────────────────────────┘
                                 v
        PHASE PIPELINE   scout → implement → verify → review → closeout → retro
        EVIDENCE GATES   pipeline_evidence._REQUIRED_EVIDENCE per phase
                           implement:{tool,pr}  verify:{test,validator}
                           review:{human}  closeout:{greptile}  retro:{log}
                         a phase that cannot prove its evidence BLOCKS. By design.
                                 │
                    ┌────────────┴────────────┐
                    v                         v
             GATE_BLOCKED                  advancing
                    │                         │
        §6 AUTO-UNWEDGE                       v
        bounded resume_pipeline()       PR opened by the implement phase
        N attempts, then DEAD-LETTER          │
        (never auto-clears fingerprint_abort) v
                                    REVIEW PIPELINE per root AGENTS.md
                                    validate + secret-scan (REQUIRED, deterministic)
                                    cross-vendor review (routine semantic)
                                    voice-gate (informational, deploy-enforced)
                                    required_conversation_resolution
                                          │
                                          v
                            §8 THE MERGE BUTTON (a Jason decision)
                              routine tier → armed auto-merge
                              load-bearing tier → human
                                          v
                              board 'done' + chain worktrees reclaimed
```

**The seam this program lives on** is `services/zoe-data/main.py:1589-1618` — the
existing backlog→todo promotion block. The warden is a call inserted between
`select_next_approved_issue()` (`:1601`) and `update_issue(status='todo')`
(`:1609`). Nothing about the board changes; nothing about `kanban_adapter`'s
2,474 lines of discovered failure modes changes.

---

## 2. What already exists (and must not be rebuilt)

The single most important framing fact: **this program is mostly wiring, not
greenfield.** Three of the four pieces of the relevance gate are already in the
repo, hardened and tested.

| piece | state | anchor |
|---|---|---|
| the promotion seam | live, deterministic, running every 30 s | `main.py:1589-1618`, `multica_admission.py:130` |
| the **judgement half** (TT1) | **built, 533 L, 102 tests, merged PR #1598, flag-dark** | `services/zoe-data/multica_triage_judge.py` behind `ZOE_MULTICA_TRIAGE_JUDGE` (default OFF, absent from every `.env`) |
| the **board-write shape** (TT2's template) | proven against the real board by a script that has already run | `scripts/maintenance/multica_apply_triage_dispositions.py`; `multica_operator.move_to_todo` (`:85-111`) |
| the **classifier** | **DOES NOT EXIST.** `Classifier = Callable[[Mapping], Any]`, injected, no model / no HTTP anywhere in the module | `multica_triage_judge.py:103` (alias), `:424` (sole invocation) |
| the enqueue half of the executor seam | live Python since 2026-07-28, untouched by Flue 2 | `executor_queue_backend.py` |
| the Omnigent kick + nonce completion protocol | lab-proven, 40 e2e asserts | `labs/flue-executor/src/omnigent.ts` |

TT1's vocabulary is already the right vocabulary: `ADMIT_REASON_CODE =
"relevant"` (`:47`) — **an admit literally means "still relevant"** — and
`stale`, `already_shipped`, `duplicate`, `out_of_scope` are already reject
codes. It fails closed to `needs_info` (`:51`, `:380`) with a `MIN_CONFIDENCE =
0.5` floor (`:72`), and its close map (`:82-92`) deliberately routes
`needs_info → None` = **hold in backlog for a human**.

**The one genuine vocabulary gap:** the operator's framing is *"still relevant /
**won't break** / **won't regress**"*, and TT1 has **no reject code for breakage
or regression risk**. This program adds two — `would_break` and `would_regress`
— both mapping to `target_status = None` (HOLD in backlog, never auto-cancel).
That asymmetry is deliberate: a *relevance* claim is about the ticket, which the
classifier can read; a *regression* claim is about code, which it can only
partially verify. TT1 already binds a verdict to a `reviewed_ref` = `ref@sha`
(`:314`, validated `:289`) precisely so that claim is attributable to a
revision. A risk verdict therefore holds a ticket for a human; it never closes
one.

---

## 3. The warden

### 3.1 Identity and registration

A **new** Omnigent agent, not polly.

| property | value | why |
|---|---|---|
| name | `warden` | polly's whole identity is fanout (`cross-review`/`fanout`/`investigate` skills, `sys_session_send` sub-dispatch). The gate wants the opposite: one cheap, bounded, single-shot read. |
| harness | **`claude-sdk`** | `elicitation: none` → structurally immune to §7's 240 s park-death. Flat-rate on the Max plan, and grading a 37-ticket backlog repeatedly is a high-volume job. Only lane with a proven three-call-site kick+poll+report recipe in this repo. Runs the SDK in-process, so the orphan blast radius is smallest. |
| `executor.type` | `omnigent` | required for `harness_override` to be legal (`helpers.py:1609-1661`). |
| skills | **empty** | nothing to delegate to. |
| agent id | read from `GET /v1/agents` at dispatch time | 0.7.0 returns **bare 32-hex**; never hand-write an `ag_` prefix back on — `_LEGACY_ID_PREFIXES` is a type-blind shim that validates nothing. |
| model tier | flat-rate Max claude-sdk | `pi` (GLM-5.2 via OpenRouter) is the **tie-breaker only**, under a hard cost cap, per the root `AGENTS.md` routing contract. Never the routine pass. |

**Rejected harnesses, with reasons:** `acp:oh-my-pi` (`elicitation:
sse-permission` → walks into §7; metered on a separate capped key; produced the
measured 2026-08-03 orphan+overspend incident; doctrine is already "omp =
builder lane only"); `codex`/`codex-native` (`elicitation: jsonrpc`, plus a
tmux + app-server tree that maximises the orphan class).

### 3.2 The policies block — real enforcement, not advice

`inner/claude_sdk_executor.py:2049-2089` installs `_can_use_tool_gate` as
`options.can_use_tool` and **runs the TOOL_CALL policy in every permission mode,
including `bypassPermissions`** (`:2063-2066`). `TOOL_CALL` is a fail-closed
phase (`policies/types.py:61`) — a policy that errors DENIES. So the block below
is a perimeter, not a prompt:

```yaml
policies:
  no_writes:
    type: function
    function:
      path: omnigent.policies.builtins.working_dir.block_working_dir_changes
      arguments: { allowed_dirs: ["/relgate"], action: deny }
  # + a custom function policy denying Write / Edit / NotebookEdit outright and
  #   any Bash whose command is not on a read-only allowlist. Build it on
  #   policies/builtins/_shell.py's parser (chaining split, sudo/env/VAR= prefix
  #   strip, bash -c unwrap to MAX_SHELL_NESTING) so the gate cannot be wrapped
  #   around.
```

**`action: deny`, never `ask`.** An ASK parks the turn and §7's 240 s idle
watchdog then fails it with a *misleading* diagnosis ("wedged LLM or tool call")
for what is actually "nobody answered".

Defence in depth, because none of the four layers alone is sufficient:

1. **Mount (strongest, outside Omnigent).** `/workspace` is the live checkout,
   mounted **RW**. The warden does not need it. Add
   `/home/zoe/.zoe/relevance-gate-ro:/relgate:ro` to the `zoe-omnigent` compose
   service and pin the session `workspace` there. Additionally scrub
   `GH_TOKEN` / `/root/.config/gh` for this lane via a fence wrapper — the
   pattern is `omp-omnigent-fenced`; a plain harness has no per-session env, so
   a wrapper is the only place a scrub can live.
2. **TOOL_CALL policy** (above) — the strongest control *inside* Omnigent.
3. **`os_env.sandbox`** (bwrap) — real kernel confinement of the shell surface,
   but it does not cover tools the claude-sdk executes in-process. Defence, not
   perimeter.
4. **Workspace pinning** — selection, not confinement. It does not stop
   `cd /workspace`. That is what (2) is for.

**Stated as an invariant, because the backlog is untrusted input:** the warden
reads ticket text written by anyone with board access and by other agents. It
must never hold a credential or a writable path it does not need.

### 3.3 Input brief

Everything goes **inline via `-p`**. Staging a brief as a session *comment* is a
**silent failure** (observed 2026-07-27), and `initial_items` with content on a
plain REST create is appended with `response_id="seed"` and **never executes**
(`orchestration.py:5545-5595`). Create with `initial_items: []`, kick with the
brief inline.

The brief carries, pre-digested (the warden should not have to go find things):

1. the ticket — id, title, body, ticket block, `created_at`, current status;
2. **a pinned `base_sha`** — `git rev-parse HEAD` of `main` plus one line on what
   that commit is, mirroring the `voice-gate --expect-revision` discipline;
3. a recent-merges digest narrowed to the files the ticket claims to touch;
4. the ticket's own file scope, plus whether each path still exists at
   `base_sha`;
5. the verdict contract and a per-dispatch completion nonce.

### 3.4 Output contract

```
RELEVANCE-VERDICT-<nonce>
{
  "ticket_id":  "...",
  "verdict":    "RELEVANT" | "STALE" | "RISKY" | "NEEDS_HUMAN",
  "confidence": "high" | "medium" | "low",
  "base_sha":   "<the 40-hex the gate was TOLD to assume>",
  "superseded_by":   ["#1617", "#1631"],
  "conflicts":       [{"path": "...", "why": "..."}],
  "regression_risk": {"level": "none|low|med|high", "areas": ["voice-path"]},
  "reasoning":  "<= 6 sentences",
  "evidence":   ["file:line or PR# per claim"]
}
RELEVANCE-VERDICT-END-<nonce>
```

Mapping onto TT1: `RELEVANT → relevant` (admit) · `STALE →
stale|already_shipped|duplicate|out_of_scope` (close per TT1's map) · `RISKY →
would_break|would_regress` (**new codes, both HOLD**) · `NEEDS_HUMAN →
needs_info` (HOLD; the model's legitimate way to abstain instead of guessing).

Parser rules — every one of these is a scar this repo already has:

- **Find the nonce, or it did not finish.** `idle` is Omnigent's *initial* state
  as well as its terminal one (`helpers.py:1031`); a dead kick is byte-identical
  on the status field to a clean completion. **Never accept `status == "idle"`
  as success.**
- **Require the literal `RELEVANCE-VERDICT-<nonce>` prefix**, not a bare JSON
  blob — a JSON object quoted from the ticket body must not be mistakable for
  the verdict (same reasoning as `_PR_URL_RE`'s mandatory `PR_URL=` prefix).
- **Mint the nonce per dispatch** (`randomBytes(6).toString('hex')`) and keep the
  assembled-token-must-not-appear-in-the-brief negative control that
  `omnigent.ts:316-318` already throws on.
- **Reject a payload whose `base_sha` is not the one you supplied** — it reasoned
  about a different tree.
- **Never gate promotion on a model-assigned severity.** `confidence` and
  `regression_risk` are inputs to a human queue. Humans triage severity;
  machines report findings (root `AGENTS.md`).

### 3.5 Failure semantics — fail closed, always

| failure | behaviour |
|---|---|
| timeout / no nonce in the transcript | **ticket stays in backlog.** One WARNING-level `activity_log` entry naming the cause. Never a promotion. |
| malformed JSON, wrong `base_sha`, unknown verdict | same — no promotion, loud reason |
| Omnigent unreachable / host offline / OAuth dead | same, and the cause is named in the reason within seconds (the executor's fail-fast login/credit/rate-limit phrasing detector is the template) |
| N consecutive gate failures in one cycle | **stop calling the gate for the rest of the cycle** and log at WARNING. A gate that cannot judge must not let the fleet run blind — but it must also not thrash. |
| a verdict the deterministic pre-filter ① then rejects | the pre-filter wins. Structural safety is never delegated. |

**The gate can only ever REMOVE tickets from eligibility in VETO mode** (§8
decision). In AUTHOR mode it additionally writes the contract block — and every
structural property of that block is still validated by
`ticket_is_dispatch_approved`, unchanged. The model supplies judgement and
criteria text; it never supplies a safety property.

### 3.6 Cost bounds

- **The dispatch budget is arithmetic, checked at startup, and strictly below the
  caller's timeout.** Copy the `BUDGET` block of `cross_review.sh:52-97` in
  shape: enumerate every bounded phase, sum them, **fail loudly** if
  `poll + overhead >= caller_timeout`. The failure it prevents is precise — the
  caller's `subprocess.run(timeout=…)` kills only the shell, the EXIT trap never
  fires, and the detached worker survives to run *beside* the next one.
- `timeout N cmd` is **not** a hard bound (TERM then wait forever). Use
  `timeout -k 5 N` on every `docker exec`.
- Serialize with a flock and **bound the wait** (`flock -w`) — the caller's clock
  is already running while you block.
- **`ZOE_MULTICA_WARDEN_MAX_TICKETS` per run — a countable cap, not "until the
  queue is empty".** Same doctrine as `MAX_SUMMONS`: a cap you can count is the
  only reliable termination condition.
- Optional in-agent belt: `policies/builtins/cost.py::cost_budget`. **Caveat:**
  it fails *closed* when token usage is present but `total_cost_usd` is absent,
  and Max-plan claude-sdk sessions may not be token-priced — prove it on a
  throwaway session first or it DENIES every turn.

### 3.7 Verify before trusting (`[[feedback_verify_your_instruments]]`)

Wave 2 does not exit until each of these has been made to go **red**:

- brief omits the nonce → the poller goes RED, not "clean";
- session pointed at a stale `base_sha` → the parser rejects the verdict;
- warden asked to `Write` under `/relgate` → TOOL_CALL policy DENIES **and the
  turn does not park**;
- omnigent server stopped mid-poll → the poller rides ~60 s then declares
  poll-lost, does not hang;
- ticket body containing a fake `RELEVANCE-VERDICT-<other nonce>` block → the
  parser ignores it.

---

## 4. THE TWO-LANES PROBLEM — options and recommendation *(Jason decides)*

**The finding.** There are **two independent execution lanes over the same
board**, they are not aware of each other, they share one kill switch, and
**both claim `todo`**:

| | **Lane A — phase chain** | **Lane B — board runner** |
|---|---|---|
| entry | `main.py` poll loop → `dispatch_issue` | `multica_board_runner.py:212 run_one()` |
| selects | `assignee_id == engineering agent`, status `todo/in_progress/blocked` | **any** `todo` issue, **ignores assignee** (`:127-137`) |
| reads | Multica HTTP API | Multica DB directly (asyncpg) |
| unit of work | 6 phases, one queue row each, evidence-gated | ONE shot: implement → PR → gates → merge |
| worker | `agent_task_queue` → flue-executor → Omnigent/local | `omnigent_issue_executor.execute_issue_dict` |
| how it runs | zoe-data in-process poll loop (live) + a separate executor process (**absent**) | **hand-run only** — no unit, no scheduler entry |
| extra flag | — | `ZOE_USE_OMNIGENT_EXECUTOR` (default `0`, **unset live**) |
| **track record** (live `activity_log`) | `task_started 5`, `task_completed 10` | **`issue_claimed 18`, `issue_completed 9`** |

**The uncomfortable part: the lane that actually shipped merged PRs is not the
lane the whole migration is about.** Lane B produced the proposal-bridge chain
(`631f4b5e` → issue #6112 → PR #1555, merged). Lane A's single live run
(ZOE-6106) ended `gate_blocked` at verify with `implement` having taken 5
attempts, and the board was closed to `done` out-of-band.

Right now the collision is masked only because every active issue has
`assignee_id = NULL`. **The moment §5/Wave 1 fixes the assignee, both lanes
become eligible for the same ticket.** This must be decided before autonomy is
armed, not after.

### Option 1 — Unify on Lane A; retire Lane B

- **For:** evidence gates, reason-on-every-transition (the migration's §4
  non-negotiable, and the thing Hermes recorded 0/128 times), per-phase recovery,
  worktree lifecycle, `multica-web` visibility via `agent_task_queue`, and the
  twelve PRs of discovered failure modes encoded in `kanban_adapter`.
- **Against:** it is the lane with the worse shipping record; open gap (a) —
  real local phase workers — is still open, so *every* real brief routes to
  Omnigent anyway; needs §6's unwedge before it is survivable unattended.

### Option 2 — Unify on Lane B; retire Lane A

- **For:** it demonstrably ships. Far fewer states. Already merges autonomously
  (`report_result` maps `result.merged → done`).
- **Against:** it discards the evidence-gate machinery and the reason
  discipline; it has no per-phase recovery and no bounded retry; it strands the
  entire Phase-2 investment (`kanban_adapter` + `executor_queue_backend` +
  the 40-assert executor suite); a single-shot Omnigent run that fails has
  nowhere to resume from. It also inverts the standing lesson recorded four
  times in memory: *respect the existing subsystem's design — build THROUGH it,
  not around it.*

### Option 3 — Keep both, made disjoint by ADMISSION *(RECOMMENDED, as a transition to Option 1)*

The lane is **decided once, at promotion time, by the warden's gate**, written
onto the ticket (label or ticket-block field), and **both lanes filter on it**.
Neither lane ever again selects a ticket the other could select. Lane B is
demoted from "claims any `todo`" to "claims tickets labelled for it", which is a
small, testable change to `claim_next_issue` (`:127-137`).

- **For:** it removes the collision *today* without betting the program on
  either lane's record. It keeps the shipping lane shipping while Lane A earns
  the ≥3-real-tickets bar the migration doc already demands. It is reversible.
- **Against:** two lanes is two maintenance surfaces, and "temporary" dual
  lanes have a way of becoming permanent. Mitigation: **Option 3 carries an
  explicit expiry** — once Lane A has landed ≥3 real tickets end-to-end (≥1
  heavy) *and* Wave 5's real local phase workers exist, Lane B is **deleted**
  (retire by removing; git keeps history). Write the expiry into the wave exit
  gate, not into a promise.

**Recommendation: Option 3 now, converging on Option 1 at Wave 5 exit.** But
Option 2 is a legitimate operator choice if Jason's priority is shipping volume
over the evidence-gate machinery, and it is the cheaper program by a wide
margin. **This is a Jason decision — see §9 D1.**

---

## 5. The executor rebuild — `labs/flue-executor-2x`

The Flue-2 dive's verdict, established by controlled negative test (the entire
workflow API is absent from `@flue/runtime@2.0.1`; there is no `./workflow`
export subpath; positive controls `createAgentRouter`/`usePersistentState`/
`setProvider`/`durable` all present):

> **Orchestration moves OUT of Flue into plain TypeScript. Flue 2 becomes a
> library the executor MAY call, not the substrate it runs on.**

This is not a workaround. It is precisely what upstream prescribes: *"Flue does
not checkpoint arbitrary TypeScript execution… Outside the agent, use the
workflow engine your platform provides and treat Flue like any other service you
call from it."*

### 5.1 Why the rebuild is small

**`labs/flue-executor` barely uses Flue at all.** Its entire Flue-1 surface is:

- `src/workflows/phase-worker.ts` (56 L) — one `defineWorkflow` bound to a
  **deliberately dead agent** (`model: 'deadend/none'`, provider pointed at
  `http://127.0.0.1:1/v1`). It writes `PROOF-<phase>.txt` and `result.json`, or
  hangs to exercise the reaper. **It never opens a model session.**
- `src/app.ts` (27 L) — `registerProvider` + the router mount, which exists
  *only* because `flue run` boots the authored app.
- `flue.config.ts` (2 L) — `defineConfig({target:'node'})`.
- `spawn.ts:96-99` — `flue run phase-worker` used purely as a **subprocess
  launcher**, `cwd: LAB_ROOT`, stdio to log files.

`invoke()`, `listRuns()`, `getRun()`, `defineTool()`, `dispatch()` are
**grep-verified absent**. There is **no `src/db.ts`**, so Flue persists nothing
across worker processes. The result channel is `result.json` in the work_dir, not
the workflow return value. **~85% of the executor is already Flue-free.**

### 5.2 Carry over verbatim

Everything load-bearing is plain TypeScript and moves unchanged:

`queue.ts` (claim SQL: `pg_advisory_xact_lock` + `FOR UPDATE SKIP LOCKED` +
`NOT EXISTS` busy-lane clause — **SKIP LOCKED alone double-dispatches**, proven);
CAS transitions (`WHERE id=$1 AND status=$<expected-from>`, `rowCount===0` ⇒
rollback); `requireReason()` + `activity_log` write-through **in the same
transaction**; `reaper.ts`'s three reap classes (dead-pid #685, stalled
`dispatched`, Omnigent-evidence — **404 is authoritative-gone, an unreachable API
is NEVER destructive**); the whole Omnigent nonce protocol including
`assertSafeSessionId` / `SESSION_ID_RE = /^(?:conv_)?[A-Za-z0-9]{16,}$/`, the
docker-exec kick, `kickLogTail` and the fatal-error regex; the anti-self-completion
guard; `spawn.ts`'s `trackedPids` handoff and work_dir defer (defers **without
burning an attempt**); `config.ts`'s modes, kill switch and **DB-name allowlist**
(*"a denylist of `/multica` is bypassable by typo"*); `live-runner.ts`'s
identity-by-name (throws on 0 rows **and on >1**) and graceful SIGTERM drain.

**And the whole Python side is untouched** — `kanban_adapter`,
`executor_queue_backend`, `worktree_bootstrap`, `pipeline_*`.

### 5.3 What changes

| Flue-1 | Flue-2 |
|---|---|
| `defineWorkflow('phase-worker')` | **deleted.** A plain Node module run as a child process. |
| `flue run phase-worker --input <json>` | `node --experimental-strip-types src/worker/phase-worker.ts <json>` |
| `defineAgent` + `registerProvider('deadend')` | **deleted** with the dead agent (`registerProvider` is gone in 2.x anyway; it is `setProvider(createProvider(...))`) |
| `app.route('/', flue())` | **nothing**, unless an agent is hosted |
| `defineConfig` from `@flue/cli/config` | `@flue/runtime/config`; **delete `root`/`output`** (strict validation rejects them) |
| `flue build --target node` | **omitted** — see below |
| `src/labdb.ts` | **stays `labdb.ts`. MUST NOT become `src/db.ts`.** |

**The build step is avoidable, and that matters.** 2.x agent registration comes
from a build-time `'use agent'` scan run by the `@flue/vite` plugin — *"mounting
registers nothing"*, and a converted module without the directive is **silently
not an agent**. But `start({ agents: [...] })` **registers explicitly and
bypasses the scan** (upstream #514, shipped in 2.0.0). So the systemd unit keeps
`ExecStart=… node --experimental-strip-types src/live-runner.ts` verbatim and the
zero-build deploy story survives. Write the `'use agent'` directive anyway
(harmless, preserves the option) but **depend on the array, not the scan.**

Two follow-on notes: built servers never load `.env` (the unit uses
`EnvironmentFile=`, so fine — as long as nobody assumes `.env` is auto-read); and
`--experimental-strip-types` **erases types without checking them**, so
`npm run typecheck` stays the only type gate and must stay in the loop.

### 5.4 New surface

- **`GET /health` on `127.0.0.1:3581`**, loopback-bound, **unauthenticated and
  mounted before any auth** (an operator and systemd must be able to tell a
  wedged process from a mis-tokened one). `:3578` is the live brain sidecar and
  `:3579` its scratch convention — do not collide.
  Report DB reachability as a **field, not an HTTP status**, or a Postgres blip
  flaps the unit under `Restart=always`.
- **`GET /status`** — current claim, in-flight worker pid / Omnigent session id,
  last reap summary. This is the honest replacement for the deleted `listRuns()`:
  upstream's own advice is "reconcile from your orchestrator's own state", and
  this endpoint *is* that state.

### 5.5 Hard rules

1. **Never put executor run-state in Flue's store.** It is "the runtime's own
   durable state", version-stamped and **reset-only** across majors, with no query
   surface. Board rows there would make an upstream bump a **data-loss event for
   the board**, and would blind `multica-web`, which reads the queue directly.
   The system of record stays Multica's Postgres.
2. **Never name the app-data module `src/db.ts`.** This already bit for real: the
   first full e2e was RED with 6 failures because the lab's Postgres module was
   named `src/db.ts` and the CLI rejected the app with *"db.ts must default-export
   a PersistenceAdapter with a connect() method"*. (Silver lining on record: it
   proved the suite goes red on a real defect.) The constraint is unchanged on 2.x.
3. **If the rebuilt executor hosts no Flue agent, omit `db.ts` entirely.** If it
   does host one, give it a **separate file-backed SQLite**
   (`sqlite('./data/executor-flue.db')`), disjoint from Multica's Postgres,
   holding only **disposable** conversations — so a reset-only bump is `rm` plus a
   restart, never a migration.
4. **Pin `@flue/*` exactly; never float a caret.** 2.0.1 is nominally a patch but
   changed **53 files** including 120 lines in `node/agent-coordinator.ts`, added a
   new public `FlueEvent` operation (`enforce_deadline`), and **structurally
   changed abort/timeout/settlement semantics on both targets**. Also: the `next`
   dist-tag is **stale and dangerous** — `@flue/runtime@next` still points at
   `0.8.0-beta.6` (2026-05-26), two major generations back. Only `latest` is
   trustworthy.
5. **Trust the 2.x docs for architecture; test them for literals.** Two confirmed
   instances of documented values being wrong: the migration guide's nested
   `{"message":{...}}` POST body returns **HTTP 400** (real shape is top-level
   `{"kind":"user","body":…}`), and "schema v8" **is not in the shipped code** —
   2.0.1 ships `FLUE_FORMAT_VERSION = 1`. Any wire shape or version constant the
   rebuild depends on must be asserted by a **test**, not quoted from a doc.
6. **Sibling directory, then retire by removing.** Build as
   `labs/flue-executor-2x/` (the `flue-zoe-brain-2x` precedent) so the 1.x
   executor stays runnable for A/B on claim semantics against the same scratch DB
   and rollback is `systemctl` pointing at the other directory. **Delete the old
   directory once the rebuild is proven** — no `_old` copies.
   *(Note: unlike the brain, `deploy.yml` has **no** executor hook — it matches
   only `labs/flue-zoe-brain/` and `labs/flue-zoe-telegram/` — so the in-place
   auto-deploy hazard does not apply here. The reasons for the sibling dir are
   A/B and rollback, not deploy safety.)*

### 5.6 Does the ~2-week Flue-2 upstream-stability hold apply here? — No

The hold's purpose is to avoid betting a **production-reachable** system on a
days-old major. The brain sidecar is exactly that. The executor is categorically
different on every axis: not production-reachable (unit installed inert, never
auto-enabled); `dry` by default (`previewNextClaim()` is a bare SELECT); blast
radius is "a dev board stops dispatching"; **no deploy hook at all**; and **no
Flue store**, so the one-way reset risk that dominates the brain cutover is ~nil
and rollback is `git checkout` plus a restart.

**Read the hold as "do not cut over production", not "do not touch Flue 2."** The
executor is in fact the *ideal* way to spend the hold period — same 2.x runtime,
same box, `git checkout` rollback, and it produces exactly the operating
experience the brain cutover decision needs. **Honest caveat:** this depends on
the executor *staying* non-production. Flipping `ZOE_EXECUTOR_DISPATCH=full`
against the real board is a separate decision and the hold's spirit reapplies to
*that flip* — which is already gated on ≥3 real tickets.

### 5.7 The two risks that actually dominate — neither is upstream's

- **`labs/flue-executor` has ZERO CI coverage.** Verified: nothing in
  `.github/workflows/`, `tools/audit/` or `.zoe/` references it. `labs/AGENTS.md`
  names `npm run test:unit` as the check, but it is **hand-run only**. A rebuild
  that breaks claim semantics goes red **nowhere** — and those asserts are all
  that stands between a rebuild and silently resurrecting the twelve PRs of
  catalogued failure modes (#592/#597 stranded chains, #520 finish-without-
  shipping, #601 PR-URL handoff, #607/#632 blocking verifiers, #672/#677 flaky
  reviewers, #679/#681 closeout agents claiming success without merging, #685
  zombie workers, #694 no-op implements).
  **Cheap first move:** `src/omnigent.test.ts` is *already* fully hermetic —
  6 tests, no DB, no network, no `npm install`, `node --test` type-strips the
  `.ts`. Adding **that one file** to a CI lane is nearly free and covers the
  shell-injection guard including its behavioural negative control. Splitting the
  e2e into a hermetic tier (claim/CAS/reap over a disposable Postgres) and a live
  tier (Omnigent) is the follow-on unlock. **Do this before the rebuild.**
- **There is no green baseline right now.** The number is stale three times over:
  "33/33" was 2026-07-22; `FINDINGS.md` says 35; **the code today has 40** (the
  extra 5 are scenario 4d, work_dir-defer). And the three live-Omnigent asserts
  were last recorded **RED** on exhausted container credits. *"No regression"* is
  unmeasurable until 40/40 is re-established. The e2e is deliberately **not**
  hermetic — an unreachable Omnigent is *"an honest test failure, not a skip"* —
  which is correct discipline and precisely why it is not already in CI.

---

## 6. Auto-unwedge — bounded retries, then a dead letter

**The gap (A6):** `pipeline_store.resume_pipeline()` (`:278-322`) and
`skip_blocked_implementation()` (`:330`) have **zero production callers**. No
HTTP route, no chat intent, no maintenance script, no CLI. Unwedging a
GATE_BLOCKED chain today means opening a Python REPL against `services/zoe-data`.
The live journal's terminal record is exactly this state, sitting since
2026-07-29.

**How a chain gets there:** `pipeline_store.py:1127-1143` — on a
`complete`/`skip_implementation` outcome, `can_complete_phase()` is false because
required evidence is missing, so `block_reason = "GATE_BLOCKED: missing required
evidence " + missing` (or `"GATE_BLOCKED: validator hash mismatch"`). One bounded
auto-retry already exists, for verify-missing-`test` only
(`ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT`, default 1). Repeats then feed
`record_block_fingerprint` → `fingerprint_abort`, which is terminal.

**The policy:**

1. **Make `resume_pipeline` reachable by something other than a REPL.** A
   maintenance script (`scripts/maintenance/pipeline_resume.py`) is the operator
   path; a bounded caller in the poll loop is the autonomous path. Both journal
   `event="operator_resumed"` with a mandatory reason, exactly as the function
   already does.
2. **Bounded, countable retries.** `ZOE_MULTICA_AUTO_RESUME_LIMIT`, default **2**,
   counted per `(task_ref, phase, block_fingerprint)` — a `MAX_SUMMONS`-style cap.
   A cap you can count is the only reliable termination condition.
3. **Only retryable block classes are auto-resumed.** Missing-evidence blocks are
   retryable (the verify/`test` precedent). `scope_split_required` and
   `fingerprint_abort` are **not** — they go straight to dead letter.
4. **Never auto-clear a fingerprint.** `reset_fingerprint=True` also **strips
   `fingerprint_abort:` records from history** (`:294-306`). Destroying the
   evidence of why a chain kept failing is exactly the wrong autonomous act.
   Fingerprint clearing stays a deliberate operator command.
5. **Dead letter after N.** Board status `blocked` + a `needs-human` label + an
   `activity_log` entry carrying the reason and the full attempt ledger (the live
   example has `implement: 5, scout: 1, verify: 1` — that ledger is the diagnosis
   and must survive). **A dead-lettered ticket is refused by every lane** and is
   never re-admitted by the warden.
6. **The dead-letter count is a health signal.** Rising dead letters mean the
   phase workers are producing work the evidence gates correctly refuse — that is
   the gates doing their job, and it is a reason to stop and look, not to loosen
   the gates.

**Related fix in the same wave (A12):** every board write in the dispatch path is
wrapped in `except … logger.debug(...)` (`main.py:1715-1720`, `:1730-1735`,
`:1790-1791`). A failed `update_issue(status='in_progress')` after a successful
dispatch leaves the board and the chain diverged, visible only at DEBUG. Backlog
ticket **#6115** already exists for exactly this. Autonomy cannot run on a
dispatch path that swallows its own write failures.

---

## 7. Safety rails — the standing invariants

### 7.1 The kill switch

`~/.zoe/multica_dispatch_paused` is a **presence-only** sentinel (contents are
an operator reason string). **It was MISSING on the live box until 2026-08-04**
— the migration doc's §4 non-negotiable ("Multica stays paused until Phase 2 is
proven") and `docs/PLANS.md`'s "kill switch present since 2026-06-18" were both
stale, and `zoe_ground_truth.sh` reported `✓ dispatch armed (no kill switch)`.
The system was inert only *by accident* — four unrelated wedges, not the designed
brake. **It has since been restored** (verified present, 2026-08-04 08:33).

Read by four consumers: the zoe-data poll loop
(`multica_dispatch_control.py:16-17`), the executor live runner
(`config.ts:147-148`), the board runner (`multica_board_runner.py:41-45`), and
`sync_multica_to_kanban.py:45-54`.

**A11 — the two sides read DIFFERENT override variables.** Python reads
`ZOE_MULTICA_DISPATCH_PAUSE_FILE`; the TS runner and the board runner read
`ZOE_MULTICA_KILL_SWITCH`. Setting one relocates half the fleet's sentinel while
the other half keeps checking the default path. Currently harmless (both default
to the same file) and therefore currently invisible — which is precisely the
shape of the bugs this repo keeps finding late.

**Required:** one canonical override name (`ZOE_MULTICA_KILL_SWITCH`), the other
accepted as a deprecated alias, **and a test that asserts all four readers
resolve the same path given the same environment**. Plus a `zoe_ground_truth.sh`
line that reports the resolved path, not just presence.

**The kill switch is removed only as an explicit wave exit gate (Wave 6), and
re-arming it must stay a one-command operation.**

### 7.2 The elicitation prohibition *(new invariant)*

**No unattended lane may use a harness whose `elicitation` capability is anything
other than `none`, and no policy in an unattended lane may use `action: ask`.**

Why it is an invariant and not a preference: `_scaffold.py:425-447` resets the
idle deadline on every non-heartbeat event; `ctx.elicit()` (`:517-549`) emits
**exactly one** event and then awaits a Future; there is no watchdog suspension
anywhere. So an unattended session that hits a permission prompt emits it, parks,
and **dies at 240 s** with `response.failed` and the message *"turn exceeded the
240s harness idle watchdog … likely a wedged LLM or tool call"* — a misleading
diagnosis for "nobody answered". This directly overrides
`_POLICY_EVAL_TIMEOUT_S = 86400.0` (`_scaffold.py:94`), which exists precisely so
a TOOL_CALL ASK can park for a day. **The two constants cannot both hold; the
effective wait is 240 s.**

Consequence, stated once so it is not rediscovered: **any eliciting harness is
broken-by-construction when run unattended.** `claude-sdk` and `pi` report
`elicitation: none`; `acp:oh-my-pi` (`sse-permission`) and `codex` (`jsonrpc`) do
not. Pin this with a test that reads `GET /v1/harnesses` and asserts the warden's
configured harness reports `elicitation: none`.

### 7.3 Positive completion evidence, never a status check

`idle` is Omnigent's **initial** state as well as its terminal one
(`helpers.py:1031`: *"No cache entry and no row value presents as `idle`"*), and
`waiting` is **nonterminal**. There is **no empty-response detector** in the
package: an LLM that returns nothing produces the same signature as a dead kick.
Every unattended consumer must carry positive evidence — a nonce token or
`has_assistant_message` — and must treat **terminal status with no evidence as an
INCIDENT, never a pass**. The two production consumers already do
(`cross_review_poll.py:156-175`; `omnigent.ts:125`); the warden must too.

### 7.4 Caps and bounds

| bound | value | note |
|---|---|---|
| concurrent chains | `ZOE_MULTICA_POLL_DISPATCH_LIMIT = 1` | `_wh_dispatched` is pre-seeded with running chains, so it is a true concurrency cap, not a per-cycle rate |
| tickets judged per run | `ZOE_MULTICA_WARDEN_MAX_TICKETS` | countable; never "until the queue is empty" |
| auto-resume attempts | `ZOE_MULTICA_AUTO_RESUME_LIMIT = 2` | per `(task_ref, phase, fingerprint)`, then dead letter |
| executor dispatch | `ZOE_EXECUTOR_DISPATCH = dry` by default | `full` is a deliberate manual edit |
| autopilots | **`ZOE_MULTICA_AUTOPILOTS` — does not exist yet (A13)** | today `ZOE_MULTICA=true` gates the poll loop, dispatch, reconcile, worktree pruning **and** autopilot registration. **8 `multica_autopilot_*` jobs are registered in APScheduler right now** and fire on schedule regardless of dispatch state. An independent kill is required before autonomy. |
| Omnigent task timeout | 1 h floor, per-task `context.max_runtime` extends it | raised from 10 min after a live ticket was killed mid-healthy-work |

### 7.5 Supply chain (A14) — pin in effect, not just in config

`docker-compose.modules.yml:75,125` pins both Multica images by `sha256:` digest,
but **both live containers run the `:latest` tag on image IDs matching neither
the pin nor the current remote digest** — they were created 2026-06-15 and
predate the pin. So the migration doc's standing hardening item is **half-done:
fixed in config, unapplied in reality**. Any `docker compose pull` on this
profile changes the board software under a system about to be given autonomy.
Both ports also publish on `0.0.0.0`. **Recreate the containers on the pinned
digests before Wave 6**, and consider loopback-binding the published ports.

### 7.6 The board is untrusted input

Ticket text is written by humans, by chat intents, by the proposal bridge and by
other agents. The warden reads it. Therefore: read-only mount, deny-by-default
tool policy, no `gh` credential in that lane, a nonce the ticket body cannot
forge, and a parser that ignores a `RELEVANCE-VERDICT-<other nonce>` block
appearing inside the ticket. Same posture the voice-gate takes toward a PR:
*the input does not get to define its own verdict.*

---

## 8. The Omnigent OAuth dependency

**This is a hard, dated, human-only dependency and the whole autonomous lane
stops when it lapses.**

```
claudeAiOauth.expiresAt             = 2026-08-04T07:04:43Z   (access token, rotates hourly, auto-refreshes)
claudeAiOauth.refreshTokenExpiresAt = 2026-08-18T13:16:22Z   ← THE HARD DEADLINE
claudeAiOauth.subscriptionType      = max
```

**`docs/knowledge/omnigent-cross-review.md` said 2026-08-22. That was wrong by
four days** — corrected in this PR. The reminder belongs at **~2026-08-14**
(four days of slack), not 08-18 and certainly not 08-22.

- Anthropic OAuth for a Max subscription is an **interactive browser login**.
  There is no non-interactive refresh once the refresh token expires.
  **Full autonomy cannot renew this.**
- The credential lives only in the docker volume `omnigent_omnigent-claude` →
  `/root/.claude`. Not on the host filesystem, not in the repo. Losing the volume
  = re-login.
- **The failure mode is silent** (§7.3): an expired token makes the kick die
  before the SDK produces anything; the session stays `idle` with zero assistant
  messages; any consumer checking only `status` reports "clean".
  `cross_review.sh` exits 2 for this. The warden **must** do the same.
- Do **not** "fix" this by baking an `ANTHROPIC_API_KEY` into the container —
  that silently swaps a flat-rate Max lane for a metered one.

**Runbook** (verify → login → confirm → smoke-test the lane end to end, not just
the token):

```bash
# 1. verify the deadline (prints no token material)
docker exec zoe-omnigent python3 -c \
 "import json,datetime;d=json.load(open('/root/.claude/.credentials.json'))['claudeAiOauth'];
  [print(k, datetime.datetime.fromtimestamp(d[k]/1000, datetime.UTC).isoformat())
   for k in ('expiresAt','refreshTokenExpiresAt')]"

# 2. interactive login INSIDE the container (a TTY is mandatory)
docker exec -it zoe-omnigent claude      # then /login, complete in a browser
#   (or: docker exec -it zoe-omnigent claude setup-token)

# 3. confirm refreshTokenExpiresAt moved forward (repeat step 1)
# 4. smoke-test the LANE:  scripts/maintenance/cross_review.sh <draft PR#> "smoke test"
#    exit 0 with a report == healthy.  exit 2 == incident.
```

**Second, separate cliff:** the container's Claude account **usage credits** also
deplete — three live e2e asserts were RED for that reason, not for a code
regression. Credits and OAuth are independent failure modes with the same silent
signature.

---

## 9. WAVES

Each wave states its **entry gate**, its **exit gate**, and whether it is
lab-only or prod-reachable. No wave exits on a claim; every exit gate is
something that can be made to go red.

### Wave 0 — Instruments and a green baseline *(lab only)*

Nothing else in this program is measurable until this holds.

- Add `labs/flue-executor/src/omnigent.test.ts` (6 hermetic tests, no DB, no
  network, no `npm install`) to a CI lane — the cheap first move against the
  zero-CI risk.
- Re-establish **40/40** on the e2e against a scratch Postgres, with the three
  live-Omnigent asserts either green or explicitly quarantined **with the credit
  reason named** (skip ≠ pass).
- Split the e2e into a hermetic tier (claim / CAS / reap) and a live tier.
- `zoe_ground_truth.sh` reports the **resolved** kill-switch path and asserts all
  four readers agree (A11 groundwork).
- Schedule the OAuth reminder for **2026-08-14** (§8).

**Entry:** kill switch armed (satisfied 2026-08-04). **Exit:** a CI lane goes red
when the shell-injection guard is deleted from its call site; 40/40 recorded with
a date and a commit.

### Wave 1 — Unwedge and de-zombie the board *(prod-reachable, small PRs)*

Closes A1, A2, A6, A11, A12, A13.

- **A1** — resolve the engineering-assignee identity (§10 D2) and apply it. Today
  `019ae0a7-…` has **no row in the live `agent` table** and every active issue has
  `assignee_id = NULL`, so dispatch, admission and stale-lane recovery all refuse
  everything.
- **A2** — a NULL-assigned `in_progress` issue is currently an **immortal lane
  holder**: `main.py:1594` counts it for the admission guard, while
  `main.py:411` forbids the recovery path from reclaiming it. Fix the asymmetry
  (the guard must not count what recovery cannot touch), and reset #6116.
- **A6** — §6's unwedge: reachable `resume_pipeline`, bounded auto-resume,
  dead letter.
- **A11** — one canonical kill-switch override name + the four-reader test.
- **A12** — raise swallowed board-mutation failures (ticket #6115).
- **A13** — `ZOE_MULTICA_AUTOPILOTS` as an independent kill.

**Entry:** Wave 0 exit. **Exit:** each proven by a negative control — a
deliberately GATE_BLOCKED chain auto-resumes once and dead-letters on the second
with a reason visible in `multica-web`; a NULL-assigned `in_progress` issue is
reclaimed within `ZOE_MULTICA_STALE_IN_PROGRESS_HOURS`; a forced `update_issue`
failure surfaces at WARNING naming the divergence; setting only the deprecated
env name still moves all four readers.

### Wave 2 — The warden, dark *(lab only)*

Build the Omnigent classifier, the dispatch wrapper, the parser, the `warden`
agent registration, the `:ro` mount and the policies block. Wire it to TT1's
`Classifier` seam. `ZOE_MULTICA_TRIAGE_JUDGE` stays **OFF**. Add the two new
reject codes (`would_break`, `would_regress`, both HOLD).

**Entry:** Wave 0 exit. **Exit:** all five §3.7 negative controls go red when
broken; a policy-denied `Write` under `/relgate` is DENIED **and the turn does
not park**; a dry run over the live 37-ticket backlog produces verdicts written
**nowhere but a report file**; measured cost and wall-clock per ticket recorded
with a date.

### Wave 3 — TT2 + the VETO wire-in *(prod-reachable, flag-gated)*

Apply the computed `DispositionAction` to the board (status per TT1's close map,
plus label and note, `needs_info` → HOLD). Insert the gate call between
`main.py:1601` and `:1609`. **VETO mode:** the human still writes
`dispatch_approved`; the warden can only reject or hold.

**Entry:** Wave 2 exit + operator flips `ZOE_MULTICA_TRIAGE_JUDGE=true`.
**Exit:** ≥20 backlog tickets triaged with the operator reviewing every verdict
and agreeing with the disposition; **zero** tickets promoted while the omnigent
container is stopped mid-run (the fail-closed proof); the manual mass-stale purge
done by hand in June (358 tickets cancelled at once) is reproducible by the gate.

### Wave 4 — Executor 2x rebuild *(lab only)*

`labs/flue-executor-2x/` per §5. Plain-TS orchestration, `@flue/*@2.0.1` pinned
exactly, `start({agents})` explicit registration, no vite build, `labdb.ts` never
`db.ts`, `:3581` health + status. Then: install the unit, run `dry`, then `full`.

**Entry:** Wave 0 (baseline) **and** Wave 1 (a board that can be unwedged).
**Exit:** 40/40 against the 2x build on the same scratch DB; an A/B against the
1.x executor on claim semantics under concurrency; `GET /health` answers with the
DB state as a *field*; the unit starts with no build step; **the 1.x directory is
deleted** only after Wave 5's ≥3 real tickets.

### Wave 5 — Real local phase workers + lane unification *(prod-reachable)*

Closes migration open-gap (a): the synthetic proof worker becomes a real phase
agent. The template is `labs/flue-harness-spike` on 2.x (`harness: true` tool +
`harness.prompt()` + `useSubagent`; `local()` survives but sandboxes are no
longer implicit). Apply the §4 lane decision.

**Entry:** Wave 4 exit. **Exit:** ≥1 light phase completed by the local lane
**with Omnigent down** (the non-negotiable "Omnigent down → the local lane still
runs" property); the lane arbiter writes the lane at promotion time and both
lanes filter on it, **or** the retired lane is deleted; ≥3 real tickets landed
end-to-end through Lane A, ≥1 heavy.

### Wave 6 — AUTHOR mode, the merge button, and the flip *(prod — the actual autonomy)*

- Warden writes the contract block; `ticket_is_dispatch_approved` still lints it
  structurally, unchanged.
- Merge-button policy applied per §10 D4.
- Multica images recreated on their pinned digests (A14); the
  `ZOE_MULTICA_POLL_REF_TIMEOUT_S=300` band-aid reverted to the 60 s default (the
  2 m 07 s → 0.17 s fix landed in #1585; nothing depends on the raised value).
- **The kill switch is removed here and only here**, as an explicit exit gate,
  and re-arming stays one command.

**Entry:** Waves 3 and 5 exit; OAuth renewed with the deadline moved forward;
digest pin in effect.
**Exit:** ≥N consecutive `backlog → merged PR` chains with **zero** human touches
other than the tier-defined merge clicks; every transition carrying a reason
visible in `multica-web`; `zoe_ground_truth.sh` reporting the autonomy state
honestly (armed / paused / dead letters outstanding).

### Feeds and dependencies

| input | state | how it feeds |
|---|---|---|
| **PR #1598** — TT1 triage judge | **merged 2026-08-02**, flag-dark | the judgement half. Waves 2-3 supply its classifier and its TT2. |
| **PR #1616** — `labs/flue-zoe-brain-2x` | **OPEN, "DO NOT MERGE until Phase-2 contract decision"** | the **Flue-2 exemplar**: it typechecks, `vite build`s and passes 191 tests on this box, and it empirically established the real POST body shape. Copy its patterns; **do not depend on it landing.** |
| PR #1582 / #1585 | merged | the five Phase-2 seam fixes and the `validate_structure.py` walk fix. Open gap (a) is Wave 5. |
| ticket **#6115** | in backlog, un-promoted | A12. Its own promotion is a fine first live test of the warden. |
| **currency sweep** | partly done | A14 digest pin **in effect**, the 300 s band-aid reverted, `@flue/*` pinned exactly (never `next`), harness CLI pins held (#1593). |
| `docs/knowledge/omnigent-cross-review.md` | corrected in this PR | the OAuth date was 4 days late. |

---

## 10. DECISIONS FOR JASON

Each is load-bearing, none is safe to default.

**D1 — The two lanes.** Unify on the phase chain (Option 1), unify on the board
runner (Option 2), or keep both made disjoint by admission with an explicit
expiry (Option 3, recommended, converging on Option 1 at Wave 5 exit)?
*Why it cannot wait:* the collision is masked today only because every active
issue has a NULL assignee. Wave 1 fixes that, and the moment it does, both lanes
become eligible for the same ticket. **§4.**

**D2 — The engineering-assignee identity.** The entire phase lane is keyed on one
equality against a **Hermes-era UUID (`019ae0a7-…`) that has no row in the live
`agent` table**, while the executor registered itself as a *different* agent
("Flue Executor", `5a30e703-…`). Options: (a) create the missing `agent` row
under the legacy UUID and keep every anchor unchanged; (b) re-point
`agents_registry.yml` at the executor's own agent id (four code sites read it, all
via one resolver); (c) mint a new `warden`-era identity and migrate. *Whichever
is chosen, the assignment itself must become automatic* — it is one of the four
human steps this program exists to remove. **§9 Wave 1, A1.**

**D3 — Warden model and mode.** (a) Confirm `claude-sdk` on the flat-rate Max
plan as the warden's harness — the recommendation, and the only lane with
`elicitation: none` **and** a proven kick recipe **and** zero marginal token cost
on a high-volume job. (b) Confirm the **VETO → AUTHOR** progression: does the
warden eventually get to write `dispatch_approved` itself (full autonomy, Wave 6),
or does a human keep writing the contract forever and the warden only ever veto?
*The honest framing of AUTHOR mode:* the model supplies judgement and criteria
text; every structural safety property stays in the deterministic linter,
unchanged. But it is still a model writing its own dispatch approval, and that is
Jason's call, not a default. **§3.1, §3.5.**

**D4 — What "full" means for the merge button.** Root `AGENTS.md` says *"the
human merges (or armed auto-merge does) — agents never bypass the gate"*, so
armed auto-merge is already in-policy. Options: (a) human clicks every merge
(status quo — not full autonomy); (b) the chain arms `gh pr merge --squash
--auto` on every PR and the deterministic gate plus
`required_conversation_resolution` decide; (c) **recommended** — armed auto-merge
for the routine tier, human for the load-bearing tier (voice path, auth,
migrations, anything flag-gated). *Residual risk to accept with (b) or (c):*
auto-merge fires the moment the required set is green, and **a required context
that has not yet REPORTED does not hold it** — measured on #1587, where a PR
merged 3 seconds before its review check even started. That property is pinned by
`tests/unit/test_required_gate_workflows.py` and must stay pinned. **§1, A7.**

**D5 — Wave order.** The program as written runs 0 → 1 → 2 → 3 → 4 → 5 → 6, with
Waves 2 and 4 able to run in parallel (both lab-only, disjoint surfaces). Two
legitimate re-orderings: **warden-first** (2/3 before 1) buys backlog hygiene
soonest and needs no executor at all — the gate is useful even with dispatch
paused; **executor-first** (4 before 2) buys the Flue-2 operating experience the
brain cutover decision wants, during the upstream-stability hold. *The
recommendation keeps Wave 0 and Wave 1 first regardless* — instruments before
measurement, and an un-unwedgeable board must not be given more work.

**D6 — Does the executor stay non-production?** §5.6's argument that the ~2-week
Flue-2 hold does not gate the executor rebuild depends entirely on the executor
remaining lab-tier: never auto-enabled, `dry` by default, no deploy hook. Flipping
`ZOE_EXECUTOR_DISPATCH=full` against the real board is a **separate decision** and
the hold's spirit reapplies to that flip. Confirm the flip stays gated on Wave 5's
≥3 real tickets.

---

## 11. Forbidden — for any agent working this program

- **Never remove or relocate the kill switch** except as the explicit Wave 6 exit
  gate. Setting only one of the two override env names is a silent half-move.
- **Never rebuild `kanban_adapter.py` or `executor_queue_backend.py`.** They
  encode twelve PRs of discovered failure modes. Rebuilding means rediscovering
  all of it.
- **Never put executor or board run-state in Flue's store**, and never name the
  app-data module `src/db.ts`.
- **Never use an eliciting harness, or `action: ask`, in an unattended lane.**
- **Never treat `status == "idle"` — or any terminal status without positive
  evidence — as success.**
- **Never gate a promotion or a merge on a model-assigned severity or confidence.**
- **Never auto-clear a `fingerprint_abort`** — it strips the history that explains
  the failure.
- **Never let the warden hold a writable mount or a `gh` credential.**
- **Never quote a Flue 2.x literal from the docs without a test asserting it.**
