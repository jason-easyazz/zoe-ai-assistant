# Hermes Optimization Playbook

Turn the Hermes gateway into a cost-efficient, self-improving, multi-model
engineering brain for Zoe — and give it Zoe's *why*, not just the port map.

This is the durable record of the staged plan plus the fixes already applied. It
covers the live failover/health fix (Phase 0), the mission-knowledge pipeline
(Phase A), and the remaining routing / self-improvement / hourly-loop / local
phases as ready-to-run prompts.

> Hermes config lives **outside** the repo at `~/.hermes/` (`config.yaml`,
> `.env`, `auth.json`, `cron/jobs.json`, `SOUL.md`, `skills/`). Apply config
> edits there; only the `agent_sync.py` builder and this doc are tracked in the
> repo.

## What's actually running

- Hermes Agent v0.15.1 gateway on `127.0.0.1:8642`.
- Primary model `gpt-5.4` via `openai-codex` OAuth (ChatGPT Plus quota; resets
  ~daily). Healthy pooled creds: `openai-api` (real `OPENAI_API_KEY`), `gemini`
  (real `GEMINI_API_KEY`), a real manual `openrouter` key, and `copilot`.
- Hourly engineering loop = Hermes built-in cron `hourly-zoe-issue-fix-greptile-merge`
  in `~/.hermes/cron/jobs.json` (every 60m).
- Zoe integrates via `services/zoe-data/routers/chat.py` (`escalate_to_hermes`),
  `background_runner.py`, and `engineering_workflow.py`.

## The bigger why — the411 / Nudge

Zoe is **component #16 (the AI Assistant)** of **the411.life** ("the Living Map
of Social Life"), with **Nudge / nudge.life** (venue check-in + social prompts)
as the go-to-market wedge. We are **actively building toward the411**: Zoe stays
Zoe (her identity, privacy model, and charter are primary and unchanged), and
the411 **extends** her with more abilities, knowledge, and connection. Hermes
should weight the411/Nudge alignment when designing work — but where a change
would answer a charter open question, it must say so, not decide silently.

(Naming note: "nudge" is also an internal term — Zoe proactive push nudges,
Hermes memory nudges. The product is the dominant meaning here.)

---

## Phase 0 — Failover + health (DONE — was the live outage)

Symptom: "the console doesn't fall over to other API keys." Root cause was a
dead fallback chain plus unauthenticated health probes. Five fixes, all applied:

### 0.1 Rebuilt the fallback chain with healthy providers

`~/.hermes/config.yaml` now uses an ordered chain of providers that have working
creds and real models (dropped `openrouter/free` and exhausted Anthropic):

```yaml
fallback_providers:
# Codex (primary) emits encrypted reasoning blobs in history; OpenAI Chat
# Completions rejects them ("Encrypted content is not supported"), so the
# proven-compatible OpenRouter/Sonnet hop goes first. gemini next (cheap),
# openai-api last (only succeeds when history did not originate from Codex).
- provider: openrouter
  model: anthropic/claude-sonnet-4.6
- provider: gemini
  model: gemini-2.5-flash
- provider: openai-api
  model: gpt-4.1
```

`model.default` stays `openai-codex / gpt-5.4` (strongest; resets ~daily) — the
chain carries it during exhaustion.

> **Key finding — provider names + encrypted content.** The Hermes provider id
> for the direct OpenAI key is `openai-api` (matching the credential-pool key),
> **not** `openai` (`openai` logs `unknown provider`). And when failing over
> *from* Codex, message history carries Codex's encrypted reasoning blobs;
> OpenAI Chat Completions rejects them with `HTTP 400: Encrypted content is not
> supported with this model`, while OpenRouter→Sonnet 4.6 accepts them. That is
> why Sonnet-via-OpenRouter is ordered first and `openai-api/gpt-4.1` is the
> last-resort entry.

### 0.2 De-placeholdered the OpenRouter key

`OPENROUTER_API_KEY` and `CUSTOM_API_KEY` in `~/.hermes/.env` were `local-llama`.
Both now hold the real `sk-or-v1-...` key (the one already in the `auth.json`
pool), so OpenRouter auth is deterministic.

### 0.3 Provider rotation (note)

`credential_pool_strategies` is left empty. Failover is fast in practice (Codex
429 → Sonnet 4.6 in ~5s) because Hermes records `last_error_reset_at` for the
exhausted Codex credential. Health-aware skip-before-retry is a future nicety,
not required for the outage fix.

### 0.4 Re-pointed the hourly cron off exhausted Anthropic

`~/.hermes/cron/jobs.json` hourly job changed from
`provider: anthropic / model: claude-sonnet-4-6` (exhausted) to
`provider: openrouter / model: anthropic/claude-sonnet-4.6` — the **same model
the operator chose**, via a working credential path.

### 0.5 Fixed health-probe + keep-warm auth (stopped the false "down")

The gateway enforces `API_SERVER_KEY` on `/v1/*` regardless of
`GATEWAY_ALLOW_ALL_USERS`. Both scripts now send the bearer key, sourced from
`~/.hermes/.env` (never hardcoded):

