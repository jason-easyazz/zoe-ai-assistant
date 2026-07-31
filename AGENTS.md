# Command Center — READ THESE FIRST (every agent: Claude Code, Codex, Omnigent, Hermes)

- **[docs/VISION.md](docs/VISION.md)** — Zoe's north star, direction & core principles (the *why*). Read this first; every decision aligns to it (the rocks; local/private/fast; lab-prove-before-prod; build-to-stick; capture-don't-lose).
- **[docs/CANONICAL.md](docs/CANONICAL.md)** — the **locked-in truth**: what's actually live, and what's settled (the rocks: Gemma 4 E4B-QAT+MTP brain, Moonshine STT, Kokoro TTS). If a system isn't listed canonical there, it's **not load-bearing** — don't extend or resurrect it. Swapping a rock fails CI (`test_canonical_invariants.py`) by design.
- **[docs/PLANS.md](docs/PLANS.md)** — what we're building + status. Before starting work, check it; after finishing a step, update it.
- **[docs/IDEAS.md](docs/IDEAS.md)** — the pin-it-so-we-don't-lose-it board. When Jason says *"pin this / put a pin in it / remember this idea"*, add a one-line entry there. Never drop an idea on the floor.

**Tool discipline (these get skipped — don't skip them):** use **Serena** + **codebase-memory** (MCP, see `.mcp.json`) for code navigation/edits over raw grep; **opensrc** (`opensrc path …`) for third-party source before guessing; the **Greptile loop** to drive PRs to merge; follow the **DOX** doc conventions below. Detail for each is in the sections that follow.

<!-- start-of-task-checklist -->
## Start-of-task checklist (non-optional)

Before any code task, you MUST — do not fall back to raw grep/guessing:

- **On the live Zoe host, read LIVE reality before trusting any doc's claim about what is live — run `scripts/maintenance/zoe_ground_truth.sh` (read-only; ~1s healthy, bounded on failure).** Not a gate on *every* code task, and not for CI / off-host / clean-container environments where there is nothing live to read (it needs the box's systemd, Docker, service ports, `/proc`, and `~/assistant` — off-host it simply reports those as absent, which is noise, not a blocker). Reach for it whenever you are about to *reason about or assert what is running* — brain lane, flags, scheduled jobs, whether a service a doc calls "paused" is actually up. codebase-memory/Serena/grep answer *what EXISTS*; they cannot tell you *what RUNS*, and a static doc physically cannot track a flag flip or a test restart. Every wrong conclusion in the 2026-07-20 session lived in that gap (a paused-doc Hermes that was live; a board assumed "inside Hermes" that is its own product; a flag read from its default; a job that never registered). **What exists ≠ what runs — verify execution, not presence** (`[[feedback_verify_your_instruments]]`).
- **Navigate + edit code via the MCP bus, not raw grep/Read/Edit.** Use **codebase-memory** for who-calls-what / architecture / seams and **Serena** for symbol read + symbolic edits. Reach for `grep`/`Read` only when the bus genuinely can't answer. If codebase-memory `list_projects` comes back empty, run `index_repository(repo_path=/home/zoe/assistant, mode="moderate")` before proceeding — an empty graph silently unmeets this mandate and agents fall back to grep.
- **Use `opensrc` for any third-party API before guessing** — `opensrc path pypi:<pkg>@<version>` (pin the installed version). Never invent library behaviour from memory.
- **Drive every PR to merge with the Greptile loop** — reply to + RESOLVE each Greptile thread, follow up until it actually merges; squash only, never `--admin`/`--force`.
- **Follow the DOX `AGENTS.md` chain** — read the root plus every nested `AGENTS.md` on the path to files you touch, and do the closeout DOX pass after editing (see *DOX framework* below).
- **Honour the rocks.** Gemma 4 E4B+MTP (brain) and Moonshine v2 Medium (STT) are fixed — optimise *around* them, never propose swapping (see `docs/VISION.md` principle 1). **Retire by removing** (git keeps history); don't hoard `_old`/`_v2`/archive copies.
- **Replay-gate every voice change (MANDATORY).** Any change to the voice path (STT / brain / TTS — `services/zoe-data/routers/voice_tts.py`, `zoe_core_client.py`, `fast_tiers.py`, the brain/Kokoro config) MUST be replay-gated against Jason's real-voice corpus `~/.zoe-voice-samples` before merge/deploy: said-vs-did must not regress (a previously-working command that now fails = a bug) and per-stage speed must not regress. Use the voice regression + speed harness — `scripts/maintenance/voice_regression_probe.py` (baseline-compared) and the underlying `scripts/perf/measure_voice.py` / `measure_tts.py` — always under `flock /tmp/zoe-voice-harness.lock` (two Kokoro loads ~2.3 GB each will OOM the box). The harness is warm + stops before TTS, so its numbers are RELATIVE (drift vs baseline), not live performance. Full tool doc: `docs/knowledge/voice-pipeline.md`.

