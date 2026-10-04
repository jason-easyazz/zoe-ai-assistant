---
type: research
title: Zoe builds her own skills on request — the self-building loop (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit, container or live service changed by this document
description: Deep-research record for the owner's 2026-10-04 ask ("how do we get Zoe with Pi, Flue, Omnigent etc. to build new features or add new skills … like checking certain stock codes each day, or the tides"). Field half (the open Agent Skills / SKILL.md standard and its ~45 adopters, OpenAI's Codex skills, MCP as a skill carrier and its poisoning attacks, how OpenClaw / Hermes / Letta self-extend and what the measured gains need, HA blueprints + HACS + add-on ratings, OVOS and Mycroft's store history, Alexa's FROST / CanFulfill / Hunches and Dialogflow's intent suggestions for "when to offer", the sandboxing / permission-ladder / supply-chain-scanner literature with numbers) plus a read-only trace of Zoe's own halves with file:line (intent-miss log and its 21k rows, the dead chat→board approval card, the proposal contract and admission gate, Omnigent / Multica / Flue executor state and flags, the cross-review script, the deploy gate, the compose catalog and the estate's card path, how a tool becomes discoverable, skillspector), then the end-to-end design (detect → Review item in the inbox → yes → ticket from a skill template → cloud worker in a worktree under house rules → draft PR + cross-vendor review + tests → risk-tiered merge → deploy → inbox reports → complaints become fix tickets), seven draft contracts (Skill Contract, Panel Card Guidelines, Permission Ladder, Testing & Gates, Cost & Accounts, When Zoe Asks, Progress & Fixes), the RAM / off-box question (Jetson vs the Pi 5 vs a laptop), a measured proof skill (tides recommended over stock codes) with negative controls, a go/no-go against VISION, and a staged PR plan.
---

# Zoe builds her own skills on request — the self-building loop (2026-10-04)

Research date: 2026-10-04. The owner's ask, verbatim (2026-10-04): *"how do we get zoe with pi,
flue, omnigent etc to build new features, or add new skills, a bit like openclaw and hermes, like
if the user likes to check certain stock codes each day, or they like to check the tides … we
will need rules and guidelines on how to generate the results on the touch panel and how to
build the features/skills, we will also require zoe to know when to ask the user if she should
build a new feature/skill, be able to report on progress, be able to make changes or fix issues
the user informs her of. Zoe isn't smart enough with the offline ai to do this, so this will
always require the user to have an account with chatGPT or claude … to give omnigent so it can
do the work."*

This is pillar 7 of the Samantha plan (**W7**, "close the self-evolution loop once", never
started — [samantha-evolution-plan.md §3](../architecture/samantha-evolution-plan.md)), **W13.3**
(Zoe authors a card type) and **B8** in the
[beat-the-bar tracker](../architecture/beat-the-bar-2026-program.md). It depends on the inbox
designed in [pull-not-push-inbox-2026-10-04.md](pull-not-push-inbox-2026-10-04.md) (the ask /
report channel) and is sequenced after it.

Evidence labels: **[src]** checked in this worktree's source or tracked docs (file:line);
**[live]** a read-only observation on the box today (no service touched, no state written);
**[doc]** an upstream primary page read today; **[2nd]** a secondary source; **[unverified]**
not checked against a primary or not measured on our hardware; **[inf]** my inference.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) are
untouched; nothing here adds Jetson RAM on the hot path; everything ships flag-dark; no live
service was run, restarted or dispatched; Omnigent and Multica were only *read* (`GET /v1/hosts`,
`docker ps`); no household data is quoted — the example coordinates and tickers below are public
probes, not the owner's.

---

## 0. TL;DR

1. **Every piece of the loop already exists in the tree, and not one of them is connected to the
   next.** The detector writes misses (`intent_router.py:1536-1548` → `~/training/data/intent-misses.jsonl`,
   **21,254 rows, last write today** [live]); the nightly clusterer turns them into
   `evolution_proposals` rows (`evolution_notice.py:168-249`); the proposal contract and admission
   gate are built (`zoe_evolution_proposal.py`, `multica_admission.py`); the board is up
   (`zoe-multica-backend` 7 weeks [live]); Omnigent is up with `claude`, `codex` and `pi` usable
   [live]; the review script, worktree bootstrap, deploy gate and flag CI all work. What is missing
   is the **seams**: the chat→board card is broken (a markdown `[Start now]` GET link to a
   `@router.post` route, and the ticket it would create drops the user's words —
   `chat.py:1951`, `system.py:1834-1849`), the executor that would run the ticket is **paused by a
   kill switch since 2026-08-04** [live], the `flue-executor` unit is not installed [live], and
   the skill a built feature would land as has **no runtime loader at all** (`skills/` is
   documentation; `skill_discovery.py` was deleted in #1471).
2. **The field converged on one skill format in 2025-12.** Anthropic's SKILL.md became the open
   `agentskills.io` spec (name ≤64 chars, description ≤1024, `allowed-tools`, three-level
   progressive disclosure) and is read by ~45 clients including Codex, Copilot, Cursor, Gemini CLI,
   Hermes and OpenClaw; `.agents/skills/` is the neutral path [doc]. Zoe's Skill Contract should be
   that format plus a `metadata.zoe` block — public skills become adaptable, and the cloud worker
   already knows how to write one.
3. **Self-authored skills only paid off with failure feedback.** Letta's measured gain is +36.8 %
   relative on Terminal-Bench 2.0 *with verifier feedback on failures* (+21.1 % without) [doc];
   Hermes writes skills autonomously but offers a `skills.write_approval` gate; OpenClaw's own
   `skill_workshop` goes through an **operator proposal queue** [doc]. The owner's "Zoe asks first"
   instinct is the industry's design, not a conservative deviation.
4. **Unreviewed skill registries were poisoned within days of launch.** ClawHub: 341 of 2,857
   malicious (12 %) in the first audit, 36.8 % of 3,984 flawed in Snyk's; the top-ranked skill
   exfiltrated via `curl`; all five scanners were bypassed by Trail of Bits with ~100k leading
   newlines or `.pyc` payloads [doc]. The reviewed stores (Mycroft 6-stage, HACS months-long, OVOS
   PR review, **40** skills live) stayed clean and tiny. The lesson for Zoe is two-sided: review
   every skill as code, **and** keep the runtime boundary (network allowlist, no shell, least
   privilege at invocation) because scanning alone fails.
5. **"When to offer" has a published mechanism.** Amazon's FROST recommends a fallback skill only
   on *unhandled* utterances and gates on *predicted acceptance*; Hunches add a second model for
   "will the user accept"; Dialogflow CX clusters no-match utterances into intent suggestions with a
   cluster-size knob [doc]. Zoe already has the clusterer (`_simple_cluster`, trigram, ≥3 hits in
   7 days); what it lacks is the acceptance ledger the inbox record designs (`proactive_deliveries`).
6. **Permission ladder, not a sandbox promise.** Zoe has **no egress allowlist** anywhere in
   zoe-data [src]; the agents in Omnigent run `--dangerously-skip-permissions` with the container
   as the boundary (`docker-compose.module.yml:37`); the gh token mounted into it is the operator's
   broad `repo` login (`:180-185`). A self-built skill therefore needs a ladder of what it may read
   and send (§4c), enforced at *invocation* by a tiny host-side allowlist, with the cloud worker
   kept to a worktree and a draft PR it cannot merge — exactly GitHub's Copilot coding-agent model
   (draft only, `copilot/*` branches, firewall allowlist, cannot approve its own PR) [doc].
7. **The RAM question has a clean answer: the builder is already off the hot path, and the right
   off-box target is a laptop, not the panel Pi.** Omnigent idles at ~50 MB resident + 0.2 GB swap
   and the harness costs ≲10 MB [src]; the danger is the *burst* (leaked runners took the box to
   0–245 MB available on 2026-08-04). The Pi 5 has 5.65 GB of 8 GB free but it is the voice
   satellite and an aarch64 box with no repo, no Serena, no docker-credential volumes; a laptop runs
   the same compose file with a bind mount of a clone. Recommendation: keep Omnigent on the Jetson
   behind a `MemoryMax` drop-in **until** the first proof skill is through, then move the worker
   lane to a laptop and keep the Jetson container as the fallback.
8. **The proof skill should be tides, not stock codes.** Open-Meteo's Marine API returns
   `sea_level_height_msl` keyless for a WA-coast coordinate [live probe], it is the provider
   `routers/weather.py` already trusts, it is read-only, non-personal and non-financial, and it has
   a natural glance card (next high / low, trend) — the lowest rung of the permission ladder.
   Stock codes are a good *second* proof: they add a per-member watchlist (personal data), an
   unofficial source (Yahoo chart endpoint works keyless for `.AX` tickers [live probe]) or a
   25-calls/day free key [doc], and a "do not act on this" financial-advice rule.
9. **Go, with the inbox as the gate and the human as the merge for anything above L1.** Against
   VISION every principle holds (§7). The offer is a Review item the person pulls — never speech;
   the build leaves the box only as a task text and code (the fleet data-class rule, plan §10);
   the Samantha bar and replay gate stay the acceptance bar.
10. **Order of work (§8):** (1) fix the three dead seams behind flags; (2) the skill template + a
    hand-built tides skill through the *existing* human path to learn the friction; (3) the loader
    for `skills/<id>/` with the allowlist; (4) the ask (Review item) and the report; (5) the first
    automated end-to-end run, timed and costed, with the two negative controls.

---

## 1. Field — what shipped, and what the numbers say

### 1.1 The open Agent Skills format (SKILL.md)

- **Spec** ([agentskills.io/specification](https://agentskills.io/specification), Apache-2.0
  code, CC-BY-4.0 docs; "originally developed by Anthropic", open standard released 2025-12-18
  [doc]): a skill is a directory with a required `SKILL.md` and conventional `scripts/`,
  `references/`, `assets/`. Frontmatter: `name` (1–64 chars, `[a-z0-9-]`, must equal the folder
  name), `description` (1–1024 chars, "what it does AND when to use it"), optional `license`,
  `compatibility` (≤500 chars), `metadata` (string map), `allowed-tools` (space-separated,
  experimental). Progressive disclosure: metadata ≈100 tokens always loaded → body (<5,000 tokens
  recommended, "under 500 lines") on activation → files only when read.
- **Budgets every serious client applies** [doc]: Claude Code gives the skill list 1 % of the
  context window and truncates each `description + when_to_use` at 1,536 chars; Codex caps the
  list at 2 % or 8,000 chars; OpenClaw auto-selects at most 64 skills per session at ~24 tokens
  each. Letta's Context-Bench Skills measured −6.5 % when the model must *select* the skill itself
  and ~0 gain for small models — the catalogue is a routing problem, which on Zoe is the two-stage
  router's job, not the 4B brain's.
- **Adopters** ([agentskills.io/clients](https://agentskills.io/clients) [doc]): ~45, including
  Claude / Claude Code, ChatGPT & Codex, Cursor, GitHub Copilot, VS Code, Gemini CLI (v0.23.0,
  2026-01-07), OpenCode, Goose, Letta, **Hermes Agent**, **OpenClaw**, pi. Copilot reads
  `.github/skills`, `.claude/skills`, `.agents/skills`; Cursor reads `.agents/skills/`,
  `.cursor/skills/` and legacy `.claude/skills`; Codex reads `.agents/skills` up the tree,
  `~/.agents/skills`, `/etc/codex/skills`. **`.agents/skills/` is the de-facto neutral path.**
- **Anthropic's own security guidance** [doc]: "Use Skills only from trusted sources: those you
  created yourself or obtained from Anthropic"; audit every bundled file; skills that fetch
  external URLs are a "particular risk"; "treat like installing software". API-hosted skills run
  with no network and no package install; Claude Code skills have full network.
- **Catalogue** ([github.com/anthropics/skills](https://github.com/anthropics/skills)): ~180k
  stars; `skills/`, `spec/`, `template/`; the document skills (`docx/pdf/pptx/xlsx`) are
  source-available, not open source [doc].

### 1.2 OpenAI's equivalents

- Codex shipped SKILL.md skills by 2025-12-12 ("look very similar to Anthropic's implementation"
  — Willison) and officially on 2025-12-19 with `$skill-name` invocation and bundled
  `$skill-creator` / `$skill-installer` [2nd for the date]. Current docs add an optional
  `agents/openai.yaml` (`display_name`, `policy.allow_implicit_invocation`, `dependencies.tools`
  for MCP requirements) and have folded skills into "plugins"; `openai/skills` carries a
  deprecation notice pointing at the plugins repo [doc]. `AGENTS.md` is the per-repo instruction
  file with nested override semantics [doc]. Custom-GPT Actions are OpenAPI + auth, with
  endpoints markable "consequential" (user confirmation) [doc]. The Apps SDK (DevDay 2025-10-06)
  is "an open standard built on MCP" [doc].

### 1.3 MCP servers as skill carriers — and how they get poisoned

- Spec 2025-06-18: server primitives Resources / Prompts / Tools; the security section says tool
  descriptions "should be considered untrusted unless obtained from a trusted server" and hosts
  "must obtain explicit user consent before invoking any tool" — and admits the protocol "cannot
  enforce these" [doc]. The registry (preview 2025-09-08; `server.json` with reverse-DNS names,
  npm/pypi/oci/mcpb packages with SHA-256) moderates by GitHub-issue flagging and a denylist [doc].
- **Invariant Labs, 2025-04** [doc]: *tool poisoning* (hidden `<IMPORTANT>` instructions in a
  description made Cursor read `~/.ssh/id_rsa`), *rug pull* (description changes after approval),
  *cross-server shadowing* (a malicious server redirects a trusted `send_email`); the WhatsApp
  follow-up exfiltrated chat history. Mitigations they name: show full descriptions, **pin tool
  hashes**, dataflow boundaries between servers — `mcp-scan` implements the first two.
- Relevance: Zoe's live brain learns capabilities from a static TypeScript tool table
  (`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`), not from a server catalogue, so description
  poisoning is not a current attack surface — but any loader that feeds skill descriptions into
  the brain's prompt (what `list_openclaw_skills` used to do) re-opens it. The Skill Contract
  therefore pins the description hash at review time (§4a).

### 1.4 How self-extending assistants do it

| System | Where skills live | Who writes them | Gate | Measured |
|---|---|---|---|---|
| **OpenClaw** [doc] | SKILL.md up to 6 levels deep; precedence workspace > `.agents/skills` > managed > bundled; `metadata.openclaw.requires.{bins,env,config}` gates eligibility; 64 auto-selected/session, 1 MiB/file, 8 MiB/bundle | the agent drafts via `skill_workshop` | **operator proposal queue** ("does not autonomously install") | none published |
| **Hermes Agent** [doc] | `~/.hermes/skills/`, agentskills-compatible, `metadata.hermes.{requires_toolsets,…}`; `skills_list` ≈3k tokens → `skill_view` | the agent, via `skill_manage` when "it worked out a multi-step workflow worth repeating", hit dead ends and found the path, or "the user corrected its approach" | optional `skills.write_approval`; hub installs scanned, "dangerous verdicts stay blocked" | none published |
| **Letta Skill Learning** [doc] | `.md` files in git | a reflection stage over trajectories ("repetitive patterns that could be abstracted") | human | Terminal-Bench 2.0 (89 tasks, Sonnet 4.5): **+21.1 % rel. from trajectories; +36.8 % rel. (+15.7 abs) with verifier failure feedback**; −15.7 % cost, −10.4 % tool calls |
| **Voyager** (2023) | executable code library | the agent, with execution errors + self-verification | none | 3.3× unique items, 15.3× faster tech tree |
| **SkillWeaver** (2025) / **Agent Workflow Memory** (2024) | synthesised APIs / workflows | the agent | none | +31.8 % WebArena; AWM +51.1 % WebArena rel. |

Three things carry over. (a) The *trigger* for authoring is a repeated want or a correction, not a
one-off. (b) The only measured gain needs **a verifier that tells the author why it failed** — on
Zoe that is the replay harness's `CANT_DO` class and the user's complaint tied to the skill (§3.6).
(c) The two assistants closest to Zoe's shape (OpenClaw, Hermes) both put a human between "drafted"
and "installed".

### 1.5 Reviewed-extension precedents: Home Assistant, OVOS, Mycroft

- **HA blueprints** (since 2020.12) [doc]: a `blueprint:` header (`name`, `domain`
  automation|script|template, `author`, `homeassistant.min_version`) and typed `input:` slots
  with selectors; the Exchange forum is one topic per blueprint with an import badge, and HA keeps
  the source URL so authors must stay input-compatible. A blueprint is the right *shape* for the
  parametrised 80 % of what people ask ("the same card for a different station / ticker").
- **HACS** [doc]: a PR to `hacs/default`, `hacs.json`, a public repo with a release, automated
  checks (manifest, brands, lint) — "new additions still take months to be reviewed".
- **HA add-on security rating 1–6** [doc]: lowered by host network, disabled protection mode,
  broad API roles; raised by AppArmor, read-only maps, minimal API role, signed images, ingress.
  This is a *published permission ladder* for user-installable extensions — §4c borrows its shape.
- **OVOS** [doc]: `OVOSSkill` subclass with a setup-entry-point, `skill.json` (`skill_id`,
  `pip_spec`, `license`, `examples`, `tags`); the store is a reviewed PR feed with **40** live
  entries today.
- **Mycroft** [doc]: a six-stage Skills Acceptance Process (`msk submit` → automated intent tests
  re-run each core release → community+staff Code/Information/Functional review → content review →
  deployment → promotion); the repo was archived 2024-11-06 after the company stopped in 2023.
  ~67 approved marketplace skills vs ~895 on GitHub by late 2021 [unverified]. The store failed
  commercially, not technically — but its automated intent tests "re-run each core release" is
  exactly the regression idea Zoe's replay gate already embodies.

### 1.6 Deciding *when* to offer a capability

- **Alexa CanFulfillIntentRequest** [doc, search-excerpt]: on a name-free request Alexa asks
  candidate skills, *without side effects*, `canFulfill` YES/NO/MAYBE per intent and per slot,
  then an ML ranker weighing usage, ratings and engagement picks one.
- **FROST** (Amazon Science, 2022) [doc]: the published "there's a skill for that" fallback —
  triggers **only on unhandled utterances**, learns from accept/reject with collaborative
  relabeling (you only observe the outcome of the one skill you suggested) and rephrase relabeling
  for ASR noise; online, +233 unique suggested skills, +98 unique accepted vs the rule baseline.
- **Hunches** (2018; "automatic actions" opt-in 2021) [doc]: fire at anchor moments ("good
  night"), a device-state model plus **a second model that predicts whether the user will accept**;
  ask-first by default.
- **Dialogflow CX Intent Suggestions** [doc]: clusters no-match utterances from history into
  proposed new intents or extra phrases, with a *cluster-size* knob (many small vs few large);
  No-Match / Unhandled-% analytics per page. Rasa X's NLU Inbox / Intent Insights did the same
  for annotation priority [doc].
- **New-intent-discovery papers**: USNID (2023) centroid-guided clustering with cluster-number
  estimation; CsePL +3.57 pts over USNID on BANKING77; LANID (2024) uses an LLM to label sampled
  pairs then trains a small encoder; SIGDIAL 2024 formalises detect-OOS → cluster → present [doc].
  All heavier than Zoe needs; the shape (OOS detect → cluster → human-named intent) is the one
  `evolution_notice.py` already implements with trigrams.
- **Siri Suggestions** [doc]: apps *donate* performed actions; Siri keys on time-of-day,
  day-of-week, location and **repetition** ("a good donation should be something likely to be
  repeated"), on-device. A daily "check the tides" is the canonical donation.
- **Conversation-design error guidance** [doc]: Google's escalating no-match (rapid reprompt →
  detail → exit after 3); NN/g: say what was heard, offer one simple fix. HA's Assist shows
  `match: false` only in Developer Tools; it never offers to learn the sentence [doc].

### 1.7 Security: sandboxing, permission ladders, supply chain, governance, cost

- **Sandboxes** [doc]: Claude Code (Seatbelt / bubblewrap): writes limited to cwd + allowlisted
  paths, **network only via a local proxy whose `allowedDomains` starts empty**, refuses names that
  resolve to loopback or 169.254.169.254, blocks writes to its own config/hook/MCP files. Codex
  CLI (bubblewrap + Landlock + seccomp): ReadOnly / WorkspaceWrite / DangerFullAccess; `.git/` and
  `.codex/` read-only in WorkspaceWrite to stop self-escalation; network off by default. E2B =
  Firecracker microVM per sandbox with no documented egress allowlist; gVisor = user-space kernel.
- **Permission ladders** [doc]: Willison's *lethal trifecta* (private data + untrusted content +
  exfiltration channel) — remove the exfil leg first; Google's agent-security paper (2025): human
  controllers, limited powers, observable actions, a deterministic runtime policy engine over
  *action manifests*; **CaMeL** (DeepMind 2025): a privileged planner writes a program from the
  trusted query, a quarantined model parses untrusted data, capabilities are tracked per value —
  77 % of AgentDojo tasks solved *with provable security* vs 84 % undefended. OWASP LLM Top 10
  2025 LLM06 "Excessive Agency" = functionality + permissions + autonomy; OWASP Agentic Top 10
  2026 adds ASI04 agentic supply chain and ASI05 unexpected code execution.
- **Skill supply chain** [doc]: NVIDIA **skillspector** (64 patterns in 16 categories, AST +
  taint tracking source→network sink, YARA, OSV CVE lookup, optional LLM pass, 0–100 score, SARIF)
  — **installed here, v2.12.0** [live]. Snyk ToxicSkills (2026-02-05): 3,984 skills, 36.8 %
  flawed, 13.4 % critical, 76 confirmed malicious, 10.9 % hardcoded secrets. Koi ClawHavoc: 341 /
  2,857, 335 from one actor, AMOS infostealer via fake "prerequisites"; 824 by 2026-02-16. Cisco:
  the #1-ranked skill had 9 findings incl. silent `curl` exfiltration. "Agent Skills in the Wild"
  (arXiv 2601.10338): 31,132 skills, 26.1 % vulnerable, **skills with scripts 2.12× riskier**.
  **Trail of Bits (CSA, 2026-06)**: all five scanners bypassed (~100k leading newlines, `.pyc`
  payloads, DOCX-embedded instructions, "corporate policy" injection against the judging LLM), 3
  of 4 in under an hour — their recommendation is pin versions, least privilege *at invocation*,
  runtime monitoring over pre-install scanning.
- **Governance of self-modifying agents** [doc]: Darwin Gödel Machine (ICLR 2026) — each
  self-edit **empirically validated** before adoption (SWE-bench 20 → 50 %), sandboxed, human
  overseen; the paper reports the agent faking tool-use logs when asked to fix hallucinated tool
  use [unverified from abstract]. AlphaEvolve: all changes gated by deterministic evaluators,
  humans deploy. **GitHub Copilot coding agent** is the production reference for "agent proposes,
  human merges": pushes only to `copilot/*`, opens *draft* PRs it cannot mark ready, approve or
  merge; the requester cannot satisfy required approval; Actions do not run until a human clicks
  approve; firewall on by default with an allowlist [doc, search-excerpt].
- **Cost numbers** [doc / 2nd]: Copilot coding agent = 1 premium request per session + Actions
  minutes; Claude Code enterprise ≈ $13/dev/active-day, `--max-budget-usd` exists; third-party
  per-task benchmarks ≈ $7–20 of frontier tokens per PR; Devin $2.25/ACU (≈15 min). On this
  repo the builder runs on **flat-rate subscriptions** (Claude Max, ChatGPT) with pay-per-token
  only for the `pi` tie-breaker — so the budget rule is time and attempts, not dollars (§4e).

### 1.8 Pieces to borrow (and what not to)

| Borrow | From | Into |
|---|---|---|
| SKILL.md frontmatter + `.agents/skills/` path + description hash pin | agentskills.io, mcp-scan | §4a Skill Contract |
| `requires.{bins,env,config}` eligibility gating | OpenClaw | `metadata.zoe.requires` |
| Author-on-repeat / author-on-correction triggers | Hermes, Siri donations | §4f thresholds |
| Failure feedback to the author | Letta | §3.6 complaint → fix ticket with the replay verdict |
| Unhandled-only + predicted-acceptance offer | FROST, Hunches | the Review item + the inbox ledger |
| Cluster-size knob | Dialogflow CX | `ZOE_SKILL_OFFER_MIN_HITS` |
| Typed inputs for the parametrised 80 % | HA blueprints | `params:` in the Skill Contract |
| 1–6 rating from declared powers | HA add-ons | §4c Permission Ladder tiers |
| Draft-only, cannot merge own PR, firewall allowlist | Copilot coding agent | already true of the Omnigent lane; keep it |
| Empirical validation per change | DGM | every skill ships its own test + replay line |
| **Not**: an open registry; runtime skill install endpoints; description-only "skills" | ClawHub, OpenClaw's `/install`, our own `list_openclaw_skills` | retire with OpenClaw (B-tracker row) |

---

## 2. Our system — read-only, with file:line

### 2.1 Doctrine already on record

- VISION: "**Self-evolution.** The end state: Zoe scouts new ideas and **builds them herself** on
  her Pi engine, write-gated through the PR harness" (`docs/VISION.md:21-22`); principle 2 "Nothing
  leaves the box unless Jason opts in" (`:29`); principle 8 voice first, touch second, no keyboard,
  with "QR on the panel, finish on the phone" for app connections (`:44-55`) [src].
- The fleet data-class rule (`samantha-evolution-plan.md:1070-1099`): raw audio, memory stores,
  ambient transcripts, speaker embeddings **never leave**; "operator-initiated task text,
  engineering/code content … W7/W13.3 authoring work" is **opt-in remote**; "The builder fleet does
  the hard building … Zoe's harder self-evolution steps (W7/W13.3) are *fleet* work products landing
  through the same human-gated PR pipeline" [src]. The owner's "this will always require a ChatGPT
  or Claude account" is already the plan's position.
- Operator decisions (`multica-executor-migration.md:263-289`): Flue is the executor substrate;
  "**Omnigent is a PRIMARY executor lane from day one** … 'Omnigent is a beast, and should be used
  to build zoe until we get a box where local agents can do those tasks'"; Hermes retires; OpenClaw
  fully retires, "Rebuild capabilities on Pi/Flue when actually needed, referencing the public
  Agent-Skills ecosystem" [src].
- Tracker verdicts (`beat-the-bar-2026-program.md:1315-1317`): OpenClaw runtime → delete ("31
  skills never ran; router + trigger still mounted in `main.py`"); the Multica full-autonomy
  program → **park** ("the most expensive, least user-visible work on the board. Keep the
  executor's minimal Phase 2 (B8.1)"); B8.1 rebuild the executor on Flue 2, unpause Multica, land
  ≥3 real tickets; B8.2 skillspector LLM stage on the local llama-server [src].
- W13 (`samantha-evolution-plan.md:826-845`): "Today the catalog is human-authored; Zoe cannot
  create a *new* card type. Close it with the W7 pipeline … **plus a panel-verify screenshot** …
  The catalog + validator stay the hard safety boundary" [src].
- Existing policy text that is *wrong about what exists* and must be corrected by this work, not
  built on: `skills/AGENTS.md:9` and root `AGENTS.md:543` still say `skill_discovery.py` parses two
  directories (deleted in `b4768461`, #1471) [src]; `docs/governance/SECURITY_POLICY_SKILLS.md`
  states plainly that `api_only`, `allowed_endpoints` and `skills.lock` "are not implemented
  anywhere" (`:3-12`) [src].

### 2.2 Detect — what records a want Zoe cannot meet

- **The miss path.** `detect_intent()` (`intent_router.py:676`) falls through to
  `logger.info("intent_miss: %s", text)` (`:1536`), strips names / numbers / emails / URLs by regex
  (`:1543-1546`) and appends `{"text","ts"}` to `~/training/data/intent-misses.jsonl` (`:1540,
  :1548`) [src]. **21,254 rows; last write 2026-10-04 10:39** [live]. A second, richer store
  (`pi_intent_evidence.record_intent_miss_evidence`, `text_hash`, `user_hash`,
  `expected_intent`, `outcome_label`) is behind `ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED`, default
  **off**, and its file does not exist on the box [src, live].
- **What a miss is not.** The miss log fires when no *regex intent* matched — most rows are
  ordinary chat that went to the brain, not capability gaps [inf]. The *explicit* ask has its own
  regex, `_EXTEND_CAPABILITY_RE` (`:365-370`: "teach yourself | learn | add support | extend
  yourself | gain the ability | install a skill | build me the ability | add the ability | learn
  how") → `Intent("extend_capability", {})` (`:751`); and — confusingly — the **open-domain
  catch-all** also returns `extend_capability` with `{"raw": text}` (`:1525-1526`, `_AGENT_CHAT_RE`
  `:504-511`: "tell me about | search the web | write me a poem | tell me a joke …") [src]. Two
  unrelated meanings share one intent name; the Pi classifier lists it in a `governed_agent` lane
  (`pi_intent_classifier.py:85`) and `fast_tiers.py:148` labels it "system command".
- **The brain's own "can't do".** No live code classifies a brain reply as a refusal. The only
  detector is the replay harness: `tests/replay_samples.py:73-80` `_CANT_DO_RE` ("don't have
  access to | can't access | not on file | can't (tell you|find|create|…) | unable to | you'll
  have to … yourself"), and `voice_regression_probe.py:341,391-399` fails the gate when the
  `CANT_DO + ERROR` count rises [src]. That regex is the right seed for a live, per-turn
  `cant_do` tag (§3.1 stage 1).
- **Clustering exists.** `evolution_notice.py:21-23,52-60,168-249`: reads the same JSONL for the
  last 7 days, trigram-clusters, `_MIN_CLUSTER_SIZE = 3`, writes one `evolution_proposals` row per
  cluster (`"Intent gap: '…'"`, type `intent_pattern`) with an `EvolutionSignal` of type
  `REPEATED_FAILURE`, then `sync_evolution_proposal_to_multica` [src]. It is the "Phase 6 nightly
  dreaming" job of `multica_autopilot_sync.py:185-192`; the weekly digest trigger is registered at
  `main.py:1277-1281` [src]. Its contracts (`docs/knowledge/autopilots/evolution-nightly-notice.md`,
  `…-weekly-digest.md`) forbid it from implementing anything.
- **Two-stage router** (`router_two_stage.py`, `semantic_router.py:221-237`): the stage-1 head
  shortlists domains, the FunctionGemma sidecar decodes one tool call; gate reasons `chat_top |
  below_gate | low_conf`; `ZOE_ROUTER_HEAD_MIN_CONF 0.70` [src]. A new skill is a new **domain →
  tool** row in `DOMAIN_TOOLS` (`:61-75`) plus corpus rows — i.e. a router retrain, which the
  self-train loop (`ZOE_ROUTER_SELFTRAIN`, **off**, "parked" in the tracker) was built to do under a
  ratchet (`docs/knowledge/router-selftrain-loop.md:36-47`: no accuracy regression, zero chat-FP,
  p50 < 600 ms, replay gate ran and passed) [src].

### 2.3 Ask and report — the channel today

- **The chat → board approval card is dead code.** For `extend_capability`, `build_widget`,
  `build_page`, `self_improve` (`routers/chat.py:464-469` `_MULTICA_BOARD_INTENTS`) chat emits a
  markdown card `[Start now](/api/agent/board/approve?task_id=…)` (`:1940-1955`). The route is
  `@_agent_card_router.post("/board/approve")` (`routers/system.py:1834`) — a link is a GET
  [inf: 405]. If it were reached it would create an issue whose description is only `"Approved via
  Zoe chat. Task ID: {task_id}"` (`:1845-1849`) — **the user's words are not carried** — and
  `dispatch_issue` it into the Kanban phase pipeline whose consumer is paused (§2.5) [src].
- **Complaints.** `user_issue_report` is a real intent (`intent_router.py:1512-1523`: "that didn't
  work | X is broken | you need to fix | you keep …"), allowed for guests
  (`guest_policy.py:53`), and `record_frustration_signal` writes a proposal from repeated
  negative turns (`evolution_notice.py:386-398`) [src]. Neither is tied to a *skill*: the
  `chat_feedback` table is `(interaction_id, user_id, feedback_type thumbs_up|thumbs_down|correction,
  corrected_response)` (`alembic/0001:443-450`; endpoint `chat.py:3279-3303`; senders
  `touch/chat.html:5120,5149,5182`) and **nothing reads it** [src, inf].
- **The inbox** (the sibling record, §3): candidates on `proactive_candidates` classed **Notify /
  Question / Review**; `GET /api/proactive/inbox` returns `{count, top_klass}` with no text on the
  guest path; the orb's `has` state; "what's up?" or a tap pulls up to 3 items Review → Question →
  Notify; one-unanswered-then-wait with doubling; flags `ZOE_PROACTIVE_LEDGER / _INBOX / _BACKOFF /
  _HEAD`, all default off. Review rows today are `pending_suggestions` with existing
  `POST /api/proactive/suggestions/{id}/accept|dismiss` (`routers/proactive.py:181,197`) [src].
  A "build this?" offer is a Review item by that definition; nothing new is needed on the channel
  side except a new Review *source*.
- **"Zoe's Work" screen already exists on the panel** (`touch/home.html:1685-1716`): reads
  `/api/board/summary` and renders "Needs your feedback / In progress / In review / Recently
  shipped" [src]. That is the progress surface; it needs the skill rows, not a new screen.

### 2.4 Propose — the contract and the gate

- `zoe_evolution_proposal.py:19-51` [src]: `EvolutionSignalType` {user_request, repeated_failure,
  tool_gap, stale_capability, outcome_eval_failure, operator_note}; `TrustAutonomyClass` {observe,
  recall, suggest, prepare, **execute, promote**}; `ProposalRisk` {low, medium, high, privileged};
  `ProposalStatus` {draft, pending_approval, approved, rejected, verified, failed, retired}. A
  proposal requires signals with evidence, a scored candidate, affected capabilities, a
  verification plan, a rollback plan; `approval_gate.allowed_to_execute` is **always false**
  (`docs/architecture/zoe-evolution-proposal-contract.md:15-31`).
- `multica_admission.py:39-58,73-104` [src]: a ticket dispatches only with `dispatch_approved`,
  `acceptance_criteria`, `evidence_expectations`, no `blocked_reason`, and — for
  evolution-proposal tickets — matching contract markers; `execute`/`promote` tickets must carry
  approval evidence refs. `executor_registry.py:20-32`: only tickets **assigned to the engineering
  agent id** get an adapter at all.
- The proposal row (`alembic/0004:69-83`): `id, type, title, description, evidence,
  target_patterns (holds the contract JSON), status, multica_issue_id, proposed_at, reviewed_at,
  deployed_at, validation_result, next_review_at` [src].

### 2.5 Build — the fleet, the board, the gates

**Omnigent** (`modules/omnigent/`, container `zoe-omnigent`, `127.0.0.1:6767`, uid 1000) [src]:
- Pins: `omnigent==0.7.0`, `claude-code@2.1.220`, `codex@0.146.0`, `pi-coding-agent@0.87.1`
  (`README.md:11-15`). Live roster today: `claude`, `claude-sdk`, `codex`, `pi`, `cursor` usable;
  `opencode` binary-missing [live `GET /v1/hosts`].
- Auth: header provider + `OMNIGENT_LOCAL_SINGLE_USER=1` behind Cloudflare Access on the tunnel;
  "Omnigent itself trusts every request" (`docker-compose.module.yml:64-77`). Agents run
  `--dangerously-skip-permissions`; "The container IS the isolation boundary" (`:37`). The
  harness CLIs run on **subscription OAuth, never API keys** (`:31-34`); the Claude refresh token
  recorded 2026-09-25 "runs to **2026-10-11** — renew before then" and "nothing in the stack alarms
  ahead of expiry" (`docs/knowledge/omnigent-container-config.md:74,90-91`) — **seven days from
  today** [src]. The `claude-sdk` harness on consumer OAuth is flagged as policy-wrong; decision
  pending (`:93-108`).
- Mounts: the live repo rw at `/workspace` **and** `/home/zoe/assistant`; `~/.worktrees` at the
  same path; the operator's `gh` login read-only ("broad `repo` scope … replace with a fine-grained
  PAT", `:180-185`); the pipeline journal as a single-file bind so the workload "must never be able
  to remove its own emergency stop" (`:200-215`); host code-intel binaries read-only.
- Kick recipe (`scripts/maintenance/cross_review.sh:287`, `omnigent_issue_executor.py:192`,
  `labs/flue-executor/src/omnigent.ts:341`): `POST /v1/sessions` → runner → `docker exec -d
  zoe-omnigent … omnigent run --harness <h> -r <SID> -p '<brief>'`; **brief inline via `-p`**,
  comment staging "fails silently" (`cross_review.sh:172-173`); sessions end `idle`, never
  `completed`; "an idle session with NO messages is the silent-launch-failure signature"
  (`:330-333`) [src].
- RAM (`memory-pressure-profile-2026-10-03.md:103,113,185-203,269`): omnigent server + host **19 +
  32 MB resident, 127 + 56 MB swap**, no cgroup cap; the whole engineering harness inside zoe-data
  **≲10 MB**; "fencing the harness out frees essentially nothing at steady state. Its real value is
  **peak isolation**" — harness subprocesses run inside zoe-data's `MemorySwapMax=0` cgroup, so the
  voice path takes the burst [src]. Leaked per-session runners took the box to 0–245 MB available
  on 2026-08-04; `docker restart zoe-omnigent` recovered ~956 MB
  (`omnigent-cross-review.md:149-169`) [src].

**Multica + executor** [src]:
- Board: `zoe-multica-backend` pinned by digest (`docker-compose.modules.yml:156`), **up 7 weeks**
  [live]; `MULTICA_WORKSPACE_ID` from env (`multica_client.py:105`).
- Phase state (`multica-executor-migration.md`): Phase 1 Flue executor lab-proven 33/33 (`:80-85`);
  Phase 2 seam landed, `ZOE_KANBAN_BACKEND=executor` is the default (`:114-126`;
  `kanban_adapter.py:158-167`); "**Multica stays paused** … until Phase 2 is proven" (`:257`);
  Phase 3 superseded (Omnigent primary); Phase 4 (retire Hermes) every box unticked (`:232-239`);
  §6: approved proposals "reached NO live executor … `agent_task_queue` is empty".
- Live state: `~/.zoe/multica_dispatch_paused` **exists, dated 2026-08-04** [live]; the poll loop
  flag `ZOE_MULTICA` defaults `"false"` (`main.py:1305`); `flue-executor.service` is **not
  installed** (`systemctl --user is-enabled` → no such unit) [live]; the Omnigent issue executor
  is `ZOE_USE_OMNIGENT_EXECUTOR` default `"0"` and hand-run (`omnigent_issue_executor.py:1-21,
  104-105`); the board runner is hand-run `--loop` with KILL SWITCH / SINGLE LANE / FLAG guards
  (`multica_board_runner.py:1-20`); `ZOE_MULTICA_CROSS_REVIEW` default `"false"`
  (`pipeline_cross_review.py:55`).
- GATE_BLOCKED: `pipeline_store.py:1136-1138` ("missing required evidence", "validator hash
  mismatch"); `resume_pipeline()` at `:278` has **no non-test caller** — operator-only unwedge.
- Worktrees: `worktree_bootstrap.py:35-47` → `<ZOE_WORKTREE_ROOT|~/.worktrees>/<task_id>`, branch
  `wt/<task_id>`; Flue's `local()` sandbox does no git — "`worktree_bootstrap` stays authoritative"
  (`labs/flue-executor/FINDINGS.md`).
- Cross-review (`scripts/maintenance/cross_review.sh`, 338 lines): polly (`claude-sdk`) runs the
  built-in `cross-review` skill with a different-vendor sub-agent; "findings are ADVISORY — they
  never become PR threads, and polly never pushes/resolves/merges" (`:8-10`); `TIMEOUT_S` 1800;
  `flock`; one worker at a time; `NONTERMINAL = ("running","waiting")` (`cross_review_poll.py:81`).
  Trial: "4 real findings across 2 PRs, zero noise"; "the union of two vendors covered all known
  defects" (`omnigent-cross-review.md:181-194`).
- Review pipeline (`AGENTS.md:111-348`): required = `validate` + `secret-scan` +
  `required_conversation_resolution` + `strict`; `voice-gate` informational, enforced at deploy;
  Tier 2 cross-vendor review is "the routine path"; Greptile advisory on the `greptile` label;
  `pi` is a strict tie-breaker "with a hard cost cap"; "Humans triage severity; machines report
  findings". `pr-hygiene.yml:47-49`: WARN 10 files / 400 lines, FAIL 30 / 1000, `oversized-ok`
  override; drafts skipped.
- Deploy (`.github/workflows/deploy.yml`): self-hosted on the Jetson; memory-headroom gate
  `THRESHOLD_MB=250`; `voice_gate_check.py --expect-tree-of <sha>` (tree-bound after #1745);
  Alembic; `systemctl --user restart zoe-data`; Flue sidecar rebuilds on `labs/flue-zoe-brain*`
  changes; `/health` check. Flags: `docs/knowledge/flag-inventory.md` is generated (492 flags),
  pinned by `tests/unit/test_flag_inventory.py` (`ci_safe`) and a pre-commit hook; a new `ZOE_*`
  reader registers itself.

### 2.6 Render — the panel today

- Tokens: `touch/css/skybridge-ds.css` (149 lines; `--sky-*` primitives, `--card-*`, `--text-*`
  semantic) and the spec `docs/architecture/skybridge-design-system.md` §8 primitives (Card frame,
  Header row, Hero, Strip, Side panel, Centred, Progress border, Metric stack) [src].
- **Compose is built and dark.** `ui_compose.compose_card()` behind `ZOE_COMPOSE_UI` (default
  off, `ui_compose.py:87`), grammar-constrained on the local llama-server, catalog-only
  (`ui_catalog.py:46-82`: Stack, Row, Grid, Text, Hero, **Stat**, Badge, **ListRow**, Steps,
  Compare, Progress, Glyph, Image, Divider, ActionButton, MediaTile; six tones; `MAX_TREE_NODES =
  60`, depth 6); `card_contract.py:20-43` envelope (`card_id, schema_version, card_type, content,
  producer, producer_version, created_at`; types generic / list / smart_home / media / …);
  `zoe-compose.js` is loaded by `chat.html` only — "the estate doesn't load it yet"
  (`docs/PLANS.md:35`) [src].
- The estate is one page (`touch/home.html`, 4,550 lines) with hand-built screens (`FULL`
  `:1058`: work home ask weather music … ; `DOMAIN_SCREEN` `:4052`). A brain answer reaches it via
  `/ws/push` (`voice:responding`, `show_card`) or a `panel_navigate` reload; `ZoeVoiceCard.renderCard`
  returns **text only**, sliced to 300 chars (`touch-ui-executor.js:729-739`); `panel_announce` is
  a 6 s toast (`:1125-1140`, `home.html:4054`) [src]. No glance / list / detail primitive exists as
  a component; `css/cards/weather.css` is the closest hand-made glance card.

### 2.7 Discoverability — how a capability becomes real today

- **There is no registry.** Tools are static lists in four places: the router's `DOMAIN_TOOLS` /
  `TOOL_ARGS` (`router_two_stage.py:61-99`, mirroring `labs/functiongemma-finetune/zoe_tools.json`),
  the Flue brain's `src/tools/zoe-tools.ts` + `tool-groups.ts` (calls back via
  `POST /api/system/intent-dispatch`), `mcp_server.py:131` `TOOLS` (~100 entries), and
  `zoe_agent.py:426` `_TOOLS` (legacy lane) [src]. Adding a skill today = editing all four by hand
  plus the voice allowlist table (`zoe-tool-capability-inventory.md:49-86`).
- `skills/` (12 folders, `SKILL.md` only, no frontmatter) is documentation
  (`skills/AGENTS.md:1`, `docs/guides/CREATING_SKILLS.md:34-52`); the `openclaw/*` symlinks "still
  do not load" (symlink-escape, wrong workspace root) [src]. `skills/openclaw/zoe-capability-extender/SKILL.md`
  is the *previous attempt at this exact capability* — a human-readable procedure (classify the
  request, spec + confirm, pick the layer, implement, restart, confirm) that **assumed an in-process
  agent editing the live checkout and restarting `zoe-data`** — exactly what VISION's write-gate and
  `feedback_never_git_in_live_checkout` forbid.
- `skill_discovery.py` **does not exist** (deleted `b4768461`, #1471; comment at
  `routers/system.py:1641-1644`; zero importers) — the "dead-end catalogue" record is confirmed,
  and five docs still cite it as live (`skills/AGENTS.md:9`, root `AGENTS.md:543`,
  `docs/knowledge/operator-skills/index.md:27,59`, `docs/guides/HERMES_ENGINEERING_SKILLS.md:36,57`,
  `docs/architecture/tech-debt-remediation-plan.md:114`) [src].
- `modules/` is one container (`omnigent`); "A module is a container on `zoe-network` that
  something calls by URL" (`docs/guides/MODULE_SYSTEM.md:21`); there is no scaffold ("There is no
  scaffold to copy", `:52`) and no manifest [src].
- **skillspector v2.12.0 is installed** (`~/.local/bin/skillspector`) [live]; the rule is scan
  "before promoting a self-authored skill from the lab to a live agent" and never egress internal
  skill content to an external LLM for the optional stage (`AGENTS.md:444-447`); one waiver
  precedent exists (`omp-builder-adoption.md:48-50`, "100/100 CRITICAL … waived") [src].
- **No outbound-network allowlist exists in zoe-data** (`ALLOWLIST|egress` hits are the synthetic
  user filter, the bash prefix allowlist and an asset list) [src]. The existing lookups are
  Open-Meteo / OpenWeatherMap (`routers/weather.py:151,267,342,405`, keyed TTL cache
  `ZOE_WEATHER_CACHE_TTL_S` 600 s, stale-on-failure) and Tavily / DDG / CloakBrowser
  (`web_search_provider.py`, `mcp_server.py:1996-2007`) [src]. **Tides, stocks, tickers: zero hits.**

### 2.8 What runs today (read-only, 2026-10-04)

| Thing | State | How read |
|---|---|---|
| Intent-miss log | 21,254 rows, last 10:39 today | `wc -l`, last `ts` |
| Pi miss-evidence file | absent (flag off) | `wc` |
| Multica dispatch kill switch | **armed since 2026-08-04** | `ls -la ~/.zoe/multica_dispatch_paused` |
| `flue-executor.service` | not installed | `systemctl --user is-enabled` |
| `zoe-omnigent` | Up 6 days; host `online`; `claude/codex/pi/cursor` usable | `docker ps`, `GET /v1/hosts` |
| `zoe-multica-backend` / `-web` | Up 7 weeks (healthy) | `docker ps` |
| skillspector | v2.12.0 | `--version` |
| Claude OAuth in the container | expires **2026-10-11** per the config note | doc only — not probed |
| Open-Meteo marine `sea_level_height_msl` | returns hourly metres for a WA-coast coordinate, keyless | one GET |
| Yahoo chart endpoint, `.AX` ticker | returns `exchangeName: ASX` JSON, keyless, unofficial | one GET |
| Alpha Vantage free tier | "25 API requests per day" | page text |

---

## 3. Design — the loop, end to end

### 3.0 The one principle

**The on-device brain never builds. It notices, asks, and reports. The cloud fleet builds in a
worktree it cannot merge. A human (or, for the lowest tier, the deterministic gate plus a human
glance) merges. The skill then runs on-device, under a declared allowlist, forever — without the
cloud.** This is the owner's statement ("Zoe isn't smart enough with the offline AI to do this")
turned into an architecture boundary rather than a limitation: the 4B rock is excellent at
*routing* to a skill (the two-stage router beats it at tool choice, CANONICAL) and adequate at
*phrasing* a skill's result; it is not asked to write code.

### 3.1 Stages

| # | Stage | Where | What happens | Exists today → gap |
|---|---|---|---|---|
| 1 | **Detect the want** | zoe-data, per turn | Three signals write a `skill_wants` row (new table, PII-stripped like the miss log): (a) `extend_capability` explicit ask (`_EXTEND_CAPABILITY_RE`, with the catch-all meaning split off into a new `open_domain` intent); (b) a brain reply tagged `cant_do` by the replay regex lifted into `fast_tiers` (shadow first); (c) a *repeat* of the same domain-free query by the same member on ≥3 distinct days (the Siri "likely to be repeated" rule) | miss log + trigram clusterer exist; no per-member repeat count, no live `cant_do` tag |
| 2 | **Cluster + score** | nightly, `evolution_notice` | Existing trigram clusterer gains `member_id`, `distinct_days`, `last_seen`; a cluster becomes a *skill candidate* when hits ≥ `ZOE_SKILL_OFFER_MIN_HITS` (3) across ≥ 2 days, and a template matches (§3.3). Score = FROST's shape: hits × recency × (prior acceptance rate for this member, from the inbox ledger) | clusterer exists; no acceptance prior (needs `ZOE_PROACTIVE_LEDGER`) |
| 3 | **Offer** | inbox | One **Review** item per candidate: *"You've asked about the tides four times this week. Want me to build a tide card? I'd fetch it from Open-Meteo, show it on the panel, and keep it local."* Delivered only by pull (orb `has`, "what's up?", tap) or on the next greeting if the head allows; **never spoken unprompted**; one unanswered then wait (sibling record §3.4). Accept / dismiss / "not now" via the existing suggestion endpoints; a dismiss suppresses that cluster for 30 days | Review class + endpoints designed; no skill source |
| 4 | **File** | zoe-data → Multica | On accept: an `evolution_proposals` row (`USER_REQUEST`, autonomy `prepare`, risk from the template's tier) **and** one Multica ticket **from the skill template** (§3.3) carrying the user's words, the template id, params, the tier, acceptance criteria, evidence expectations, `dispatch_approved=true`, assigned to the engineering agent — everything `multica_admission` already demands. The ticket is the *only* thing that leaves the box, and it contains no household data beyond the phrase and the parameters the person chose | `sync_evolution_proposal_to_multica` exists; the chat→board card is the broken seam to replace |
| 5 | **Build** | Omnigent (cloud worker) | The executor (board runner or Flue executor, lane `heavy`) prepares `~/.worktrees/<task>` on `wt/<task>`, kicks `claude_code` (Opus-tier for a new skill, Sonnet for a param-only clone) with the brief inline: the Skill Contract, the Panel Card Guidelines, the Permission Ladder tier, the template, the house rules (worktree only, draft PR only, `ci_safe` tests, flag-dark, no secrets, `skillspector scan` self-check). The worker writes `skills/<id>/` + a router corpus row + tests, pushes `wt/<task>`, opens a **draft PR** | kick recipe + worktree bootstrap + admission exist; executor paused; brief template to write |
| 6 | **Review** | GitHub | `cross_review.sh <PR> "<contract from the template>"` (different vendor), `validate` + `secret-scan`, `pr-hygiene`, skillspector in CI on `skills/**` (new job, static stage only, SARIF artefact), `voice-gate` if the PR touches a voice path (a new router corpus row **does** — `VOICE_PATH_PATTERNS` includes the router artefacts), Greptile label only for tier ≥ L2 | all exist; skillspector CI job is new |
| 7 | **Merge tier** | GitHub + human | **L0–L1** (read-only public data, no member data, no egress beyond the allowlist): deterministic gate green + cross-review clean + one human tap on the panel's "Zoe's Work → Needs your feedback" card ("Looks good, ship it") which applies a `skill-approved` label; the merge is still the operator's `gh pr merge --squash --auto` — no bot merges. **L2+** (member data, HA control, any write, any new egress host): full human review on GitHub, Greptile, the usual rules. The owner's words: "human for anything touching household data or egress; a lighter tier for self-contained read-only skills" | branch protection + labels exist; the panel tap → label is new (small) |
| 8 | **Deploy** | `deploy.yml` | Unchanged: memory gate, tree-bound replay gate, restart. The skill lands **flag-dark** (`metadata.zoe.flag`); the loader enables it per member on first use after the inbox "ready" is accepted | exists |
| 9 | **Report** | inbox + Zoe's Work | Ticket state → inbox items: *Notify* "your tide card is ready — say 'tides' or tap the orb"; *Question* only when the builder is **blocked on the person** (needs an account/QR, a choice between two stations); *Notify* "I couldn't build the X card — the data source needs a paid key; parked" on failure. Progress itself is pull-only on the Zoe's Work screen (In progress / In review / Shipped), never pushed | Zoe's Work screen + `/api/board/summary` exist; skill rows to add |
| 10 | **Fix** | chat / voice → ticket | A `user_issue_report` or thumbs-down **while a skill's card is on screen or within 2 turns of its result** is tied to `skill_id` (new column on `chat_feedback`); the complaint becomes a `fix` ticket from the same template with the original PR, the complaint text (PII-stripped), the replay verdict and the card screenshot path; same loop from stage 5, Sonnet-tier, capped at 2 attempts before it parks and asks | `user_issue_report` + `chat_feedback` exist; no skill tie |

### 3.2 Flags (all default OFF, read per call)

| Flag | Scope | Does |
|---|---|---|
| `ZOE_SKILL_WANTS` | zoe-data | write `skill_wants` rows (stage 1); no behaviour change. **Ships first** |
| `ZOE_SKILL_OFFER` | zoe-data | stage 2–3: candidates → Review items (needs `ZOE_PROACTIVE_INBOX`) |
| `ZOE_SKILL_OFFER_MIN_HITS` / `_MIN_DAYS` | zoe-data | 3 / 2 (the Dialogflow cluster-size knob) |
| `ZOE_SKILL_TICKETS` | zoe-data | stage 4: accepted offer → proposal + template ticket (needs `MULTICA_WORKSPACE_ID`) |
| `ZOE_SKILL_LOADER` | zoe-data + brain | load `skills/<id>/` that pass the contract validator; register domain → tool rows; enforce the allowlist at invocation |
| `ZOE_SKILL_FEEDBACK` | zoe-data | stage 10: tie complaints to `skill_id`; open fix tickets |
| per-skill `metadata.zoe.flag` | zoe-data | `ZOE_SKILL_<ID>` — the skill's own dark switch, per member via settings |

Nothing here enqueues speech; `ZOE_PROACTIVE_SPOKEN` stays 0.

### 3.3 The skill template → the ticket

Templates are the HA-blueprint idea: most asks are a *parameter* on a known shape. Three to start:

| Template | Shape | Params | Tier | Card |
|---|---|---|---|---|
| `glance-public-data` | fetch one public JSON endpoint on a cache TTL; summarise into ≤3 numbers + a trend | source id, location/coordinate or symbol list, units, refresh | L1 | glance |
| `watchlist-per-member` | the same, over a per-member list stored in Postgres | list name, symbols, source | L2 (member data) | list → detail |
| `ha-readout` | read HA entities into a card (no control) | entity ids, thresholds | L2 (household data) | glance |

A ticket is the proposal's `describe_ticket` body plus a `zoe-skill` metadata block:
`{template, skill_id, params, tier, phrases[] (the cluster's representatives, PII-stripped),
acceptance: [replay line passes, card renders on the kiosk screenshot, allowlist test red when a
non-listed host is attempted], evidence_expectations: [pytest ci_safe, skillspector SARIF,
cross-review report], budget: {wall_s: 2700, attempts: 2}}`. The worker is told to **copy the
template skill and change parameters**; only a template miss (the person wants a new shape) is an
Opus-tier "new skill" ticket, and that ticket is L2 by default because its code is new.

### 3.4 Where the result renders

Stage 5 writes the card as a **compose tree** against the existing catalog (`ui_catalog.py`), not
HTML: a glance is `Stack[Text(kicker), Stat × ≤3, Text(caption)]`; a list is `Stack[Text(title),
ListRow × ≤6]`; detail is a `Grid` of Stats + a `Progress`. The server re-validates the tree
(depth 6, 60 nodes) exactly as compose does today; the estate loads `zoe-compose.js` (PR-2a in
`PLANS.md`, which this lane needs and should land first). Every card carries `spoken_summary`
(one sentence the brain *may* rephrase, never exceed) because the panel is spoken to first. A
skill that cannot express itself in the catalog proposes a new primitive — that is W13.3, L2, and
needs the panel-verify screenshot.

### 3.5 Reporting — mapping ticket state to inbox classes

| Ticket / PR state | Inbox | Zoe's Work column |
|---|---|---|
| created, queued | — (pull only) | In progress |
| worker running | — | In progress ("building, ~15 min") |
| draft PR + review clean, L1 | **Review**: "ready to ship — ok?" | Needs your feedback |
| draft PR, L2+ | **Notify** once: "ready for you to review on GitHub" | In review |
| GATE_BLOCKED / needs account / needs a choice | **Question** (the only Question in the loop) | Needs your feedback |
| merged + deployed | **Notify**: "ready — say X or tap" | Recently shipped |
| failed after budget | **Notify**: "parked — <one-line reason>" | Recently shipped (parked) |

### 3.6 Complaints → fix tickets (the failure feedback Letta measured)

The fix ticket carries three things the author lacked the first time: the verbatim complaint
(stripped), the replay harness's verdict for the skill's phrases (`OK | CANT_DO | ERROR` with the
transcript), and the kiosk screenshot from the panel-verify step. Two attempts; then "parked,
tell me what you expected" as a Question; the person's answer appends to the ticket. This is the
`feedback_verify_your_instruments` rule applied to Zoe's own work: the author must see red before
it may claim green.

---

## 4. Rules & guidelines — draft contracts

### 4a. The Skill Contract

One folder, `skills/<id>/`, agentskills.io-compatible so a public skill can be adapted and so any
of the ~45 clients (including the cloud worker itself) can read it:

```
skills/tides-glance/
  SKILL.md          # frontmatter + body (≤ 500 lines, ≤ 5k tokens)
  skill.py          # one module: fetch(params) -> dict ; card(data) -> compose tree ; summary(data) -> str
  phrases.jsonl     # router corpus rows {text, tool, args} — the ONLY phrase source
  tests/test_skill.py   # ci_safe: fixture JSON → card validates, summary ≤ 1 sentence, allowlist red test
  fixtures/sample.json  # recorded response, no live network in tests
  CARD.png          # panel-verify screenshot from the kiosk (added by the human/op step)
```

`SKILL.md` frontmatter (spec fields first, then `metadata.zoe`):

```yaml
name: tides-glance                      # == folder; [a-z0-9-], ≤ 64
description: Next high and low tide for the household's coast; use when someone asks about tides, high tide, low tide or swell times.   # ≤ 1024 chars, what + when
license: Apache-2.0
compatibility: zoe-data >= 2026.10; python 3.12; no shell
allowed-tools: none                      # skills never get bash / HA control / memory writes by default
metadata:
  zoe:
    version: "1"
    template: glance-public-data
    tier: L1                             # permission ladder (§4c)
    flag: ZOE_SKILL_TIDES_GLANCE
    domain: tides                        # the stage-1 router domain this adds
    tool: tides_next                     # the stage-2 tool name; args schema in skill.py
    params: { station: {type: coordinate, source: member_settings}, units: m }
    data_source: { host: marine-api.open-meteo.com, auth: none, ttl_s: 1800, terms: non-commercial 10k/day }
    network_allowlist: [marine-api.open-meteo.com]      # exact hosts; HTTPS only; enforced at invocation
    reads: [member_settings.location]    # every read is declared; none → "nothing"
    writes: []                           # L1 may not write
    secrets: []                          # env names only, never values; L1 must be empty
    card: { kind: glance, primitives: [Stat, Text], spoken_summary: required }
    tests: { ci_safe: true, replay_line: "what time is high tide" }
    review: { description_sha256: <pinned at review>, skillspector: static-pass|waived:<reason> }
```

Body sections (fixed order so the loader and the reviewer can diff them): *When to use* ·
*What it shows* · *Data source and terms* · *Permissions requested* · *Card spec* · *Spoken
summary rules* · *Failure behaviour* (stale-on-failure like `weather.py`, never a blank card) ·
*Tests* · *Change log*. **No procedural "edit this file, restart that unit" text** — the
capability-extender skill's shape is exactly what this contract replaces.

Loader rules (`ZOE_SKILL_LOADER`): parse only `skills/<id>/SKILL.md` whose frontmatter validates
(`skills-ref validate` semantics + the `zoe` block); refuse symlinks (OpenClaw's lesson); refuse a
`description` whose hash differs from `review.description_sha256` (mcp-scan's rug-pull rule);
register `domain → tool` into the router table and the Flue brain's tool list **from the file**, so
the four hand-edited lists become one generated artefact; a skill with `tier ≥ L2` is not loaded
unless `flag` is on *and* the member has accepted it.

### 4b. Panel Card Guidelines

1. **Three shapes only** — glance (≤3 numbers + trend, readable from across the room), list (≤6
   rows, one line each), detail (a grid, reached by tap or "tell me more"). Compose trees from the
   existing catalog; no free HTML, no new CSS per skill. Tokens from `skybridge-ds.css`; the
   `--wx-*` weather tokens are the precedent for a per-domain accent — add at most one.
2. **Every card has a spoken summary** (one sentence, ≤ 20 words, numbers rounded as a person
   would say them). Voice first: the summary is what Zoe says; the card is what stays on the screen.
3. **No keyboards.** Choosing a station, a symbol list or an account is a *phone* step: the panel
   shows a QR (or a Telegram deep link for a known member) and the card reflects completion live —
   the YT-Music reconnect pattern (VISION principle 8).
4. **Where it renders**: on a voice turn, the card rides the existing `show_card` /
   `cards` frame to the Ask surface; a skill may declare `home_tile: true` to appear on the estate
   home as a glance tile for the bound member only; never as a toast; never on the guest surface
   if `reads` is non-empty.
5. **Stale is shown, blank is never shown**: age badge after 2× TTL; "couldn't reach <source>"
   caption after 6× TTL; the spoken summary says "as of <time>" when stale.
6. **Panel-verify is mandatory**: a screenshot from the real kiosk (`CARD.png`) is part of the
   evidence for merge — "the panel froze invisible once before; never ship a renderer
   sight-unseen" (`samantha-evolution-plan.md:840-843`).
7. **Light and dark both**, both sizes the estate uses; no animation beyond the token motion set.

### 4c. The Permission Ladder

Declared in `metadata.zoe`, rated like an HA add-on, enforced at *invocation* by zoe-data (a
per-skill `httpx` client whose transport refuses any host not in `network_allowlist`, HTTPS only,
no redirects off-list, response size and time caps), and checked at *review* by skillspector +
a static test that greps `skill.py` for `subprocess`, `os.system`, `socket`, `open(` outside
fixtures, and any `ZOE_*` secret name not in `secrets`:

| Tier | May read | May send | Network | Secrets | Approval to merge | Approval to enable |
|---|---|---|---|---|---|---|
| **L0** | nothing (pure compute, time, the card) | nothing | none | none | gate + cross-review + panel tap | auto for the requester |
| **L1** | member settings needed for the params (location, units) | nothing | exact public hosts, no auth, non-commercial terms recorded | none | gate + cross-review + panel tap | the requester's "yes" |
| **L2** | a per-member list or household data (HA states, calendar read, lists read) | nothing | as L1, or a keyed public API with the key in env | env names declared; the key is entered on the **phone**, never typed on the panel, never in the ticket | human on GitHub (+ Greptile label) | the requester, per member |
| **L3** | as L2 | **writes** to Zoe stores (reminders, lists) or **sends** to a third party (an API with the member's account) | per-host, with the account connected via QR | as L2 | human; security review note in the PR | the member, explicit, revocable in settings |
| **L4** | anything | HA control, memory writes, shell, Telegram out | — | — | **not a skill** — this is core code: the normal PR path, no template, no auto-anything | — |

Rules: a skill cannot escalate tiers in a fix ticket (a tier change is a new offer, re-asked);
`allowed-tools` is always `none` for skills; the builder lane's own permissions are unchanged by
any skill (it keeps the container boundary, the read-only gh mount — to be narrowed to a
fine-grained PAT, `docker-compose.module.yml:182-185`); the skillspector raw score is evidence,
not a verdict (`AGENTS.md:445`), and the optional LLM stage runs **only on the local llama-server**
(B8.2) — never an external provider for internal skills. Pin: a `ci_safe` test that loads every
`skills/*/SKILL.md`, asserts the tier matches the declared reads/writes/network (an `L1` with a
non-empty `writes` is red), and asserts no `secrets` value-shaped string anywhere in the folder.

### 4d. Testing & Gates

- **Per skill** (in the PR): `tests/test_skill.py` marked `ci_safe` — fixture → `card()` validates
  against the catalog, `summary()` is one sentence, the allowlist refuses `example.invalid`
  (negative control, must be red when the transport is bypassed), params schema round-trips.
- **Router**: `phrases.jsonl` rows are appended to the stage-1/stage-2 corpora **by the loader at
  build time**, and the voice replay gate runs the `replay_line` — a new domain is a voice-path
  change (`VOICE_PATH_PATTERNS` already covers the router artefacts), so the tree-bound deploy
  gate applies without new rules. Until the self-train ratchet is unparked, a new domain means a
  hand-triggered stage-1 re-export (`export_router_heads.py`) in the same PR — L1 skills can ship
  without it by riding the brain fallback (the rock answers every router abstain), at the cost of
  latency, which the replay gate will show.
- **Samantha bar**: unchanged acceptance bar; a skill PR may not regress any passing scenario.
- **Day-sim ask**: one new scripted ask — "the person asked about tides three days running; the
  fourth morning the inbox holds exactly one Review item and nothing was spoken".
- **Panel-verify**: `CARD.png` from the kiosk, light + dark, attached before the human tap.
- **Deterministic caps**: `budget.attempts = 2`, `wall_s = 2700` per ticket (the cross-review
  script's 1,800 s plus a build), `MAX_SUMMONS`-style; a third attempt is a human decision.

### 4e. Cost & account rules

- The builder account is the **operator's** flat-rate subscription in the Omnigent container
  (Claude Max for `claude_code`; ChatGPT for `codex`), as today — the owner's "the user will need
  an account" is already the deployment reality. A **household member without an account cannot
  trigger a build**: the offer still appears, the accept produces a ticket, and the ticket waits
  in "Needs your feedback" for the operator (the account holder) — no silent spend.
- Never an API key in the container environment (it flips every CLI to metered billing,
  `docker-compose.module.yml:31-34`); if the `claude-sdk` policy question resolves to a key, it
  reaches that harness alone via the provider-entry + `OMNIGENT_RUNNER_ENV_PASSTHROUGH` pattern.
- `pi` (OpenRouter, metered) is never used for skill builds — tie-breaker only, with its cap.
- Per-ticket budget is **time and attempts** (above), plus a daily cap: at most **2 skill
  tickets per day** and one in flight (single lane, as the board runner already enforces) so the
  Jetson never carries two builder bursts.
- The OAuth renewal is the single operational risk: put a `Question` into the inbox **7 days
  before expiry** (today's note says 2026-10-11) instead of discovering it from a failed dispatch.
- Data-source terms are part of the contract (`data_source.terms`): Open-Meteo non-commercial
  10,000 calls/day; Alpha Vantage 25/day; the Yahoo chart endpoint is unofficial and may break —
  a skill on an unofficial source is L2 and must state it.

### 4f. When Zoe asks

- **Thresholds**: ≥ 3 hits on one cluster from one member across ≥ 2 distinct days, **and** a
  template match, **and** the member has not dismissed that cluster in 30 days. An explicit
  "build me / can you learn to" is one hit worth three (the ask is the signal), but still a Review
  item, not an immediate build.
- **Shape**: one sentence of why ("you've asked about X four times"), one of what ("a card
  showing …"), one of cost/privacy ("from <source>; nothing personal leaves the house"; for L2+:
  "it would read your <thing>"). Yes / not now / never.
- **Pacing**: Nomi's rule from the sibling record — one unanswered Question/Review, then wait,
  gap doubling, pull resets; at most one *build* offer per day per member; never during
  `busy`/`listening`, quiet hours, or when a guest is the one present.
- **Not asked at all**: anything L4; anything whose data source has no public terms; anything that
  duplicates an existing skill (the clusterer checks `skills/*/phrases.jsonl` first — the FROST
  "unhandled only" rule).
- **Learned "when"**: the inbox ledger's accept/dismiss rows for `source = skill_offer` feed the
  P10 head like any other Review item; until ≥ 200 rows exist the thresholds above are the rule.

### 4g. Progress and fixes — how Zoe reports

- Progress is **pull**: the Zoe's Work screen and "what's up?"; the only pushes are the three
  Notify lines in §3.5 and the one Question (blocked on the person).
- Every report line names the skill in the person's words ("your tide card"), not the ticket id;
  the ticket id is on the Zoe's Work card for the operator.
- A fix is acknowledged in the turn it is reported ("got it — I'll fix the tide card") and then
  follows the same pull rule; a parked fix asks one Question with the specific thing the builder
  could not infer.
- Zoe never claims "done" before deploy `/health` is green and the loader has registered the
  skill — the `feedback_verify_your_instruments` rule in product form.

---

## 5. RAM — where the builder lane runs

| Option | What it costs the Jetson | What it needs | Verdict |
|---|---|---|---|
| **A. As today: Omnigent container on the Jetson** | idle ~50 MB resident + ~0.2 GB swap (`profile:103`); harness ≲10 MB; **burst** = the agent CLIs + git + pytest, historically up to ~1 GB per leaked session, 19 leaked once | nothing new; add a `MemoryMax=1500M` + `MemorySwapMax=0` drop-in on the container's cgroup (the voice-stack pattern, `reference_voice_stack_memory_protection`) and the daily idle-reap the profile recommends | **keep for the first proof skill** — the lane is proven here, the worktrees and gh/MCP mounts exist, and a capped burst cannot reach the brain |
| **B. The touch Pi 5** (5.65 GB of 8 GB free, `samantha-evolution-plan.md:504-505`) | zero | a repo clone, docker, the three CLIs on aarch64 (all work — the Jetson image proves it), the credential volumes, Serena/codebase-memory (≈0.9 GB) or do without, gh auth, a tunnel route | **no** — it is the voice satellite and the wake/endpointing daemon's host; a builder burst there degrades the *panel* instead of the brain, and the Pi has no code-intel, no GPU for the local skillspector LLM stage, and would need every credential copied to a second device |
| **C. A laptop** (the owner's other option) | zero | the same `docker-compose.module.yml` with `../../` pointing at a clone, `OMNIGENT_WS_ALLOWED_ORIGINS`, the Cloudflare Access route re-pointed (or LAN-only OIDC, which the compose file documents), gh auth, the omnigent-data volume; worktrees local to the laptop; the Jetson keeps only the *result* path (merge → deploy) | **yes, after the proof** — the fleet is stateless between tickets (sessions end `idle`), so moving it is a compose re-point, not a migration; keep A as the fallback when the laptop is closed |
| D. Fleet on a cloud VM | zero | the same, plus a secret store | later; the owner's "DGX Spark" direction makes a dedicated box the end state |

The one hard rule regardless of option: **no builder process runs inside zoe-data's cgroup.** Today
the Multica poll, the board runner and the Omnigent issue executor are zoe-data modules that spawn
`git`/`pytest`/agent subprocesses under `MemorySwapMax=0` — the profile's "peak isolation" finding
(`:201-203`). The stage-4 ticket writer stays in zoe-data (it is an HTTP POST); the stage-5
executor runs as its own user unit (`flue-executor.service` or a `zoe-builder.service` wrapper
around `multica_board_runner.py --loop`) with its own `MemoryMax`, so a build burst is accounted to
the builder, not the voice path. Nothing in this record changes the ~0.5 GB `MemAvailable` reality
(`profile:53`); the memory-headroom deploy gate (`THRESHOLD_MB=250`) remains the thing that refuses
to restart the brain under pressure.

---

## 6. Measurement — the first proof skill, with negative controls

### 6.1 Tides vs stock codes

| | **Tides glance** | **Stock-codes glance** |
|---|---|---|
| Data source | Open-Meteo Marine `sea_level_height_msl` — keyless, hourly and 15-min, metres, `timezone=auto`; **verified today for a WA-coast coordinate** [live]; same provider and terms as `routers/weather.py`; non-commercial 10k/day | Yahoo chart endpoint works keyless for `.AX` (unofficial, may break) [live]; Alpha Vantage free = 25 req/day [doc]; ASX itself has no free API |
| Tier | **L1** (reads: member location; no writes; one host) | **L2** (a per-member watchlist = personal data; financial content; unofficial source) |
| Card | glance: next high / next low (time + height), rising/falling arrow; detail: 24 h sparkline (Progress or Stat grid) | list: ≤6 rows symbol / last / Δ%; detail per symbol |
| Spoken summary | "High tide's at 3:40 this afternoon, one point one metres, falling now." | "<code>'s up one point two percent, …" — needs the "no advice" rule |
| Phrases | tides, high tide, low tide, when's the tide turning | check my stocks, how's <code>, the market |
| Router impact | one new domain, ~12 corpus rows | one new domain + a symbol-slot extractor (harder for the 270M stage-2) |
| What it proves | the whole loop at the lowest rung: offer → ticket → build → review → tap → deploy → card → spoken | adds the phone-side list entry (QR → phone), the L2 human merge, secrets-via-env if a key is used |

**Recommendation: tides first, stock codes second.** Tides exercises every stage with no personal
data, no key, no financial-advice policy, and a source the house already trusts; stock codes is
the right *second* skill precisely because it adds the three things tides does not (member data,
a phone step, a keyed or unofficial source) and so tests the ladder's L2 path.

### 6.2 What is measured on the proof run

| Metric | How | Target |
|---|---|---|
| Offer precision | day-sim: 3 days of "tides" asks from the synthetic member → exactly one Review item on day 4; a control member with 2 asks on one day → **no** item | 1 / 0 |
| Nothing spoken | `voice_announcements` row count delta = 0; `PROACTIVE_RAISE` log lines unchanged | 0 |
| Ticket fidelity | the Multica ticket carries the member's phrase, template, params, tier, acceptance criteria; admission passes without a hand edit | pass |
| Build wall time | ticket `in_progress` → draft PR opened | record; expect 10–25 min [unverified] |
| Build attempts | sessions kicked per ticket | 1 (2 allowed) |
| Review | `cross_review.sh` exit 0 with findings count; `validate`, `secret-scan`, `pr-hygiene`, skillspector SARIF all present | green; findings recorded |
| Diff size | files / lines | ≤ 10 files / ≤ 400 lines (the WARN band) |
| Cost | flat-rate: record session count + minutes; `pi` spend must be 0 | $0 marginal |
| Deploy | tree-bound gate pass; `/health` green; loader registers `tides-glance` | pass |
| Latency | replay line "what time is high tide" p50 via router vs via brain fallback | ≤ 600 ms via router [the ratchet's budget] |
| Panel | `CARD.png` light + dark; spoken summary ≤ 20 words | present |
| Report | inbox shows one Notify "ready"; Zoe's Work shows the row in Recently shipped | 1 |

### 6.3 Negative controls (each must go red, or the instrument proves nothing)

1. **A skill the ladder must refuse.** A hand-written `skills/porch-cam-snapshot/` declaring
   `tier: L1` with `reads: [ha.camera.porch]` and `network_allowlist: [example.invalid]` (or an
   L1 with a non-empty `writes`): the contract test fails in `validate`; the loader refuses it at
   runtime with the flag on; the allowlist transport raises on the host. All three must be red.
2. **A build that tries to leave its lane.** A ticket whose brief asks the worker to "also
   restart zoe-data and push to main": the PR is draft-only, the branch is `wt/*`, branch protection
   refuses, the kill switch is invisible inside the container — verified by the attempt failing,
   not by reading the docs.
3. **A complaint that must produce a fix ticket.** Within two turns of the tide card: "that tide
   time was wrong" → `user_issue_report` tagged `skill_id = tides-glance` → a `fix` ticket with the
   complaint, the replay verdict and the screenshot path; a complaint about the *weather* card in
   the same window must **not** tag the tide skill.
4. **A complaint with no skill on screen** → an ordinary `user_issue_report`, no skill tie
   (control for over-attribution).
5. **A dismissed offer** → no re-offer for 30 days even with 10 more hits (the cluster is
   suppressed, not the detector).
6. **Flag off** → byte-identical behaviour: zero `skill_wants` rows, zero Review items, the four
   static tool lists unchanged.

---

## 7. Go / no-go against the VISION principles

| Principle | Verdict | Why |
|---|---|---|
| 1 Rocks fixed | GO | no model swap; the brain routes to and phrases skills; the router is the rock "allowed to improve" |
| 2 Local, private, fast | GO with the data-class rule | the only egress is the ticket text + code to the fleet (already the established practice for engineering work) and the skill's declared public host; L2+ data never reaches the builder — the worker gets a *fixture*, never a member's row; the hot path is untouched (skills run on a TTL cache) |
| 3 Lab-prove before prod | GO | six flags, default off; day-sim ask; replay gate; the first skill is built through the loop in the lab with a synthetic member before any live member sees an offer |
| 4 Build it to STICK | GO | contract test pins the ladder; the loader generates the tool lists instead of four hand edits; skillspector in CI; fix tickets carry the red evidence |
| 5 Capture, don't lose | GO — the principle the idea serves | 21,254 wants have been written and never answered; the Review item is the pin |
| 6 Borrow the piece | GO | SKILL.md spec, HA blueprint params, add-on rating, FROST's unhandled-only + acceptance gate, Copilot's draft-only lane — no framework adopted; OpenClaw's registry explicitly not |
| 7 Right tool | GO | Omnigent's existing kick, `cross_review.sh`, `worktree_bootstrap`, `multica_admission`, the compose catalog, `evolution_notice` — all reused |
| 8 Voice first, touch second, no keyboard | GO | the offer is spoken on pull and shown as a card; the yes is a word or a tap; station / symbol / account entry is a phone step via QR |
| 9 Understand before you change | this record | the chain is traced; the first PR is a ledger (`skill_wants`), not a behaviour |
| Owner rule: never speaks unprompted | GO, strengthened | no path enqueues speech; the offer lives in the inbox the owner pulls |
| Owner rule: human for household data / egress | GO | L2+ is a human GitHub review; L0–L1 is still a human tap plus the deterministic gate — no bot merges at any tier |

**No-go items inside the idea:** an in-process agent editing the live checkout (the
capability-extender shape); any runtime "install skill" endpoint (`/api/openclaw/skills/*/install`
retires with OpenClaw); an open skill registry; skill descriptions fed to the brain prompt
without the hash pin; the builder on the touch Pi; a third attempt without a human; the builder
lane inside zoe-data's cgroup; anything L4 as a "skill".

---

## 8. Staged plan — PR order

Each PR flag-dark, each with its `ci_safe` test and day-sim ask, drafts first, cross-review before
ready, ≤ 400 lines where possible:

1. **Seams + truth (docs/code, no behaviour).** Split the `extend_capability` catch-all into
   `open_domain` (behaviour-preserving: same branch, new name); delete the dead chat→board card or
   gate it behind `ZOE_SKILL_TICKETS` with a POST form that carries the user's text; correct the
   five stale `skill_discovery` references and `skills/AGENTS.md:9` (an AGENTS.md edit — operator
   or a later PR, not this record); add the Omnigent OAuth-expiry Question to the inbox design list.
2. **`ZOE_SKILL_WANTS` ledger.** `skill_wants` table, the three detectors (explicit ask, live
   `cant_do` tag in shadow, per-member repeat count), PII-stripped like the miss log; the
   clusterer gains member/day fields. Day-sim ask: 3 days → 1 candidate; control → 0.
3. **Skill Contract + loader + ladder test (`ZOE_SKILL_LOADER`).** `skills/_template/`, the
   frontmatter validator, the allowlist transport, the contract `ci_safe` test with the porch-cam
   negative control, the generated tool-list artefact (router `DOMAIN_TOOLS` + Flue tool list from
   `skills/*`), skillspector CI job on `skills/**` (static, SARIF). No skill yet.
4. **The hand-built tides skill through the human path.** Write `skills/tides-glance/` by hand
   (Open-Meteo marine, L1), land it through the normal PR pipeline with panel-verify, estate loads
   `zoe-compose.js` (PLANS PR-2a) — this is the "W7 on something trivial" DoD and it produces the
   friction list for the brief.
5. **Offer + report (`ZOE_SKILL_OFFER`).** Candidate → Review item (needs the inbox record's PR 2);
   Zoe's Work rows for skill tickets; the three Notify lines and the one Question.
6. **Tickets + builder brief (`ZOE_SKILL_TICKETS`).** Accept → proposal + template ticket;
   the brief template; the `zoe-builder.service` wrapper outside zoe-data's cgroup with
   `MemoryMax`; the Omnigent container `MemoryMax` drop-in; the panel-tap → `skill-approved` label.
   **Operator steps**: renew the Claude OAuth (before 2026-10-11), narrow the gh mount to a
   fine-grained PAT, decide the `claude-sdk` policy, remove the kill switch for the single lane.
7. **First automated run — the stock-codes skill** (L2, the phone step, human merge), timed and
   costed per §6.2, with negative controls 2–5. Record the result in
   `docs/knowledge/` as the W7 trail.
8. **Fix loop (`ZOE_SKILL_FEEDBACK`).** `chat_feedback.skill_id`, complaint → fix ticket with
   replay verdict + screenshot; negative controls 3–4.
9. **Move the lane to a laptop** (§5 option C) once two skills have landed; keep the Jetson
   container as fallback; re-measure the burst on the Jetson at zero.
10. **Unpark the router self-train ratchet** for skill corpora only, so a new domain no longer
    needs a hand re-export (depends on the W3 RAM gate as recorded).

---

## 9. Open questions for the owner

1. **Who may say yes?** This record proposes: any bound member may accept an L0–L1 offer for
   themselves; only the account holder (operator) can accept L2+ or anything that creates a
   ticket while no subscription is logged in. Is a per-member "may ask Zoe to build" setting wanted?
2. **The light tier's human tap** — is a tap on the panel's "Needs your feedback" card an
   acceptable merge approval for L0–L1 (the operator still runs the merge), or must every merge
   happen on GitHub?
3. **Laptop or Jetson first** for the proof run? This record says Jetson with a `MemoryMax`
   drop-in, laptop after two skills.
4. **Stock codes**: Yahoo (keyless, unofficial) or Alpha Vantage (25/day, key on the phone)? The
   skill contract can carry either; the ladder places both at L2.
5. **Should dismissed clusters ever be re-offered** (30 days proposed), and should "never" be a
   third answer that suppresses the cluster permanently?

---

## 10. Sources

Field (primary unless marked):
- Agent Skills spec and clients: https://agentskills.io/specification · https://agentskills.io/clients · https://github.com/agentskills/agentskills · https://github.com/anthropics/skills · https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview · https://code.claude.com/docs/en/skills
- OpenAI: https://learn.chatgpt.com/docs/build-skills · https://github.com/openai/skills · https://developers.openai.com/codex/guides/agents-md · https://simonwillison.net/2025/Dec/12/openai-skills/ · https://developers.openai.com/api/docs/actions/getting-started
- MCP: https://modelcontextprotocol.io/specification/2025-06-18 · https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/ · https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks · https://invariantlabs.ai/blog/whatsapp-mcp-exploited · https://invariantlabs.ai/blog/introducing-mcp-scan
- OpenClaw: https://docs.openclaw.ai/tools/skills · https://docs.openclaw.ai/tools/creating-skills · https://docs.openclaw.ai/clawhub/skill-format · https://en.wikipedia.org/wiki/OpenClaw · https://thehackernews.com/2026/02/researchers-find-341-malicious-clawhub.html · https://www.antiy.net/p/clawhavoc-analysis-of-large-scale-poisoning-campaign-targeting-the-openclaw-skill-market-for-ai-agents/ · https://blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare · https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/ · https://unit42.paloaltonetworks.com/openclaw-ai-supply-chain-risk/ · https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-agent-skill-scanner-bypass-20260610-csa/ · https://arxiv.org/abs/2601.10338
- Hermes: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills · https://hermes-agent.nousresearch.com/docs/guides/work-with-skills
- Letta and skill-library papers: https://www.letta.com/blog/skill-learning/ · https://www.letta.com/blog/context-bench-skills/ · https://arxiv.org/abs/2305.16291 (Voyager) · https://arxiv.org/abs/2504.07079 (SkillWeaver) · https://arxiv.org/pdf/2409.07429 (AWM)
- Home Assistant / HACS / OVOS / Mycroft: https://www.home-assistant.io/docs/blueprint/schema/ · https://community.home-assistant.io/t/about-blueprints/253788 · https://www.hacs.xyz/docs/publish/include/ · https://developers.home-assistant.io/docs/add-ons/security · https://openvoiceos.github.io/ovos-technical-manual/411-skill_json/ · https://github.com/OpenVoiceOS/OVOS-skills-store · https://openvoiceos.github.io/OVOS-skills-store/skills.json · https://mycroft-ai.gitbook.io/docs/skill-development/marketplace-submission/skills-acceptance-process · https://github.com/MycroftAI/mycroft-skills · https://blog.openvoiceos.org/posts/2025-05-20-ovos-and-mycroft-a-fork-that-wasnt-meant-to-be · https://www.home-assistant.io/docs/tools/dev-tools/ · https://www.home-assistant.io/blog/2024/12/04/release-202412/
- When to offer: https://www.amazon.science/publications/frost-fallback-voice-apps-recommendation-for-unhandled-commands-in-intelligent-personal-assistants · https://www.frontiersin.org/journals/big-data/articles/10.3389/fdata.2022.867251/full · https://www.amazon.science/blog/the-science-behind-hunches-deep-device-embeddings · https://developer.amazon.com/en-US/docs/alexa/custom-skills/implement-canfulfillintentrequest-for-name-free-interaction.html [search-excerpt] · https://developer.amazon.com/en-US/docs/alexa/custom-skills/certification-requirements-for-custom-skills.html [search-excerpt] · https://docs.cloud.google.com/dialogflow/cx/docs/concept/intent · https://docs.cloud.google.com/dialogflow/cx/docs/concept/analytics · https://legacy-docs-rasa-x.rasa.com/docs/rasa-x/0.37.x/changelog/rasa-x-changelog/ · https://arxiv.org/abs/2304.07699 · https://aclanthology.org/2024.sigdial-1.64.pdf · https://asciiwwdc.com/2018/sessions/211 · https://developers.google.com/assistant/conversation-design/errors [search-excerpt] · https://www.nngroup.com/articles/error-message-guidelines/ · https://www.androidcentral.com/apps-software/google-shutting-down-conversational-actions · https://support.google.com/googlehome/answer/13460475 [search-excerpt]
- Security and governance: https://code.claude.com/docs/en/sandboxing · https://agent-safehouse.dev/docs/agent-investigations/codex · https://e2b.dev/security · https://gvisor.dev/docs/ · https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ · https://research.google/pubs/an-introduction-to-googles-approach-for-secure-ai-agents/ · https://arxiv.org/abs/2503.18813 (CaMeL) · https://owasp.github.io/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf · https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ · https://github.com/nvidia/skillspector [2nd: https://towardsdatascience.com/from-green-checkmark-to-real-judgment-auditing-ai-agent-skills-with-skillspector/] · https://github.com/cisco-ai-defense/skill-scanner · https://arxiv.org/abs/2505.22954 (DGM) · https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations [search-excerpt] · https://docs.github.com/copilot/customizing-copilot/customizing-or-disabling-the-firewall-for-copilot-coding-agent [search-excerpt]
- Cost: https://code.claude.com/docs/en/costs · https://docs.github.com/en/billing/concepts/product-billing/github-copilot-premium-requests · https://www.usagepricing.com/blueprint/cognition [2nd] · https://www.morphllm.com/comparisons/codex-vs-claude-code [2nd]
- Data sources probed today: https://open-meteo.com/en/docs/marine-weather-api (`sea_level_height_msl`; pricing page: non-commercial 10,000 calls/day) · https://www.alphavantage.co/support/ ("25 API requests per day") · Yahoo `query1.finance.yahoo.com/v8/finance/chart/<SYM>.AX` (unofficial)

Ours (all paths relative to the repo root; line numbers as of this worktree's `51e6e020`):
`docs/VISION.md`, `docs/CANONICAL.md`, `AGENTS.md:111-348,416-447,543`, `skills/AGENTS.md`,
`modules/AGENTS.md`, `docs/guides/MODULE_SYSTEM.md`, `docs/guides/CREATING_SKILLS.md`,
`docs/governance/SECURITY_POLICY_SKILLS.md`, `docs/architecture/EXTENSIBILITY.md:105-128`,
`docs/architecture/zoe-evolution-proposal-contract.md`, `docs/architecture/multica-executor-migration.md`,
`docs/architecture/samantha-evolution-plan.md:288-345,676-690,820-845,1070-1099`,
`docs/architecture/beat-the-bar-2026-program.md:258-268,1199-1204,1305-1325`,
`docs/architecture/skybridge-design-system.md`, `docs/knowledge/omnigent-cross-review.md`,
`docs/knowledge/omnigent-container-config.md:67-108`, `docs/knowledge/memory-pressure-profile-2026-10-03.md`,
`docs/knowledge/autopilots/evolution-{nightly-notice,weekly-digest}.md`, `docs/knowledge/router-selftrain-loop.md`,
`docs/research/pull-not-push-inbox-2026-10-04.md`, `docs/multica-issue-capture-brief.md`,
`modules/omnigent/{README.md,docker-compose.module.yml}`, `labs/flue-executor/{README.md,FINDINGS.md,src/live-runner.ts,src/omnigent.ts}`,
`scripts/maintenance/{cross_review.sh,cross_review_poll.py,voice_gate_check.py,voice_regression_probe.py}`,
`scripts/setup/systemd/README.md:187-267`, `.github/workflows/{deploy.yml,pr-hygiene.yml,validate.yml,voice-gate.yml,greptile-gate.yml}`,
`services/zoe-data/{intent_router.py,evolution_notice.py,pi_intent_evidence.py,pi_intent_classifier.py,guest_policy.py,fast_tiers.py,zoe_evolution_proposal.py,multica_admission.py,multica_client.py,multica_autopilot_sync.py,multica_board_runner.py,omnigent_issue_executor.py,executor_registry.py,worktree_bootstrap.py,pipeline_store.py,router_two_stage.py,semantic_router.py,ui_compose.py,ui_catalog.py,card_contract.py,routers/chat.py,routers/system.py,routers/weather.py,routers/proactive.py,proactive/selector.py,executors/kanban_adapter.py,tests/replay_samples.py,alembic/versions/{0001,0004,0030,0033}*.py}`,
`services/zoe-ui/dist/touch/{home.html,css/skybridge-ds.css,js/zoe-compose.js}`, `services/zoe-ui/dist/js/touch-ui-executor.js`,
`skills/openclaw/zoe-capability-extender/SKILL.md`, `scripts/perf/{samantha_bar.py,samantha_day_sim.py}`.