- `/home/zoe/bin/zoe-watchdog.sh` — `check_service` takes an optional auth
  header; the hermes-agent check passes `Authorization: Bearer $HERMES_API_KEY`.
- `/home/zoe/bin/hermes-keepwarm.sh` — adds the same header to the POST.

### 0.6 Self-audit prompt (read-only)

```
You are Zoe's staff engineer running as the Hermes gateway. Audit your own setup,
change nothing. Run `hermes doctor`. Read ~/.hermes/config.yaml, auth.json, and
~/.hermes/cron/jobs.json. Report per provider: healthy vs exhausted (+reset time),
the active fallback chain in order, delegation.model, max_turns. Then force-simulate
the primary being down and tell me exactly which provider+model you would land on
and whether it can run the engineering loop. Output any remaining config diff and WAIT.
```

Restart after edits: `systemctl --user restart hermes-agent.service`.

---

## Phase A — Give Hermes the why (DONE)

An agent that knows *why* Zoe exists makes better engineering calls than one that
only knows the port map. Source of truth: `docs/governance/ZOE_DESIGN_PRINCIPLES.md`.

Implemented durably (not a hand-edit):

- `services/zoe-data/agent_sync.py` gained `_build_zoe_why()` and a generalized
  `_patch_hermes_soul(..., marker=...)`. `run_agent_sync()` now patches a
  `ZOE_WHY_BEGIN/END` block into `~/.hermes/SOUL.md` alongside the existing
  `ZOE_SELF` block, so it regenerates on every `POST /api/system/agent-sync` and
  survives the dreaming-cycle sync instead of being clobbered.
- The `zoe-engineering` skill (`~/.hermes/skills/zoe-engineering/SKILL.md`) gained
  a **Mission Alignment** section: read the relevant charter section + the
  graphify map at task start; honor the universality + harness rules; favor
  the411-aligned designs.
- `ZOE_WHY` is kept lean (~2k chars, capped at `_MAX_ZOE_WHY_CHARS`) per charter
  S9 (minimize structure), not the full charter pasted in.

What `ZOE_WHY` teaches: the Samantha North Star; one instance / many users /
"who is speaking now"; the three memory scopes (`personal` / `shared` /
`ambient`, never unscoped); trust envelopes (proposal path for world-changing
actions); the universality hard rules (no home/family/household in the kernel,
per-user/scope creds, allow-list tools, skills as files, non-Jetson portability);
harness-first engineering; and the the411/Nudge product context.

Bootstrap prompt to validate Hermes absorbed it:

```
Read /home/zoe/assistant/docs/governance/ZOE_DESIGN_PRINCIPLES.md and your own
SOUL.md. In 6 bullets, tell me Zoe's North Star, the three memory scopes, the
trust-envelope rule, the universality rules you must never violate, and the
harness-first engineering bias. Then list 3 ways these change how you'd implement
a typical Zoe feature. Change nothing.
```

---

## Phase 1 — Multi-model routing (strong planner + cheap coder)

The "Anthropic plans, cheaper model codes" pattern maps onto Hermes natively via
`delegation.model` (subagents do bulk edits) while `model.default` stays a strong
planner. No custom orchestration code.

Set in `~/.hermes/config.yaml`:
- `model.default` = strong planner (Claude Sonnet/Opus or GPT-5.4).
- `delegation.model` = cheap coder (an OpenRouter coding model / DeepSeek /
  GPT-mini). Currently empty (subagents inherit the expensive default).
- Keep `auxiliary.web_extract/compression/flush_memories` on the mini model.

Activation prompt:

```
Configure yourself for cost-efficient engineering: use the main model only for
planning and review, and delegate all code execution to subagents on the cheaper
delegation model. Set delegation.model accordingly, keep orchestrator_enabled.
Prove it on a trivial task: read one file, propose a one-line change, delegate the
edit to a subagent, report which model ran each step and the cost split.
```

Optional stronger isolation: `hermes profile create zoe-planner --clone` /
`zoe-coder --clone`, each with its own `SOUL.md` and model. Start with
`delegation.model` (simpler) before profiles.

## Phase 2 — Make it self-improving

Pin critical skills so the Curator can patch but never archive them:

```
Pin my Zoe-critical skills so the Curator can patch but never archive them:
run `hermes curator pin zoe-engineering`, and the same for github-greptile-loop,
zoe-graphify, agentic-engineering-workflow, zoe-board.
```

Standing instruction for skill capture (add to `~/.hermes/SOUL.md` or the cron
mission):

```
After any Zoe engineering task of 5+ tool calls, or where you hit and solved a
dead end, use skill_manage to capture the working procedure as a SKILL.md
(prefer `patch` over full rewrite). Keep domain policy in routes/intents, reusable
mechanics in skills.
```

GEPA offline self-evolution (~$2-10/run, no GPU; companion repo
`NousResearch/hermes-agent-self-evolution`):