---

## Codebase navigation — codebase-memory + Serena (graphify retired)

graphify is **retired**: there is no committed `graphify-out/` graph and no `graphify query`/`path`/`explain` workflow. Do not resurrect it.

- **codebase-memory** (MCP) — who-calls-what, architecture, seams, cross-module "how does X relate to Y".
- **Serena** (MCP) — symbol read + symbolic edits.

Reach for raw `grep`/`Read` only when the MCP bus genuinely can't answer.

### Serena is ONE shared server — read via Serena, EDIT in your own worktree

`.mcp.json` points every agent at a **single long-lived** Serena server
(`http://127.0.0.1:9121/mcp`, unit `scripts/setup/systemd/serena-mcp.service`).
It is not spawned per agent. Two consequences are load-bearing:

- **It is pinned to `--project /home/zoe/assistant` — the LIVE checkout.** So
  **navigation/read is correct** (agents branch off fresh `origin/main`, so the
  live checkout on `main` *is* their baseline), but **symbolic EDITS would
  target the live checkout, not your worktree.** Do your Read/Edit **in your own
  worktree** with the normal file tools. Two agents hit this on 2026-07-16 via
  absolute paths. This pinning is not new — the old stdio config had the same
  `--project` — but one shared server makes it fleet-wide instead of incidental.
- **Serena serialises the fleet.** All tool calls run through one
  `SerenaAgent` TaskExecutor queue (one worker, strict FIFO), so concurrent
  calls queue rather than interleave, and every agent waits for the whole queue
  to drain. Measured: 6 concurrent cold calls = 6.15s wall for 4.53s of work.
  That tax is deliberate — 6 separate servers (up to 2G each) do not fit in
  15.6G and OOMed the live voice brain on 2026-07-16. Keep Serena calls
  purposeful; it is a shared single-lane resource, not free parallelism.

If code-intel tools vanish, the server is down — Claude Code does **not**
auto-start a URL-based MCP server. Check
`scripts/maintenance/serena_mcp_health.sh`.

## opensrc

Use `opensrc` for third-party library source before guessing API behavior.

Rules:
- Keep the global source cache outside the repo at `~/.opensrc/repos/`.
- Prefer `opensrc path pypi:<package>@<version>` or `opensrc path owner/repo` to locate already-fetched source.
- Pin the version to the one Zoe actually installs (for example `opensrc path pypi:chromadb@0.6.3`). A bare package name resolves to the latest published version (e.g. bare `chromadb` is 1.5.9), which can be a major-version mismatch from the running stack and will mislead you.
- Search package source directly when debugging integrations, for example:
  `rg "class FastAPI" "$(opensrc path pypi:fastapi)"`
- When source context informs an implementation, report the package files, examples, or tests that were used.
- Do not vendor opensrc cache contents into Zoe.
- Be cautious with brand-new dependencies; avoid adopting packages less than about 14 days old unless the user explicitly accepts the risk.

Currently useful cached sources include FastAPI, ChromaDB, LiveKit, faster-whisper, MCP/FastMCP, APScheduler, pyannote-audio, and AG-UI.

## code structure cleanup

Build the smallest working feature first, then run a cleanup pass before review.

Use the cleanup pass to remove duplicated runtime mechanics: repeated provider calls, parsing, validation, command execution, payload transforms, or business logic. Keep product/domain policy in routes, actions, intents, and UI handlers. Move only reusable mechanics into service-layer helpers.

Service helpers should be small capability blocks with explicit parameters, structured returns, consistent failure semantics, and no hidden global state or unrelated database mutation.

Do not refactor the whole app as cleanup. Do not create `_new`, `_fixed`, `_v2`, `_old`, backup, or duplicate router files.

## Review pipeline — deterministic gate, semantic review, advisory SaaS

