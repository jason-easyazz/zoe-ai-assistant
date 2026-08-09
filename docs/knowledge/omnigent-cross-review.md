---
type: knowledge
title: Omnigent cross-review — the in-house advisory review tier
description: How and when to run polly (Omnigent) cross-reviews of PRs; validated 2026-07-27 on live PRs with a scored A/B; replaces Bugbot's seat after it was disabled 2026-07-28 due to cost and front-runs billed Greptile rounds.
---

# Omnigent cross-review (polly) — advisory review tier

Adopted 2026-07-27 after a live trial (operator decision). polly — Omnigent's
claude-sdk agent with the built-in `cross-review` skill — reviews a PR diff with
an independent, different-vendor sub-agent. It is the in-house replacement for
Bugbot's seat (disabled 2026-07-28 due to cost) and the pre-ready advisory tier
that keeps findings from becoming billed Greptile rounds.

## When to run it

- **Pre-ready (default):** run it on the DRAFT PR once the work is complete,
  fix findings, push ONCE, then mark ready. (It reads `gh pr diff`, so the diff
  must be pushed — drafts are free, so push-to-draft costs nothing.) Findings caught here never become billed review
  rounds or blocking threads.
- **Post-open (advisory):** on an open PR while the gate runs. The report stays
  in the Omnigent session — never copied into PR threads.
- **Not** wired into `greptile-gate.yml`, and it must stay that way: Greptile
  remains the sole required reviewer; polly is advisory. Greptile is on
  probation vs polly (log unique catches each way) before any change to that.

## How

```bash
scripts/maintenance/cross_review.sh <PR#> "<2-4 sentence contract>"
```

The script creates a session, kicks it (docker-exec — REST cannot start
claude-sdk sessions), polls to completion, and prints the report. The brief
goes INLINE via `-p`; staging it as a session comment fails silently.

Rules baked into the brief (keep them if you hand-roll):
- **Reviewer, never driver:** no pushes, no thread resolution, no GitHub
  comments, no merges.
- Contract states what the PR must do, what is out of scope, and that new
  tests must be able to FAIL.
- Structured report or explicit CLEAN verdict.

Handling the report:
- Findings are **hypotheses** — verify each with a negative control
  (break → red → restore → green) before adopting.
- Batch all adopted fixes into ONE push (each push triggers a Codex re-review).

## Omnigent ids are BARE hex (0.7.0) — do not re-add a type prefix

Omnigent `<=0.4.0` returned type-prefixed ids (`ag_<hex>`, `conv_<hex>`,
`host_<hex>`); **0.7.0 dropped the prefix** and returns the bare 32-char hex
form for agents, sessions and hosts alike. Read the id from the server
(`GET /v1/agents`, `GET /v1/hosts`) — never hand-write a prefix back on.

The prefixed form still *resolves on input*, but that is a **deprecation shim,
not a contract**: `uuid_to_bytes` (`omnigent/db/db_models.py`) strips any member
of a `_LEGACY_ID_PREFIXES` allowlist so "old bookmarked URLs, pasted ids, and
pre-migration clients keep resolving". It is also **type-blind** — the allowlist
is checked, the *type* is not — so `host_<agent-hex>` binds the agent just fine
(verified live against 0.7.0, alongside a bogus-hex control that correctly 404s).
A prefix therefore validates nothing while riding a path upstream has marked
legacy. The 0.7.0 upgrade already broke on the output side for exactly this
reason; the launcher defaults were de-prefixed so the input side cannot follow.

Validators that touch these ids must accept BOTH shapes and keep the charset
strict — the shell-safety of an id interpolated into a `docker exec … sh -c`
string is the real property, and the prefix never was.

## Worker routing (cost policy, updated 2026-07-30)

The fleet has two flat-rate implementation platforms: `claude_code` on Claude Max
and `codex` on the ChatGPT subscription. That makes cross-vendor review available
without marginal token cost, which is why **cross-vendor diff review is the ROUTINE
semantic gate** in the re-tiered pipeline — the reviewer that actually reads intent,
run on every PR, at no marginal cost.

**Correction (2026-07-30) — Fable is METERED, not the free always-on checker.**
Earlier text here implied `claude-fable-5` rode the flat-rate Max plan alongside
Opus/Sonnet. It does not. **Fable draws on a SEPARATE metered credit pool that can be
exhausted**, and when those credits run out Fable is simply unavailable — a bad property
for anything a routine process depends on.

- **The free, always-on Anthropic-family checker is Opus/Sonnet on the Max plan.** That
  is the default for reviewing and checking, and it is what "the Claude side of the
  cross-vendor pair" means.
- **Reserve Fable for topped-up-credit strong-check moments** — a deliberate deep check
  on genuinely load-bearing work, chosen because the work warrants the spend and the
  credits are known to be available. Never bulk implementation, never the assumed default
  reviewer, never a routine step's dependency.
- If a routine step appears to "need Fable", the routing is wrong: use Opus/Sonnet.

The 2026-07-27 A/B showed why the third platform remains useful but exceptional:
GLM 5.2 produced the strongest OpenRouter result, while no single model caught every
known defect. So `pi` (OpenRouter, pay-per-token) is a **strict tie-breaker** — a genuine
disagreement between two independent reviewers, or a case neither could settle — always
under a hard cost cap, never as a routine third pass.

Bugbot was disabled 2026-07-28 due to cost; this fleet covers its former seat.