```
Set up the GEPA offline self-evolution pipeline to optimize my zoe-engineering and
github-greptile-loop skills using my real session history in ~/.hermes/state.db.
Generate an eval set, run the optimizer, apply the constraint gates, and open the
best variant as a PR against the skill - never a direct commit.
```

## Phase 3 — Optimize the hourly loop

- **Dedupe**: the Hermes cron and Zoe's Multica board-review autopilot
  (`services/zoe-data/multica_autopilot_sync.py`) can both dispatch engineering
  work on the same issues. Pick one owner (recommend the Hermes cron) or add a
  claim/lock.
- Align the cron `model`/`provider` override with a healthy provider (done in
  Phase 0.4 → `openrouter / anthropic/claude-sonnet-4.6`).
- Tighten the mission prompt: one issue per tick, small PR, run validators +
  Greptile loop, merge only when green, update Multica, report to Telegram.

## Phase 4 — Path to fully-local (future hardware)

Hermes is model-agnostic via an OpenAI-compatible layer, and Zoe already runs a
local `llama-server` with an OpenAI-compatible endpoint at
`http://127.0.0.1:11434/v1` (verified 200 on `/v1/models`). When local models are
good enough, migrate in two reversible steps — **cheap coder first, planner last**.

### Step 1 — localize the cheap coder (`delegation.model`)

Subagents do the bulk edits and are the easiest, lowest-risk thing to localize.
In `~/.hermes/config.yaml`:

```yaml
delegation:
  model: <local-model-name-as-served-by-llama-server>
  provider: openai-api          # OpenAI-compatible shim
  base_url: http://127.0.0.1:11434/v1
  api_key: ''                   # llama-server ignores it; any non-empty placeholder if required
```

Keep `model.default` on the strong cloud planner. Run the Phase 1 activation
prompt and confirm in `~/.hermes/logs/agent.log` that subagent calls hit
`base_url=http://127.0.0.1:11434/v1`. If tool-calling quality holds, proceed.

### Step 2 — localize the planner (`model.default`)

Only after the local coder is proven:

```yaml
model:
  provider: openai-api
  default: <local-model-name>
  context_length: <local context window>
fallback_providers:
  - provider: openrouter        # keep a strong cloud model as the safety net
    model: anthropic/claude-sonnet-4.6
```

### Reversibility

Both steps are pure config edits — revert by restoring the previous
`model`/`delegation` blocks and `systemctl --user restart hermes-agent.service`.
`hermes model` (interactive picker) can also re-select a provider/default model
and supports `--refresh` to re-fetch each provider's live `/v1/models`. Keep the
strong cloud model in `fallback_providers` throughout so a local-model regression
never causes a hard outage — the harness portability (charter S9) is what makes
this swap low-risk.

---

## Best practices from the wild

Synthesized from the NousResearch Hermes docs, the Avi Chawla "Hermes
Masterclass", and the DigitalOcean deploy guide — mapped to Zoe:

- **Strong planner + cheap executor**: orchestrate on a strong model, push code
  execution to a cheaper model via `delegation.model`. Hermes supports this
  natively — no custom orchestration.
- **Profiles for true isolation**: `hermes profile create zoe-coder --clone`
  gives a fully separate config/SOUL/skills/model. Use later if
  `delegation.model` isn't enough; start simple.
- **Self-improving skills loop**: let Hermes author SKILL.md files after
  multi-step tasks; pin the Zoe-critical ones; let the Curator GC the rest. The
  skill library is the durable "tackle anything" asset.
- **GEPA offline evolution**: optimize skills from real execution traces rather
  than the agent's self-grade (it over-rates itself). PR-only, ~$2-10/run.
- **Cron chaining with `context_from`**: one job's output feeds the next (e.g. a
  "scan Multica for issues" job feeds a "fix one issue" job) — cleaner than one
  monolithic hourly prompt.
- **Keep the harness portable** (charter S9): the durable asset is harness +
  skills + tool surface, not one provider's weights — which is exactly what makes
  the Phase 4 local-model swap low-risk.

## Verification checklist

- `hermes doctor` clean; `systemctl --user status hermes-agent.service` active.
- Auth'd probe: `curl -s -H "Authorization: Bearer $HERMES_API_KEY" 127.0.0.1:8642/v1/models` → 200.
- **Failover proof**: with Codex exhausted, a `/v1/chat/completions` call
  completes on a healthy fallback (Sonnet 4.6 via OpenRouter), not `openrouter/free`.
- `zoe-watchdog.sh` reports hermes-agent UP (no false "not responding" push);
  `hermes-keepwarm.sh` returns HTTP 200.
- Hourly cron tick completes on a healthy provider (no `HTTP 400 credit balance too low`).
- Phase A: after `POST /api/system/agent-sync`, `~/.hermes/SOUL.md` contains a
  populated `ZOE_WHY_BEGIN/END` block; the bootstrap prompt makes Hermes recite
  the North Star + scopes + universality rules.
- Zoe side healthy: `/health`, `/api/system/status`; and
  `python3 -m pytest services/zoe-data/tests/test_agent_sync.py -q` passes.