**Re-tiered 2026-07-31** (4-vendor review consensus + this repo's own incident history).
The principle: **what BLOCKS a merge must be deterministic, locally runnable, and
reproducible on demand.** Everything judgement-shaped — an LLM reading a diff — is
advisory. It is still where most real defects are caught, and it is still mandatory
process; it just does not hold the merge button.

Why the change: the required path used to depend on a non-deterministic SaaS reviewer
and on bot reviewers whose availability we do not control. That produced (a) a
repo-wide Copilot outage on 2026-07-27 that deadlocked **every** open PR including the
one carrying the fix, (b) a gate controller of ~573 lines of inline JavaScript whose
only CI coverage was actionlint and whose `REQUIRED_CHECKS = []` made its central
condition vacuously true, and (c) an 11-round fail-open cascade in review. A gate you
cannot run locally is a gate you cannot debug when it is the thing that is broken.

### Tier 1 — the REQUIRED gate (deterministic, blocking)

Exactly three contexts block a merge. Each is reproducible on a laptop:

| context | what it proves | run it locally |
|---|---|---|
| `validate` | structure + critical-file validators, offline-memory policy, Alembic migrations, **actionlint with a negative control**, `py_compile` over every zoe-data module, the `ci_safe` unit lanes + full zoe-auth suite | `python3 tools/audit/validate_structure.py`; `pytest services/zoe-data/tests -m ci_safe` |
| `secret-scan` | ggshield over **branch history**, not just the head tree | `ggshield secret scan ci` |
| `voice-gate` | a voice-path diff has a fresh, passing replay-gate artifact **produced by exercising this PR's head commit** | `python3 scripts/maintenance/voice_gate_check.py --scope-only --diff origin/main...HEAD` |

Plus `required_conversation_resolution` (every thread resolved) and `strict` (up to date
with `main`). Keep this set MINIMAL — every addition is a new way to freeze `main`.

**`voice-gate` is new and closes a real gap.** The replay gate used to run post-merge
only, fail-closed, so a voice-path PR could go green, merge, and then be permanently
refused by the deploy gate: a *green main that will not deploy*, discovered after the
fact. It now runs at PR time too. It is safe as a universal required context because it
**always reports a conclusion**: PRs touching no voice-path file pass trivially and never
involve the Jetson; only a voice-path diff escalates to the self-hosted assertion. The
post-merge deploy check STAYS — defence in depth.

Two properties make it trustworthy rather than merely present:

- **The evidence is BOUND to the PR head.** Freshness plus `status: pass` does not say
  *which code* was tested — a passing run against `main` would otherwise clear every voice
  PR for the whole 24h window. The probe now records the commit it exercised and the gate
  passes `--expect-revision <PR head sha>`, so an artifact for any other commit (or from a
  dirty worktree, which cannot be attributed to a commit at all) is refused. Practical
  consequence, and it is the correct fail-closed cost: a voice-path PR needs the probe run
  against a **checkout of that PR's head**, and re-run after every push to it.
- **The PR is DATA, never code — and it does not get to define its own gate.** A pull
  request is untrusted input, and the evidence job runs on the Jetson, the box that also
  runs the live voice brain. Two rules hold the line. (1) Every checkout pins `base.sha`;
  the PR enters only as an API-supplied changed-file list plus a 40-hex head sha. Running
  the PR's own copy of the checker allowed both a **bypass** (edit it to report no voice
  files) and **arbitrary code execution on the Jetson**. (2) The workflow triggers on
  **`pull_request_target`**, so its *definition* comes from the base ref — on a plain
  `pull_request` trigger GitHub runs the workflow file from the PR head, and the PR could
  simply delete the fork gate or make the verdict `exit 0`, authoring the very check that
  gates it. Because a `pull_request_target` run is associated with the base commit, the
  `verdict` job publishes the `voice-gate` context explicitly against the PR head. Fork PRs
  cannot reach the self-hosted runner without a maintainer applying `voice-gate-approved`.
  A base-owned guard in the `verdict` job also fails the gate if a PR adds any OTHER
  workflow that would publish a `voice-gate` check — branch protection matches a required
  context by NAME and cannot authenticate its producer, and a competing producer takes
  effect PRE-merge, on the PR's own head.
  The full trust model and its residual risk are at the top of
  `.github/workflows/voice-gate.yml` and in
  [merge-and-deploy.md](docs/knowledge/merge-and-deploy.md); if you edit that workflow, the
  question is not "is this safe?" but "does this read, write, or execute anything the PR
  author controls?"

