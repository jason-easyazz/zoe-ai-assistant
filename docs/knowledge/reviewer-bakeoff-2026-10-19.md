---
type: Record
title: pi fallback-reviewer bake-off 2026-10-19 - models and OpenRouter routers vs 12 known blocking bugs
description: Which model cross_review.sh's lean pi pass should use. 24 candidates (6 concrete models, a control, and every OpenRouter model-selection mechanism that applies to a single tool-less call) run through the production harness on three real PRs at the heads Codex reviewed, scored mechanically against 12 Codex-confirmed blocking bugs. Winner openai/gpt-6.1-sol (31/40 vs 9/40 for z-ai/glm-5.2); routers are refused as a default because they can serve Anthropic.
tags: [review, cross-review, pi, openrouter, bakeoff, models, routing]
timestamp: 2026-10-19T10:10:00+08:00
---

# pi fallback-reviewer bake-off (2026-10-19)

`scripts/maintenance/cross_review.sh` falls back to ONE tool-less pi pass through OpenRouter when codex is
unavailable. Earlier pi/GLM-5.2 passes called three PRs CLEAN that Codex then showed had 12 blocking bugs.
This record is the evidence for the new default. Scores and finding summaries only; no diff text.

## Verdict

- **New default: `openai/gpt-6.1-sol`.** 31/40 known-bug instances over 10 runs (21/24 when it saw the whole
  diff), 0 failed runs, p50 78 s / max 156 s (inside the 420 s phase timeout), about US$0.19 a pass.
  The old default `z-ai/glm-5.2` found 9/40 and returned **VERDICT: CLEAN on #2152 in both whole-diff runs**.
- **Raise `CROSS_REVIEW_PI_DIFF_MAX_BYTES` 200000 -> 600000** (the codex cap). At 200 KB the cut fell
  inside `broker.py` on #2152, so no candidate found any of its 3 bugs in any capped run; with the whole
  diff gpt-6.1-sol found 2/3 in both runs. All three PRs (190 KB, 210 KB, 346 KB) fit in 600 KB; a whole
  346 KB pass cost US$0.26.
- **No OpenRouter router as the default, and `cross_review.sh` now refuses one** (`openrouter/*`, `*/auto`,
  `typesafe/jev-router`, `nvidia/switchyard`, `~*-latest`, `@preset/*`, any Anthropic id). The best router,
  Jev with Anthropic excluded, scored 28/40 *because it picked gpt-6.1-sol for 5 of its 10 runs*; it was
  slower (p50 298 s, one 900 s timeout) and needs request plugins pi cannot send. Unconfigured, the routers
  served Anthropic models (`auto-beta` served claude-sonnet-5.5 three times; `pareto-code` at its default and
  high tiers served claude-fable-5.1), and the `REVIEWER-VENDOR:` line names the router, not the model, so the
  same-vendor guard could not see it.
