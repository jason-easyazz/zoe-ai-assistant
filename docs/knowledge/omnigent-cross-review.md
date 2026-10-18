---
type: knowledge
title: Omnigent cross-review — the in-house advisory review tier
description: How and when to run cross-vendor PR reviews (polly via Omnigent, with automatic direct codex / lean pi fallback); validated 2026-07-27 on live PRs with a scored A/B; replaces Bugbot's seat after it was disabled 2026-07-28 due to cost. Includes the polly Claude-auth root cause and the owner runbook for the fix.
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

`cross_review.sh` is a dispatcher with **automatic fallback** (2026-10-18). It tries, in
order, and stops at the first cross-vendor report:

1. **polly** — `cross_review_polly.sh` (the original wrapper, lifecycle unchanged): creates a
   session, kicks it (docker-exec — REST cannot start claude-sdk sessions), polls to
   completion, prints the report. The brief goes INLINE via `-p`; staging it as a session
   comment fails silently.
2. **codex, directly** — `docker exec -i zoe-omnigent codex exec -s read-only -C /workspace -`
   with the same contract and review prompt on stdin (codex and pi exist ONLY inside the
   container on this host; auth is the `omnigent-codex` volume). Used when polly/Omnigent
   cannot produce a report: runner start failure, Claude OAuth refresh contention, auth
   error, timeout, or a report that is not provably cross-vendor. No `--model`: the
   ChatGPT-account Codex rejects invented ids (`gpt-5-codex`, `gpt-5.1-codex` -> HTTP 400;
   polly's brief now says to omit `args.model` for the codex worker too).
3. **pi, one lean pass** — only when codex is unavailable (quota, auth, crash, timeout):
   `pi --provider openrouter --model z-ai/glm-5.2 --no-tools --no-session`, diff on stdin.

If all three fail it exits **2** with `ALARM: cross-review unavailable` and each backend's
reason on stderr — a caller can never mistake that for a clean review. There is deliberately
no Claude backend: a same-vendor review is never substituted. A polly report is accepted
only if it carries a `REVIEWER-VENDOR:` line naming a non-Anthropic worker and not the
`CROSS-VENDOR-REVIEW-UNAVAILABLE` sentinel (the polly brief demands both).

stdout is the report (direct backends prefix a `REVIEWER-VENDOR:` line); backend choice and
failure reasons go to stderr. Knobs: `CROSS_REVIEW_BACKENDS="polly codex pi"` (subset/order),
`CROSS_REVIEW_{POLLY,CODEX,PI}_TIMEOUT_S`, `CROSS_REVIEW_{CODEX,PI}_DIFF_MAX_BYTES`. Phase
timeouts are clamped to what is left of the caller's 2500 s subprocess timeout; a fallback
with under 120 s left is skipped, not started. Tests (stubbed polly/codex/pi/gh):
`tests/unit/test_cross_review_fallback.py`.

**The pi cap is deterministic, not a budget it can exhaust.** An Omnigent-dispatched pi
worker hit its $1.50 cap on a 3366-line diff and returned nothing. The direct pass is one
tool-less completion (`--no-tools`), the diff is capped at 200 KB (~50k tokens, cents), and
the run is bounded by a timeout. pi has no summary-on-budget-exhaustion flag, so instead
the prompt demands findings in priority order, a truncated diff is labelled
`PARTIAL COVERAGE`, and a timed-out run keeps what it streamed, labelled `PARTIAL`.

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
pass. The standing risk is the container's Claude credential: see **Polly Claude auth**
below. `cross_review.sh` now absorbs that failure by falling back to codex/pi, but the
fallback is a workaround; fixing polly's auth is the actual repair.

## Polly Claude auth — root cause and the fix (2026-10-18)

**Symptom:** polly sessions die in ~20 s with `Failed to refresh OAuth token: another Claude
Code process is refreshing it or exited mid-refresh` (kick log `/tmp/zoe-cross-review-<sid>.log`
in the container). 26 auth-failure items in the week to 2026-10-17, in bursts: 10-15
11:32-11:57, 10-16 00:25-01:26, 10-17 03:26-03:27 (container clock is UTC).

**Root cause (evidence: `chat.db` timelines, the container, and the installed Claude Code
2.1.287 binary).** The container authenticates with a *stored* `/login` credential in
`/root/.claude/.credentials.json` (volume `omnigent_omnigent-claude`): an access token that
expires every ~8 h plus a rotating refresh token. Every Claude process that finds the access
token expired must refresh it; Claude Code serialises that with a lock, and the loser raises
the contention error above (message text and error class are in the binary). Three things
feed it:

- **Concurrency.** One review is several Claude processes (polly's SDK brain plus CLI
  children), and the busy windows above had up to 5 overlapping sessions.
- **Killed mid-refresh.** `cross_review.sh` cleanup `kill -9`s a stuck worker, and
  `docker restart` or the 2500 s caller timeout kill runners outright. A process that dies
  holding the refresh lock is exactly the "or exited mid-refresh" branch.
- **It is not only a race.** The 10-17 03:26 and 03:27 failures happened with NO other
  session active (previous activity was 9 h earlier, 10-16 18:25): the first processes after
  an idle gap failed twice in a row, a minute apart, so "retry in a minute" does not reliably
  clear it. Not verified: which lock artifact survives (none is present now), so a stale lock
  versus a rejected rotated refresh token is not distinguished. Both sit on the refresh path.

Every fix below works by taking the refresh path out of the picture.

| | (a) `claude setup-token` OAuth token | (b) dedicated Console API key |
|---|---|---|
| mechanism | `CLAUDE_CODE_OAUTH_TOKEN` env: one-year, used as-is, **never refreshed**; precedence #5, above a stored `/login` (#7) per the Claude Code authentication docs | Console key, never refreshed, billed per token |
| refresh race | gone for *every* Claude process in the container (SDK brain and `claude_code` workers) | gone for whatever uses it |
| cost | flat (Max plan), but still draws the shared weekly Max limit | metered against the $200/mo Console credits, with its own spend limit |
| policy | Agent SDK on subscription credentials is the "wrong side" of Anthropic policy for the SDK brain (`omnigent-container-config.md`, policy note) | the sanctioned auth for the SDK harness |
| plumbing | survives omnigent's host-to-runner forward by default (`HARNESS_CREDENTIAL_ENV_VARS`) | **not plain `ANTHROPIC_API_KEY`**: the claude-sdk executor strips that name before spawning the CLI, and in the container env it would flip every native worker to metered billing and risk the custom-key approval menu. The supported route is omnigent's `auth: {type: api_key, api_key: $VAR}`, which becomes an `apiKeyHelper` |
| tested live | not yet (needs the owner's browser login) | not yet (needs the owner's key); code paths read from omnigent 0.7.0 source |

**Recommendation: both, split by lane.**

- **(a) for the container as a whole.** It removes the refresh race for polly's Claude CLI
  children and the `claude_code` builder workers (the Multica executor lane) at no marginal
  cost.
- **(b) for polly's claude-sdk brain**, under a Console spend limit. Policy-clean, and it
  takes polly's orchestration off the Max weekly limit entirely.
- **Cost check for (b)** (an estimate, not a measurement). The week of 2026-10-11 had **86
  real polly reviews** (109 kicks; 53 on 10-14 alone, the night-100 PR drive). At roughly
  $0.5-2.5 per orchestration session (API rates, ~25 turns over 30-80k tokens of mostly
  cached context) that is about **$45-215/week**: a heavy week can eat the whole $200/mo.
  Set the Console limit just under the credits ($190), read the first week's usage page, and
  if polly's average is above ~$1 pin a cheaper brain model (`ANTHROPIC_MODEL` is
  container-wide today; changing it is an owner decision, not made here). The codex and pi
  reviewers do the heavy reading and are not billed to this key.