**Coverage is EVIDENCE, not a gate.** Report it, read it, do not auto-block on a
threshold; a coverage number is trivially satisfiable without testing anything real.

### Tier 2 — cross-vendor diff review (the routine SEMANTIC gate, flat-rate)

`scripts/maintenance/cross_review.sh <PR#> "<contract>"` — the default pre-ready step on
the draft PR, and the reviewer that actually reads intent. **Flat-rate, so it is the
routine path, not the exceptional one.** Findings are hypotheses: verify each with a
negative control, batch every fix into one push. Advisory to the machine, mandatory to
the process — a PR should not be marked ready until it has had one.

Binding worker routing:

- **`claude_code` (Claude Max, flat-rate):** primary implementer. Sonnet-tier for routine
  work, Opus-tier for hard or multi-file work. **Opus/Sonnet on the Max plan is the free,
  always-on Anthropic-family checker** — reach for these by default.
- **`codex` (ChatGPT subscription, flat-rate):** second implementer for narrow changes,
  and primary independent reviewer of Claude-implemented work.
- **Fable (`claude-fable-5`) — METERED, NOT the free always-on checker.** *(correction,
  2026-07-31: earlier notes implied Fable rode the flat-rate Max plan.)* Fable draws on a
  **separate metered credit pool that can be exhausted**, and when it is exhausted it is
  simply unavailable. Reserve it for **topped-up-credit strong-check moments** — a
  deliberate deep check on genuinely load-bearing work — never for bulk implementation and
  never as the assumed default reviewer. If a routine step "needs Fable", the routing is
  wrong: use Opus/Sonnet.
- **Cross-vendor both ways:** the reviewer's platform must differ from the implementer's.
  Claude work → `codex`. Codex work → `claude_code` (Opus/Sonnet by default; Fable only
  when credits are topped up and the work warrants it).
- **`pi` (OpenRouter, pay-per-token) — STRICT tie-breaker only.** Primarily GLM 5.2, for a
  genuine disagreement between two independent reviewers or a case neither could settle.
  **Every dispatch carries a hard cost cap.** Never routine, never a third routine pass.
- **Cursor Bugbot:** disabled 2026-07-28 due to cost; this tier covers its former seat.
  Fleet rationale and history: [docs/knowledge/omnigent-cross-review.md](docs/knowledge/omnigent-cross-review.md).

**Deterministic round caps, and no AI-assigned severity as a gate input.** Keep the
`MAX_SUMMONS`-style bounded-attempt pattern everywhere a loop asks a model to try again —
a cap you can count is the only reliable termination condition. But do **NOT** gate on a
severity label a model assigned to its own finding (`P1`/`blocking`/`critical`): that is
the model grading its own homework, it is not reproducible across runs, and it hands
merge control to whichever prompt phrasing was in play. Humans triage severity; machines
report findings.

### Tier 3 — Greptile: ADVISORY, on demand for high-risk changes

Greptile was a REQUIRED status check from 2026-07-27 until **2026-07-31, when it was demoted
to advisory** — a non-deterministic third-party service must not be able to freeze `main`. It is
still genuinely good at whole-repo context, so keep it for high-risk work — voice path,
auth, migrations, anything flag-gated — and skip it for routine changes.

`.github/workflows/greptile-gate.yml` is now a **cost controller, not a gate**: Greptile
is dashboard-filtered to PRs carrying the `greptile` label, and the workflow applies that
label only once a PR is settled (up to date, no unresolved threads), then summons once.
That ordering is still load-bearing — Greptile dedups by PR diff, so a review on an early
head followed by a `strict` branch update means it correctly refuses to re-review and the
spend bought nothing. The workflow holds `checks: read`, never `checks: write`; it cannot
publish a required context, and a test asserts that.

### Tier 4 — Copilot: optional, never blocking

`gh pr edit <n> --add-reviewer @copilot` (that syntax; the bot login does NOT resolve).
~$10/mo flat. Its reviews are always `COMMENTED`, so it can never block a merge — and it
is no longer WAITED FOR by anything. The bounded-grace summon machinery that used to wait
for it is **deleted**: it existed to sequence a required Greptile behind Copilot, and with
Greptile advisory there is nothing to sequence. Copilot's inline comments still create
review threads that count toward `required_conversation_resolution`, so they must be
resolved like any other.

