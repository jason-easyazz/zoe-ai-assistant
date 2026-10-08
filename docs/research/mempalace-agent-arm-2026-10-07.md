---
type: Research
title: "MemPalace set up as designed: the agent-operated arm (MPA), its integrations with Hindsight (HMA) and with Zoe's own stack (ZMA), and the night reflection phase (2026-10-07)"
description: "Owner challenge: every earlier MemPalace measurement used it passively. This PR builds MemPalace the way it is meant to be operated (the model calls its MCP tools, its protocol is injected, hooks wrap the session, an LLM closet pass runs at idle) as bake-off arm MPA; integrates it with Hindsight (HMA) and with Zoe's current stack (ZMA) as one memory with two organs instead of two engines side by side; wires all three into the planner, gates and winner clause; adds a night REFLECTION PHASE (4B at 32k, and the authorised 12B) so the 8k slot does not decide the reflective axis. Lab-measured: plumbing, floors, token costs, schemas. Brain-measured: nothing yet (declared with pass bars for run 2)."
tags: [memory, mempalace, hindsight, bake-off, agent-operated, protocol, reflection, night-window, samantha]
timestamp: 2026-10-07
status: BUILT, NOT YET MEASURED ON A BRAIN. Code + tests + planner in this PR; the brain-tier numbers (tool-call validity of the 4B on MemPalace's schemas, search-before-answer, supersede, J / K / L / M through the agent) are run 2's. Nothing deployed, no flag flipped, the live checkout / store / DB never touched. All names synthetic.
evidence_labels: "[measured] I ran it today | [src] file read | [derived] arithmetic on measured numbers | [declared] a cell with a pre-registered bar that only a window can run | [not done] cut at the deadline, listed in section 10"
---

# MemPalace set up as designed (2026-10-07)

Read with: `mempalace-deep-dive-2026-10-06.md` (modules, protocol, pilots), `bakeoff-ram-latency-optimisation-2026-10-06.md`, `docs/knowledge/bakeoff-howto.md`.

## 0. Answer first

1. **The owner was right.** Every MemPalace number so far came from a passive store (arm MV, the verbatim tier of HM) or from its modules called from a script. MemPalace is designed to be OPERATED BY THE AGENT: `search` before answering, `add_drawer` / `kg_supersede` by the model, wings and rooms it chooses, a protocol its status tool hands it, hooks around sessions, an LLM closet pass. That mode had never met our brain. It is now an arm: **MPA** (`scripts/perf/zmb/arms/mempalace_agent.py`).
2. **Three arms, one idea each.** MPA = MemPalace operated by the brain, Zoe's floors around what the brain writes. **HMA** = MPA as the episodic tier + Hindsight (concise + observations) as the reflective tier, integrated (section 3). **ZMA** = Zoe's current live stack (Z0e) + MemPalace on top, integrated (section 4: "MemPalace on our own setup"). All three are wired into the planner, the report, the gates and the winner clause (section 6); `bakeoff_window.sh --dry-run` prints them and what they cost.
3. **Measured today, without a brain:** the tool schemas validate and malformed calls are caught; the protocol costs 3,370 tokens with its tools (2,418 of them the ten tool schemas) against a 4,200 bar, and the full 45-tool server would cost about 8,950 tokens, more than the whole 8k slot; every floor holds against a scripted AND a rogue brain and **every one of the 35 negative controls turns its cell red** (MPA 12, HMA 15, ZMA 8); a scripted 10-turn smoke made 20 model calls and 11 tool calls, all valid. The real `mempalace-mcp` server (stdio, 3.10.0) answered every tool in 1-143 ms and sat at 74-105 MB RSS with a loopback embedder.
4. **NOT measured:** anything about what a 4B does with the tools. The live-brain smoke (at most 30 calls) was **not completed by the deadline**: the brain was contended for the first hour (a Samantha bar ran under the voice-harness lock), then the box sat at 0.8-1.2 GB MemAvailable (other sessions' test runs), under the 1.7 GB the smoke wrapper requires. When every condition finally held (MemAvailable 1,773 MB) the wrapper started, and its own 1.5 GB guard stopped the MemPalace server at 1,498 MB before the first turn: **0 live model calls were spent**. The wrapper (`mpa_smoke.sh`) is committed; the live smoke is a run-2 item. The brain cells are declared with pass bars (section 5) and run in the window.
5. **Decision I recommend, before any number:** run MPA, HMA and ZMA in run 2 with one seed each beside H1 / H2 / HM / H0; adopt nothing from this record. The structural findings that already hold: the 45-tool server cannot be the brain's tool surface on an 8k slot (ten tools fit); MemPalace's tools take identity fields from the model, so the production transport must be a `defineTool` shim, not a mounted MCP server; one MemPalace server process per account is the only isolation MemPalace has.

## 1. How MemPalace was set up (as its docs intend)

| Piece of the design | What the arm does | Source |
|---|---|---|
| a palace per user | one `mempalace-mcp --palace <dir>` server process per synthetic account (MemPalace has no tenant isolation inside a palace: the account boundary is the process boundary; its knowledge graph is `<palace>/knowledge_graph.sqlite3`, so it is per account too) | [src] `mcp_server/_guards.py:937`; [measured] server 74 MB at start, 94-105 MB after 12 tool calls, init 1.4 s |
| wings and rooms by its conventions | the brain files into the wing / room it chooses (`wing_mode="free"`, the design); the prompt names the convention (own wing = the account, a relative gets a first-name wing, rooms are one topic word) and MemPalace's own rule "if unsure, search without filters". Ablation `pinned` exists. The cell counter `filter_misses` records a filtered search that came back empty where an unfiltered one would not (the deep dive's 0 of 360 wrong-room finding) | [src] `instructions/search.md` |
| the protocol, injected | `PALACE_PROTOCOL` (five rules, byte for byte from the package, frozen in `arms/mpa_snapshot.json`) + the AAAK spec `mempalace_status` hands out + the "Memory (recall + writing)" paragraph of `shared_brain_rules.md` + a wing / room conventions line + the wake-up overview. **Extra prompt tokens (the H arms inject none):** protocol 277, AAAK spec 318, memory rules 161, conventions 98, persona + role 69, overview 29, tool schemas 2,418 = **3,370** (chars/3.6 estimate; the window replaces it with the clone's `/tokenize` count). Without the AAAK spec: 3,052 | [measured] `arm.prompt_cost()` |
| the tools | ten of 45: `status`, `search`, `add_drawer`, `update_drawer`, `kg_query`, `kg_add`, `kg_invalidate`, `kg_supersede`, `kg_timeline`, `diary_write`: MemPalace's own descriptions and JSON schemas byte for byte. The full surface is 32,242 bytes (about 8,950 tokens) through `tools/list`: it does not fit an 8k slot. The light 3-tool PQL server was not used (a 4B writing a query DSL is a different experiment: run 3 if the ten-tool arm shows promise) | [measured] `tools/list`; [src] `mcp_light_server.py` |
| how the brain gets the tools | a **faithful shim**, not an MCP mount. Flue 2.1.1 can mount a remote MCP server (`useMcpConnection`), but a mounted MemPalace tool takes `wing`, `added_by` and `agent_name` from the model, and Zoe's rule (`zoe-tools.ts`) is that identity is bound in trusted code. Production = one `defineTool` per tool over a loopback bridge to the per-account server; this arm's `_exec_tool` IS that bridge, and the lab drives the same real server over stdio. The shim changes three things only: pins identity fields, fills `source_file` / dates the model left out, renders results compactly (search = text, wing, room, date, similarity; framed as quoted data) | [src] `labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` header; `@flue/runtime` docs `guide/mcp.md` |
| hooks | session start = the wake-up `status` call (a harness call, not a model call) and its overview in the prompt; stop = MemPalace's own save request (`STOP_BLOCK_REASON`) every 15 messages (`SAVE_INTERVAL`); session end (the simulated day changes, or the idle pass) = one save request if anything was said since the last. PreCompact is not mapped (Flue compacts its own context; sessions here are short) | [src] `hooks_cli.py:38,139` |
| the closet pass | MemPalace's own `closet_llm` CLI against the clone brain's `/v1`, run at the idle step with the server STOPPED (two processes must never write one Chroma palace); one model call per source file (the shim stamps `source_file = session:<date>`, so one call per session). Its summaries are the arm's **observations** (axis K) | [src] `closet_llm.py` |
| Zoe's floors around the writes | the same helpers as the H arms (`hm_policy.classify`, `HashedLedger`, `alias_candidates`, `frame`): guest gate and speaker classes decide who reaches the brain at all (unverified / pasted words are filed by the HARNESS in reserved rooms, never shown to the brain); router bypass (device turns never reach the brain); write tools refuse a forgotten entity and reserved rooms; identity fields pinned; an authority anchor labels what the brain composes (`model_from_transcript`, rank 0) and a composed value cannot retire a fact the owner stated; quarantine rooms are filtered out of search; verbatim hits are framed as quoted data; forget = ledger + drawers + triples + closets + write-ahead log + a palace rebuild in a clean child process | `arms/mempalace_agent.py` |

## 2. What the brain can and cannot be measured on

| Axis / gate | Without a brain (done) | With the clone brain (declared, run 2) |
|---|---|---|
| tool schemas | `MPA-T1`: ten schemas well-formed, valid call validates, malformed caught (instrument check) | `MPA-B1.tool_call_validity`: valid / total >= 0.95 over >= 30 calls |
| protocol cost | `MPA-T2`: static prompt + tools <= 4,200 tokens (half the slot); HMA's merged protocol undercuts the unmerged by >= 400; ZMA fits beside Z0's prompt | the clone's `/tokenize` count replaces the estimate; `prompt_tokens_max + 2,048 < 8,192` |
| (m) protocol | lab half `M1-M3` with a scripted reader (existing) | `M4.fire_when_needed / quiet_when_not_needed / cite_precision / idk_when_silent .mempalace5` (bars 0.90 / 0.90 / 0.95 / 0.90, `scorers_cap.PROTOCOL_BARS`): the brain gets MemPalace's protocol and 14 taught facts, then the 32 prompts of the protocol corpus with the router bypass OFF (the protocol's job is to stay quiet on device turns) |
| (j) exact words | `recall_exact` = the arm's first search, the sentences as filed; generic cells `J0-J2` | `MPA-J4.exact-words.brain`: taught through the agent, asked back through the agent, >= 0.90 |
| (k) reflection | closet pass against a scripted loopback LLM through the REAL `closet_llm` (cell `MPA-C1`, needs the real server; RAM-blocked today) | K1-K5 on the closet summaries made by the clone brain (HMA: Hindsight's observations too); K1 precision >= 0.95 or the observation veto applies |
| (l) multi-hop | `recall_linked` = search + `kg_query` of each name | `MPA-L4.two-facts.brain`: both facts in what the agent gathered, >= 0.70 |
| supersede | `MPA-A1` (a composed value cannot retire a stated fact) | `MPA-B2.supersede_correct`: 10 "X lives in A" then "X has moved to B": >= 80% right, <= 2 wrong |
| G0 | server 74-105 MB measured with a loopback stub embedder | steady / burst of the server with the brain driving it (budgets 600 / 900 MB added); the real embedder is the window's shim (one session) |

## 3. HMA: Hindsight and MemPalace integrated

HM was two engines with a gate in front (passive verbatim tier, Hindsight reading a copy of every chunk, two recalls, two instruction sets, two forget paths, observations OFF so no axis K). HMA (`arms/hma.py`) is one memory with two organs. Each integration is a switch with a cell that goes red when it is off (`HMA-W1`, `HMA-F6`, `HMA-W2` in `mpa_cells.py`):

| Integration | What it is | What it saves |
|---|---|---|
| one ingest path | Hindsight ingests ONLY the drawers the brain filed: `document_id` = drawer id, `metadata.source_ids` = the same id, class / wing / room as tags; a re-filed drawer replaces its document; no raw-turn stream, no diary (AAAK summaries are not re-ingested), nothing from the quarantine rooms | no second verbatim copy, no double extraction: Hindsight's model calls scale with drawers filed, not with turns (a device turn, a guest and a pasted email cost zero) |
| one embedder | MemPalace (`openai-compat`) and Hindsight ask the window's shim | one ONNX session instead of two: -120..191 MB [measured, RAM lab] |
| one packet | the brain calls ONE tool; its result carries the verbatim hits AND up to three reflective lines (observations, facts the drawers do not hold), deduplicated against the hits by drawer id and by text, authority-gated (a derived fact contradicting a user-stated drawer on the same attribute is dropped) | one tool call and one model round trip instead of two tools; no repeated line |
| one protocol | MemPalace's five rules + one reflections paragraph (73 tokens); the brain never sees a Hindsight tool | the unmerged alternative (Hindsight's recommended usage + three tool schemas) is about 602 tokens: **-529 tokens per turn** [derived, estimate] |
| one forget | one ledger; the forgotten drawers' ids cascade into Hindsight, then the text route sweeps what still names the entity, then the Postgres scrub | one contract, one residue scan; the distiller skips ledger-matching text |
| one idle step | stop hook + closet pass + per-authority-scope consolidation in one window on one brain | one brain-slot window |

Known risks, stated before the run: Hindsight's reflect loop assumes more context than the 8k slot gives it (measure tool-call validity >= 95% first; the night phase exists for this); RAM = HM's about 496 MB + the MemPalace server under the brain (G0 reports the sum); the K1 precision veto applies to HMA's observations.

## 4. ZMA: MemPalace on our own setup

ZMA (`arms/zma.py`) is Z0e (the real `MemoryService`, Postgres facts and people, authority classes, the permanent forget ledger #1883, alias sweep, Chroma + MiniLM recall, the nightly digest and conflict pass) with MemPalace integrated:

```
verified owner turn -> gate -> ONE verbatim chunk (the harness files it, no model call) -> Z0's extractor reads FROM the stored chunk;
                              every Z0 row cites the chunk id  -> Z0 rows (authoritative, authority classes, ledger)
read:   the brain's turn carries Z0's authoritative packet; MemPalace gives it `status` (wake-up) and `search` (the owner's exact words); ONE packet: Z0's rows first,
        then verbatim lines the rows do not already cover; on a conflict Z0's authority order wins (G0-G3 are floors, not the contest)
night:  Z0's digest + conflict pass (unchanged) and MemPalace's closet pass in one idle window
forget: Z0's ledger authoritative; the same entity and the ledger's hashes purge the chunk store, triples, closets, WAL; both residue scans clean
```

| Integration | Saves | Cell |
|---|---|---|
| one ingest path: every turn written once (the chunk); Z0 reads it back and cites its id | the second copy of the transcript and the second embedding of the same words; the chunk is the exact-words tier (J) for free | `ZMA-W1` (control `one_ingest`) |
| one embedder: Chroma's collection embeds through the shim (`ShimEmbeddingFunction`), MemPalace through the same shim; the shim is served as MiniLM (`BAKEOFF_SHIM_MODEL=minilm`) for ZMA so Z0e's retrieval is unchanged | one model resident: -120..191 MB; the in-process Chroma session leaves the arm | measured in the window (`driver.embedder_shared_mb`) |
| one packet: a verbatim line a Z0 row already cites is not repeated; Z0's order wins | tokens and the authority floors | `ZMA-W2` (control `one_packet`) |
| one forget: Z0's ledger, then MemPalace by entity and by the ledger's hashes, plus the alias sweep over both tiers | one contract | `MPA-F1`, `MPA-F5` on ZMA (controls `forget_verbatim`, `alias_sweep`); `MPA-F3` (physical residue) on the real server |
| one protocol: Z0's existing brain prompt stays; MemPalace adds rules 1-3 of its protocol and two read tools (`status`, `search`); no write tools, no AAAK, no diary | **the brain never writes in ZMA** (no model call on the write path: the owner's design); Z0's conflict pass keeps retiring the old fact | `ZMA.protocol_cost()` |

**Per-turn token cost against Z0 alone** [derived from the committed sources, estimate]: Z0's memory prompt (soul recall paragraph + two recall doctrines + the `recall_memory` description) is about 937 tokens; ZMA adds 975 (protocol rules 1-3 137, conventions and persona 167, two tool schemas 642, overview 29) = **+975 tokens**, 937 + 975 + 1,536 reserved output = 3,448 of the 8,192 slot: it fits; the schema of `mempalace_search` (about 600 tokens) is the expensive line, so a compact schema is the first optimisation if the brain cells show the slot is tight. A protocol that does not fit the slot fails (m): `ZMA.protocol_cost()["fits_with_z0_prompt"]` and `prompt_tokens_max` gate it.

## 5. Pre-registered bars (brain tier; written before any brain run, never changed after)

`mpa_cells.BRAIN_BARS`: tool-call validity >= 0.95 over >= 30 calls; supersede >= 0.80 right and <= 2 wrong over >= 10 pairs; exact words >= 0.90; two-fact >= 0.70; the four M4 bars above. Gate items in `bakeoff_gates.gate_mpa / gate_hma / gate_zma` (RULE keys marked "pre-registered 2026-10-07 before any MPA run").

## 6. The planner, the gates, the winner clause

`bakeoff_window.sh --dry-run` prints it all (the numbers below are from `bakeoff.py --dry-run` today; every constant lives in ONE block of `bakeoff_measure.py`: `MPA_CALLS`, `HMA_CALLS`, `ZMA_CALLS`, `MPA_S_PER_CALL = 3.0`, `REFLECT_CALLS`, `REFLECT_S_PER_CALL`, `REFLECT_LOAD_MIN`, to be re-set from the first window's measured seconds per call):

| Arm | Minutes (cells on the clone brain + one seed box) | Model calls |
|---|---|---|
| MPA | 15.5 + 1 | 301 x 3 s (protocol 85, exact words 32, multi-hop 36, reflection 86, behaviour 50, closet 12) |
| HMA | 20.0 + 1 | 361 x 3 s + 90 s Hindsight consolidation |
| ZMA | 9.0 + 1 | 180 x 3 s (no model call on its write path; ALL store cells A-M in its seed box) |
| reflection 4B@32k | 13.5 for H2 + HMA + ZMA (9.5 for HMA + ZMA) | optional: runs only if the minutes remain behind the tail |
| reflection 12B | 39 (27 for HMA + ZMA) | optional; the preflight skips it with the arithmetic printed unless the margin holds |

**The default plan does not fit the 90-minute cap and says so** (`DOES NOT FIT`: 121.2 min of work against 77.5 available; a cap of about 134 min holds it). Cost of the three new arms together: 47.5 min; with all of them the H2 box shrinks 3 -> 1 min and HM's 2.5 -> 1; H1 keeps its three full seeds (the rule needs them). Two windows each fit 90 minutes exactly: **A** `--arms H1,H2,HM,H0` (77.2 of 77.5) and **B** `--arms MPA,HMA,ZMA` (76.6 of 77.5). The reflection pairs need extra minutes: B with both pairs about 127 min (4B@32k 9.5 + 12B 27 for HMA + ZMA); one combined window about 134 min without and about 187 min with them.

Gates: `gate_mpa / gate_hma / gate_zma` in `bakeoff_gates.py`, thresholds in `RULE` marked "pre-registered 2026-10-07 before any MPA run" and pinned by a test: cells zero violations / all ran / controls red; server RSS steady <= 600 / peak <= 900 MB; tool-call validity >= 95% over >= 30 calls; M4.fire_when_needed passes; supersede >= 80% right and <= 2 wrong; prompt fits (`max prompt + 2,048 < 8,192`); floors (forgetting, authority, identity / guest / poisoning) all pass; glue lines <= 1,000 (MPA 932 with `hm_policy.py`); HMA: K1 precision >= 95% (the observation veto) and total RAM of both stacks, forgetting through both tiers; ZMA: zero hard violations on Z0's own floors, forgetting through both stores, total RAM. `decide()` never lets MPA, HMA, ZMA or HM win: one seed is INCOMPLETE, the owner recommends it (tests cover it).

## 7. The night phase: reflection and consolidation off the 8k slot

**Why.** Hindsight's reflect loop, the closet pass and the nightly digest are exactly the work the 8k voice slot constrains; the voice brain never needs a big context, the night does. The owner's idea: run them late, or on a different model.

**In the bake-off window (built).** After the live-slot phases the window restarts the clone brain with `--ctx-size 32768` (same model and flags, KV q8_0 as live; the live unit file is never modified) and runs ONLY the K (reflection) work against it: `H2@32k`, `HMA@32k`, `ZMA@32k`; then, authorised by the owner, the same on the **12B** (`H2@12B`, `HMA@12B`, `ZMA@12B`; not MPA) with a clone generated from the parked unit `llama-server-12b-deepbrain.service.disabled` (its ExecStart, `--host 127.0.0.1 --port 11500 --ctx-size 32768`, no mmproj). Variants are reported beside the 8k numbers; they are never contest entrants, and the K1 veto applies to them. Recorded per variant: K cells, closet pass, tool-call validity, wall time, peak prompt tokens, clone RSS, MemAvailable floor; the phase aborts and restores as usual below 1.2 GB.

**Memory arithmetic** [derived from the GGUF headers, `gemma4` metadata, and the live numbers; nothing downloaded]:

| | 4B (E4B QAT, live) | 12B QAT (parked unit) |
|---|---|---|
| weights | 4.22 GB file | 6.98 GB file (6.50 GiB) |
| layers | 42 (24 own KV: 18 shared), 5:1 sliding (window 512) : global, 2 KV heads | 48: 40 sliding (window 1024, 8 KV heads x (256+256)) + 8 global (1 KV head x (512+512)) |
| KV at 8k (live) | about 96 MB | n/a |
| KV at 32k, q8_0 (1.0625 B / element), llama.cpp's reduced sliding-window cache | about 310 MB (**+215 MB over today**) | about 486 MB (global 268 M elements + sliding 189 M) |
| compute / output buffers | in the live 6.7 GB RSS | about 0.6 GB (262k vocabulary, ubatch 128) [estimate] |
| resident | live RSS 6.7 GB; 4B at 32k about 6.9 GB | about 7.5 GB |

The box today: 15.6 GB, MemAvailable 1.3-1.85 GB with the 4B (6.7 GB) running, Kokoro 2.3 GB, zoe-data 1.6 GB, the router 0.65 GB. With the 4B stopped: about 8.0-8.5 GB, minus Hindsight + Postgres + shim (about 0.5-1 GB) = 7-8 GB before the 12B's 7.5 GB: **the 12B leaves 0-0.9 GB, under the 1.2 GB floor.** So the 12B pair is built to skip, with the arithmetic printed, unless headroom is added: the one lever that costs nothing the window does not already cost is stopping `kokoro-tts.service` (2.3 GB, a voice-only sidecar, down-time anyway while the brain is stopped) for the 12B pair only, restored on every exit path (`BAKEOFF_REFLECT_STOP_UNITS=kokoro-tts.service`, default empty: nothing is stopped that is not listed). With it, the margin is about 2.3 GB above the floor. The 4B at 32k needs +215 MB over a clone that already runs: it fits.

**In production: the nightly reflection window (design; nothing is changed by this PR).** The nightly jobs already own 01:45-03:15 (digest, dreaming, Hindsight consolidation) and the box already treats 04:18-04:52 as the deploy / gate window.

| Time | Step |
|---|---|
| 01:35 | night mode: the panel's voice path answers conversational turns with a short spoken "I'm doing my night thinking, ask me in the morning" (to be built: today there is no such reply); device commands keep working (the router sidecar is separate and handles about 64% of turns) |
| 01:40 | restart `llama-server.service` with a drop-in `--ctx-size 32768` (same model, KV q8_0; about +215 MB, MemoryLow 6G unaffected) **or**, if the owner adopts the 12B after run 2, swap to the 12B unit with `kokoro-tts.service` stopped (about 2.3 GB freed, 3 min load) |
| 01:45-03:15 | the existing nightly passes plus: Hindsight consolidation / observations / mental-model refresh, MemPalace closet pass, the digest and its judge, quote-backed retirement (S10x) judge: one brain, 32k |
| 03:15-03:25 | drop-in removed, `llama-server.service` restarted at 8192, `/health` polled, the voice replay gate (the 11-scoreable replay) run, night mode off |
| 04:18 | the voice gate / deploy window is untouched |

**Voice is unavailable for conversation from about 01:35 to 03:25 (about 1 h 50 min) and degraded (commands only) for the two restarts (about 1-2 minutes each, mid-window and at the end).** The panel's idle sleep is a screensaver gated by room toggles and the house has no presence sensors, so nobody is assumed asleep: the night-mode reply is the contract. The restore is the risk: a failed restart leaves the brain down; the existing `OnFailure=` fallback and the 04:18 gate are the net, and the first production run is attended. **Owner decisions left:** (a) approve the nightly window at all (it takes the voice brain offline 01:35-03:25); (b) after run 2, 4B@32k or 12B, by K1 precision >= 0.95 and the K2 / K3 gain over the 8k arms.

## 8. Floors, cell by cell (lab, test double + scripted / rogue brain)

| Cell | Claim | Control (must go red) |
|---|---|---|
| `MPA-T1` / `MPA-T2` | schemas valid; prompt fits | (instrument checks) |
| `MPA-S1` / `MPA-S2` | stored and recalled; a lazy brain stores nothing and the instruments see it | sanity |
| `MPA-O1` | wake-up status + a save request every 15 messages | `hooks` |
| `MPA-R1` | device turns never reach the brain | `router_bypass` |
| `MPA-G1` | a guest's words reach neither brain nor palace | `guest_gate` |
| `MPA-I1` / `MPA-I2` | a pasted email's instruction / a third person's words never reach the brain, results or packet | `speaker_class`, `quarantine_filter` |
| `MPA-H1` | the brain cannot choose the author or the diary owner | `tool_floor` |
| `MPA-F1` / `MPA-F2` / `MPA-F5` | forget clears every tier; the brain cannot re-file the name; the misspelling is proposed, kept until confirmed | `forget_verbatim`, `tool_floor`, `alias_sweep` |
| `MPA-F3` | no byte of the name in the palace, config / WAL, server log | `physical_erase` (real server only) |
| `MPA-A1` / `MPA-A2` | a composed value is labelled and cannot retire a stated fact; a proposal that contradicts a stated fact is held back | `anchor_check`, `authority` |
| `MPA-C1` | the REAL closet pass against a scripted loopback LLM | (real server only) |
| `ZMA-W1/W2`, `HMA-W1/W2/F6` | the integrations | `one_ingest`, `one_packet`, `one_forget` |

Result today [measured, test double]: MPA 12/12 graded pass with 12/12 controls red; HMA 15/15 with 15/15; ZMA 8/8 with 8/8; 2 cells per arm skipped (`MPA-F3`, `MPA-C1`: they need the real server and RAM). The first run of the ZMA cells turned up two controls that stayed green (a guest gate that did not reach the chunk gate, an alias sweep that ignored its switch) and one cell that read the wrong export: all three fixed, which is what the rule is for.

## 9. What would change my mind / known limits

* The scripted brain proves plumbing, never a 4B. If the 4B's tool-call validity is under 95% on MemPalace's schemas the arm's gate fails and the compact-schema / three-tool variants are the next experiment, not a verdict on MemPalace.
* ZMA's brain is read-only on purpose; the deep dive's "brain-led supersede" (rule 5) is MPA's and HMA's, not ZMA's (Z0's conflict pass owns retirement there). If MPA's supersede cell passes and Z0's S10 stays red, the fix is Z0's judge, not a second fact store.
* `recall_exact` / `recall` for the store-tier cells are the agent's FIRST search, harness-side; whether the brain makes that call is the brain tier's.
* Per-account MemPalace servers are about 100 MB each: a four-person household with all members active is 400 MB: G0 reports one server; the owner's adoption review must multiply.

## 10. Not done by the deadline

* The live-brain smoke (at most 30 calls): see section 0 point 4; `scripts/perf/zmb/mpa_smoke.sh OUT.json` is committed and waits for the lock, the anchored busy check, a quiet panel and 1.7 GB MemAvailable. A scripted 10-turn smoke (20 model calls, 11 tool calls, all valid, 4 of 5 questions searched first, the supersede right) stands in for the plumbing; it says nothing about a 4B.
* Per-tool latency rows and the real-embedder RSS of the MemPalace server (only the loopback-stub numbers above exist).
* The real-server lab run (`MPA-F3` physical residue, `MPA-C1` closet pass, RSS with the real shim): the box stayed under the 1.5 GB floor the owner set for anything I start (the rebuild child and the closet child each import chromadb, about 150-200 MB). Run `bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/mpa_cells.py --arm HMA --store library` when MemAvailable allows.
* Extra seeds, extra optimisation variants (compact search schema, three-tool light server, pinned-wing ablation run), the PreCompact hook mapping, a Flue `defineTool` implementation of the shim (the arm's `_exec_tool` is its specification), AAAK / diary ablation numbers.

## 11. Reproduce

```
python3 scripts/perf/zmb/mpa_cells.py --arm MPA|HMA|ZMA --store double          # the lab cells with their negative controls (slim lane)
bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/mpa_snapshot.py --check   # the frozen MemPalace surface still matches the installed package
scripts/perf/zmb/bakeoff_window.sh --dry-run                                     # the plan: MPA / HMA / ZMA, their cost, what was cut, the reflection phase
scripts/perf/zmb/mpa_smoke.sh OUT.json 30                                        # the live-brain smoke (waits for a free brain)
```
