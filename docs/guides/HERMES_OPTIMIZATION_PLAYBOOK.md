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
  `background_runner.py`, and the executor seam (`executor_registry.py` →
  `executors/kanban_adapter.py`) that dispatches Multica issues to Hermes Kanban.

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
creds and real models. The original outage fix used Sonnet/Gemini/OpenAI API
fallbacks, but the Phase 0.7 cost-control refresh superseded that chain. The
live fallback chain is now intentionally:

```yaml
fallback_providers:
- provider: openrouter
  model: openrouter/free
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

### 0.7 Cost-control routing refresh (2026-06-01)

Routing was tightened to match the board-cost policy:

- Main Hermes (`~/.hermes/config.yaml`) now uses `openai-codex / gpt-5.4` as primary.
- Main fallback is now a single controlled path: `openrouter / openrouter/free`.
- Main fallback entries for direct `gemini` and `openai-api` were removed from the fallback chain.
- Planner profile now uses `openrouter` directly (`anthropic/claude-sonnet-4.6`) instead of `provider: auto`.
- Kanban worker profiles (`zoe-planner`, `zoe-coder`, `zoe-reviewer`) now keep fallback chains OpenRouter-only:
  - `anthropic/claude-sonnet-4.6`
  - `google/gemini-2.5-flash`
  - `openrouter/free`
- Root background auxiliaries were moved away from direct Gemini/OpenAI and `provider: auto` to `provider: openrouter` with `openrouter/free` where they are text-only (`web_extract`, `compression`, title/session/search/triage/curator/approval/MCP helpers, etc.) to avoid background calls silently routing to Codex or paid direct APIs.
- Specialized non-engineering slots such as vision/TTS remain separate and are not part of the Kanban cost-control route.

Verification run after apply:

- `systemctl --user restart hermes-agent.service`
- `hermes doctor` (profiles + connectivity)
- `curl -sf http://127.0.0.1:8642/health`

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

### GEPA offline self-evolution (BUILT — DSPy + GEPA)

Optimizes a skill's `SKILL.md` body via DSPy + GEPA (companion repo
`NousResearch/hermes-agent-self-evolution`). No GPU; ~$0.70 per budget-40 run on
OpenRouter (measured), scaling roughly linearly with `--max-metric-calls`.

Everything lives OUTSIDE the MIT Zoe repo:

- `~/.hermes/self-evolution/` — cloned pipeline + dedicated `.venv` (DSPy 3.2.1).
- `~/.hermes/self-evolution/zoe_evolve.py` — Zoe driver (see below).
- `~/.hermes/self-evolution/zoe_evolve_loop.sh` — round-robin continuous loop.
- `~/.hermes/self-evolution/zoe_evolve_apply.sh` — human-in-the-loop apply step.
- `~/.hermes/scripts/zoe-gepa-loop.sh` — Hermes cron entrypoint.
- `~/.hermes/zoe-skills-lab/` — local (no-remote) git repo, `skills/<name>/SKILL.md`
  staged from `~/.hermes/skills`, giving evolved variants reviewable diffs + rollback.

**Why a Zoe driver instead of `python -m evolution.skills.evolve_skill`:** upstream
(a) calls `dspy.GEPA(max_steps=...)`, which DSPy 3.2.1 rejects (then falls back to
MIPROv2, which needs `optuna`); (b) passes the skill body as an *input field*, so GEPA
only mutates a wrapper signature and `optimized.skill_text` returns the original
unchanged (the "ghost mutation" bug); and (c) never wires the strong model into GEPA.
`zoe_evolve.py` reuses upstream's dataset builder / LLM-judge / constraints but makes
the skill body the *optimizable instruction* (real evolution), uses the strong model as
GEPA's `reflection_lm` and the cheap model for the task + judge, and is **synthetic-only**
(no `sessiondb`/PII path). One local patch: `dataset_builder.py` parses with
`strict=False` to tolerate control chars in model JSON.

Run one skill manually:

```bash
cd ~/.hermes/self-evolution
export OPENROUTER_API_KEY=$(grep '^OPENROUTER_API_KEY=' ~/.hermes/.env | cut -d= -f2-)
.venv/bin/python zoe_evolve.py --skill github-greptile-loop --max-metric-calls 80 \
  --optimizer-model openrouter/anthropic/claude-sonnet-4.6 \
  --eval-model openrouter/google/gemini-2.5-flash
# Review:  diff output/<skill>/<ts>/baseline_skill.md output/<skill>/<ts>/evolved_skill.md
# Apply (only if better + constraints pass):  ./zoe_evolve_apply.sh <skill> <ts>
```

**Caveat — the eval is a proxy with a verbosity bias.** GEPA scores how well an LLM judge
thinks a model *following the skill text* answered synthetic tasks. It optimizes
instructional clarity, not real tool-use success, and the judge rewards longer/more
complete answers — so GEPA reliably pads skills. The +20% growth gate is therefore
essential, not cosmetic. Measured first runs (budget 80, ~$0.85 each):

- `github-greptile-loop`: baseline 0.954 → 0.930 (−0.024 raw), +72% growth → **rejected**.
- `zoe-engineering`: baseline 0.779 → 0.970 (+0.191 raw) but +79% growth → **rejected**
  (including a subtly wrong “put every endpoint in chat.py” rule).

Both correctly produced no merge. Treat positive holdout delta AND passing constraints as
*necessary, not sufficient* — a human reviews every diff.

**Density-adjusted metric (wired in `zoe_evolve.py`).** GEPA now optimizes
`quality × (baseline_chars / current_chars)` instead of raw judge score alone, and
feeds GEPA reflection text when the skill body grows. Holdout reports both raw and
density scores; the weekly loop queues on **density** improvement only. Example on the
`zoe-engineering` run above: raw +0.191 would have become density **−0.258** — GEPA would
not treat bloat as a win.

**Eval data / privacy.** Default and only path in the driver is `--eval-source synthetic`.
Upstream's `sessiondb` mode would mine `~/.claude/history.jsonl`,
`~/.copilot/session-state/*/events.jsonl`, `~/.hermes/sessions/*.json`; its scrubber
catches secrets but not general PII. Keep it OFF until an explicit PII review — it is not
reachable through `zoe_evolve.py`.

**Continuous loop (weekly, PAUSED until you enable it).** Registered as Hermes cron
`weekly-zoe-gepa-skill-evolution` (`0 4 * * 1`, `--no-agent`). Each tick syncs one
round-robin skill from live, runs a capped synthetic evolution, and — only if holdout
improved and constraints pass — appends a candidate to
`~/.hermes/self-evolution/REVIEW_QUEUE.md`. It NEVER auto-applies. Guards: single-flight
`flock`, monthly OpenRouter cap (`ZOE_GEPA_MONTHLY_CAP_USD`, default $30), distinct hour
from the hourly issue-fix cron.

```bash
hermes cron resume f4957eb425b9   # turn the weekly loop ON
hermes cron pause  f4957eb425b9   # turn it OFF
```

**Pin critical skills** so the Curator can patch but never archive them:

```
hermes curator pin zoe-engineering   # and github-greptile-loop, zoe-graphify,
                                      # agentic-engineering-workflow, zoe-board
```

**Darwinian Evolver (deferred, do NOT integrate).** Upstream's optional Phase-4 code
engine (`imbue-ai/darwinian_evolver`) is **AGPL-3.0** and Zoe is **MIT**. Never import,
vendor, or link it into the Zoe tree or a distributed Zoe image. If ever used for code
evolution, run it strictly as an isolated external CLI (separate process/container), keep
only its outputs, and never expose it as a networked user-facing service.

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
  completes on the controlled fallback (`openrouter/free`).
- `zoe-watchdog.sh` reports hermes-agent UP (no false "not responding" push);
  `hermes-keepwarm.sh` returns HTTP 200.
- Hourly cron tick completes on a healthy provider (no `HTTP 400 credit balance too low`).
- Phase A: after `POST /api/system/agent-sync`, `~/.hermes/SOUL.md` contains a
  populated `ZOE_WHY_BEGIN/END` block; the bootstrap prompt makes Hermes recite
  the North Star + scopes + universality rules.
- Zoe side healthy: `/health`, `/api/system/status`; and
  `python3 -m pytest services/zoe-data/tests/test_agent_sync.py -q` passes.