### Sequence

1. **Draft PR.** Invisible to Greptile (`triggerOnDrafts: false`), so all iteration is free.
2. **Cross-vendor review** (Tier 2) — the routine semantic gate.
3. **Local `/review` (Cursor) — free.** `.cursor/BUGBOT.md` carries the repo's guide for
   this IDE-side command. Agents cannot run it; it is the operator's step.
4. **Copilot** (optional).
5. **Batch the fixes.** Collect every finding, fix once, push once. Fix-push-fix-push
   multiplies reviews AND multiplies the chance a fix introduces a new bug — exactly what
   happened on #1560.
6. **Mark ready.** The deterministic gate must be green and every thread resolved. Add the
   `greptile` label for high-risk work if you want the advisory pass.

### THE GUARANTEE — every merge is up-to-date AND deterministically verified at that commit

| setting | value | guarantees |
|---|---|---|
| branch protection `strict` | **true** | the PR is up to date with `main` |
| `validate` + `secret-scan` + `voice-gate` required | **yes** | the merged commit is deterministically verified |
| `required_conversation_resolution` | **true** | no finding is merged unaddressed |
| `triggerOnDrafts` | **false** | iteration in draft stays free |
| Greptile required | **no** (advisory) | a SaaS outage cannot freeze `main` |

The guarantee is now carried by checks we own and can run locally, so a vendor outage
degrades review quality instead of halting the repo.

### Owner break-glass — `main` can never be unrecoverably frozen

A required context that stops reporting blocks every PR **forever**; GitHub has no
timeout. With `enforce_admins: true` and a solo owner there is no in-band escape, and on
2026-07-27 that is exactly what happened. So:

- Apply the **`break-glass`** label to a PR (or run the `break-glass` workflow with a PR
  number and a reason). `.github/workflows/break-glass.yml` verifies the actor is a repo
  **admin** via the API (`write` is not enough), force-publishes only the required contexts
  that are **not already green**, posts a permanent audit comment naming actor / head /
  reason / each forced context and its real state, and then **removes the label** — the
  override is single-use and applies to that head commit only.
- **It overrides ANY non-green required context, including a genuinely FAILING one.** It
  cannot distinguish "never reported because the vendor is down" from "ran and said no".
  `secret-scan` is covered, so it **can override a real secret-scan failure**. That is a
  deliberate audited owner-emergency capability, not an oversight — excluding `secret-scan`
  would recreate the permanent freeze whenever GitGuardian is what is down. The audit
  comment records each context's real state, so a forced `failure` is distinguishable from
  a forced `missing` forever.
- It does **not** bypass `required_conversation_resolution`. Unresolved threads still
  block, deliberately: an outage cannot make a thread unresolvable. It also cannot
  force-push, merge, or change branch protection — it only publishes check runs.
- **The intended use is an OUTAGE.** Using it on a genuinely red check is a decision to
  merge known-bad code, and the audit trail makes that permanently attributable.
- Normal enforcement is unchanged. Still never `--admin` / `--force`.

### Tier by risk

Four reviewers on a one-file docs change is friction, not safety.

- **Routine** (docs, config, generated files, tests, UI) → deterministic gate + cross-vendor
  review. Skip Greptile.
- **Load-bearing** (voice path, auth, migrations, anything flag-gated) → the full chain,
  Greptile included as the advisory whole-repo pass.

Cross-review applies to BOTH tiers — it is the free default, not a load-bearing-only extra.

### Cost

Measured 2026-07: **400+ reviews across 112 PRs (3.6× per PR)** in one month — Greptile
~$380/mo, Bugbot $430 before it was disabled, Copilot $10 flat. The multiplier, not the
PR count, was the cost, and it came from **concurrency**: `strict` cascading across ~8
simultaneously open PRs, where every merge updated the other seven and each update billed
a review. The fixes are draft-first and **serialising PRs** — one or two in flight. Making
Greptile advisory removes the rest: an advisory reviewer you invoke for high-risk work
only is billed a handful of times a month rather than on every head of every PR.

(Historical note, kept because it explains the `triggerOnUpdates` setting: turning it off
was tried on 2026-07-26 and reverted the same day — `strict` forces a branch update, and
Greptile then correctly skips the unchanged diff, leaving the merged commit unreviewed
with the check permanently absent. That failure mode is now moot for MERGE purposes, since
Greptile no longer gates, but leave the setting alone: an advisory review of a stale head
is still a wasted one.)

