---
type: research
title: Failure-fed self-improvement proposals — the failure ledger (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit, table or live service changed by this document
description: Deep-research record for the owner's Q19 tick "failure-fed self-improvement proposals". Our half first, read-only with file:line — every failure signal Zoe already produces (the voice regression probe and its artifact contract, the Samantha bar and the day-sim and what a scored FAIL looks like, the router's confident misses and the parked self-train ratchet, BRAIN_LANE / FLUE_ABORT / intent-dispatch outcomes, the correction cue, the frustration and issue-report writers, the proactive lifecycle and its response ledger, the incident runbook as the human ledger, where every log lives) and what is NOT captured (a live "can't do", a voiced raise, a confident misroute on a real turn, a correction that was Zoe's fault, a thumbs-down anyone reads). Field half: the pieces worth borrowing from Reflexion, Anthropic's and Hamel Husain's evals-from-failures guidance, Sentry fingerprint grouping, Google SRE postmortem triggers, LangSmith's negative-feedback datasets, OpenAI's self-evolving-agent cookbook, Hermes' write-approval gate and Replika's reaction labels. Then a flag-dark design — one failure ledger (schema, severity, fingerprint, sampling, retention, never a transcript), classes ("fix the class, not the instance"), a weekly digest that becomes proposals with an evidence pack, the L1–L3 approval gates from the self-building record, proposal → Multica ticket through the existing admission gate, how Zoe reports and when she asks — with flags, ~zero RAM/CPU cost, a measurement plan that must reproduce the four known 2026-10-04 failures with negative controls, a go/no-go against VISION, four decisions for the owner and the PR order if GO.
---

# Failure-fed self-improvement proposals — the failure ledger (2026-10-04)

Research date: 2026-10-04. The owner ticked **P9 "failure-fed self-improvement proposals"** in
the 2026-10-04 questionnaire (Q19, research all four) alongside **P1** (pull-not-push inbox,
[record](pull-not-push-inbox-2026-10-04.md)), **P7** (incomplete-turn marker) and **P8**
(presence). It is the *input side* of the self-building loop designed in
[self-building-skills-2026-10-04.md](self-building-skills-2026-10-04.md): that record built the
path from a **want** to a skill (detect → offer → ticket → build → review → merge → report) and
left stage 10, "complaints → fix tickets", as a sketch (§3.6) with the Letta number that justifies
it (the only measured self-authoring gain, **+36.8 % rel.**, needed *verifier feedback on
failures*; without it +21.1 %). This record is that stage, generalised: not only complaints, but
every failure signal Zoe already emits, turned into **proposals** a human approves. It also
answers the operator's standing note that Zoe must *know when to ask, report progress, and fix
issues* — for failures specifically.

Tracked neighbours: **W7** (close the self-evolution loop once), **W16** (the Samantha
scoreboard, "NOT STARTED", `samantha-evolution-plan.md:1032`), **W18.2** (feedback consumers,
`:1040`), **B8.1** (rebuild the executor, `beat-the-bar-2026-program.md:1200`). Standing rules
honoured: [fix the class, not the instance](../knowledge/incident-runbook.md) (the operator's
36-round distillation), [verify your instruments](../knowledge/incident-runbook.md#7-scheduled-job-runs-on-time-and-does-nothing--the-zero-effect-blind-spot-2026-07-22)
("a gate that can silently not-run is not a gate"), dive deep before changing.

Evidence labels: **[src]** checked in this worktree's source or tracked docs (file:line, head
`4adfe59b`); **[live]** a read-only observation on the box today (file names, sizes, counts and
log *labels* only — no service touched, no transcript read); **[doc]** an upstream primary page
read today; **[2nd]** secondary; **[unverified]** not checked against a primary; **[inf]** my
inference.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) untouched;
nothing here adds Jetson RAM on the hot path; everything ships flag-dark; no live service was run,
restarted or queried; no household transcript is quoted — every example turn below is the bar's
or the day-sim's synthetic seed, or a count.

---

## 0. TL;DR

1. **Zoe already produces nine kinds of failure signal, and not one of them lands in a place
   where the next one can be compared to it.** The voice probe writes a durable artifact with a
   verdict map (`CANT_DO / ERROR / EMPTY / OK`) and a 484-run trend file; the Samantha bar and
   the day-sim write per-scenario `FAIL` verdicts with a machine-stable `why` string; the router
   logs one `router_two_stage` line per routed turn with its confidence; the brain lane logs one
   `BRAIN_LANE … outcome=ok|fallback|error|interrupted` line per turn; intent-dispatch returns
   `ok: False` with a reason; the correction cue, the frustration tracker, the issue-report
   intent and the thumbs-down endpoint each write *something*. They go to **seven different
   places** (§2.9): three JSON artifacts, two JSONL files, a rotating app log, journald, a Postgres
   table nobody reads, and a Multica board whose consumer has been paused since 2026-08-04.
2. **The four failures the owner knows about were each found by a human reading a log.** S1
   (router misroute to `calendar`, `head_conf 0.5371`, answered in 488 ms, brain never consulted);
   S4 (the day-1 worry not acknowledged, three root causes over three rounds); day-sim ask 9 (the
   dentist time routed to `time` at `head_conf 0.9971`, "It's 10:41 PM"); and the "CANT_DO storm"
   after a sidecar restart (14 `ERROR` + 5 `OK` on the #1821 probe, 2026-10-04T02:29Z, confirmed
   no regression 7 min later). The artifacts held every number needed; nothing joined them. §4
   makes "the ledger reproduces these four from the existing files" the first acceptance test.
