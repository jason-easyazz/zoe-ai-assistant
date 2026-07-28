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

## Worker routing (cost policy, 2026-07-28)

The fleet has two flat-rate implementation platforms: `claude_code` on Claude Max
and `codex` on the ChatGPT subscription. That makes cross-vendor review available
without marginal token cost. Claude Fable is also included in the Max plan and is
useful for deep checking, while `pi` reaches OpenRouter for a metered third opinion.
Bugbot was disabled 2026-07-28 due to cost, so this fleet covers its former seat.

The 2026-07-27 A/B showed why the third platform remains useful but exceptional:
GLM 5.2 produced the strongest OpenRouter result, while no single model caught every
known defect. Flat-rate workers therefore handle the routine path and metered Pi is
reserved for cases where another independent opinion is worth the spend.

The binding routing, model-tier, cross-vendor, and cost-cap rules live in the root
[`AGENTS.md`](../../AGENTS.md) review-pipeline contract.

## Fail-loudly (the Copilot lesson)

A dead reviewer looks like silence, and silence reads as "clean". The known
failure signature is a session that ends `idle` almost immediately with zero
messages (observed 2026-07-27: a silently-failed comment POST). The script
exits 2 and prints an ALARM for that case — treat exit 2 as an incident, not a
pass. Standing risk: the container's Claude OAuth expires **2026-08-22**; when
it does, every kick will die this way.

## Known limitation — reviewer-only is enforced by PROMPT, not permissions

The brief forbids pushes/comments/merges, but the claude-sdk harness runs with
skipped permission prompts in a container whose `/workspace` is the writable
live checkout with authenticated GitHub credentials (Codex P1, #1578). A
prompt-injected or misbehaving reviewer COULD mutate state. Accepted for now as
an operator-trust tradeoff; hardening path when it matters: a read-only
workspace mount + an unauthenticated `gh` for review sessions. Until then,
treat cross-review output on UNTRUSTED diffs with the same suspicion as any
agent given write access.

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