## One workstream, one PR — combine before review, not after

Reviews are billed **per PR and per push**, and every reviewer re-runs on every update. So
splitting one piece of work across several PRs multiplies the cost, and two PRs over the same
files pay twice and then conflict with each other. Combine first.

- **One branch per WORKSTREAM, not per change.** Several commits on `feature/<slug>`, one PR,
  one review — rather than three PRs that each touch the same module.
- **A draft PR is a parked PR, and parking is free.** Greptile is `triggerOnDrafts: false`, so
  a draft can stay open for days at zero cost. Open early as a draft, iterate, and mark ready
  only when the work is genuinely finished — marking ready is the act of spending the review.
- **Before opening a PR, check for an open one over the same files.** If it exists, add to that
  branch or wait for it to land. `pr-hygiene.yml` posts an overlap notice automatically, but
  the cheaper move is not creating the second PR at all.
- **A merge queue is NOT AVAILABLE to this repo** (resolved 2026-07-27): GitHub gates merge
  queues to organization-owned repositories and this repo is personal — no setting we
  control changes that. The churn answer remains serialisation (below). The useful piece
  shipped anyway: the required secret check is now the first-party `secret-scan` job, so a
  GitGuardian App outage can no longer freeze `main`. Analysis, org-transfer trade-offs and
  rollback: [`docs/knowledge/merge-queue-switch.md`](docs/knowledge/merge-queue-switch.md).
- **Serialise.** Keep one or two PRs in flight. With `strict: true` every merge pushes the
  others behind, and each branch update triggers a fresh review — the measured 3.6 reviews/PR
  in July was this, not oversized changes.

**Size** (`pr-hygiene.yml`) — thresholds come from the RESEARCH, not from our habits.
Defect detection is ~87% at 1-100 changed lines, ~65% at 301-600, ~28% at 1000+
(SmartBear/Cisco ~2500 PRs; Google): 200 lines is the target, 400 the ceiling. Our own
distribution (40 merged PRs, 2026-07) is median 246 / p75 401 / p90 653 / max 975 — so the
median is healthy but the top quartile is already in the degraded band, and the warning is
meant to fire there:
- warn **at or above 10 files / 400 lines**, fail **at or above 30 files / 1000 lines**
  (inclusive: 1000 lines IS the limit, not one line under it)
- generated files (flag inventory, vendored `dist/lib/`, lockfiles, wheels) are excluded — they
  move in bulk and say nothing about review burden
- `oversized-ok` label overrides a genuine exception
- the hard limit exists because **Greptile silently skips PRs over ~50 files** — past that you
  get no review at all while still paying for it, which is worse than a blocked PR

## PR loop

For reviewable development work:
- Work from feature branches and open pull requests; `main` is protected.
- Do not bypass branch protection or use administrator merges unless the operator explicitly asks for that emergency path. The **`break-glass` label** is the sanctioned, audited escape for a required-check OUTAGE — see the Review pipeline section.
- Keep PRs small; use `/split-to-prs` when a branch grows too large.
- **The deterministic gate is the merge condition** (`validate`, `secret-scan`, `voice-gate`); cross-vendor review is the routine semantic pass; Greptile is advisory and on-demand for high-risk work.
- For Zoe engineering tasks, prefer `scripts/maintenance/greploop_guard.py --packet-only` or `--once` before broad expensive-agent repair.
- Cheap models must receive one generated fix packet for one finding or CI failure; never hand them the whole PR.
- Use Cursor's Greptile MCP to fetch review status/comments.
- Use the `github-greptile-loop` Hermes skill to delegate heavier fix/re-review loops.
- Do not treat any AI reviewer as a replacement for local Zoe verification; run focused tests and live health checks before marking work merge-ready.