### Owner steps (exact; no secret enters the repo, the compose file or a log)

The token and key live in an env file **outside the repo**, read by compose `env_file`
(`required: false`; path overridable with `OMNIGENT_CLAUDE_ENV_FILE`).

```bash
# 1. Create the file empty and locked down. You paste values into it; nothing is echoed.
install -d -m 700 ~/.config/zoe-omnigent
install -m 600 /dev/null ~/.config/zoe-omnigent/claude.env

# 2a. (a) Mint the long-lived token (interactive browser login; it prints once).
docker exec -it zoe-omnigent claude setup-token
#     Add this line with an editor, NOT echo (echo lands in shell history):
#       CLAUDE_CODE_OAUTH_TOKEN=<token>
$EDITOR ~/.config/zoe-omnigent/claude.env

# 2b. (b) In the Anthropic Console create a NEW key "zoe-omnigent-polly" in its own
#     Workspace; in that Workspace's limits set a monthly spend limit of $190.
#     Add this line the same way:
#       ZOE_OMNIGENT_ANTHROPIC_API_KEY=<key>
$EDITOR ~/.config/zoe-omnigent/claude.env
ls -l ~/.config/zoe-omnigent/claude.env      # must read -rw------- zoe zoe

# 3. Restart. OWNER step: it kills any review in flight, so do it between PRs.
#    Use the README bring-up verbatim (same -p project and --env-file as always).
cd ~/assistant/modules/omnigent && docker compose --env-file ../../.env \
  -f docker-compose.module.yml up -d

# 4. Verify (a). None of these print a secret.
docker exec zoe-omnigent claude auth status | grep -E 'authMethod|loggedIn'
docker exec zoe-omnigent claude -p "reply with the single word ok"
docker exec zoe-omnigent sh -c 'test -n "$CLAUDE_CODE_OAUTH_TOKEN" && echo token-present'

# 5. Switch (b) on for polly's brain. No restart; applies to NEW sessions.
python3 ~/assistant/scripts/maintenance/omnigent_claude_auth_mode.py status
python3 ~/assistant/scripts/maintenance/omnigent_claude_auth_mode.py api
#    Rollback at any time:
python3 ~/assistant/scripts/maintenance/omnigent_claude_auth_mode.py subscription

# 6. End-to-end smoke test on a draft PR.
scripts/maintenance/cross_review.sh <draft PR#> "smoke test"   # stderr: "reviewed via polly"
```