- **Second choice if vendor diversity from Codex matters more than recall:** `google/gemini-4-argon`
  (18/40, 0 failures, fast, US$0.22, but about 1.9 unmatched blocking claims a run). `x-ai/grok-4.7` found
  more (20/40) and was the only model to catch #2152's self-approval bug both times, but 3/10 runs failed
  (twice it returned one sentence of preamble and no review on #2128) and its p50 was 484 s, over the timeout.

## Protocol

- **Answer key** (written before any candidate ran): the 12 blocking findings of the Codex reviews of
  #2135 (4), #2128 (5) and #2152 (3), each with file anchors (Codex's line +/-40, widened to the actual code
  at the head where Codex's number was off) and 2-3 required regex groups for the substance. A finding matches
  when it names an anchor file, cites a line in range (or no line), and hits every group. A known bug counts
  for recall in any severity. Controls: each Codex report scores 12/12 against its own key and 0 against the
  other two PRs' keys.
- **Key revisions, each re-applied to every run:** v2 after the baseline smoke test (2135-1's "fail" token
  matched a "no failing test" note); v3 after rounds 1-2 (2128-3 missed "the lock is released before
  `rs.deploy()` and a forget can land during deploy"); v4 after a near-miss audit (2128-1/2128-4 phrasings;
  the splitter now keeps a numbered finding's sub-bullets with it, and bare `:NNN` refs after a file count).
  Two further matches the location rule missed were adjudicated by hand and are starred in the table
  (Jev #2152 whole r1: post-submit redirect at `fill.py:521-533`, one line outside the anchor; kimi-k3 #2152
  200 KB: approval bearer token exposed via `notices`, cited at the verb list instead of the handler).
- **Heads:** the diffs Codex saw - #2135 `f2f891ab`, #2128 `4b23b6a0` (before the 00:02Z "codex round"
  push), #2152 `33112577` (its only commit; the fix was not pushed yet). Each diff is the three-dot diff from
  the merge base (#2152 is byte-identical to GitHub's PR diff; it is 346 KB, not ~1 MB).
- **Harness = production:** every run is `cross_review.sh <pr> "<the exact contract Codex got>"` with
  `CROSS_REVIEW_BACKENDS=pi`, a gh stand-in that prints the frozen diff, and `CROSS_REVIEW_PI_BIN` pointing at
  a shim. For a model id the shim runs the real pi in the omnigent container with `--mode json` added (to
  read the generation id); for mechanisms that need request fields pi cannot send (plugins, `models`, server
  tools) it sends pi's own system prompt and the identical user message straight to
  `/api/v1/chat/completions` (`max_tokens` 32000, reasoning medium like pi). The served model, provider, cost
  and timings come from OpenRouter's `/api/v1/generation` record of each call. The phase timeout was raised
  to 900 s so latency is measured rather than cut; anything over 420 s would time out in production.
- **Diff size:** every candidate ran with the 200 KB cap, plus a whole-diff run where the diff exceeds it
  (#2128, #2152); #2135 is under the cap, so its one run is the whole diff. Every model's context fit the
  whole diff except `openrouter/free` (200k context; its #2152 whole run failed).
- **Repeats:** the leading and baseline models ran twice on all five PR/cap cells (10 runs each); the rest
  once (routing mechanisms with a multi-call cost ran once, on #2152 whole, where the baseline did worst).
  Variance is large: the same glm-5.3 scored 4/5 and 1/5 on identical input.

## Results (model x PR: known bugs found; F = failed run - no report, no verdict, or timed out)

| candidate | served model(s) | #2135 (whole) | #2128 200 KB | #2128 full | #2152 200 KB | #2152 full | total | US$/run | p50 / max s | failed |
|---|---|---|---|---|---|---|---|---|---|---|
| `openai/gpt-6.1-sol` | openai/gpt-6.1-sol | 3/4, 4/4 | 5/5, 5/5 | 5/5, 5/5 | 0/3, 0/3 | 2/3, 2/3 | **31/40** | 0.19 | 78 / 156 | 0/10 |
| `typesafe/jev-router` [jev_default] | google/gemini-4-argon, openai/gpt-6.1-sol, x-ai/grok-4.7 | 2/4, 4/4 | 5/5, 5/5 | 5/5, 5/5 | 0/3, 0/3 | 2/3*, 0/3 F | **28/40** | 0.26 | 298 / 900 | 1/10 |
| `x-ai/grok-4.7` | x-ai/grok-4.7 | 4/4, 4/4 | 5/5, 5/5 | 0/5 F, 0/5 F | 0/3 F, 0/3 | 1/3, 1/3 | **20/40** | 0.35 | 484 / 591 | 3/10 |
| `google/gemini-4-argon` | google/gemini-4-argon | 3/4, 3/4 | 2/5, 3/5 | 2/5, 2/5 | 0/3, 0/3 | 1/3, 2/3 | **18/40** | 0.22 | 60 / 266 | 0/10 |
| `deepseek/deepseek-v4.1-flash` | deepseek/deepseek-v4.1-flash | 1/4 | 4/5 | 3/5 | 0/3 | 0/3 F | **8/20** | 0.05 | 325 / 900 | 1/5 |
| `~z-ai/glm-latest` | z-ai/glm-5.3 | 1/4 | 4/5 | 2/5 | 0/3 | 0/3 | **7/20** | 0.07 | 152 / 217 | 0/5 |
| `z-ai/glm-5.3` | z-ai/glm-5.3 | 2/4, 2/4 | 1/5, 1/5 | 0/5, 1/5 | 0/3, 0/3 | 0/3, 0/3 | **7/40** | 0.08 | 112 / 303 | 0/10 |
| `z-ai/glm-5.2` | z-ai/glm-5.2 | 1/4, 1/4 | 1/5, 2/5 | 2/5, 1/5 | 1/3, 0/3 | 0/3, 0/3 | **9/40** | 0.10 | 65 / 231 | 0/10 |
| `moonshotai/kimi-k3` | moonshotai/kimi-k3 | 2/4 | 0/5 F | 0/5 | 1/3* | 1/3 | **4/20** | 0.27 | 522 / 900 | 1/5 |
| `openrouter/auto` [auto_xhigh] | moonshotai/kimi-k3 | 2/4 | 1/5 | 0/5 | 0/3 | 0/3 | **3/20** | 0.35 | 51 / 101 | 0/5 |
| `openrouter/auto-beta` | anthropic/claude-sonnet-5.5, openai/gpt-6.1-sol, x-ai/grok-4.7 | 4/4 | 0/5 F | 0/5 F | 0/3 F | 0/3 F | **4/20** | 0.22 | 56 / 211 | 4/5 |
| `openrouter/auto-beta` [autobeta_xhigh] | google/gemini-4-argon, x-ai/grok-4.7 | 2/4 | 0/5 F | 0/5 F | 0/3 | 0/3 | **2/20** | 0.18 | 32 / 101 | 2/5 |
| `typesafe/jev-router` [jev_high] | google/gemini-4-argon, z-ai/glm-5.3 | 0/4 F | 2/5 | 0/5 F | 0/3 | 1/3 | **3/20** | 0.17 | 150 / 339 | 2/5 |
| `openrouter/pareto-code` [pareto_med] | moonshotai/kimi-k3 | 1/4 | 0/5 | 0/5 | 0/3 | 0/3 | **1/20** | 0.22 | 97 / 274 | 0/5 |
| `openrouter/auto` | deepseek/deepseek-v4.1-flash | 0/4 F | 1/5 | 0/5 F | 0/3 | 0/3 | **1/20** | 0.03 | 113 / 170 | 2/5 |
| `openrouter/free` | thinkingmachines/inkling | 0/4 | 0/5 | 0/5 | 1/3 | 0/3 F | **1/20** | 0.00 | 40 / 82 | 1/5 |
| `openrouter/fusion` [fusion] | (none returned) | - | - | - | - | 2/3 F | **2/3** | 0.00 | 900 / 900 | 1/1 |
| `z-ai/glm-5.2:nitro` [glm52_nitro] | z-ai/glm-5.2 | - | - | - | - | 2/3 | **2/3** | 0.52 | 55 / 55 | 0/1 |
| `z-ai/glm-5.3` [fallbacks] | z-ai/glm-5.3 | - | - | - | - | 1/3 | **1/3** | 0.03 | 58 / 58 | 0/1 |
| `nvidia/switchyard` [switchyard] | google/gemini-4-argon | - | - | - | - | 0/3 | **0/3** | 0.31 | 106 / 106 | 0/1 |
| `deepseek/deepseek-v4.1-flash` [advisor] | deepseek/deepseek-v4.1-flash | - | - | - | - | 0/3 | **0/3** | 0.09 | 634 / 634 | 0/1 |
| `z-ai/glm-5.2:exacto` [glm52_exacto] | z-ai/glm-5.2 | - | - | - | - | 0/3 | **0/3** | 0.31 | 217 / 217 | 0/1 |
| `z-ai/glm-5.2:floor` [glm52_floor] | z-ai/glm-5.2 | - | - | - | - | 0/3 | **0/3** | 0.37 | 261 / 261 | 0/1 |
| `openrouter/auto` [auto_plain_direct] | deepseek/deepseek-v4.1-flash | - | - | - | - | 0/3 F | **0/3** | 0.02 | 114 / 114 | 1/1 |

Every "200 KB" cell for #2128 and #2152 is truncated (#2135 never is). The 4 runs killed at 900 s returned
no generation id, so their cost is only in the key total below. Bracketed labels ran through the direct
shim with the request fields described in the routing table.

### Which known bugs the leading models found (whole-diff runs, 2 runs each)

| bug | gpt-6.1-sol | Jev | grok-4.7 | gemini-4-argon | glm-5.2 |
|---|---|---|---|---|---|
| 2135-1 stock-bond wipe failure ignored | 2 | 2 | 2 | 2 | 0 |
| 2135-2 120 s pairing window at every unbonded boot | 2 | 1 | 2 | 2 | 2 (non-blocking) |
| 2135-3 CTR salt falls back to the cycle counter | 2 | 2 | 2 | 2 | 0 |
| 2135-4 `PairableTimeout` failure ignored | 1 | 1 | 2 | 0 | 0 |
| 2128-1 `trained_keys` ignores warm-start lineage | 2 | 2 | 0 | 2 | 0 |
| 2128-2 replay sample not filtered against held-out | 2 | 2 | 0 | 0 | 1 (non-blocking) |
| 2128-3 forget/adopt race before `rs.deploy` | 2 | 2 | 0 | 0 | 0 |
| 2128-4 forget before a run record keeps the wording | 2 | 2 | 0 | 2 | 1 |
| 2128-5 post-deploy failure, no rollback | 2 | 2 | 0 | 0 | 1 (non-blocking) |
| 2152-1 zoe-data can approve its own capability | 0 | 0 | 2 | 1 | 0 |
| 2152-2 redirect hops not validated | 2 | 1* | 0 | 2 | 0 |
| 2152-3 lock does not stop an in-flight login | 2 | 1 | 0 | 0 | 0 |

grok-4.7's two #2128 whole-diff runs both failed; on the 200 KB #2128 runs it found 5/5 twice. No model found
everything: gpt-6.1-sol never found 2152-1, which grok-4.7 found every time, so the union argument for
cross-vendor review still holds.

## Extra findings and false positives

Unmatched blocking claims per run: glm-5.2 0.1, gpt-6.1-sol 1.0, glm-5.3 1.1, Jev 1.4, gemini-4-argon 1.9,
grok-4.7 about 1.7 (it writes no severity headers, so its claims score as "unspecified"). Spot-checked
against the code at the heads:

- **gpt-6.1-sol (all 10 claims reviewed).** Real: the audit chain never authenticates the LAST record, so
  editing it (or dropping a suffix) still verifies (`zoe_vaultd/audit.py` `_walk`, lines 50-71; Codex had only
  the suffix half, as non-blocking) - 3 runs; the 32-bit random salt can collide across CLEAR/re-init (Codex
  listed it as a README overclaim, non-blocking) - 1 run; the Pi-side bond refusal is opt-in
  (`omi_sync/config.py` `require_bond` defaults to empty) - true, though the flag-dark rollout is deliberate -
  2 runs. Plausible, unverified: a check-to-insert focus race between the last binding check and
  `Input.insertText` (`fill.py` `insert`, about lines 464-490) - 2 runs. Outside the stated threat model,
  counted as false positives: "a site that copies the password into a cookie leaks it through `redeem`" - 2
  runs (`fill.py:157` puts a compromised approved site out of scope).
- **glm-5.2:** almost no blocking claims at all. Its failure is recall: the bugs it did see went under
  non-blocking, and its verdicts were CLEAN or PARTIAL.
- **gemini-4-argon, Jev, grok-4.7 (sampled):** several models independently flagged the firmware patch's
  `restore_tail_batch()` re-keying the tail in RAM without updating the batch header or marking it dirty -
  plausible, unverified, worth a look on #2135. Also seen: vacuous V8/V9 lab negative controls on #2152 (the
  same pattern Codex flagged for V3) and IDNA look-alike origin conversion (unverified). Not every claim was
  checked; read the per-run counts as an upper bound on false positives.

## OpenRouter model-selection mechanisms (surveyed 2026-10-19 from the live `/api/v1/models` list and the routing docs)

| mechanism | how to invoke | applies to one tool-less review call? | ran as | what it actually served |
|---|---|---|---|---|
| Auto Router | `openrouter/auto`; plugin `auto-router` (`allowed_models`, `excluded_models`, `cost_tier` low to max); or account defaults on the Routing page | yes (no setting = the `low` band) | slug via pi; plugin `cost_tier: xhigh` with Anthropic excluded; slug direct with 32k max_tokens | deepseek-v4.1-flash (plain, 6 runs); kimi-k3 (xhigh, 5 runs). Via pi it inherits pi's catalog maxTokens of 4096, so reasoning used the budget and 2/5 runs returned nothing |
| Auto Router beta | `openrouter/auto-beta`, plugin id `auto-beta-router` | yes | slug via pi; xhigh with Anthropic excluded | **claude-sonnet-5.5 (3 runs)**, gpt-6.1-sol, grok-4.7; with xhigh: gemini-4-argon, grok-4.7 |
| Pareto Code Router | `openrouter/pareto-code`, plugin `pareto-router` `min_coding_score` | partly: it has no author exclusion | slug via pi (HTTP 404 "No endpoints"); direct at 0.5 (medium) | **default and 0.9 (high) served claude-fable-5.1** (probe only, not run); medium served kimi-k3 |
| Jev Router (TypeSafe) | `typesafe/jev-router`, plugin `jev-router` (`cost_tier` low/medium/high, `excluded_models`) | yes, with the plugin | direct, default policy and `high`, Anthropic excluded | default: gpt-6.1-sol (5 runs), gemini-4-argon (2), grok-4.7 (2), 1 timeout; high: gemini-4-argon, glm-5.3. An unconstrained probe also chose gpt-6.1-sol |
| Author routers `<author>/auto` (Jev-backed) | e.g. `openai/auto` | would be | - | none in the live catalog |
| Switchyard (NVIDIA) | `nvidia/switchyard` with `models` (1-3) and plugin `switchyard-router` `algorithm` | yes | `capability` judge between deepseek-v4.1-flash and gemini-4-argon | gemini-4-argon; the judge sends part of the prompt to gemini-3.5-flash-lite |
| Fusion | `openrouter/fusion` (plugin `fusion` sets panel and analyst; `fusion-flash` is documented but not listed) | poorly: N panel calls with web search/fetch at 4-5x cost, and the default panel includes Claude Opus | non-Anthropic panel (grok-4.7, gemini-4-argon, glm-5.3), analyst gemini-4-argon, `tool_choice: required` | killed at 900 s; the partial output was raw panel answers, and a panel model reported a truncated payload |
| Free Models Router | `openrouter/free` | yes, quality aside (200k context) | slug via pi | thinkingmachines/inkling (free) |
| Body Builder | `openrouter/bodybuilder` | no: it returns request JSON, not a review | - | - |
| Latest aliases | `~vendor/family-latest` | yes; it is one model, silently re-pointed | `~z-ai/glm-latest` via pi | z-ai/glm-5.3 |
| Model fallbacks | `models: [...]`, failover on error only | yes, but pi cannot send it | `[glm-5.3, deepseek-v4.1-flash]` | glm-5.3 (no failover happened) |
| Routing variants | `:nitro` (throughput, priority tier), `:floor` (price, flex tier), `:exacto` (quality-sorted providers); deprecated `:online`; catalog `:free`, `:batch` | yes: same model, another provider | glm-5.2 with each suffix, #2152 whole | glm-5.2 on other providers: nitro 2/3 (US$0.52), exacto 0/3, floor 0/3 |
| Provider routing and service tiers | `provider` (`sort`, `order`, `only`, `ignore`, `quantizations`, `max_price`); `service_tier` | yes, same model | covered by the variants | - |
| Auto Exacto | automatic, tool-calling requests only | no (tool-less call) | - | - |
| Presets | `@preset/<name>` bundles any of the above | packaging, not a selector | - | - |
| Advisor / Subagent server tools | `tools: [openrouter:advisor ...]` | yes | deepseek-v4.1-flash consulting gemini-4-argon | 0/3 in 634 s |
| Decisions API / Jev-verified cascade | `/api/alpha/decisions`, a cookbook pattern | no: a multi-call recipe | - | - |

Verdict on routers: none beat the concrete model it most often chose, each adds a moving part (the pick can
change any day with market spend or a shortlist update), and several served the implementer's own vendor.

## Surprises worth acting on

- **A one-sentence non-review passes as a report.** grok-4.7 twice answered #2128 with only "I'll review the
  retrain-and-adopt path ..." (pi's system prompt says it can read files; with no tools it stopped).
  `cross_review.sh` accepted it (rc 0, over `MIN_REPORT_BYTES`=40, no `VERDICT:` line). Follow-up: require a
  `VERDICT:` line before accepting a direct-backend report.
- **pi's catalog caps `openrouter/auto` and `auto-beta` at 4096 output tokens**, so a reasoning model spends
  it all thinking and the pass returns nothing (`stop_reason: length`).
- **`openai/gpt-6.1-sol` is not in pi 0.87.1's catalog** ("Using custom model id"), so pi sends no reasoning
  setting and the model's own default applies. A later `pi update` that adds it would switch on pi's default
  `medium` reasoning: re-measure the cost then.
- **Answer-key bias.** The key came from Codex (OpenAI). gpt-6.1-sol is the same vendor and plausibly shares
  its eye and its blind spots, so part of its lead is "agrees with Codex". Two checks against that: grok-4.7
  found all five #2128 bugs in its 200 KB runs, and gpt-6.1-sol's real extras above are ones Codex missed or
  under-rated. For Claude-implemented work it is still cross-vendor; it is no substitute for a different
  vendor on Codex-implemented work (AGENTS.md routes that to `claude_code`, not here).
- **The Omnigent pi worker** (the tie-breaker in AGENTS.md, configured in the container) still defaults to
  `z-ai/glm-5.2`; this change is only `cross_review.sh`'s direct pass.

## Cost

About **US$23.9** of OpenRouter credit for the whole bake-off (key usage before and after: 204.76 and
228.69), under the US$25 cap. The table's per-run costs come from each generation's record (US$21.58
summed); the rest is the four runs killed at 900 s (Fusion the largest) and the request-shape probes.

## Reproduce

The scripts and raw run directories (reports, pi JSON events, generation records, the answer key with its
revision note) lived on the box under a session scratch directory and are not kept. The method above is
enough to redo it: freeze the three diffs at the shas listed, run `cross_review.sh` with
`CROSS_REVIEW_BACKENDS=pi`, a `CROSS_REVIEW_GH_BIN` that prints the frozen diff, and `CROSS_REVIEW_PI_MODEL`,
and score against the twelve blocking findings in the Codex reports.