Merge mechanics & gotchas — canonical record: **[docs/knowledge/merge-and-deploy.md](docs/knowledge/merge-and-deploy.md)** (read it before driving any PR to merge). The load-bearing rules:
- A green check **≠ resolved threads**. `required_conversation_resolution` is the gate that enforces "5/5, every comment sorted" — mark every thread resolved (GraphQL `resolveReviewThread`), don't just reply. The `break-glass` override does NOT bypass this.
- **Arm auto-merge** (`gh pr merge <n> --squash --auto`) instead of merging by hand. `strict` drains a batch **serially** — nudge one PR per merge. **Auto-merge fires the moment the required set is green, and a required context that has not yet REPORTED does not hold it**: measured on #1587, a PR merged 3 seconds before its review check even started (which then concluded `failure`, on code already on `main`). This is why every required context must always report a conclusion — see `voice-gate`'s always-reports design and `tests/unit/test_required_gate_workflows.py`.
- **New tests reach CI by marker, not enumeration, on the main lanes.** `services/zoe-data/tests` + repo-root `tests/unit` are marker-based (co-located `pytestmark = pytest.mark.ci_safe`, registered in `pytest.ini`) and `services/zoe-auth/tests` runs full-directory — all three are enumeration-free, and hand-listing files there silently drops new tests (the failure this rule used to cause). Do NOT edit `validate.yml` for these lanes. Only the remaining explicitly-enumerated lanes need a YAML entry — confirm the file actually runs in its CI job. SSOT: [tests/AGENTS.md](tests/AGENTS.md) + [docs/knowledge/merge-and-deploy.md](docs/knowledge/merge-and-deploy.md).
- GitGuardian scans **branch history**: a leaked/test cred in an intermediate commit fails even with a clean head tree. Scrub via a clean re-branch (squash to one commit on a new branch, replacement PR) — force-push is blocked by design.
- Never `--admin`/`--force`; squash-only; the **human merges** (or armed auto-merge does) — agents never bypass the gate.

Local pre-commit — a tracked `.pre-commit-config.yaml` at repo root runs the repo's own `tools/audit/validate_structure.py` + `validate_critical_files.py` plus standard hygiene hooks. Run `pre-commit install` once per clone to arm it. `validate_structure.py` treats any root file not in `.zoe/manifest.json` `approved_root_files` as an orphan and fails — register new root files there.

## Cursor MCP

The tracked Cursor MCP config intentionally includes only non-secret local servers. `zoe-tools` launches the operator-local helper at `/home/zoe/bin/zoe-tools-mcp.py` through `uv run --with fastmcp --with httpx`; provision that helper on Zoe hosts before relying on the repo-local MCP entry. Keep token-backed servers such as Greptile in user-global Cursor config or environment-backed local config, never in tracked repo files.

## Hermes-First Delegation

Hermes is Zoe's default engineering and browser agent. Use it for planning, code review, implementation repair, architecture analysis, Greptile loops, codebase-memory/Serena-guided codebase work, Multica board repair, generated knowledge refresh, and browser work through Zoe's CloakBrowser tools.

Local Zoe Hermes engineering skills live under `~/.hermes/skills`, including `zoe-engineering`, `agentic-engineering-workflow`, `source-code-context`, `code-structure-cleanup`, `github-greptile-loop`, `grep-loop-review-workflow`, and `zoe-status-refresh`. They are operator-level Hermes skills, not user-facing Zoe runtime skills under `skills/`.

The `agentic-engineering-workflow` and `grep-loop-review-workflow` names are kept as compatibility entrypoints for the Micky-style workflow pack, but they map onto Zoe's Hermes-first codebase-memory/opensrc/Greptile process rather than introducing a second parallel system.

OpenClaw is **being fully retired** (operator decision 2026-07-22, recorded in `docs/architecture/multica-executor-migration.md` §5): do not route ANY work to it, do not extend it, and its runtime + builder intents are to be deleted via gated PRs (its 31 workspace skills never executed once — verified; backups live in `docs/knowledge/operator-skills/`). Capabilities are rebuilt on Pi/Flue when actually needed, referencing the public Agent-Skills ecosystem. Zoe's Multica-first engineering driver owns workflow state and phase advancement; execution moves to the Flue executor + Omnigent per the migration plan.

## Branching policy

Trunk-based development off protected `main`. No permanent `develop` or `staging` branch.