**Deterministic caps, not model-assigned severity.** Keep bounded-attempt caps
(`MAX_SUMMONS`-style) on any loop that re-asks a model — a countable cap is the only
reliable termination condition. But never gate on a severity a model assigned to its own
finding: it is not reproducible across runs and it hands merge control to prompt phrasing.
Humans triage severity; machines report findings.

The binding routing, model-tier, cross-vendor, and cost-cap rules live in the root
[`AGENTS.md`](../../AGENTS.md) review-pipeline contract.

## Fail-loudly (the Copilot lesson)

A dead reviewer looks like silence, and silence reads as "clean". The known
failure signature is a session that ends `idle` almost immediately with zero
messages (observed 2026-07-27: a silently-failed comment POST). The script
exits 2 and prints an ALARM for that case — treat exit 2 as an incident, not a
pass. Standing risk: the container's Claude OAuth hard deadline is **2026-08-18**
(verified 2026-08-04 from live credentials metadata; an earlier note said 08-22 —
that misread the ACCESS-token `expiresAt`, which is short-lived and auto-refreshes
continuously; `refreshTokenExpiresAt` is the REAL cliff). When the refresh token
lapses, every kick dies this way. Renewal is an INTERACTIVE browser login — not
automatable: `docker exec -it zoe-omnigent claude` then `/login` (or `claude
setup-token`), re-verify both timestamps via the credentials-metadata one-liner
(see omnigent-deep-dive notes), then an end-to-end
`scripts/maintenance/cross_review.sh <draft PR#> "smoke test"`. The credential
lives ONLY in docker volume `omnigent_omnigent-claude` (/root/.claude) — losing
the volume means re-login. Reminder set for 2026-08-14 (4 days of slack).

## Known limitation — reviewer-only is enforced by PROMPT, not permissions

The brief forbids pushes/comments/merges, but the claude-sdk harness runs with
skipped permission prompts in a container whose `/workspace` is the writable
live checkout with authenticated GitHub credentials (Codex P1, #1578). A
prompt-injected or misbehaving reviewer COULD mutate state. Accepted for now as
an operator-trust tradeoff; hardening path when it matters: a read-only
workspace mount + an unauthenticated `gh` for review sessions. Until then,
treat cross-review output on UNTRUSTED diffs with the same suspicion as any
agent given write access.

## Leaked per-session runners — the memory failure, and the escape hatch

Every dispatch makes `omnigent host` spawn a dedicated runner, launched as
literally `python -m omnigent.runner._entry`. Two properties made it invisible
to every guard we had: **the session id is in its ENVIRONMENT, never its argv**
(`OMNIGENT_PROCESS_LOG_FILE=…/runner-<sid>-<ts>.log`), so the wrapper's cmdline
scan walked past it, and **its parent is the live host daemon**, so orphan
reaping never saw it either. It also leaked worst on the SUCCESS path, where a
completed review sets `REVIEW_DONE=1` and correctly suppresses `stop_worker`.

MEASURED 2026-08-04 during a six-lane merge drive: 19 resident (one per
dispatch), box down to 0–245 MB available, and Omnigent's host daemon then
refused to come online under load (~20:08–21:00), killing two review dispatches
mid-flight and recovering only when the load dropped.

Fixed on both sides:

- **Synchronously**, in `cross_review.sh`: `cleanup` now posts Omnigent's own
  `stop_session` event (`POST /v1/sessions/<id>/events`) unconditionally and
  BEFORE `stop_worker`. The server resolves the runner from the owner-gated
  session row, so it can only ever stop THIS session's runner. Order matters —
  the route forwards the stop to the runner first and raises on a non-2xx, so
  killing the harness first aborts it before the host-runner teardown.
- **As a safety net**, hourly:
  `scripts/maintenance/reap_stale_omnigent_runners.py` (dry-run by default,
  `--execute` to act). It catches the dispatches whose EXIT trap never ran — a
  wrapper SIGKILLed by its caller's 2500s timeout, a wedged host daemon — and
  the launch paths that never use this wrapper at all.

**Operator escape hatch: `docker restart zoe-omnigent`.** It clears every leaked
runner at once (verified: 20 → 1, ~956 MB recovered) but **kills any review in
flight**, so use it only when nothing is dispatched. The reaper is the safe
equivalent and can be run at any time.

## Constraints

- ONE polly worker at a time repo-wide (RAM discipline). The wrapper flock
  serializes only its own invocations — omnigent_issue_executor and the Flue
  heavy lane do not take the lock, so avoid overlapping those manually; a
  shared lease across all launch paths is recorded future work.
- debby (`debate` skill) is for design-level disputes, not diff review.

## Validation record (2026-07-27)

Live trial: 4 real findings across 2 PRs (#1575, #1576), zero noise, zero
boundary violations — including a vacuous-by-construction test assertion that
had survived the author's own negative control, later independently confirmed
by Codex. Model A/B on ground-truth diffs (same brief, three OpenRouter
models): GLM-5.2 best (4 real + 1 novel catch, correct severities);
MiniMax M3 sharpest single catch but failed report discipline (unusable for
automation); DeepSeek V4-Pro disciplined but caught least. Pi's default is
`z-ai/glm-5.2` (rollback: `config.yaml.bak-minimax` in the zoe-omnigent
container). Raw scorecard: session records, 2026-07-27.

No single model caught everything — the union of two vendors covered all known
defects. That is the argument for cross-vendor review in one sentence.