Done when step 4 passes, step 6 says `reviewed via polly` (not a fallback), and the Console
usage page shows a few cents for the smoke test. If step 6 falls back, read the
`cross-review: polly unavailable` line and
`docker exec zoe-omnigent tail -40 /tmp/zoe-cross-review-<sid>.log`.

**Rollback:** delete the two lines from the env file and `up -d` again, and run the
auth-mode script with `subscription`. The stored `/login` credential is untouched in the
volume (`claude auth login` renews it if it lapses).

**Never** put `ANTHROPIC_API_KEY` in the compose file or the env file: it would flip every
Claude CLI in the container, `claude_code` workers included, to metered billing.

### Subscription weekly limit: what exists and what does not

Claude Code has **no automatic fallback** from a Max limit to an API key or credits. Its
docs say the session or weekly limit blocks requests until the reset time, `/usage-credits`
is a manual purchase, and no env var or setting provides a fallback. So the design is by lane:

| lane | what hits the Max limit | protection |
|---|---|---|
| cross-review reviewer | nothing: reviewers are codex or pi | `cross_review.sh` falls back automatically; no Claude involved |
| polly's orchestration brain | the SDK brain | (b) moves it off the Max plan; `omnigent_claude_auth_mode.py` flips it either way for new sessions |
| `claude_code` builder workers (Multica executor lane) | builds on the Max plan | **not solved here.** A running session cannot change credential mid-job, and the native harness's API-key route (tmux TUI plus the custom-key approval menu) is unverified without a restart. Interim: the executor retries after the reset. Logged in `open-problems.md` |

The API key belongs to the dev-tooling container only. Zoe runtime and household data
(zoe-data, the voice path, memory) never see it.

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