- One branch per issue or Multica task, created from fresh `origin/main`.
- Naming: `codex/<slug>` (agent work), `feature/<slug>`, `fix/<slug>`, `verify/<slug>` (throwaway validation).
- Use a dedicated git worktree under `~/.worktrees/<slug>` for development; do not switch the live checkout (`/home/zoe/assistant`) to feature branches for agent work.
- Branches die at merge: remote branches auto-delete (`delete_branch_on_merge`); local task worktrees are reclaimed automatically — see below.
- Automatic cleanup (Multica owns its worktrees):
  - On chain completion, the harness removes each task's worktree + `wt/<id>` branch once merged (`worktree_bootstrap.remove_task_worktree`, called from `kanban_adapter`).
  - A daily safety-net sweep in the Multica poll loop reclaims orphaned merged worktrees (`worktree_bootstrap.prune_merged_worktrees`). Interval via `ZOE_WORKTREE_PRUNE_INTERVAL_S` (default 86400).
  - Both detect **squash-merged** branches via `gh pr view` (not just git-ancestor merges) and never touch dirty, locked, unmerged, or the live checkout.
- Manual prune still available: `scripts/maintenance/prune_worktrees.sh` (dry-run first, `--execute` after operator review).

## Skill & extension safety

Third-party skills and extensions run with the agent's privileges. Treat them as untrusted code.

- Before installing any third-party skill, Pi/OpenClaw extension, or code-bearing MCP server into a Zoe agent runtime — and before promoting a self-authored skill from the lab to a live agent — scan it: `skillspector scan <dir|file|git-url>` (installed at `~/.local/bin/skillspector`).
- The static stage needs no credentials but is deliberately conservative: it flags legitimately powerful, process-spawning extensions as HIGH/CRITICAL. Do not treat the raw static score as a verdict — use the optional LLM stage (`SKILLSPECTOR_PROVIDER=...`) plus human judgement for promotion decisions.
- Do not egress internal Zoe skill content to an external LLM provider for scanning without operator consent; prefer static scans, or a local/NV provider, for internal skills.
- Record the scan outcome (or a deliberate waiver) when adopting a new skill or extension.

# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Knowledge vs. Records (OKF)

DOX governs two kinds of document; do not conflate them.

- **Contracts** — `AGENTS.md` files. Prescriptive, binding, prose. They change only through the deliberate DOX pass below — never by an autonomous loop.
- **Records / knowledge** — curated facts, schemas, learned insights, and durable reference (e.g. memory exports, tool/topology notes). Write these as **Open Knowledge Format (OKF)** bundles: a directory of markdown files with YAML frontmatter (required `type`), an `index.md` per directory, and cross-links via relative markdown links.
- Register every OKF bundle in the nearest owning AGENTS.md's Child DOX Index so the DOX walk discovers it. An OKF bundle stays inside DOX governance; it is not a parallel system.
- The autonomous memory/knowledge loop may freely create, update, and lint OKF records. It must never edit an AGENTS.md contract — contract changes go through the DOX pass and human review.

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists
- A subtree that owns an autonomous loop or agent MUST state a Forbidden list (what the agent must never do, e.g. paths/actions out of scope). It is the most load-bearing part of the contract; omit it only when nothing is autonomous in the subtree

Default section order:
- Purpose
- Ownership
- Local Contracts
- Forbidden
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

## Child DOX Index

- [services/AGENTS.md](services/AGENTS.md) — runtime services: zoe-data production web/chat API, zoe-ui static UI + nginx, zoe-auth, MCP bridges, LiveKit
- [skills/AGENTS.md](skills/AGENTS.md) — Zoe skill definitions (SKILL.md dirs); documentation only — NOT wired to runtime discovery, which reads `~/.openclaw/workspace/skills` + `~/.hermes/skills`
- [tools/AGENTS.md](tools/AGENTS.md) — audit, cleanup, validation, and generator utilities (structure/critical-file validators live here)
- [scripts/AGENTS.md](scripts/AGENTS.md) — setup, maintenance, deployment, and utility scripts, including systemd unit templates
- [tests/AGENTS.md](tests/AGENTS.md) — unit, integration, performance, e2e, and voice test suites
- [docs/AGENTS.md](docs/AGENTS.md) — categorized documentation; governance charter is normative
- [modules/AGENTS.md](modules/AGENTS.md) — optional add-on modules served under /modules/
- [config/AGENTS.md](config/AGENTS.md) — deployment configuration and key material locations (values never documented)
- [labs/AGENTS.md](labs/AGENTS.md) — lab-only experiments & spikes, isolated from the runtime (e.g. the Flue harness substrate spike)

Not indexed (runtime/data/generated, no durable editing contracts): `backups/`, `checkpoints/`, `data/`, `models/`, `ssl/`, `homeassistant/` (live Home Assistant runtime), `demos/`.
