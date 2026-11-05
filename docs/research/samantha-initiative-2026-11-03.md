---
type: research
title: The Samantha Initiative (2026-11-03) - what Zoe is, what the field knows, what comes after the assistant, and what to build first
date: 2026-11-03
status: research-only. Seven lanes (A current state vs code; B agents and self-improvement; C memory; D persistent cognition; E conversation; F personality and proactive; G embodiment and distributed) plus a citation check are in the bundle. Nothing was built, flipped or deployed. Absorbs the open Samantha records of 2026-10-07/09 (gap register, best-ideas register, mind layer, person-likeness) into one plan of record.
description: Owner brief - investigate and engineer what comes after the traditional AI assistant, with Samantha as the inspiration and Zoe as the foundation; challenge everything; be ambitious and grounded. Answer first - Zoe does not need a new architecture. She has one, distributed across modules and half-closed; what she knows almost never reaches what she says, and the household barely talks to her. The next generation is a closed loop over what exists, measured by follow-through and continuity, with one new edge (outcome) and one new habit (push memory into every turn). Everything speculative is labelled.
evidence: samantha-initiative-2026-11-03/ (lane-A..G, citation-check); the measured baselines - voice replay 18/18 clips, warm medians stt 491 / brain 1299 / e2e 1618 ms (relative); Samantha bar baseline S1-S8 PASS (2026-09-30, 8 of 23 cells); 349 real member turns on 19 of the last 30 days against 416 total (harness share now ~16%, down from 97% in October); brain tool calls 1 in 51 turns, recall_memory 0, continuity injection 0 of 91; MemAvailable ~500-650 MB waking; the 12B night window ~20 min usable, 5 of 8 nights completed.
---

# The Samantha Initiative (2026-11-03)

> The brief asked for ambition and criticism in equal measure. The criticism comes first because it decides the ambition: Zoe's problem is not a missing architecture but an open circuit. Every lane, from four different starting points, arrived at the same wire.

## 0. Answer first

1. **Zoe already has the cognitive architecture the brief describes.** Mapped onto CoALA's slots (lane D): working memory (the prompt packet), episodic (verbatim turns and drawers), semantic (authority rows, people graph), procedural (fast tiers, router, doctrines), salience (the proactive selector), reflection (night mind), metacognition (struggle detector), consolidation (digests, dreaming), engagement (pull-not-push). Nine of ten components exist; six are live. [verified code + live]
2. **The circuit is open at one point.** The brain made one tool call in 51 turns and never called recall; continuity injection matched 0 of 91; the recall gate serves nothing (shadow); night-mind readers are off; the voice path replays three chat rows of history; each turn carries ~2,840 tokens of persona prompt and ~164 of history. Memory, reflection and identity are written and never read into a conversation. [verified live, lane A]
3. **The field does not have a better store; it has the same lesson.** Zoe's memory beat the candidate engines on every shared axis (bake-off 2026-10-08, KEEP_Z0). The agents, memory and cognition literature converges on "edit text around a frozen model, push context in, accept only what an external evaluator confirms"; Zoe's designs already have that shape and are ahead on safeguards. What the field has and Zoe lacks is a running improver, pass^k reliability measurement and an outcome edge. [lanes B, C, D]
4. **"Natural" is turn-taking and manner, not a bigger or duplex model.** Full-duplex models take over during pauses (Moshi 0.985 takeover rate on Full-Duplex-Bench) and need 24-80 GB; the cascade with a judged end-of-turn is the right shape, and Zoe's interruption handling, hesitation tails and speculative start are built and switched off. The owner's "half-baked robot" is the two ends of the turn (a fixed 640-800 ms endpoint wait, a canned first line), not the middle. [lane E]
5. **Perception is thin by policy, and that is mostly right.** Zoe reads no Home Assistant presence entity and does not subscribe to its event bus; there are no presence sensors. The cheapest real sense (reading `person`/`device_tracker`, then one mmWave sensor) costs hours and no RAM; cameras, robots and vision-language-action models are research for a household, not engineering. [lane G]
6. **The one hard constraint is one llama slot on 16 GB.** A daytime 4B call holds the slot ~8 s and evicts the panel's prefix (3.6 s penalty on the next household turn). So: by day the 4B is spent only inside a turn the person started; code, bge-small and the 270M router run any time; the night window (4B always, 12B when it loads) does consolidation and reflection. Background cognition is a policy, not a daemon. [lane D]
7. **The signal problem is real but improving.** October: one member on 9 of 30 days, 97% harness traffic. Last 30 days: 349 real turns on 19 days, harness ~16%. Self-improvement has something to learn from for the first time, but 40 real open loops to measure follow-through could still take months (lane D section 4). [verified live by the author: database count on 2026-11-03, method in section 12; the lanes did not have this count]
8. **What to build first:** close the circuit (section 9). Ten changes, all inside the rocks, nine of them flags, readers and injectors that already exist. Measured by two numbers Zoe cannot fake: the share of household turns whose reply used a relevant memory it had, and timely follow-through on dated things the person said.
9. **What is beyond this hardware:** full duplex, reliable emotion from a single natural clip, a persistent 4B "inner voice" by day, and facts in weights. Each is labelled and each has a cheaper stand-in.
10. **The Spark changes the budget, not the design.** A DGX Spark would lift the daytime 12B and the slot ceiling; every item below is sized for the Orin and survives the upgrade unchanged.