3. **What is not captured is the half that matters to a member.** No live code tags a brain
   reply as "can't do" (the regex exists only in the replay harness); a greeting raise is never
   recorded as voiced or answered (PR #1821 found it voiced 0/5 and production could not tell);
   a confident misroute on a real turn is logged but never labelled right or wrong; the
   correction cue supersedes a fact without asking whether *Zoe* was wrong; `chat_feedback`
   rows are written by three senders and read by a Prometheus counter; the Pi daemon's failures
   stay on the Pi. §2.10 lists twelve such gaps.
4. **The field's lesson is small and consistent.** Anthropic: start with "20–50 simple tasks
   drawn from real failures", pulled from "your bug tracker and support queue", keep capability
   and regression evals apart and graduate one into the other. Hamel Husain and Shreya Shankar:
   open-code ≥30 traces yourself, group into a failure taxonomy ("the most important step"),
   count by class, score binary, validate any judge against human labels. Sentry: a deterministic
   **fingerprint** per event, in-app frames only, rules change only future grouping. Google SRE:
   named **triggers** for a postmortem (user-visible degradation beyond a threshold, data loss,
   on-call intervention, monitoring failure), blameless, action items with owners — "an
   unreviewed postmortem might as well never have existed." Hermes: every self-written skill
   is **staged** under `write_approval` until a human says `/skills approve`; skills are "lessons,
   not logs". Reflexion: the failure's *verbal reflection*, not the trajectory, is what helps
   (+8 pts over trajectory-only memory) — i.e. the evidence pack is the product, not the dump.
5. **Design (flag-dark, ~0 RAM):** one `failure_events` table (signal, severity S1–S4,
   fingerprint, class key, hashed member, turn/sample refs, aggregate evidence JSON, run ref —
   never text; a derived `source_key` with a UNIQUE constraint so a re-ingested artifact or a
   retried write cannot duplicate a row), fed by (a) ingesters for the three harness artifacts, (b) the existing runtime
   emitters re-pointed (BRAIN_LANE sink, intent-dispatch, the correction cue joined to the
   previous assistant turn, frustration / issue / thumbs, FLUE_ABORT when A1 lands, the P1
   delivery ledger), (c) a shadow refusal classifier (the replay regex) run on the *served* reply
   at the one sink every brain lane already passes through (`brain_dispatch._LaneRecord`), with a
   small second hook on deterministic fast-tier replies. A nightly rollup in the dreaming job
   groups rows into **classes** by fingerprint — a *semantic* key (layer + failure kind +
   discriminator) that harness and live witnesses share, `source` being witness metadata and
   never part of the key — with Sentry-shaped states
   (new / active / proposed / fixed / regressed). A weekly digest (the existing trigger) emits
   **proposals** — `evolution_proposals` rows with an evidence pack (class, counts, severity,
   first/last seen, the harness `why`, example refs, suspected layer, the negative control that
   must go red → green) — under SRE-shaped thresholds, capped at three a week. Approval is the
   self-building record's ladder: L1 test/corpus/flag-default → gate + cross-review + panel tap;
   L2 code → human on GitHub; L3 voice path / memory writes / proactive → human + replay gate +
   Greptile; rocks → not a proposal, a decision. A yes becomes a Multica ticket through the
   existing `multica_admission` contract and the Omnigent lane. Reporting is pull (Zoe's Work);
   one Notify when a class is fixed *and quiet for 7 days*; Zoe asks a member only to *label* an
   ambiguous S1 ("did I get that wrong just now?"), once a day at most, and never to approve.
6. **Cost:** one INSERT per failure (tens per day at most), one nightly GROUP BY, a regex on the
   reply. No model, no RAM, nothing on the Pi.
7. **Go, in this order:** ledger + harness ingesters + backfill + the four-failure test →
   runtime emitters → classes → digest → proposals → tickets → the member label question. Not
   before P1's delivery ledger for the proactive rows, and not before the executor is unparked
   for the ticket step.

---

## 1. Field — the pieces worth borrowing

### 1.1 Evals from failures (Anthropic, Hamel Husain / Shreya Shankar, LangSmith)

- **Anthropic, "Demystifying evals for AI agents" (2026-01-09)** [doc]: begin with "the manual
  checks you run during development" and "your bug tracker and support queue" — user-reported
  failures "make the best test cases"; start with **"20–50 simple tasks drawn from real
  failures"**; **capability evals** start at a low pass rate, **regression evals** sit near 100 %
  and a capability eval "graduates" into the regression suite when it reaches a high pass rate;
  three grader types (code-based: fast, objective, brittle; model-based: nuanced, needs
  calibration against humans; human: gold, slow); a *transcript* is "the complete record of a
  trial, including outputs, tool calls, reasoning, intermediate results", the *outcome* is "the
  final state in the environment"; `pass@k` vs `pass^k` diverge as k grows; conversational agents
  "require a second LLM to simulate the user". Zoe's shape already matches: the bar is the
  regression suite (only a previously-PASSing scenario can turn it red, `samantha_bar.py:9-10`),
  the day-sim is the capability eval with a simulated member, `pass^k` is the bar's
  `--samples 3` majority vote with a tie counted as FAIL (`:410-419`).
- **Hamel Husain & Shreya Shankar, "AI Evals: everything you need to know"** [doc]: "Start by
  annotating at least 30 traces yourself before reviewing suggestions from an agent"; a working
  pool of ~100 diverse traces, reviewed until "new traces stop revealing failure modes"
  (theoretical saturation); note the **first** failure in a trace, "as upstream errors can cause
  downstream issues"; **open coding** (free notes) → **axial coding** ("the most important
  step": categorise the notes into a failure taxonomy, then count); binary pass/fail over Likert
  ("force clearer thinking and more consistent labeling"); a judge is validated by TPR/TNR against
  human labels on a held-out split; "build automated evaluators only for problems you'll iterate
  on repeatedly", code checks first, LLM-judge for the subjective residue. The incident runbook's
  entry 11 is exactly an open-coding pass: 517 distinct panel transcripts run through the old
  classifier, 9 wrong claims found, one class fixed (`incident-runbook.md:466-472`) [src].
- **LangSmith evaluation concepts** [doc]: "User feedback: add runs that received negative
  feedback to test against"; annotation queues "with prescribed rubrics"; online evaluators are
  reference-free ("quality heuristics, safety checks"); regression tests "can assert that new
  versions must outperform baseline versions". The piece: a thumbs-down is a *dataset candidate*,
  not a metric.
- **OpenAI cookbook, "Self-evolving agents — autonomous agent retraining"** [doc]: four graders
  produce *named* failure reasons ("not all chemical names … were included"), a metaprompt agent
  proposes a new prompt from the failing output plus the reason, candidates are re-scored, success
  = "75 % of graders must pass" and "85 % average score", `max_retry = 10`, then escalate to a
  human; the authors add that a real deployment needs "additional guardrails and a
  human-in-the-loop approach to approve new prompts". The piece: the *reason string* is the unit
  that feeds a proposal, and the loop is bounded.

### 1.2 Grouping failures into classes (Sentry)

- **Sentry grouping and fingerprints** [doc]: every event gets a **fingerprint** (a list of
  strings); same fingerprint ⇒ same issue. The default fingerprint is built from the stack trace,
  exception type and message, considering **in-app frames** and collapsing framework/middleware
  frames so that "two stack traces [that] are the same function execution path but differ by one
  or more frames corresponding to code in middleware, third-party libraries, or the framework"
  still group. **Fingerprint rules** match on `error.type`, `message` (with `*` wildcards), file
  path and function; **stack-trace rules** choose which frames count; and "neither configuring
  fingerprint and stack trace rules nor using SDK-side fingerprinting will affect existing events
  or issues, only … future events". The pieces: (a) a deterministic class key computed from the
  *shape* of the failure, never from who or what was said; (b) the member, the session and the
  timestamp are "system frames" — excluded from the key; (c) the harness's `why` string is the
  "message with wildcards"; (d) changing a rule re-groups forward only, so the ledger keeps the
  key it was written with.

### 1.3 When a failure earns a review (Google SRE)

- **SRE book, postmortem culture** [doc]: triggers — "user-visible downtime or degradation beyond
  a certain threshold", "data loss of any kind", "on-call engineer intervention (release rollback,
  rerouting of traffic, etc.)", "a resolution time above some threshold", "a monitoring failure
  (which usually implies manual incident discovery)", and "any stakeholder may request a
  postmortem"; **blameless** = "identifying the contributing causes … without indicting any
  individual"; contents = impact, root cause(s), mitigation, prevention, **action items with
  owners**; review criteria include "is the root cause sufficiently deep" and "is the action plan
  appropriate"; "an unreviewed postmortem might as well never have existed."
- **SRE workbook, error-budget policy** [doc]: an incident that consumes >20 % of the four-week
  budget "must conduct a postmortem that must contain at least one P0 action item"; over budget ⇒
  releases halt except P0/security. Zoe already runs the freeze half: a red replay artifact
  blocks a voice-path deploy (`voice_gate_check.py:14, 682-686`) [src]. What it lacks is the
  *trigger* half — a rule that says which failure must become a proposal, by when.

### 1.4 Agents that reflect on failures (Reflexion, Letta, Hermes, OpenClaw)

- **Reflexion** (Shinn et al., NeurIPS 2023, [arXiv 2303.11366](https://arxiv.org/abs/2303.11366))
  [doc]: agents "verbally reflect on task feedback signals, then maintain their own reflective
  text in an episodic memory buffer"; feedback may be "scalar values or free-form language" from
  "external or internally simulated" sources; HumanEval 91 % pass@1 vs GPT-4's 80 %. The ablation
  that matters here [2nd, alphaxiv overview]: full Reflexion beat an agent with only episodic
  *trajectory* memory by **8 pts absolute** — the natural-language explanation of the failure is
  worth more than the raw transcript. A 2026 follow-up on **confabulated reflections**
  ("Honest Lying: understanding memory confabulation in reflexive agents", arXiv 2605.29463)
  [unverified, title only] is the caution: a small model writing its own failure story can
  invent one. For Zoe that settles a design choice: the **evidence pack is deterministic** (counts,
  confidences, verdict strings, refs); the 4B never authors the reflection.
- **Letta skill learning** [doc, via the sibling record §1.4]: +21.1 % rel. from trajectories,
  **+36.8 % rel. with verifier feedback on failures**, −15.7 % cost. The verifier is the thing.
- **Hermes Agent skills** [doc]: the agent is told to write a skill "when it hit errors or dead
  ends and found the working path" and "when the user corrected its approach"; with
  `skills.write_approval: true` "every `skill_manage` write … is **staged** instead of committed"
  under `~/.hermes/pending/skills/` until `/skills approve` or `/skills reject` — "whether writes
  originate from foreground conversations or background self-improvement processes"; skills are
  "lessons, not logs". **OpenClaw** routes its `skill_workshop` drafts through an operator proposal
  queue (sibling §1.4). Omnigent documents no proposal pattern of its own — it is the runner; the
  proposal is ours (`evolution_proposals`, §2.6).

### 1.5 Companion feedback loops (Replika, Nomi)

- **Replika** [doc, blog "Creating a safe Replika experience"; 2nd for the fine-tuning claim]:
  per-message thumbs up/down plus reaction labels ("love", "funny", "meaningless", "offensive")
  via the dots next to a message; the labels are used to fine-tune the model and "over time it
  adapts its personality to the user". The piece is the **label set**: a thumbs-down with a
  *reason* is a class; a bare thumbs-down is noise. Zoe's `chat_feedback` already has
  `feedback_type ∈ thumbs_up | thumbs_down | correction` and a `corrected_response` column
  (`alembic/0001:443-450`) [src] — the shape exists, the readers do not.
- **Nomi**: its published documentation covers proactive cadence (the P1 record) but no explicit
  "did I get that wrong?" loop was found [unverified — none located]. The pattern this record
  proposes (Zoe asks the member to *label* a suspected miss, once, pull-first) is borrowed from
  Replika's reactions and from FROST's observation that you only ever see the outcome of the one
  suggestion you made (sibling §1.6), not from a companion app.

### 1.6 Pieces to borrow (and what not to)

| Borrow | From | Not borrowing |
|---|---|---|
| 20–50 tasks from real failures; capability → regression graduation | Anthropic | a new eval framework (the bar + day-sim are it) |
| open coding → taxonomy → count; first failure; binary verdicts; judge validated vs humans | Hamel/Shankar | Likert scores; an unvalidated judge as a gate |
| negative feedback ⇒ dataset candidate | LangSmith | a hosted tracing product |
| a *reason string* per failed grade; bounded retries; human approves the change | OpenAI cookbook | automatic prompt rewriting in production |
| deterministic fingerprint; in-app frames only; rules change forward only | Sentry | sending events anywhere off the box |
| named triggers; blameless; action items with owners; "unreviewed = never existed" | Google SRE | a formal postmortem document per failure |
| the reflection is the evidence, not the trajectory; a small model confabulates | Reflexion | letting the 4B write the reflection |
| staged writes until a human approves; "lessons, not logs" | Hermes | an agent-side skill store |
| labelled thumbs (reason), one question to the person | Replika | fine-tuning on feedback (the rocks are fixed) |

---

## 2. Our system — read-only, with file:line

### 2.1 The voice regression probe and its artifact

`scripts/maintenance/voice_regression_probe.py` (1,166 lines) [src] replays the newest N
household-voice samples through the live path (`scripts/perf/measure_voice.py`) and compares
against a baseline on two axes: function ("the OK rate … must not drop, and the CANT_DO/ERROR
count must not rise", `:8-11`) and speed (per-stage medians, `:12-14`).

- **Verdicts** come from `services/zoe-data/tests/replay_samples.py` [src]: `CANT_DO` = "asked
  for something Zoe couldn't fulfil (extractor empty, 'I don't …')", `EMPTY` = "STT heard
  nothing", `ERROR` = "a stage raised" (`:19-22`); `_CANT_DO_RE` (`:73-80`) matches capability
  disclaimers ("don't have access to", "not on file", "can't tell you | find | create | add | set
  | schedule | do that", "unable to", "you'll have to … yourself"), deliberately not
  conversational hedges; `_classify` (`:250-266`) orders EMPTY → ERROR (a stage raised, or the
  brain's canned fallback, imported from `zoe_flue_client._FALLBACK_TEXT` so the harness cannot
  drift from the client, `:66-70`) → CANT_DO (regex, or the brain ran and produced no spoken text).
- **The artifact contract** (`:25-35`, written by `emit_result` `:670-727` on *every* exit path —
  "a skip/timeout/error MUST leave an artifact with status != pass — never an ABSENT file"):
  `{status: pass|fail|skip|error, timestamp, said_vs_did_regressions, per_stage_speed_deltas,
  baseline_ref, reason, revision, summary, non_pass_streak, non_pass_alert_after, non_pass_alert,
  vad_stage, vad}`. `summary` (`summarize`, `:314-357`) carries `n_samples, ok_rate (over
  SCOREABLE = total − EMPTY, None when 0), ok, fail (= CANT_DO + ERROR, :341), total, empty,
  scoreable, verdicts{}, medians_ms{stt,brain,e2e}, memory_recall, interpreter`. `revision`
  binds the evidence to a commit + tree + dirty flag (the #1745 tree-bound gate).
  `said_vs_did_regressions` is the list of `FUNCTION:` warnings from `compare()` (`:371-411`):
  `NO-EVIDENCE` (0 scoreable), `FUNCTION: OK rate x vs baseline y`, `FUNCTION: CANT_DO/ERROR count
  rose to n from baseline m` (`:395-399` — added because a rate alone "can hide a new CANT_DO when
  the scoreable denominator grows"), and `SPEED <stage>` per stage. `non_pass_streak` counts
  consecutive non-pass runs and alarms at 3 (`:729-741`, `ZOE_VOICE_ALERT_NON_PASS_RUNS`).
- **Paths** (`:108-110`): `~/.cache/zoe/voice_regression_{baseline,last}.json`,
  `voice_regression_trend.jsonl` (one line per run). The deploy gate reads exactly this contract
  (`voice_gate_check.py:14`, `:682-686`: missing/unknown → block, fail/error → block, skip → "no
  opinion").
- **Live today** [live]: `voice_regression_last.json` 2026-10-04T11:59:53Z `status=pass`, 20
  samples, `verdicts {OK: 18, EMPTY: 2}`, `fail 0`, medians stt 588 / brain 1707 / e2e 2044.5 ms,
  `revision.clean_verified true`. The trend file holds **484 runs**: 277 pass, 88 skip, 55 fail,
  20 error, 44 pre-contract rows with no status. **39 runs carry `fail > 0`**. The verdict maps of
  the big ones are all `ERROR`, not `CANT_DO`: 2026-08-09 (ERROR 19), 2026-09-27 13:18Z (ERROR
  20), 2026-09-29 02:25Z (ERROR 20), 2026-09-28 18:02Z (ERROR 10), and **2026-10-04T02:29:21Z —
  `{ERROR: 14, OK: 5, EMPTY: 1}`**, the "CANT_DO storm" of the owner's list. Genuine single
  `CANT_DO` rows exist (2026-08-07, 2026-08-10: 1 each).
- **What the artifact does not hold:** *which* sample failed. Per-sample verdicts are printed by
  `measure_voice` (`replay_samples.py:497-498`) and the CANT_DO rows listed (`:509`), but only the
  counts reach the artifact, and the replay rows are swept (`cleanup_replay_artifacts`, `:787`).
  That is by design — the corpus is household audio — and it means the class ("which *kind* of
  utterance regressed") is lost unless an operator re-runs the measure by hand.
- **The storm's real class** [src + memory note]: the #1821 probe scored OK 5/19 because the Flue
  sidecar had been restarted 2 min earlier (an operator token rotation); the confirmation day-sim
  7 min later was 10/10; the land script now exits 4 on a non-pass artifact and re-checks the
  restart age after the deploy wait. The artifact *recorded* the storm faithfully (`revision`,
  `verdicts`, `reason`) but carries no "sidecar age" feature, so a reader cannot tell a cold
  prompt cache from a brain regression without the journal. §3.2 adds that feature to the
  fingerprint so the class is "probe during warm-up" (severity S4, instrument) and never a brain
  proposal — the *verify your instruments* rule as a column.

### 2.2 The Samantha bar — what a scored FAIL looks like

`scripts/perf/samantha_bar.py` (1,721 lines) [src]: twelve scripted multi-day scenarios against
throwaway `demo_bar_<hex>` users through the live `/api/chat`, "scored deterministically where
possible and by the brain itself (fixed rubric, temperature 0, llama-server :11434) where a
judgement is needed" (`:6-8`); "only a regression of a previously PASSING scenario is red"
(`:9-10`). Verdicts `PASS | FAIL | SKIP | ERROR` (`:124`); scenarios `S1…S12` with `judged`,
`expected` and a `proves` line each (`:205-241`); the judge system prompt + rubrics are
sha-pinned (`JUDGE_PROMPT_SHA256`, `:278-284`) so "a rubric change changes what a PASS means";
an unparseable judge is `ERROR`, "never read as a pass" (`:299-305`); `majority_vote` needs a
strict majority and "a tie is a FAIL" (`:410-419`).

A scored FAIL is a `(verdict, evidence)` pair whose evidence carries a **stable `why` string** —
the thing a ledger can fingerprint on:

- `score_s1` (`:422-424`): deterministic — both needles (`marisol`, `lisbon`) or FAIL, evidence
  `{found, method}`.
- `score_s4` (`:451-462`): FAIL "the day-1 worry (interview) is not acknowledged" / FAIL "quotes
  the user back (N words)" / ERROR "judge unavailable" / judge verdict + reason.
- `score_s5_raise` (`:478-493`): FAIL "the selector kept no candidate carrying the day-1 worry" /
  "the worry was not raised on the first open turn" / "the worry was raised again on the next
  open turn".

**Artifact** (`~/.cache/zoe/samantha_bar_last.json`) [live]: `scenarios[{id, verdict,
evidence}]`, `compare{has_baseline, regressions, improvements, new, red, notes}`,
`judge_prompt_sha256`, `revision{commit, tree, dirty, clean_verified}`, `teardown{proven, …}`,
`samples`, `duration_s`. Trend: **27 runs** 2026-09-28 → 09-30; FAIL counts **S1 5, S4 5, S8 3**;
5 runs `status=regression`.

**The two named failures** (`docs/knowledge/samantha-bar.md`) [src]:
- **S1 = a router misroute, not a memory failure** (`:155-165`): "routed by the two-stage router
  to `calendar` and answered deterministically in 488 ms; the brain and the recall packet were
  never consulted" — the app-log line `router_two_stage {"actual_routed": "calendar", "gated":
  false, "head_conf": 0.5371, "mode": "active", "shortlist": ["people","calendar","reminders"],
  "two_stage_tool": "show_calendar"}`. Fixed over three rounds (router floor, intent gate, the
  event-question recall floor, #1763–#1770); authoritative PASS on `269bb680` (`:299-305`).
- **S4 = the emotional thread** (`:167-240`): the worry "not acknowledged on day 2 in any of the
  three samples (`mentions_interview: false`)" although it "had landed in the recall packet";
  round 1 added the continuity injection (fired, "did not work"); round 2 found three causes — the
  digest stored the worry "as the neutral fact", so it "did not rank as emotional" and fell
  outside the pins, and "a soft 'connect if relevant' instruction … did not make the 4B model
  check in"; round 3 found the focus flipping to today's mood. Three rounds, **one class, three
  fixes** — a class that regressed twice before a fix stuck — which is why the ledger must carry
  the fix's PR ref and *reopen* the class rather than open a new one (§3.2), and why the key is
  the `why`, not the scenario id.

### 2.3 The day-sim — the capability eval with a simulated member

`scripts/perf/samantha_day_sim.py` (1,203 lines) [src]: "a judged, teardown-asserted week in the
life of one demo user" (`:2`), proving "the CHAIN" the bar proves only in pieces (`:5-10`).
`ASKS` carry **pre-committed PASS criteria** (`:189-245`), sha-pinned with the rubrics
(`criteria_digest`, `:287-295`, pinned by `tests/unit/test_samantha_day_sim.py`). Score functions
return `(verdict, evidence)` with `why` strings that are already class labels:
`score_raise_open` (`:388-413`) — "the stood-in nightly selector kept no candidate after a
week", "N candidates surfaced on the first open turn", **"the raise was injected and settled but
the reply never voiced it"** (`:406`), "the reply raised N follow-up topics"; `score_no_reraise`
(`:416-429`); `score_spacing` (`:432-446`); `score_when` (`:500-511`, "the packet holds no dated
dentist row"); `score_isolation` (`:577-590`, "the week reached the stranger").

**Artifact** (`samantha_day_sim_last.json`) [live]: `asks[{id, title, criterion, verdict,
evidence, synthetic_steps}]`, `turns[{tag, session, ms, reply_sha, reply_excerpt, reply_len,
error}]` (synthetic replies only), `landed{}`, `nights{d1,d2,d3}`, `candidates_after_nights`,
`overall{scored, passed, failed, skipped, errors, verdict, complete}`, `revision`, `teardown`.
Trend: **9 runs** 2026-10-03 → 10-04; FAIL counts **1r 6, 4 4, 6n 3, 6 2, 9 2, 3 1, 8 1**;
overall FAIL 7 / PASS 1 / SKIP 1 — the last run (`305be46c`, 02:39Z) is the first overall PASS.

**The first run's findings** (`samantha-bar.md:639-672`) [src] are a worked example of the
open-coding pass this record wants to make routine: ask **9** — "routed by the two-stage router
to `time` (`head_conf 0.9971`) and answered 'It's 10:41 PM.' in 488 ms — a misroute that states a
time the user never gave"; asks **6/6n** — the recall floor's shape regex did not match "Am I
still …" / "Do I still …" and the 4B did not call `recall_memory`; ask **1r** — the raise was
voiced as "I don't have any information about how your dentist appointment went" (a CANT_DO
shape on a *proactive* turn); plus three mechanism findings (open loops never reconciled with
corrections; most loops can never become candidates; a calendar request landing a week late).
Every one of those is a class, and every one was found by a person reading `evidence` and the
app log.

### 2.4 The router — confident misses and the parked self-train loop

- **Per-turn record.** `semantic_router.py` [src]: a dedicated logger `zoe.router_head_shadow`
  (`:38-40`); `_two_stage_log` (`:408-419`) writes one `router_two_stage {...}` JSON line per
  routed turn to the app log *and* appends to `ZOE_ROUTER_HEAD_LOG` (default
  `data/router_head_shadow.jsonl`, `:129-136`), rotated at 16 MiB × 4 (`:154-155`). The record is
  **keyed by an utterance hash**; raw text only under `ZOE_ROUTER_SHADOW_TEXT` opt-in, default off
  (`:241-256`) — the precedent for "hashes, never text" in the ledger. `_head_shadow` (`:363-404`)
  runs only in `shadow` mode (`:370`). [live]: the shadow file is 2.1 MB, last written today
  20:05; the current app-log segment (rotated 12:35 today) holds 20 `router_two_stage` lines.
- **Confidence and gating.** `router_two_stage.py` [src]: `ZOE_ROUTER_TWO_STAGE_GATE 0.5`,
  `ZOE_ROUTER_HEAD_MIN_CONF 0.70` (`:29-42`, `min_conf` `:135-160`), `gate_reason` ∈
  `chat_top | below_gate | low_conf` (`:163-178`), sidecar timeout 1.5 s → brain (`:39`). So the
  S1 miss (`head_conf 0.5371`) is now a `low_conf` gate; the day-sim ask-9 miss
  (`head_conf 0.9971`) is a **confident miss** — the class the tracker names "router retrain for
  confident misses" (`samantha-bar.md:334`) and the one no gate can catch.
- **No live label.** Nothing joins a `router_two_stage` line to whether the route was *right*.
  The only ground truth available at runtime is the person's next turn — a correction cue, a
  repeat of the same utterance, an issue report, a thumbs-down — and none of those reads the
  router line (§2.10).
- **The self-train loop** (`docs/knowledge/router-selftrain-loop.md`, driver
  `scripts/maintenance/router_selftrain.py`) [src]: `ZOE_ROUTER_SELFTRAIN` default **off**
  (`:14-16`; flag inventory `:410`); the ratchet — no accuracy regression, chat-FP zero, p50 <
  600 ms, replay gate *ran and passed*, corpus intact (`:33-45`); "the rig noise is bigger than
  the signal" (same GGUF: 86.4 % warm vs 71.6 % contended, `:48-70`); the frozen 81-case corpus is
  never a training input (`:105-110`); the mined candidate is "verbatim user utterances" and
  git-ignored (`:228-233`); the warm-start checkpoint "was not preserved" and must be
  re-established before the first real run (`:170-185`). [live]: `data/router_selftrain/` holds
  **one** candidate, `candidate_20260714T150709Z.jsonl` (July), `runs/`, `scoreboard.jsonl`. The
  loop's missing input is exactly a labelled miss list; the ledger's `router.misroute` class is
  that list, hashed, with the candidate text re-joined on the box at mining time.
- **The miss log** (`intent_router.py:1536-1548`) [src]: `logger.info("intent_miss")` + a
  PII-stripped `{text, ts}` row to `~/training/data/intent-misses.jsonl` — **21,279 rows** today
  [live] (21,254 at the sibling's count this morning). Most rows are ordinary chat that went to
  the brain, not failures [inf, sibling §2.2].

### 2.5 Runtime outcomes: the brain lane, tools, Flue aborts, the correction cue

- **BRAIN_LANE** (`services/zoe-data/brain_dispatch.py`) [src]: "every turn emits ONE greppable
  `BRAIN_LANE` line naming the lane attempted and the lane that served it" (`:61-64`), "exactly
  one, on every path a turn can leave by — one-shots included", so an interrupted turn logs
  `outcome=interrupted` from cleanup "instead of logging nothing at all" (`:69-76`); the outcome
  "is the client's verdict, not 'the generator finished'" — `ok / fallback / error` from the
  client's opt-in sink (`:77-87`; `zoe_flue_client.py:172-193`, `FLUE_OUTCOME_*`), because before
  that "an HTTP status error, a read timeout, an empty 200, a post-admission stream death — was
  logged `outcome=ok`". Lanes without a reporting client keep `outcome=dispatched` (`:89-91`).
  Format: `BRAIN_LANE lane_attempted=%s lane_served=%s outcome=%s reason=%s session=%s` (`:299`).
  The user-facing fallback is `_FALLBACK_TEXT` = "Sorry, I had trouble reaching my brain just
  now…" (`zoe_flue_client.py:167`). [live]: the current log segment holds 10 lines — 4 `flue …
  ok`, 4 `core … dispatched`, 2 `legacy … dispatched`. **This is the best runtime failure signal
  Zoe has, and it is a log line.**
- **Tool calls.** The brain's tools call back `POST /api/system/intent-dispatch`
  (`routers/system.py:2798`): a refused intent returns `{ok: False, result: "", reason}`
  (`:2819`), an exception logs `intent-dispatch failed intent=%s` and raises 500 (`:2832-2833`),
  success is `ok: result is not None` (`:2834`). On the Flue side each tool fetch is bounded at
  8 s by `AbortSignal.any([turn, timeout])` (`zoe-tools.ts:134-148`), and a tool call off a
  length-stopped message writes a `tool_outcome` with no `tool_results_committed`, after which
  "the very next reduce throws `ConversationRecordInvariantError` and kills the whole turn"
  (`capped-completions.ts:463-466`) — a failure that reaches zoe-data only as `outcome=fallback`.
  The sibling record's finding stands: **no live code classifies a reply as a refusal**; the
  `_CANT_DO_RE` lives in the replay harness only.
- **Flue abort / cancel.** `turn-guard.ts` [src]: `AbortOutcome.outcome ∈ requested |
  skipped:<why> | error` (`:52-56`), the skips being `no_submission_id`, `admission_in_flight`,
  `unknown_instance`, `superseded` (`:97-125`); one greppable line `FLUE_ABORT side=sidecar
  session= submission= reason= emitted_chars= deltas= outcome= latency_ms=` (`:172-177`), to
  journald (the sidecar's unit, `runtime-topology.md:32`). The client flag
  `ZOE_FLUE_ABORT_ON_CANCEL` is per-call, default off (`zoe_flue_client.py:340-341`; inventory
  `:150`). The 2026-10-03 Flue record measured the gap it closes: **"0 of 4,620 stored
  submissions ever recorded an abort"** (`flue-and-agent-runtimes-2026-10-03.md:26-28`) — every
  interrupted reply was generated to the end. An abort is not a failure, but an abort *rate* per
  class of turn is the interruptibility signal P7/P2 need, and it belongs in the same ledger.
- **The correction cue** (`services/zoe-data/memory_supersede.py`) [src]: the `correction` cue
  (`:95-105`) — `^(actually|wait|sorry|no)… (wrong|meant|mistake|not …)`, "i got that wrong",
  "that's not right", "i meant", "correction" — is `fact_level=False`: it acts "through the
  same-topic / exclusive-slot match in memory_digest + supersede_for_turn" (`:90-96`).
  `supersede_for_turn` (`:254-302`) logs `MEMORY_SUPERSEDE user= cue= superseded= new=` (`:296`)
  and resolves the matching open loop; a failure is a one-line warning (`:302`). "No, that's not
  what I said" matches (leading `no`, then `not` followed by a word the negative lookahead does
  not exclude) [inf, by reading the pattern]. What the cue does **not** do: ask whether the
  superseded fact came from *Zoe's* previous turn (she asserted it) or from the person's earlier
  statement (they changed their mind). The first is a Zoe failure; the second is life. Both are
  logged identically. [live]: 1 `MEMORY_SUPERSEDE` line in today's segment.
- **Frustration and issue reports.** `routers/chat.py` [src]: `_check_frustration` (`:736-760`)
  counts identical normalised messages per session within a 30-minute window
  (`_FRUSTRATION_WINDOW_S = 1800`, `_FRUSTRATION_THRESHOLD = 3`, `:617-619`) and fires
  `record_frustration_signal` (`evolution_notice.py:386-505`) → an `evolution_proposals` row of
  type `user_frustration` plus a Multica sync. `user_issue_report` (`intent_router.py:1505-1521`:
  "you got that wrong", "that didn't work", "X is broken", "you keep …", "you need to fix") →
  the handler (`:3640-3652`) **creates a Multica issue with the label `user-feedback`** and calls
  `record_user_issue` (`evolution_notice.py:508-626`), whose description is "User {user_id}
  explicitly reported a problem: {message}" (`:530`) with a 500-char `message_excerpt` (`:540`)
  — **the person's words leave the box verbatim** to the board, unhashed, unstripped (the miss
  log strips names/numbers/emails/URLs, `intent_router.py:1543-1546`; this path does not) [src,
  inf]. `chat_feedback` (`:3280-3298`) writes `(interaction_id, user_id, feedback_type,
  corrected_response)`; the only reader is the Prometheus counter `zoe_chat_feedback_count`
  (`memory_metrics.py:185-186`) [src].

### 2.6 The proposal machinery that exists

- **`evolution_proposals`** (`alembic/0004:69-83`): `id, type, title, description, evidence,
  target_patterns, status, multica_issue_id, proposed_at, reviewed_at, deployed_at,
  validation_result, next_review_at` [src, sibling §2.4]. Contract
  (`zoe_evolution_proposal.py:19-51`): signal types `user_request | repeated_failure | tool_gap |
  stale_capability | outcome_eval_failure | operator_note`; autonomy `observe → recall → suggest
  → prepare → execute → promote`; risk `low | medium | high | privileged`; status `draft →
  pending_approval → approved | rejected → verified | failed | retired`; a proposal "requires
  signals with evidence, a scored candidate, affected capabilities, a verification plan, a
  rollback plan" and `approval_gate.allowed_to_execute` is always false.
- **Writers today** (`evolution_notice.py`) [src]: (1) nightly intent-miss trigram clusters,
  7-day lookback, `_MIN_CLUSTER_SIZE = 3` (`:21-23`, `:52-77`, `:168-249`), signal
  `REPEATED_FAILURE`; (2) **agent health**: tiers with >10 % slow/error calls in 24 h from
  `llm_call_log` (`:300-383`, signal `OUTCOME_EVAL_FAILURE`) — but `llm_call_log` is written only
  by `zoe_agent.py:3668` and `chat_hermes_stream.py:208`, the legacy lanes; the live Flue lane
  writes no row, so this detector is blind to the brain that serves today [src, inf]; (3)
  frustration and (4) issue reports (§2.5). `ZOE_AUTO_APPROVE_THRESHOLD` default 0 (`:25-28`;
  inventory `:49`) — human review for all. Dedup is by *title string* (`_proposal_exists`,
  `:80-88`).
- **Readers / gates** [src]: `GET /api/agent/evolution/proposals` (`routers/system.py:1973`),
  `POST …/{id}/action` with `approve | reject | defer`, admin-only, "an approved+executable
  proposal is what the board lane implements and merges" (`:2053-2070`);
  `sync_evolution_proposal_to_multica(proposal_id, title, description, evidence, proposal_type,
  label_name="evolution-proposal", contract_snapshot)` (`multica_client.py:562-570`); admission
  (`multica_admission.py:39-66`): assignee = engineering agent, `schema 1`, `dispatch_approved`,
  no `blocked_reason`, `acceptance_criteria` **and** `evidence_expectations` present, not a
  parent, no live-checkout paths, not smoke/e2e, and for `source = evolution_proposal:<id>` the
  matching contract markers (`:69-104`; `execute`/`promote` need approval refs).
- **Schedules** [src]: the nightly notice is dreaming "Phase 6" (`multica_autopilot_sync.py:185-192`,
  `:447-449`); the weekly digest trigger is registered at `main.py:1279`
  (`proactive/triggers/evolution_weekly_digest.py`). Their loop contracts
  (`docs/knowledge/autopilots/evolution-{nightly-notice,weekly-digest}.md`) are read-only over
  data: "NEVER create, modify, or delete `evolution_proposals` rows", one issue per real gap, "a
  quiet night creates nothing", the digest "reports priorities, humans schedule them", "an empty
  week yields an explicit 'no new signal' digest".
- **Consumers** [live, sibling §2.8]: `~/.zoe/multica_dispatch_paused` exists (dated
  2026-08-04); `ZOE_MULTICA` default `false` (inventory `:270`); `ZOE_USE_OMNIGENT_EXECUTOR`
  default `0` (`:468`); `ZOE_MULTICA_CROSS_REVIEW` default `false` (`:279`). A proposal that is
  approved today reaches no executor.

### 2.7 The proactive lifecycle — what is and is not recorded

`services/zoe-data/proactive/engine.py` [src]: `fire_notification` (`:80-210`) — quiet hours
reschedule the row and log why (`:125-136`); a `proactive_pending` row is always created
(`:143-150`); a `notifications` row is inserted and broadcast (`:177-190`); `delivered =
subscribers_reached > 0 or in_app_fallback_ok` (`:196`); an undelivered reminder is a warning
(`:202-206`). APScheduler listeners record missed and errored jobs (`_on_job_missed` `:581`,
`_on_job_error` `:589`) and retry errored reminders (`:601`); `_slow_loop` (`:379`) runs the
sweeps. The response ledger `proactive_responses` (`alembic/0030:13-16`) records `accepted /
ignored / undelivered / unknown` — but only for the *spoken* brief path
(`arrival.py:495-552`, `PROACTIVE_RESPONSE trigger= user= outcome= window_s=`), which is off by
the owner's decision; the live `[RAISE]` path records only `surfaced_count` and
`last_surfaced_at` (`selector.py:487-491`). The P1 record's conclusion applies verbatim: the raise
voiced 0/5 under the pre-#1821 wording was "indistinguishable from success"; the day-sim's
"injected and settled but the reply never voiced it" (`samantha_day_sim.py:406`) is the only
place that failure has a name. [live]: 0 `PROACTIVE_RESPONSE` lines in today's segment.

### 2.8 The incident runbook — the human ledger

`docs/knowledge/incident-runbook.md` (741 lines, 22 entries) [src] is the failure ledger Zoe has
today, kept by hand: "each entry: signature → diagnosis → fix → prevention" (`:11-14`). Two
entries define the rules this record turns into columns:
- **§7, the zero-effect blind spot** (`:249-300`): "a heartbeat that carries only liveness is not
  a heartbeat" — every scheduled run now records `effect_count`, consecutive zeros accumulate into
  `zero_effect_streak`, alert after 5 (`ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS`), a PromQL rule
  `MemoryLoopZeroEffect`, and the prevention rule "any scheduled job's heartbeat must record
  **what the run did, not just that it ran**".
- **§11, the fast-path over-claim** (`:456-487`): the signature is a log pair (`SKYBRIDGE TIMING …
  reply='I found 0 contacts.'` after a `router_two_stage … "chat"` line); the fix was a router
  veto (`SKYBRIDGE_GATE … decision=allow|veto reason=`, `skybridge_service.py:1508`); the
  prevention is a shape regex plus a negative-control test, because "the replay gate cannot see
  this class (it never calls Skybridge)". The diagnosis ran 517 panel transcripts through the old
  classifier by hand — the open-coding pass of §1.1, done once, by a person.

### 2.9 Where the logs are (and what they can and cannot answer)

| Store | What | Rotation / retention | Reads it |
|---|---|---|---|
| `~/.zoe-logs/zoe-data.app.log` | app records: `BRAIN_LANE`, `router_two_stage`, `SKYBRIDGE_GATE`, `SKYBRIDGE TIMING`, `MEMORY_SUPERSEDE`, `PROACTIVE_RESPONSE`, `SEAM_CONTINUITY`, `intent_miss`, warnings | `RotatingFileHandler`, 5 backups ⇒ ~60 MiB ceiling (`logging_setup.py:46, 108-109`); rotated today 12:35 [live] | humans with `grep`; `router_shadow_report.py` / `router_shadow2_report.py` for the router lines |
| `~/.zoe-logs/zoe-data.{stdout,stderr}.log` | access lines only, "append, unrotated; **not** journald" (`runtime-topology.md:28`) | none | — |
| journald | `llama-server`, `kokoro-tts`, `functiongemma-router`, `flue-zoe-brain-2x` (incl. `FLUE_ABORT`), `flue-zoe-telegram` (`:29-33`) | journald defaults | humans |
| `~/.zoe/zoe-data-memory-loops.log` | the loops' run-complete lines with `effect_count`, `ZERO-EFFECT ALERT` (runbook §7) | append; 489 KB [live] | `GET /api/system/memory-loops/status` |
| `services/zoe-data/data/router_head_shadow.jsonl` | one hashed record per routed turn | 16 MiB × 4 | the miner, the shadow reports |
| `~/training/data/intent-misses.jsonl` | PII-stripped `{text, ts}` | none; 21,279 rows | `evolution_notice` nightly |
| `~/.cache/zoe/*_last.json`, `*_trend.jsonl` | the three harness artifacts (+ `recall_evidence_probe`, `latency_*`) | last = overwritten; trend = append | `voice_gate_check.py` (probe only); `router_selftrain` (probe only); humans |
| `~/.zoe-logs/crash-loop-watch.log`, `nondeterministic-test-failures.jsonl` | two ad-hoc failure ledgers already on the box (2.3 MB; Sep 27) [live] | none | humans |
| Postgres `chat_feedback`, `evolution_proposals`, `proactive_responses`, `llm_call_log` | thumbs / proposals / spoken-brief outcomes / legacy-lane calls | none | a counter; the admin endpoint; the arrival sweep; the agent-health detector |
| Multica board | `user-feedback` issues (verbatim), `evolution-proposal` issues | — | nobody since 2026-08-04 (consumer paused) |
| the Pi | the voice daemon logs to stderr (journal on the Pi) and optionally `ZOE_VOICE_LOG` (`zoe_voice_daemon.py:42, 115-119`) | on the Pi | humans over ssh |
| the panel | 401/403 storms, per-failure toasts (`common.js:408-410`, `ui-deep-review-2026-10-04.md:123`), WS reconnects | nowhere | — |

The pre-2026-07-20 caveat stands: "before the app log existed the root logger had no handler, so
every `logger.info()` was discarded" (`runtime-topology.md:28`) — the absence of a signal before
that date proves nothing.

### 2.10 What is NOT captured today (the gaps the ledger closes)

| # | User-visible failure | Trace today | Why it is invisible |
|---|---|---|---|
| 1 | Zoe says she can't do something she can (a live `CANT_DO`) | none on the live path | `_CANT_DO_RE` exists only in `replay_samples.py:73-80`; the day-sim's ask-1r reply ("I don't have any information…") was a CANT_DO on a proactive turn and only the judge saw it |
| 2 | A raise injected and never voiced; a question never answered | `surfaced_count` only (`selector.py:487-491`) | no delivery ledger (P1's first PR) |
| 3 | A confident misroute on a real turn (ask 9's `time` at 0.9971) | a `router_two_stage` line with no label | nothing joins the route to the next turn's correction / repeat / complaint |
| 4 | A correction that was Zoe's fault | `MEMORY_SUPERSEDE` with the cue name | the cue never looks at the previous *assistant* turn |
| 5 | A brain fallback / truncated reply the person heard | a `BRAIN_LANE … outcome=fallback\|error` log line | log only; rotated at 60 MiB; not counted, not joined to the session |
| 6 | A tool call that failed inside the brain (8 s timeout, 500, a length-stopped tool call) | `intent-dispatch failed` warning; Flue store (journald, "98 % harness junk") | no per-turn tool outcome reaches zoe-data; the record is in the sidecar's store |
| 7 | A thumbs-down | a `chat_feedback` row | read by a counter only; no `interaction_id` join to the turn's router / lane record |
| 8 | An issue report | a Multica issue (verbatim) + a proposal row | the board's consumer is paused; the text left the box unstripped |
| 9 | Which *kind* of utterance regressed in the probe | counts only | per-sample verdicts are swept by design (household audio) |
| 10 | The bar or day-sim did not run at all | nothing | neither has a timer (`scripts/setup/systemd/` has only `zoe-voice-regression.{service,timer}` [live]); runs happen when an agent runs them |
| 11 | A probe that failed because the box was cold / contended, not because Zoe regressed | `status=fail`, `revision`, medians | no restart-age / contention feature; the class is in a memory note (never probe within ~3 min of a sidecar restart), not in the artifact |
| 12 | Panel-side and Pi-side failures (toasts, auth storms, a wake with no turn, STT `EMPTY` on a real turn) | panel: nowhere; Pi: its own journal | no beacon from the kiosk; no join from the daemon to the server turn |

Two cross-cutting absences: there is **no fingerprint** anywhere (the nearest is `evolution_notice`'s
title-string dedup), and there is **no severity** — a judge `ERROR`, a probe `SKIP`, a user's
"that's wrong" and a 20-sample `ERROR` storm are all equally loud or equally silent.

---

## 3. Design — a failure ledger, classes, a digest that becomes proposals

### 3.0 The one principle

**Every failure Zoe can see becomes one row. Rows become classes. Classes become proposals. A
proposal needs a human yes. Zoe never writes the reflection; she writes the evidence.** The
operator's rule — fix the class, not the instance — is the schema: the class key is what a
proposal is *about*, the instances are its evidence, and a fix is accepted only when the class's
negative control goes red → green and the class stays quiet afterwards.

### 3.1 The failure ledger

One table, `failure_events` (Postgres, beside `chat_feedback` and `evolution_proposals`, so it
joins on `interaction_id` / `session_id` and the Zoe's Work board can read it):

| Column | Type | Rule |
|---|---|---|
| `id` | uuid | — |
| `source_key` | text, **UNIQUE** | the stable identity of the source event — **derived, never generated**: harness rows = `sha256(run_ref.commit \| run_ref.tree \| artifact timestamp \| scenario/ask/sample_ref \| why)`; runtime rows = `sha256(interaction_id or session_id+turn_seq \| signal \| class_key)`; heartbeat rows = `sha256(loop \| run timestamp \| signal)`. Every writer inserts `ON CONFLICT (source_key) DO NOTHING`, so re-ingesting a `_last` or trend run, a retry after an uncertain DB result, or a backfill overlapping live ingestion changes nothing |
| `ts` | timestamptz | event time (the artifact's `timestamp` for harness rows) |
| `signal` | text enum | §3.1a |
| `source` | `probe \| bar \| daysim \| runtime \| heartbeat \| member` | where it came from — **witness metadata, never part of the class key** (§3.2) |
| `severity` | `S1 \| S2 \| S3 \| S4` | §3.1b; assigned by the emitter, never by a model |
| `fingerprint` | sha256 hex | §3.2 — deterministic over the class key only |
| `class_key` | text | the human-readable semantic key the fingerprint hashes — `<layer>.<kind>\|<discriminator>`, e.g. `router.misroute\|calendar`; shared by a bar S1 witness and a live confident misroute |
| `member_hash` | text, nullable | `sha256(user_id + salt)`; null for harness and heartbeat rows; the demo-user allowlist regex is refused (harness rows carry `source` instead) |
| `session_id` / `interaction_id` / `sample_ref` | text, nullable | the join key, or the probe sample's filename hash — **never text** |
| `evidence` | jsonb | aggregates only: `head_conf, routed, shortlist[], gated, lane, outcome, reason, verdict, why, ms{…}, sidecar_age_s, mem_available_mb, n_samples, judge_sha` |
| `run_ref` | jsonb, nullable | `{artifact, commit, tree, dirty}` from the harness `revision` block |
| `pr_ref` | text, nullable | set when a fix PR names the class (§3.3) |

**What may never be in a row** — pinned by a `ci_safe` test that scans every emitter's payload
shape and the backfill output: any string longer than 80 characters that is not a known `why`
constant; anything matching the miss log's four strip patterns (`intent_router.py:1543-1546`);
a reply, a transcript, a candidate text, a corrected fact. Hashes, counts, enums, confidences,
milliseconds. This is the router shadow log's rule (`ZOE_ROUTER_SHADOW_TEXT` default off) applied
to the whole ledger, and it is what lets the evidence pack leave the box in a ticket.

**Sampling.** Per `(fingerprint, day)` cap of 50 rows; above it, a single `suppressed_count`
row per day (Sentry's rate-limited issue, not a dropped one). **Retention.** Events 90 days;
the nightly class rollup (`failure_classes`: `fingerprint, class_key, severity, first_seen,
last_seen, count_7d, count_28d, count_total, state, proposal_id, pr_ref, members_7d (a count)`)
is kept forever and is what W16 reads.

**Idempotence (the ingester contract).** A writer never invents identity: it derives `source_key`
from the event and lets the UNIQUE constraint decide. The backfill dry-run prints, per class,
`would_insert` and `already_present`; a second dry-run over the same files must print
`would_insert = 0` everywhere. Every threshold in §3.3 counts **distinct `source_key`s**, never
rows seen, so a duplicated run can never cross a proposal threshold on its own.

#### 3.1a Signals (what writes a row)

| Signal | Source | Severity | Where it comes from today | Class key (shared by harness and live witnesses, §3.2) |
|---|---|---|---|---|
| `replay.cant_do`, `replay.error`, `replay.empty_storm`, `replay.speed` | probe | S2 / S2 / S4 / S3 | `voice_regression_last.json` + per-sample verdicts emitted *into the ledger* by `measure_voice` before the sweep (hash of the sample filename, verdict, `stt_ms`, `brain_ms`; the regex alternative a `CANT_DO` matched) | `CANT_DO` → `brain.refusal\|<regex-alt>\|<routed-or-chat>`; `ERROR` → `brain.fallback\|<reason>\|cold:<sidecar_age_s<180>\|contended:<mem<700MB>` (the same keys the live BRAIN_LANE rows write); speed → `voice.speed_regression\|<stage>`; empty storm → `instrument.empty_storm` |
| `replay.no_evidence`, `replay.skip`, `replay.non_pass_alert` | probe | S4 | `status`, `reason`, `non_pass_alert` | `instrument.probe\|<reason-class>` |
| `bar.fail`, `bar.error`, `bar.regression` | bar | S2 (regression S1) | `scenarios[].verdict/evidence.why`, `compare.regressions`; for a recall-shaped FAIL the ingester also reads the ask turn's `router_two_stage` record from the shadow log (bar sessions and times are known) and, when the ask left the brain lane, keys the row as a misroute | the `why` → key table (§3.2 item 2): e.g. S1 needles missing + routed away → `router.misroute\|<routed>`; S4 → `memory.thread_not_acknowledged`; scenario id only in `evidence.harness_ref` |
| `daysim.fail`, `daysim.error` | daysim | S2 | `asks[].verdict/evidence.why`, same shadow-log join | same table: ask 9 → `router.misroute\|time`; 1r unvoiced → `proactive.raise_unvoiced`; 1r "no information" → `brain.refusal\|no_information\|raise`; 6/6n → `memory.recall_floor_miss\|<shape>`; ask id in `evidence.harness_ref` |
| `router.low_conf`, `router.timeout`, `router.misroute` | runtime | S4 / S3 / **S1** | `router_two_stage` record (hash, routed, conf, shortlist, gated) joined to the **next member turn** within 90 s: a correction cue, a `user_issue_report`, a thumbs-down, or a repeat of the same utterance hash ⇒ `misroute`; else no row | `router.misroute\|<routed>` (`shortlist[0]`, `head_conf` in evidence) — the same key the bar/day-sim witnesses write |
| `brain.fallback`, `brain.error`, `brain.interrupted` | runtime | S1 / S2 / S4 | the BRAIN_LANE outcome sink (`brain_dispatch.py:77-87`) — a second sink, no second log line; the live row also carries `sidecar_age_s` so a post-restart fallback groups with the probe's | `brain.<outcome>\|<reason>\|cold:<…>` |
| `brain.refusal` | runtime (shadow) | S1 | the shared refusal classifier (`_CANT_DO_RE` lifted out of `replay_samples.py:73-80` into a module both import) run on the **served reply text** at the one sink every brain lane already passes through: `brain_dispatch._LaneRecord` (`:308-340`), which wraps `_flue_streaming_with_failover` (`:552`) and the one-shot path in `try/finally` and is reached by chat (`routers/chat.py:355-356, 2035, 2171, 2951`), voice (`voice_tts.py:4299`) and the Telegram lane (re-slotted through `/api/chat`, `labs/flue-zoe-telegram-2x/src/brain.ts:2-12`). The record gains a capped (2 KB) accumulator of the yielded deltas and classifies in `emit`, after the stream is paid out — **shadow first**: it writes a row, never changes the reply. `fast_tiers.resolve()` is **not** the place: it sees only deterministic Tier-0/1/1.5 replies and returns `None` whenever the brain should run (`fast_tiers.py:273-290`), so a hook there would miss every brain-generated refusal (the day-sim's "I don't have any information…") | `brain.refusal\|<regex-alternative-id>\|<context: raise\|routed\|chat>` |
| `tool.empty_result` | runtime | S2 | the second, smaller hook on replies that never reach a brain: `fast_tiers.resolve()`'s `DispatchResult.reply` and the voice intent handlers' `reply_text` (`voice_tts.py:3466-3471`), matched against the deterministic empty-extractor shapes ("I found 0 contacts", an empty calendar — runbook §11) | `tool.empty_result\|<intent>` — a different class from a refusal |
| `tool.refused`, `tool.failed`, `tool.timeout` | runtime | S2 | `intent_dispatch` (`system.py:2819, 2832-2833`); the Flue tool wrapper's 8 s abort reported back as a `reason` | `tool.<signal>\|<intent>\|<reason>` |
| `flue.abort` | runtime | S4 (a counter, not a failure) | the `FLUE_ABORT` outcome once A1 (`ZOE_FLUE_ABORT_ON_CANCEL`) lands | `flue.abort\|<reason>\|<outcome>` |
| `proactive.undelivered`, `proactive.ignored`, `proactive.raise_unvoiced`, `proactive.raise_refusal` | runtime | S2 / S4 / S1 / S1 | the P1 delivery ledger (`proactive_deliveries`) — the `undelivered` detection is the day-sim's `topics_in` check moved into the settle path (P1 §3.5); a refusal on a raise turn is the `brain.refusal` classifier with `context = raise` | `proactive.undelivered\|<klass>`, `proactive.ignored\|<klass>`, `proactive.raise_unvoiced`, `brain.refusal\|<regex-alt>\|raise` — the keys the day-sim 1r witnesses write |
| `memory.zoe_wrong` vs `memory.user_changed` | runtime | S1 / — | the correction cue (`memory_supersede.py:95-105`) **joined to the previous assistant turn**: if that turn's reply contained the superseded slot's value (string match on the fact value, on the box, never stored) ⇒ `zoe_wrong`, else `user_changed` (no row) | `memory.wrong_fact\|<attribute>` (lane in evidence) |
| `member.issue`, `member.frustration`, `member.thumbs_down` | member | S1 / S2 / S2 | the existing three writers, re-pointed to also write a row with the regex alternative that matched (`_USER_ISSUE_RE`), the repeat count, or the `feedback_type`; **the text stays in Postgres; the Multica issue carries the class key, not the message** | a member signal is a *label on the previous turn*: when the ledger already holds a row for that turn (a misroute, a refusal, a fallback) the member row takes **that row's class key** and becomes its witness; otherwise `member.<signal>\|<regex-id or feedback_type>\|<routed-or-lane of the previous turn>` |
| `job.zero_effect`, `job.missed`, `job.errored` | heartbeat | S3 | runbook §7's `zero_effect_alert`; APScheduler `_on_job_missed` / `_on_job_error` (`engine.py:581-601`) | `job.<signal>\|<loop>` |
| `harness.not_run` | heartbeat | S4 | a daily check: no `samantha_bar_trend` / `day_sim_trend` line in N days (gap 10) | `instrument.harness_not_run\|<name>` |

#### 3.1b Severity (assigned by the emitter, from the signal, never by a model)

- **S1 — a member heard something wrong or useless**: a confident misroute confirmed by the next
  turn, a live refusal, a brain fallback served, a raise that was voiced as "I don't have any
  information", a correction of Zoe's own assertion, an issue report. Google SRE's
  "user-visible degradation" trigger.
- **S2 — a silent loss or a harness FAIL**: a bar/day-sim FAIL, an undelivered proactive item, a
  tool failure the brain papered over, a thumbs-down, a frustration repeat.
- **S3 — degraded**: speed regressions, router timeouts, zero-effect loops, missed jobs.
- **S4 — instrument / counter**: skips, no-evidence runs, a probe during warm-up, aborts,
  low-confidence gates, "harness did not run". Never a proposal on its own; always in the digest
  (the *verify your instruments* rule: a gate that keeps skipping is itself the finding).

### 3.2 Classes — "fix the class, not the instance"

The **fingerprint** is `sha256(class_key)` and the `class_key` is built the Sentry way:

1. **In-app frames only — and `source` is a system frame.** The key never contains the member,
   the session, the timestamp, the text, the sample name, the commit, the scenario or ask id, or
   where the witness came from (`source`). It contains the **layer** (`router | brain | memory |
   proactive | tool | voice | job | instrument`), the **failure kind**, and one stable
   **discriminator** (the routed tool, the regex alternative id, the attribute, the loop, the
   outcome reason) plus the instrument features that change the diagnosis (`cold`,
   `contended`): `<layer>.<kind>|<discriminator>`. A bar S1 witness and a live confident misroute
   to `calendar` are therefore **one class**, `router.misroute|calendar`, with two witnesses of
   different `source` — which is what the "one live + one harness" trigger in §3.3 needs, and
   what per-source prefixes (`bar|…`, `daysim|…`, `proactive|…`) would have made impossible.
2. **The `why` is the message-with-wildcards, and it maps to a semantic key.** The bar's and
   day-sim's `why` strings are constants in source (`samantha_bar.py:456-458`,
   `samantha_day_sim.py:400-408`); a `why → class_key` table in the ingester turns each constant
   into the shared key (S1 needles missing + routed away → `router.misroute|<routed>`; S4 "not
   acknowledged" → `memory.thread_not_acknowledged`; 1r "never voiced it" →
   `proactive.raise_unvoiced`; "{len(raised)} candidates surfaced" → `proactive.raise_multiple`).
   A `why` without a mapping is still ledgered, as `harness.unclassified|<id>|<why>`, and the
   completeness test (`ci_safe`: every `why` the scorers can emit has a mapping) is red until the
   operator adds one — Hamel's open-coding residue, made visible.
3. **Rules change forward only.** A key is written once; re-fingerprinting old rows is a
   deliberate migration with a `fingerprint_version` column, never a silent re-group.
4. **Instrument features split the class.** The 2026-10-04T02:29Z storm fingerprints as
   `brain.fallback|flue|cold:1` (sidecar restarted 120 s earlier) — a different class from
   `brain.fallback|flue|cold:0`, which *is* a brain regression, and the same key a live
   `BRAIN_LANE outcome=fallback` row writes in the minutes after a restart. The probe gains two
   cheap reads at run time: `systemctl --user show -p ActiveEnterTimestamp flue-zoe-brain-2x` and
   `MemAvailable` (already read, `mem_available_mb`, `:138`) — into `evidence`, into the key.
5. **Harness and live witnesses share the class; `source` and severity tell them apart.** The
   day-sim ask-1r reply "I don't have any information about …" and a live refusal on a greeting
   raise share the regex alternative and the `raise` context, so both are
   `brain.refusal|no_information|raise`; the harness row is S2 with `source = daysim`, the live
   row S1 with `source = runtime`. One class, two witnesses — the pair a proposal needs.
6. **One class, many fixes.** S4 was fixed three times (#1756, #1762+#1763, #1770) under one
   `why`; in the ledger that is one class with three `pr_ref`s and two `regressed` reopenings,
   never three classes. A backfill that splits a `why` by round is red (§4).

**Class states** (Sentry's, on `failure_classes.state`): `new` (first seen this week) → `active`
→ `proposed` (a proposal row exists) → `fixed` (its PR merged and deployed; `pr_ref` set) →
`regressed` (a new row after `fixed` — reopens with the PR ref in the evidence pack, severity
raised one step: a fix that did not stick is worse than the original). `muted` is operator-only
(a known instrument class such as `instrument.harness_not_run|daysim` during a planned pause)
and expires in 30 days.

### 3.3 The weekly digest → proposals with an evidence pack

The existing weekly trigger (`main.py:1279`) keeps its contract (one digest, read-only over the
data, "humans schedule") and gains a ledger section. Separately, a **proposal writer** runs after
the nightly rollup and emits `evolution_proposals` rows of a new `type = failure_class` under
SRE-shaped triggers:

| Trigger | Rule | Latency |
|---|---|---|
| S1 class | ≥ 2 distinct `source_key`s in 7 days from ≥ 1 member **or** 1 live witness + 1 harness witness of the **same class** (possible only because the key excludes `source`, §3.2 item 1) | next nightly |
| Harness regression | any `bar.regression` or a day-sim ask that was PASS on the previous run and FAIL now | next nightly (the bar is already red; the proposal is the ticket) |
| S2 class | ≥ 3 instances in 7 days | weekly |
| S3 class | ≥ 5 in 7 days, or a `non_pass_streak ≥ 3`, or `zero_effect_alert` | weekly |
| S4 | never a proposal; always a digest line ("the day-sim has not run for 9 days") | weekly |
| `regressed` | immediately, with the old `pr_ref` | next nightly |

Caps: **≤ 3 new proposals per week**, one per class, dedup by fingerprint (replacing the title
dedup at `evolution_notice.py:80-88`); every count is over distinct `source_key`s; a `rejected` class is not re-proposed for 30 days unless
its severity rises; `ZOE_AUTO_APPROVE_THRESHOLD` stays 0.

**The evidence pack** is the proposal's `evidence` JSON and the whole of what a ticket may carry
off the box:

```
{ class_key, fingerprint, severity, state,
  counts: {7d, 28d, total, members_7d},
  first_seen, last_seen,
  witnesses: [ {source, source_key, ts, run_ref|interaction_ref, harness_ref?, evidence} × ≤5 ],   # refs + aggregates, no text
  harness: { refs: [S1, "ask 9"], why, judge_sha, criteria_sha, last_pass_commit, first_fail_commit },
  suspected_layer: router | brain | memory | proactive | voice | tool | instrument,   # from the signal, a lookup, not a model
  runbook_match: "incident-runbook.md#11" | null,                        # by class_key prefix table
  negative_control: "the test that must be red before the fix and green after",
  acceptance: ["<negative control> green", "class count 0 for 7 days after deploy", "bar/day-sim unchanged"],
  tier: L1 | L2 | L3,                                                     # §3.4, from suspected_layer
  prior_fix: {pr_ref, merged_at} | null }
```

`suspected_layer` is a table lookup from the signal (a `router.misroute` is `router`; a
`memory.wrong_fact` is `memory`; a `brain.fallback|…|cold:1` is `instrument`), not an inference —
Reflexion's lesson that the explanation helps and the confabulation caution that a small model
must not write it. The brain is never asked to explain a failure; the operator and the cloud
worker read the pack.

For the operator the digest is also an inbox **Notify** line (P1's channel) once
`ZOE_PROACTIVE_INBOX` exists: "Three things I kept getting wrong this week — on the Work screen."
For a member, nothing — proposals are system-to-operator.

### 3.4 Approval gates — who may say yes

From the self-building record's ladder (§4c there), mapped to *fix* proposals:

| Tier | What the fix touches | Who approves the merge | Who may approve the proposal |
|---|---|---|---|
| **L1** | a test, a corpus row (`phrases.jsonl`, `train_misses.jsonl`), a regex shape, a flag default *under a flag* | deterministic gate + cross-review + one panel tap on "Zoe's Work → Needs your feedback" (the `skill-approved` label pattern) | the operator (any bound member may *see* it) |
| **L2** | zoe-data code off the voice path; a prompt block's wording; a selector rule | human on GitHub, Greptile label, the usual rules | the operator |
| **L3** | anything in `VOICE_PATH_PATTERNS`; memory writes / supersession; proactive delivery; the router heads or GGUF | human on GitHub + the tree-bound replay gate + Greptile + a bar/day-sim compare on the PR head | the operator, with the record's "Decisions" if it changes a default |
| **rocks** | a model swap, a threshold on the STT/brain | **not a proposal** — a digest line and a decision for the owner | — |

The proposal endpoint's admin check (`routers/system.py:2053-2066`) is the gate; a member never
sees an approve button. "Who may say yes" is therefore the one-line answer the sibling record's
open question 1 also needs: **the account holder, always, for fixes** — because a fix is code,
and code lands on the running box.

### 3.5 Proposal → ticket on the existing pipeline

On `approve`, the proposal writer calls `sync_evolution_proposal_to_multica`
(`multica_client.py:562-570`) with a description that is the evidence pack plus the ticket block
`multica_admission` demands (`:39-66`): `schema: 1`, `dispatch_approved: true`,
`acceptance_criteria` (= the pack's `acceptance`), `evidence_expectations` (= `ci_safe` test for
the negative control; the replay/bar/day-sim artifact bound to the PR head via `revision`; the
cross-review report), `source: evolution_proposal:<id>` with the matching contract markers
(`:69-104`; autonomy `prepare`, risk from the tier), assigned to the engineering agent id. The
executor is the Omnigent lane the sibling record unparks (its PR 1: the kill switch, the executor
unit, the chat→board seam); the brief is inline via `-p` (the kick recipe) and tells the worker:
reproduce in a worktree with the harness (bar/day-sim have `--dry-run`; the probe is live-only, so
a probe-dependent fix asks for one operator run as a **Question**), write the negative control
first and show it red, fix the class, open a **draft** PR, cite the class key in the PR body so
the ledger can set `pr_ref` and later detect `regressed`. Budget as the sibling: 2 attempts,
2,700 s wall, then "parked" with the reason as a digest line.

What leaves the box: the pack (hashes, counts, keys, `why` constants, artifact *paths*, commits)
— the fleet data-class rule (`samantha-evolution-plan.md:1070-1099`). What does not: the issue
report's text (today's `record_user_issue` description, `evolution_notice.py:530`, is replaced by
the class key; the text stays in the local proposal row), any reply, any sample.

### 3.6 How Zoe reports — and when she asks

- **Progress is pull.** The Zoe's Work screen (`touch/home.html:1685-1716`, sibling §2.3) gains a
  "Fixing" column fed by `failure_classes` joined to tickets: *Needs your feedback* (a proposal
  awaiting the operator, or a parked fix with its one question), *In progress* (ticket running),
  *In review* (draft PR), *Recently fixed* (merged + deployed + quiet 7 days). Nothing is pushed.
- **One Notify per fixed class**, only after the 7-day quiet period, in the person's words, never
  the ticket id: "the thing where I answered the time instead of your appointment — that's fixed,
  and it hasn't happened since." The sibling's rule applies — Zoe never claims "done" before
  `/health` is green and the class has been quiet.
- **When Zoe decides without asking:** writing a row (always); grouping; proposing to the
  operator; muting an instrument class she can prove (`cold:1`).
- **When Zoe asks the operator:** every proposal (approve / reject / defer — the endpoint that
  exists); any fix that needs a live probe run; a parked fix's one specific question.
- **When Zoe asks a member — only to label, never to approve.** Behind `ZOE_FAILURE_ASK`
  (default off, needs the P1 inbox): after a *confident* S1 signal on a live turn (a
  `router.misroute` whose next turn was a correction cue, or a `brain.refusal` on a routed
  domain), Zoe may ask **once**, on the next turn or by pull, "Did I get that wrong just now?" —
  yes / no / "it's fine". The answer is a ledger label (`member.label = confirmed | denied`),
  Replika's reaction with a reason, FROST's "you only observe the outcome of the one you asked
  about". Pacing is Nomi's: one unanswered question, then wait; at most one a day per member;
  never on a guest; never when `busy`/`listening`; never for S2–S4. Denied labels feed the
  fingerprint's precision (a class whose labels are mostly `denied` is demoted to S4), which is
  the only learning this record allows the ledger to do on its own.
- **What Zoe never says:** "I'm improving myself", a count, a class key, anyone else's failure.

### 3.7 Flags (all default OFF, read per call)

| Flag | Scope | Does |
|---|---|---|
| `ZOE_FAILURE_LEDGER` | zoe-data + harness `--ledger` | write `failure_events` rows from the three harness ingesters and the heartbeat signals; **ships first**; no behaviour change |
| `ZOE_FAILURE_LEDGER_RUNTIME` | zoe-data | the runtime emitters: BRAIN_LANE sink, intent-dispatch, correction join, the three member writers, the shadow `brain.refusal` classifier at the lane record (+ the `tool.empty_result` fast-tier hook), the `router.misroute` join |
| `ZOE_FAILURE_CLASSES` | zoe-data (dreaming phase) | nightly rollup into `failure_classes`, states, `regressed` detection |
| `ZOE_FAILURE_PROPOSALS` | zoe-data | the trigger table → `evolution_proposals(type=failure_class)` + the digest section + the operator Notify line (needs `ZOE_PROACTIVE_INBOX` for the line; the row is written regardless) |
| `ZOE_FAILURE_TICKETS` | zoe-data | approve → Multica ticket through admission (needs `MULTICA_WORKSPACE_ID` and the unparked executor) |
| `ZOE_FAILURE_ASK` | zoe-data | the member label question (needs `ZOE_PROACTIVE_INBOX`, `ZOE_PROACTIVE_LEDGER`) |
| `ZOE_FAILURE_RETENTION_DAYS` / `_DAILY_CAP` | zoe-data | 90 / 50 |

Nothing here enqueues speech; `ZOE_PROACTIVE_SPOKEN` stays 0; `ZOE_AUTO_APPROVE_THRESHOLD` stays 0.

### 3.8 Cost

| Piece | Jetson RAM | CPU / latency | Pi |
|---|---|---|---|
| ledger INSERT | 0 | one indexed insert per failure; failures are tens/day at most; harness ingestion reads files already written | — |
| `brain.refusal` shadow classifier | 0 | a ≤2 KB delta accumulator in `_LaneRecord` and one compiled regex in `emit`, µs, after the stream is paid out | — |
| `router.misroute` join | 0 | an in-memory ring of the last routed hash per session (the frustration tracker's shape, `chat.py:617-620`) | — |
| correction join | 0 | one string search in the previous assistant reply held in the session context already | — |
| nightly rollup | 0 | one `GROUP BY fingerprint` in the dreaming job, ms | — |
| digest / proposals | 0 | a SELECT; a row write | — |
| probe features | 0 | one `systemctl show` and the existing `MemAvailable` read per probe run | — |
| the ask | 0 | an inbox Review item (P1's path) | 1 phrase → Moonshine check on the corpus |

No model loads, no embedding, no new process, no cgroup change; nothing touches the brain rock,
the STT rock, the W3 gate or the voice stack's memory protection.

---

## 4. Measurement — the ledger must reproduce the known failures, with negative controls

**Backfill is the first instrument.** `scripts/maintenance/failure_ledger_backfill.py --dry-run`
reads `samantha_bar_trend.jsonl` (27 runs), `samantha_day_sim_trend.jsonl` (9), the two `_last`
artifacts and `voice_regression_trend.jsonl` (484) and prints, per class, `would_insert` and
`already_present`. Run twice back to back, the second pass must print `would_insert = 0` for every
class (the `source_key` contract). From the files as they are today it must produce:

| Known failure (2026-09-28 → 10-04) | Expected class row | Evidence the backfill must carry |
|---|---|---|
| **S1 router misroute** | `router.misroute\|calendar` ×5 (09-28/29), witnesses `source = bar`, `harness_ref = S1`, keyed through the shadow-log join to each ask turn's `router_two_stage` record (`data/router_head_shadow.jsonl`, 2.1 MB, bar session ids and times known); state `fixed` once `pr_ref` #1770 is attached by hand in the test | **the same class a live confident misroute to `calendar` writes** — no live witness exists yet (no runtime emitter), and the backfill must report `witnesses: bar 5, live 0`, not invent one. If the shadow-log join fails (rotated away), the row lands in `harness.unclassified\|S1\|needles_missing` and the test is red |
| **S4 emotional thread** | `memory.thread_not_acknowledged` ×5 — **one class**, with `pr_ref`s #1756, #1762+#1763, #1770 attached by hand in the test and two `regressed` reopenings between them | the `why` is the same constant across all three rounds; a backfill that yields more than one class for it is red (fix the class, not the instance — §3.2 item 6) |
| **Day-sim ask 9 (confident misroute to `time`)** | `router.misroute\|time` ×2, witnesses `source = daysim`, `harness_ref = 9` | **the same class a live confident misroute to `time` writes** (day-sim ask F1 adds the live witness and the pair must share one fingerprint); `evidence.head_conf` comes from the shadow-log join, else `null` and the gap is listed |
| **The ERROR storm, 2026-10-04T02:29:21Z** | `brain.fallback\|flue\|cold:1` (14) **if** the restart age can be recovered for the backfill (journal), else `cold:unknown` — a separate class from the 2026-09-29 `ERROR 20` run | the row must carry `revision.commit`, `verdicts`, `reason`; the 11:59Z pass run on the same day writes **zero** rows |
| Day-sim 1r | `proactive.raise_unvoiced` ×6 (the `why` at `samantha_day_sim.py:406`); the judged "I don't have any information" replies → `brain.refusal\|no_information\|raise` | the second key is the one a live refusal on a raise writes; two sources, one class |

**Negative controls (each must go red, or the instrument proves nothing):**

1. **Flag off ⇒ byte-identical.** With every `ZOE_FAILURE_*` unset: zero rows, the harness
   artifacts byte-identical to today's contract (the `voice_gate_check` contract test still
   passes), no new log line.
2. **A pass run writes nothing.** Ingest the 2026-10-04T11:59Z artifact ⇒ 0 rows; ingest the
   first all-PASS day-sim (02:39Z) ⇒ 0 FAIL rows (SKIPs are S4 counters, allowed).
3. **Break the fingerprint ⇒ classes collapse.** Drop the `why` from the key in a test build ⇒
   S4's two FAIL shapes (`memory.thread_not_acknowledged`, `memory.verbatim_quote`,
   `samantha_bar.py:456-458`) merge, and day-sim 6 / 6n ("asserts the superseded half-marathon"
   / "asserts the negated fact as current") merge ⇒ the class-count assertions are red.
4. **Text cannot enter.** Feed an emitter a payload with an 81-char free string or a `[NAME]`-shaped
   token ⇒ the PII guard raises and the row is refused; the `ci_safe` scan over emitter payload
   shapes is red if any emitter can pass a reply field.
5. **Instrument split.** Replay the storm with `sidecar_age_s = 3600` ⇒ class `cold:0` ⇒ it *is*
   proposable (S2); with `sidecar_age_s = 120` ⇒ `cold:1` ⇒ S4, never proposed. Both branches
   asserted.
6. **Proposal thresholds.** One S1 instance ⇒ no proposal; two in 7 days ⇒ one; a third ⇒ still
   one (dedup by fingerprint); a `rejected` class with 10 new rows inside 30 days ⇒ none.
7. **Admission refuses a pack without acceptance criteria.** Strip `acceptance` ⇒
   `ticket_is_dispatch_approved` is False (`multica_admission.py:57`).
8. **The ask is bounded.** In the day-sim, after a seeded confident misroute plus a correction
   cue, exactly one Review item "did I get that wrong" exists; a second misroute the same day
   adds none; the stranger's panel holds none.
9. **Idempotence.** Ingest the same `_last` artifact twice, then run the backfill over the trend
   file that contains the same run ⇒ the `failure_events` count is unchanged, every class reports
   `already_present = rows, would_insert = 0`, and no proposal threshold moves; a retried runtime
   write with the same `source_key` ⇒ one row. Remove the UNIQUE constraint in a test build ⇒ the
   count doubles ⇒ red.
10. **Cross-source class.** The live `router.misroute|time` row seeded by F1 and the ask-9 harness
    row share one fingerprint; add `source` to the key recipe in a test build ⇒ two fingerprints
    ⇒ the "1 live + 1 harness" S1 trigger never fires ⇒ red.

**Day-sim additions:** ask **F1** — after the seeded ask-9 misroute the ledger holds exactly one
`router.misroute` row for the demo user (joined on the correction turn) and zero for the
stranger, and its fingerprint equals the ask-9 harness row's; ask **F2** — a
`brain.refusal|no_information|raise` row for the 1r "no information" reply when the shadow
classifier is on — a brain-generated reply, so it must be seen at `_LaneRecord`, not at the fast
tier — and the reply byte-identical to the classifier off.

**W16 counters** (deterministic, from `failure_classes`, per week): classes new / active /
proposed / fixed / regressed; S1 instances per member-week (the one number that says whether
Zoe is getting *worse to live with*); proposals written / approved / rejected (the
false-proposal rate — Google's "is the action plan appropriate"); median days class → proposal →
merge → quiet; instrument share (S4 / all); harness coverage (classes with a harness witness /
classes with a live witness). The first two weeks of ledger are the baseline; the proposal
writer is judged against it.

**Judge validation** (Hamel's rule): for any class whose witnesses are judge verdicts only, the
digest shows the judge's agreement with the operator's label on the asked rows; a class below
80 % agreement is demoted to S4 until the rubric is fixed — the ledger never promotes a judge it
has not measured.

---

## 5. Go / no-go against the VISION principles

| Principle | Verdict | Why |
|---|---|---|
| 1 Rocks fixed | GO | no model, no embedding; the ledger feeds the router's ratchet (the one rock allowed to improve) with the labelled misses it has never had |
| 2 Local, private, fast | GO with the data-class rule | rows are hashes/counts/enums; the pack leaves the box only on an operator's approve and contains no text; today's verbatim `user-feedback` issue is *removed* by this design |
| 3 Lab-prove before prod | GO | seven flags, default off; backfill dry-run first; day-sim asks F1/F2; ten negative controls |
| 4 Build it to STICK | GO — this is the principle the idea serves | `regressed` reopens a class with its PR; W16 counts fixed-and-quiet, not merged; the instrument features keep the storm class honest |
| 5 Capture, don't lose | GO | 39 failing probe runs, 13 bar FAILs, 19 day-sim FAILs and every BRAIN_LANE fallback exist today and are compared by nobody; the ledger is the pin |
| 6 Borrow the piece | GO | Sentry's fingerprint, SRE's triggers, Anthropic's 20–50-from-failures, Hamel's taxonomy-and-count, Hermes' staged approval, Replika's labelled thumbs — no framework, no hosted tracer |
| 7 Right tool | GO | `evolution_proposals`, the admin action endpoint, `multica_admission`, the weekly trigger, the dreaming phase, the harness `why` strings, BRAIN_LANE's sink, the shadow log's hashing rule — all reused |
| 8 Voice first, touch second, no keyboard | GO | the only member-facing piece is one spoken yes/no; everything else is the operator's board and the Work screen |
| 9 Understand before you change | this record | the chain is traced; the first PR is a ledger and a backfill, not a behaviour |
| Owner rule: never speaks unprompted | GO, strengthened | no path enqueues speech; the fixed-class Notify rides the inbox pull |
| Owner rule: fix the class, not the instance | GO — the schema | a proposal is about a fingerprint; its acceptance is the class going quiet |
| Owner rule: verify your instruments | GO | S4 + `cold`/`contended` features; `harness.not_run`; judge agreement demotion |

**No-go items inside the idea:** letting the 4B write a "reflection" into the ledger or the pack;
storing replies, transcripts or candidate texts in any row; a proposal that auto-approves
(`ZOE_AUTO_APPROVE_THRESHOLD > 0`); member-approved fixes; any spoken "I noticed I got X wrong"
push; sending `chat_feedback.corrected_response` or the issue text to the board; a ticket without
a negative control; re-fingerprinting old rows without a version bump.

---

## 6. Decisions for Jason

1. **Where the ledger lives.** One Postgres table beside `chat_feedback` (recommended — it joins
   to the turn, and the Work screen can read it; 90-day rows, class rollups kept), or a JSONL file
   like the miss log (simpler, but no joins and no board view)?
2. **Who says yes to a fix.** This record says: only you, always — a fix is code on the running
   box. Members can see "Needs your feedback" items but never an approve button. Is a
   lighter path wanted for L1 (test/corpus-only) fixes — a panel tap by you, as in the skills
   record — or does every approve happen on the board/GitHub?
3. **May Zoe ask a member "did I get that wrong just now?"** Once a day at most, only after a
   confident live miss, pull-first, answer = a label, never a build. Yes (recommended, after P1's
   inbox lands), or operator-only signals forever?
4. **Where the weekly digest goes.** The Multica board only (today's contract; invisible unless
   you open it), or also one Notify line in your inbox — "three things I kept getting wrong this
   week" — pointing at the Work screen? This record recommends both.

## 7. Next steps if GO

1. **PR 1 — the ledger + harness ingesters + backfill (`ZOE_FAILURE_LEDGER`).** Table +
   migration; `measure_voice` emits per-sample verdict rows (hash, verdict, ms) into the ledger
   before the sweep; the probe adds `sidecar_age_s` and `mem_available_mb` to `evidence`; bar
   and day-sim gain `--ledger`; `source_key` UNIQUE + `ON CONFLICT DO NOTHING` in every writer;
   the `why → class_key` table with the shadow-log join; `failure_ledger_backfill.py --dry-run`
   must reproduce the five rows of §4 from today's files and print `would_insert = 0` on its
   second pass; the PII guard test; negative controls 1–5 and 9. Byte-identical with the flag
   off.
2. **PR 2 — runtime emitters (`ZOE_FAILURE_LEDGER_RUNTIME`).** The BRAIN_LANE second sink; intent-
   dispatch outcomes; the correction join to the previous assistant turn; the three member
   writers re-pointed (and `record_user_issue`'s verbatim description replaced by the class key);
   the shared refusal-classifier module hooked at `brain_dispatch._LaneRecord` (chat, voice,
   Telegram) plus the `tool.empty_result` hook on fast-tier and intent-handler replies; the
   `router.misroute` join. Day-sim asks F1/F2; negative control 10.
3. **PR 3 — classes (`ZOE_FAILURE_CLASSES`).** Nightly rollup in the dreaming phase; states;
   `regressed` via `pr_ref` (PR bodies cite the class key; a `ci_safe` check reads it from the
   merge commit); the `why`-constant → key table with its completeness test; W16 counters.
4. **PR 4 — digest + proposals (`ZOE_FAILURE_PROPOSALS`).** The trigger table; `type =
   failure_class` rows with the evidence pack; the weekly digest section; the operator Notify
   line once P1's inbox exists; negative control 6.
5. **PR 5 — tickets (`ZOE_FAILURE_TICKETS`).** Approve → `sync_evolution_proposal_to_multica`
   with the admission block; the worker brief (reproduce → red control → fix the class → draft
   PR → cite the key); negative control 7. Depends on the skills record's PR 1 (executor unparked).
6. **PR 6 — the member label question (`ZOE_FAILURE_ASK`).** Depends on P1's ledger + inbox;
   negative control 8; the first 200 labels are data, not behaviour.
7. **Then:** feed `router.misroute` rows (re-joined to the hashed utterances on the box) into the
   self-train miner as the labelled candidate it has never had — behind the ratchet, after the
   warm-start checkpoint is re-established (`router-selftrain-loop.md:170-185`); and add timers
   for the bar and the day-sim so `harness.not_run` has something to count.

---

## 8. Sources

Field (primary unless marked):
- Anthropic, "Demystifying evals for AI agents" (2026-01-09) — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Hamel Husain & Shreya Shankar, "AI Evals: everything you need to know" (FAQ) — https://hamel.dev/blog/posts/evals-faq/
- LangSmith, evaluation concepts — https://docs.langchain.com/langsmith/evaluation-concepts
- OpenAI cookbook, "Self-evolving agents — autonomous agent retraining" — https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining
- Sentry, "Issue grouping and fingerprints" — https://docs.sentry.io/product/issues/grouping-and-fingerprints/
- Google SRE book, "Postmortem culture: learning from failure" — https://sre.google/sre-book/postmortem-culture/ ; SRE workbook, "Error budget policy" — https://sre.google/workbook/error-budget-policy/
- Shinn et al., "Reflexion: language agents with verbal reinforcement learning", NeurIPS 2023 — https://arxiv.org/abs/2303.11366 ; ablation summary [2nd] — https://www.alphaxiv.org/overview/2303.11366v4 ; "Honest Lying: understanding memory confabulation in reflexive agents" [unverified, title only] — https://arxiv.org/abs/2605.29463
- Letta, "Skill learning" (via the sibling record) — https://www.letta.com/blog/skill-learning/
- Hermes Agent, skills (write_approval, triggers) — https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- OpenClaw, creating skills (operator proposal queue; via the sibling record) — https://docs.openclaw.ai/tools/creating-skills
- Replika, "Creating a safe Replika experience" — https://blog.replika.com/posts/creating-a-safe-replika-experience ; feedback-to-fine-tuning claim [2nd] — https://www.bitdegree.org/ai/replika-ai-review
- Amazon FROST (unhandled-only, observe-one-outcome; via the sibling record) — https://www.amazon.science/publications/frost-fallback-voice-apps-recommendation-for-unhandled-commands-in-intelligent-personal-assistants

Ours (read-only; paths relative to the repo root; line numbers as of worktree head `4adfe59b`):
`scripts/maintenance/{voice_regression_probe.py,voice_gate_check.py,router_selftrain.py,router_shadow_report.py,router_shadow2_report.py}`,
`scripts/perf/{samantha_bar.py,samantha_day_sim.py,measure_voice.py}`, `scripts/setup/zoe_voice_daemon.py`, `scripts/setup/systemd/`,
`services/zoe-data/{brain_dispatch.py,zoe_flue_client.py,semantic_router.py,router_two_stage.py,intent_router.py,memory_supersede.py,evolution_notice.py,zoe_evolution_proposal.py,multica_admission.py,multica_client.py,multica_autopilot_sync.py,logging_setup.py,memory_metrics.py,pi_intent_evidence.py,skybridge_service.py,open_loop_lifecycle.py,main.py}`,
`services/zoe-data/routers/{chat.py,system.py,voice_tts.py}`, `services/zoe-data/proactive/{engine.py,selector.py,arrival.py}`,
`services/zoe-data/tests/replay_samples.py`, `services/zoe-data/alembic/versions/{0001,0004,0030}*.py`,
`labs/flue-zoe-brain-2x/src/{turn-guard.ts,tools/zoe-tools.ts,providers/capped-completions.ts}`,
`docs/knowledge/{incident-runbook.md,samantha-bar.md,router-selftrain-loop.md,runtime-topology.md,flag-inventory.md,ui-deep-review-2026-10-04.md,autopilots/evolution-nightly-notice.md,autopilots/evolution-weekly-digest.md}`,
`docs/architecture/{samantha-evolution-plan.md,beat-the-bar-2026-program.md,zoe-evolution-proposal-contract.md,multica-executor-migration.md}`,
`docs/research/{self-building-skills-2026-10-04.md,pull-not-push-inbox-2026-10-04.md,flue-and-agent-runtimes-2026-10-03.md}`.
Live artifacts read for shape and counts only: `~/.cache/zoe/{voice_regression_last.json,voice_regression_trend.jsonl,samantha_bar_last.json,samantha_bar_trend.jsonl,samantha_day_sim_last.json,samantha_day_sim_trend.jsonl}`; file listings of `~/.zoe-logs/`, `~/.zoe/`, `~/training/data/`, `services/zoe-data/data/`, `data/router_selftrain/`; label counts from the current `zoe-data.app.log` segment.