## 1. Current state assessment (Phase 1, 12.1)

Lane A re-verified every record against the code at `origin/main` and the running box on 2026-11-03. Full table in `lane-A-current-state.md` section 2.

**What Zoe is.** One FastAPI process (zoe-data, ~443 routes, 60 router modules) runs STT (Moonshine, in-process), fast tiers, the two-stage tool router (numpy stage 1 + FunctionGemma-270M), memory, identity, scheduling and the panel protocol. Conversation that is not a direct command goes to the Flue 2.x sidecar (22 tools) and from there to Gemma 4 E4B QAT + MTP on llama-server with a single 8,192-token slot (now llama.cpp b11982). Kokoro is the only voice. Memory is Postgres (people, relationships, chat, consolidation state) plus a Chroma-backed MemPalace for raw-first recall, with a nightly 12B window for digest, dreaming, night mind and the pendant day review. Identity is live (ResNet34 speaker, face, voice sessions, enrolment offers). Self-healing of restarts is live; self-improvement does not run.

**Strengths worth preserving** (lane A section 3): the authority model and forgetting (237/237 authority cells; forgotten means forever enforced in the palace); the instrument culture (replay gate bound to a commit, control arms, negative controls, a research-index discipline); the rocks themselves (a 4B that answers in ~1.3 s warm with MTP; a 270M router that picks tools better than the brain); pull-not-push; the per-panel targeting and privacy filters; a night window that exists at all.

**Weaknesses that block the Samantha direction, ranked** (lane A section 4):

| # | Weakness | Evidence | Class |
|---|---|---|---|
| 1 | What Zoe knows does not reach what she says | recall tool 0/51, continuity 0/91, gate shadow, night-mind readers off, history 164 of ~3,750 tokens | architectural (wiring) |
| 2 | The box is a development machine first | Kokoro starts 24-51 on dev days vs 2 on quiet days; night aborts on memory; restore drill refused; voice gate flaky | operational |
| 3 | Silence as a failure mode | Kokoro the only voice, stopped by probes; no spoken degraded state | architectural |
| 4 | One process carries everything | 196k lines, voice path + memory + Multica autopilots + scheduling on one loop; 0.74 s stalls still logged | architectural |
| 5 | Thin usage signal | ~51 brain turns in 4.5 days; people table untouched since 10-04 | product |
| 6 | Accumulated generations | 3 brain lanes, 842 flags + 45 live env vars nothing reads (incl. a retired runtime's execution-allow flag), stubs, callerless routes | hygiene |
| 7 | Docs lag the box | CANONICAL build, ROADMAP Now, PLANS flag block, the 11-02 record's alarms line each wrong today | process |

**Intended vs demonstrated** (selected): "Samantha-grade memory" (VISION) is built and not demonstrated at the conversation surface; the nightly 12B is demonstrated 5 nights of 8; backups are designed and not demonstrated (drill refused, off-box unconfigured); self-evolution is planned-only; the struggle detector is partial (shadow, 5 hits in two days); Music Assistant has been stopped four weeks while its discovery job runs weekly.

**Underutilised** (lane A section 5): 22 brain tools, one call; the night mind's nightly output, read by nothing; five shadow instruments whose logs nobody reads; the 12B doing ~9 minutes of work in a ~64-minute budget; 261 pendant recordings with 164 empty transcripts; 159 callerless routes before slice 1 of the removal.

**Security, privacy, reliability** (classes only; detail stays in the private review): a device token is still a strong credential at the panel edge; an MCP panel remote-command tool lacks an allowlist in its handler; the forget inventory shows 41 of 107 stores uncovered and the brain sidecar's transcript store is outside it; stale env flags from retired runtimes remain set; reliability tracks development activity.

## 2. Frontier research report (Phase 2, 12.2)

The lane bundle holds ~120 projects and papers with purpose, architecture, maturity, strengths, weaknesses, hardware, licence, benefit and verdict, every URL run through the citation check (`citation-check-2026-11-03.md`; items it could not verify are marked in place). What matters, by theme:

- **Agents and self-improvement (lane B).** Harness² (propose-probe-compose over a frozen model), GEPA (reflective prompt evolution, MIT, pip-installable, sample-efficient), ACE (playbook with incremental delta updates), DGM and SICA (code self-modification; wrong risk class for a household), AWM/Voyager/ExpeL (skill and routine libraries), tau-bench pass^k, RAG-MCP (tool retrieval beats a flat tool list). Verdicts: adopt the GEPA loop on the overlay and ACE's data structure; adapt AWM-style cached routines; reject DGM/SICA/ADAS and multi-agent planners for Zoe's risk and latency class. Why it matters: it confirms Zoe's nightly-loop design and names the two missing instruments (pass^k, collateral damage).
- **Memory (lane C).** MemGPT/Letta core blocks and sleep-time compute, mem0, Zep/Graphiti, Hindsight, A-MEM, HippoRAG 2, LightRAG/GraphRAG, Cognee, continual learning (LoRA, SEAL, Doc-to-LoRA, Titans), benchmarks (LoCoMo's audited 6.4% key errors, LongMemEval-S now fitting one context window, TriggerBench/PM-Bench, GateMem). Verdicts: adopt the core-block idea as a nightly "now card"; keep continual learning non-parametric; reject facts-in-weights (no provenance, no reliable unlearning); treat public memory benchmarks as weak and extend Zoe's own bench. Why it matters: it moves the problem from storage to delivery and gives the continuity metrics.
- **Cognitive architectures (lane D).** CoALA, LIDA/GWT, ACT-R, Soar, Generative Agents, sleep-time compute, Temporal/Restate, Home Assistant's event bus. Verdicts: adopt CoALA as the map and three formula-sized ideas (Soar's impasse-as-subgoal onto the can't-do signal; ACT-R activation replacing a single 72-hour half-life; sleep-time precompute as a code-and-embedding recall cache at first presence); reject a literal global workspace and durable-workflow engines. Why it matters: it turns "persistent cognition" from a daemon into a scheduler policy with measurable cost.
- **Conversation (lane E).** Full-Duplex-Bench results for Moshi, Gemini Live, GPT-4o Realtime; Smart Turn v3.2 (8M params, BSD-2, 10-65 ms CPU); LiveKit's adaptive interruption; Kokoro's actual control surface (phonemes, style vector, speed; no emotion input); speech-emotion models (90-99% acted, 28-35% real); DuplexCascade's turn-act idea. Verdicts: adopt Smart Turn on the Pi after an offline corpus test; turn on what is built; reject full duplex on this box; label audio-native Gemma (mmproj) as a rock-adjacent experiment. Why it matters: it locates "natural" at the two ends of the turn and inside the rocks.
- **Personality and proactive (lane F).** Persona-drift measurement, Stable Personas (self-report stays stable while observers see decline), the MIT/OpenAI four-week trial on dependence, calm-technology and mixed-initiative HCI, EQ-Bench 3, SOTOPIA. Verdicts: identity as four stored layers (invariants, one voice for every tier, per-member register, relationship state); drift measured from outside by a fixed probe battery; silence wins ties; dependence counters as a standing metric. Why it matters: it converts personality from adjectives into tests.
- **Embodiment and distribution (lane G).** mmWave presence, Home Assistant Bayesian sensors, YAMNet-class audio events, Frigate, VLAs (RT-2, OpenVLA, pi0), exo/Petals, cross-device speculative decoding, RouteLLM/FrugalGPT. Verdicts: adopt HA presence reading and one radar; adapt audio-event labels on the Pi later; reject model sharding and whole-home camera NVR; label VLAs research. Why it matters: it fixes the device-role map (Pi senses and gates; Orin answers; Spark does night and development; phone carries identity and away-from-home) and keeps the world model honest (observed and inferred tables that never merge).

## 3. Capability gap analysis (12.3)

| Samantha experience | What Zoe has | The gap | Class |
|---|---|---|---|
| Flowing conversation, interruptions, unfinished thoughts | fast tiers, hard-stop barge-in, adaptive tails (off), duck-decide-resume (off), speculative start (off), 3-row history | judged end of turn; interruption resume never run live; a one-and-a-half-exchange window | missing wiring + one small model (Smart Turn) |
| Remembers meaningful things and uses them | authority rows, people graph, drawers, night mind, recall gate (shadow), person-centred memory D1-D12 accepted | delivery: 83% of replies use no memory; no now card; readers off; no ageing; 27% provenance labels | architectural (wiring) |
| Knows who is talking and adapts | ResNet34, face, voice sessions, persona envelope, manner block | per-member register as stored state; identity breaks at the tiers (canned lines) | missing functionality |
| Notices and follows through | selector, open loops, night-mind threads, pull queue, orb dot | no outcome edge; no due-today re-score at first presence; follow-through unmeasured | architectural (one edge) |
| Proactive without intrusion | pull-not-push, quiet hours, back-off | no written channel ladder and caps; policy never run even in shadow | missing policy |
| Understands her environment | HA proxied for UI, no presence read, no event subscription | observed/inferred tables; HA presence; one radar | missing functionality (cheap) |
| Independent problem-solving | 22 tools, router, write-claim receipt guard | authority table, step/time budget, escalation card, code-checked success | missing policy + small build |
| Emotional intelligence, honestly | struggle detector (text cues), tone record, emotional-safety note | act-on-text behaviours (apologise, shorten, offer) not wired to the tiers; prosody as a corroborating vote only | missing wiring; model limit for audio emotion |
| Gets better over time | nightly-loop design, struggle shadow, router self-train (dormant), Zoe score design | nothing runs; no pass^k; no gap clustering; 716 stale proposals | missing implementation |
| Years of continuity | portraits, threads, dates | no episode objects, no timeline, no cross-month matcher, forget-completeness unproven across derived layers | architectural (view over pointers) |
| Sub-second feel | warm brain 1.3 s, e2e 1.6 s relative; p90 ~6 s live | endpoint wait, prefix eviction, Kokoro reloads, one slot | hardware + operations |
| Full-duplex overlap, hearing tone, laughter | - | - | beyond this hardware (duplex, audio emotion) or a rock lift (expressive TTS) |

## 4. Proposed cognitive architecture (Phase 10, 12.4)

Not a rewrite. A closed loop over the components that exist, with a scheduler policy, one new edge and one new habit. Lane D section 2 carries the component table and the cost table; lane C section 2 the memory layers; lane E section 2 the turn; lane F section 3 the engagement policy; lane G section 3 the device roles.

```
                 PI PANEL (ears, eyes, presence; gates)                 PHONE (identity, away-from-home, approvals)
  wake  ──▶ VAD ──▶ speaker/face ──▶ judged end-of-turn (Smart Turn) ──▶ audio ──┐
  ◀── duck/decide/resume ◀── Kokoro audio ◀────────────────────────────────────┐ │
                                                                                │ │
 ORIN  ┌──────────────────────── zoe-data (one process, policy-bounded) ────────┼─┼──────────────────────────────┐
       │ PERCEPTION JOURNAL (append-only: turns, sessions, pulls, raises, outcomes, HA state changes [allow-list])│
       │        │                                                                                                 │
       │ ATTENTION / SALIENCE (code; selector: importance×recency×relevance, ACT-R activation)  ◀─ night mind     │
       │        │                                                     ▲                          (threads, raise/│
       │ CONTEXT MANAGER (code + bge-small): identity line, NOW CARD (nightly, by key), recall  │  leave, quotes) │
       │   gate ENFORCE (400-token budget), continuity, one RAISE, history 8 rows ─▶ prefix-stable packet         │
       │        │                                                                                                 │
       │ REASONING SCHEDULER (policy): day = 4B only inside the person's turn; code/bge/270M any time;            │
       │   night = 4B always, 12B when it loads; every escalation logs reason + cost                               │
       │        │                                   │                                                              │
       │   fast tiers ──▶ one voice for every tier  │  Flue brain (Gemma 4 E4B+MTP, one slot, 22 tools)           │
       │        │                                   │        │ write-claim receipt guard; authority table           │
       │ ENGAGEMENT MANAGER (code): channel ladder silence→dot→card→spoken-on-pull→spoken-unprompted (asked-for  │
       │   reminders only); caps, back-off, DND, guest/unverified first                                            │
       │        │                                                                                                 │
       │ OUTCOME EDGE (new): welcomed / ignored / resolved written back to the item, the thread, the selector     │
       └─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 NIGHT WINDOW (02:50, 4B first, 12B upgrade): consolidation, now cards, dormant-thread matcher, gap clustering,
   reflection, uncertainty sampling, the self-evolution improver (GEPA on the overlay, shadow-apply, canary)
 MEMORY: authority rows + people graph + drawers (stores) ─▶ derived VIEWS over pointers (now card, chapters,
   associations) that a forget can rebuild; observed vs inferred world-model tables never merge
```

**Responsibilities and protocols.** Perception journal: one Postgres table, keyed `(item_id, local_day, touch_point)` for idempotency across the ~17 daily restarts; HA state changes enter only through an allow-list, per-entity debounce and a per-minute cap. Salience: SQL and code, re-scored at a member's first presence of the day and on the slow loop; no model. Context manager: builds the packet in a fixed order (identity, now card, recall, continuity, raise, history) so the llama prefix stays cacheable; the now card is 120-180 tokens per member, built nightly, read by key. Reasoning scheduler: the written policy of lane D section 3.3. Engagement: lane F's 14-row decision table and channel ladder. Outcome edge: the only new table column family (`outcome`, `outcome_at`, `outcome_source`), fed by the pull answer, the next turn's text and resolution of the loop. Security boundaries unchanged: restraint, authority, forget, guest policy, identity, the untrusted-content fence and tool permissions stay code; the self-evolution loop edits only the overlay; nothing in the daytime path may call the 4B outside a turn. Deployment: no new daemon, no new container; four flags, one table, one Pi model (Smart Turn, 8 MB), one radar.

**Why not independent services or specialised agents.** The brief's components map one-to-one onto functions already in zoe-data; splitting them into services would add IPC, memory and failure modes on a 16 GB box to gain nothing measurable (lane D section 6). The only separation that pays is the one that exists: sidecars for the models. The one structural debt worth paying later is moving the engineering harness (Multica autopilots, pipeline sync) out of the household process (synthesis cut list), which is an operational split, not a cognitive one.

## 5. Challenge everything (Phase 11)

- **Poor decisions to reverse:** daytime harness traffic on the household slot (now largely fixed by quiet hours and a scratch DB; keep it that way); the legacy KV warm at boot that fills the slot with prompts Flue never sends; a code default brain backend of `core` while `flue` is live; three brain lanes with precedence instead of failover.
- **Unnecessary complexity:** three proposal writers and 716 unread proposals; a dormant router self-train loop beside the struggle feed; 45 live env variables nothing reads; 842 inventoried flags; Multica autopilots inside zoe-data; stubs that return fake success; the remaining OpenClaw/Hermes lanes (slice 2 of the removal).
- **Weak implementations:** the voice history window of 3 rows; trigger-gated recall; the proactive selector's single 72-hour half-life; the flag registry missing `serve` and `shadow2` values the code uses; `zoe_memory_layers.py` naming engines that were never adopted.
- **Fragile integrations:** Kokoro as the only voice with no spoken degraded state; the single llama slot with no save/restore; the nightly voice gate flipping on STT speed under memory pressure; LiveKit prewarm spawning a worker for an exited container.
- **Features to remove:** the `/ws/voice` remnants and dead touch pages already cut; next the builder intents that map to a retired runtime, `chat_hermes_stream.py`, the agent-sync tool's retired targets, the fake-success stubs, the memories collections/tiles UI against GET-only stubs.
- **Open-source that should replace custom code:** Smart Turn v3.2 for end-of-turn (replace the fixed tail), GEPA for the improver loop (replace designing one), Home Assistant's presence entities and Bayesian sensor as the comparison for fusion. Not: a memory engine, a workflow engine, an agent framework, a duplex model.
- **Technically unrealistic as asked:** a persistent 4B "inner voice" by day on this box; full duplex; reliable emotion from one natural clip; unlearning facts from weights.
- **Assumptions that could invalidate this plan:** that pushing memory into every turn does not degrade the 4B's answers (test with the bar's control arms before enforce); that the household will generate enough dated loops to measure follow-through (if not, measure the failure loop instead, lane D E8); that Smart Turn's accuracy holds on this household's speech (a third-party test scored it 68.6% on another language; measure offline first); that the Spark arrives (nothing here depends on it).

## 6. Prioritised development roadmap (12.5)

**Immediate (flags, readers, injectors; no architectural disruption; two to three weeks)**
1. Recall gate shadow to enforce after a replay of its `would_add`/`would_drop` log (lane C E2); widen voice history from 3 to 8 rows behind the stable prefix (lane E E3).
2. Night-mind readers to `serve` (owner flag), after the replay gate (lanes A, D).
3. Duck-decide-resume at the panel in a lab session with the owner (lane E E2); addressedness and the closing detector on.
4. One voice for every tier: a single phrase set read by fast tier, fallback, error and empty-transcript paths (lanes E E4, F E2).
5. Coverage instrument: per turn, "relevant memory existed / fired / used" so the 83% can be split (lane C).
6. Hygiene with measurable payoff: remove the boot KV warm; set the brain backend default to flue; delete the 45 unread env vars and the retired-runtime flags; fix CANONICAL's build line, the registry's missing values, `zoe_memory_layers.py`.

**Short term (established technology; four to eight weeks)**
7. The now card: nightly, 120-180 tokens per member, by key, prefix-stable, flag-dark then bar-tested (lane C E1).
8. The outcome edge and due-today re-score at first presence; follow-through, welcome and resolution measured (lane D E1, E7).
9. Smart Turn v3.2 on the Pi after the offline corpus test (lane E E1); continuation window for unfinished thoughts.
10. HA presence reader (`person`/`device_tracker`, 30 s TTL) and one mmWave sensor; observed/inferred world-model tables (lane G E1-E3).
11. The proactive policy run log-only for two weeks with the owner tagging a sample welcome/neutral/unwelcome (lane F E3).
12. pass^k and collateral-damage checks on the bar and day-sim (lane B E2); the 23-cell clean baselines.

**Medium term (substantial engineering; one to three months)**
13. Self-evolution Phase 1: GEPA-style improver on one overlay file, two candidates vs a sampling control, shadow-apply, 04:00 slot, cloud reflection on a capped key, fourteen-night stop rule (lane B E1).
14. Capability-gap clustering of can't-do/fallthrough events into a ranked backlog; recipe-builder pilot after the skill loader is restored (lane B E3, E6).
15. Memory layers as views over pointers: episode objects, pointer-assembled chapters, dormant-thread matcher, person-keyed contradiction and ageing, speakable provenance, forget-completeness probe across derived layers (lane C E4, E7-E11). First slice (episodes, slot contradictions + ageing, speakable provenance, dormant matcher; flag-dark): [memory-views](../knowledge/memory-views.md).
16. Identity as four stored layers with a per-member register and a drift probe battery per release (lane F E1, E4).
17. The household problem-solving loop: authority table, 6-step/90-second budget, escalation card, code-checked success (lane F E7).
18. Engineering harness out of the household process; slice 2 of the retired-runtime removal; the cut list.

**Long-term research (open questions; label speculative)**
19. Turn-act micro-classifier on the resident 270M router (wait / respond / continue / closing) (lane E E8).
20. Slot save/restore to allow bounded daytime background reasoning (lane D E3); a Spark-era daytime 12B.
21. Gemma hearing the flagged turn via the E4B audio encoder, if MTP and multimodal co-operate (rock-adjacent, lane E).
22. Style-only adapters (never facts) for a more recognisable voice of thought (lane C section 6).
23. Audio-event labels on the Pi; multi-room arbitration when a second listening device exists (lane G).

## 7. Ten highest-impact opportunities (12.6)

Ranked by expected impact on the household's experience, feasibility on this hardware, complexity and dependencies. All inside the rocks.

| # | Change | Improves | Feasibility | Complexity | Depends on |
|---|---|---|---|---|---|
| 1 | Push memory into every household turn: recall gate enforce + now card + history 8 rows | memory, personalisation, intelligence | high (built, shadow) | S-M | coverage instrument; bar control arms |
| 2 | Judged end of turn (Smart Turn on the Pi) + duck-decide-resume live | naturalness, responsiveness | high | S (Pi) | offline corpus test; owner lab hour |
| 3 | One voice for every tier + human re-ask on empty transcript | naturalness | high | S | replay gate |
| 4 | Night-mind readers to serve + due-today re-score + outcome edge | proactive quality, continuity | high | M | owner flag; one table change |
| 5 | Reasoning-scheduler policy written and enforced (no daytime 4B outside a turn; harness off the slot) | responsiveness, reliability | high | S | none |
| 6 | Self-evolution Phase 1 on the overlay with the fourteen-night stop rule | autonomy, intelligence over time | medium | M | #12 instruments; dev credits |
| 7 | pass^k + collateral-damage on the bar; 23-cell baselines; coverage instrument | reliability of every other claim | high | M | test lane time |
| 8 | HA presence + one radar; observed/inferred tables | awareness, proactive timing | high | S-M | A$5-40; HA read token |
| 9 | Identity as stored layers + per-member register + drift battery | personalisation, consistency | medium | M | #3 |
| 10 | Hygiene that frees the box: boot warm, unread flags, retired lanes, harness out of zoe-data, Kokoro degraded-state line | reliability, efficiency | high | M (many small PRs) | none |

## 8. Experimental prototypes (12.7)

Forty-seven experiments are specified across the lanes with hypothesis, implementation, resources, evaluation, success and failure criteria and effort. The eight to run first, in order:

| Order | Experiment (lane) | Hypothesis | Success | Failure | Effort |
|---|---|---|---|---|---|
| 1 | Recall gate replay (C E2) | enforce would have added relevant rows on real turns without crowding the budget | would_add precision ≥ 0.8 on a hand-scored sample; bar S1-S8 hold | precision < 0.6 or any bar regression | 2 days |
| 2 | Baseline the loop that exists (D E1) | follow-through on dated loops is measurable today | a number with n ≥ 20 loops | n < 20 after 30 days → switch to the failure loop (D E8) | 3 days + waiting |
| 3 | Endpoint arbiter offline (E E1) | Smart Turn + trailer lexicon beats the fixed tail on the household corpus | ≥ 90% correct end-of-turn, median tail −300 ms | < 80% or more cut-offs than today | 3 days |
| 4 | Duck-decide-resume lab (E E2) | the built policy resumes after false interruptions without regenerating | ≥ 8 of 10 scripted barges correct; no stuck mute | any stuck state | half a day |
| 5 | Now card shadow (C E1) | a nightly 150-token card raises memory use per turn | sources-used share ≥ 2× baseline on the bar; no e2e p90 regression | p90 +100 ms or bar regression | 1 week |
| 6 | One voice for every tier (E E4 / F E2) | a shared phrase set removes the robotic seams | owner blind test prefers new in ≥ 7 of 10; replay 18/18 | replay regression | 3 days |
| 7 | Proactive policy log-only (F E3) | the table's choices would have been welcome | ≥ 70% welcome, 0 unwelcome spoken | > 10% unwelcome | 2 weeks logging |
| 8 | GEPA improver on manner.md (B E1) | an improver beats repeated sampling at equal cost | wins on held-out cells 10 of 14 nights | ≤ sampling → stop | 2-week lab build + 14 nights |

## 9. Success metrics and baselines (12.8)

| Metric | Baseline today | Target (90 days) | How measured |
|---|---|---|---|
| Speech end to first sound, panel | ~3.2 s median, 6.5 s p90 (lane 2-I); warm replay e2e 1.6 s (relative) | 2.0 s median, 4.0 s p90 | daemon timing line; replay artifact |
| Memory coverage: share of household turns whose reply used a relevant memory Zoe had | unknown (83% used none; instrument missing) | ≥ 60% of turns where relevant memory existed | new coverage instrument (lane C) |
| Continuity: thread carry at 1 / 7 / 30 days; re-telling rate; stale-serve rate | not measured | carry ≥ 70% at 7 d; re-telling < 10%; stale-serve < 2% | ZMB continuity cells (lane C E3) |
| Follow-through on dated open loops (timely / welcomed / resolved) | 0 measured | ≥ 60% / ≥ 70% / ≥ 50% | outcome edge (lane D) |
| Said-vs-did on the voice corpus | 18/18 OK; 8 of 23 bar cells baselined | 23 cells baselined; 0 regressions | replay + bar |
| Turn-taking: cut-offs and over-waits per 100 turns | not measured | < 5 cut-offs, < 5 over-waits | Smart Turn offline + panel log |
| Interruption recovery | never run | ≥ 90% correct resume | lab script (E E2) |
| Proactive quality: welcome / unwelcome / dependence counters | not measured; hooks must be 0 | ≥ 70% / 0 spoken unwelcome / hooks 0 | log-only run, owner tags |
| Persona consistency | not measured | no probe regression per release | fixed probe battery (lane F E1) |
| Resource: waking MemAvailable; added RAM; idle 4B calls | ~500-650 MB; n/a; unknown | ≥ 700 MB floor held; ≤ +20 MB; 0 | ground truth; scheduler log |
| Reliability: Kokoro starts/day; night windows completed; restarts in waking hours | 24-51 dev days; 5 of 8; ~17/day | ≤ 3; ≥ 7 of 8; 0 in 07:00-22:00 except owner-approved | journal |
| Use: real member turns and days per 30 | 349 turns, 19 days, 1 member | 2 members, 25 days | DB count (joined via sessions) |
| Improver: wins vs sampling at equal cost | none running | 10 of 14 nights or stop | nightly loop run rows |

## 10. The ultimate question (Phase 14)

A person who has lived with Zoe for years experiences continuity, timing, recognition and follow-through, not software. Of that list: **continuity and follow-through are wiring and measurement on this box, now.** Recognition is live and needs a stored register. Timing is one small model on the Pi plus switching on what is built. Independent problem-solving is an authority table and a budget around tools that exist. Environmental awareness is two cheap sensors and honest tables. Getting better over time is a loop whose design exists and whose first night is a two-week lab build. Beyond this hardware: overlapping speech, hearing tone, laughter in the voice, a daytime inner monologue; each has a cheaper stand-in or waits for the Spark and a rock decision.

**What to build first:** close the circuit between what Zoe knows and what she says (opportunity 1), make the turn feel judged rather than timed (2 and 3), and record outcomes so the loop can learn (4). Everything else in this record is sequenced behind those four because they produce the signal the rest needs.

## 11. Owner decisions (DECIDED 2026-11-03, 22:40)

The owner went with the recommendations on 1-6 and overruled 7.

1. `ZOE_NIGHT_MIND=serve` after the replay gate. **Yes.**
2. "No daytime 4B outside a person's turn" is policy; the harness stays off the household slot. **Yes.**
3. One owner lab hour at the panel: duck-decide-resume live test + a blind test of the single tier phrase set. **Yes** (owner to pick the hour).
4. A read-only Home Assistant token for presence now; one mmWave sensor when convenient; observed and inferred tables never merge. **Yes.**
5. Capped dev credits for the first GEPA-style improver night, after pass^k and collateral-damage checks land on the bar. **Yes.**
6. The "personal growth" layer (claims about change in a person's inner state): **not built for now**; chapters and the "us" line proceed.
7. Affective memory for children: **the owner keeps it ON for every member** (`household` mode stays the default) and chose to test toward safety rather than switch it off: "we leave it on for everyone and we will have to test this; eventually we will get it to a point where it is safe for everyone." The emotional-safety note's section 4 is amended to record this; the lanes' stricter assumption no longer holds. Consequence for the work: the affect path for minors needs its own measured safety cells (false-positive rate on real speech, no retained row a guest can be linked to, one-step correction and forget) before any record about a child is served, and the drift and bench scorers must now include minor turns under a kid-safe rubric rather than skip them.

## 12. What this round could not do, and research continuity

No lane cloned or read third-party source; verdicts rest on papers, READMEs and search summaries, tagged in place, and every link was run through the citation check. Lane A could not read the panel's own env or separate harness from household traffic in today's logs. The usage figures in section 0 item 7 and section 9 (349 real member turns on 19 of the last 30 days, 416 turns in all, one member) are the author's own read-only count on 2026-11-03: `chat_messages` rows with role `user` joined to `chat_sessions` for the user id, excluding the guest sentinels, `guest-<id>` principals and the synthetic test/probe/demo/bench prefixes of `user_filters.SYNTHETIC_USER_RE`; counts only, no content read. The harness share (~16%) is 1 - 349/416 and should be re-measured with the traffic-class marker once it is on every row. The 23-cell bar baseline, the coverage instrument and the follow-through number do not exist yet; the first week of the roadmap builds them, and until they exist every "improvement" claim in this space is unmeasured and therefore zero.
