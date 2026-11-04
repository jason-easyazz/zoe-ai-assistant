---
type: record
title: Citation check, Samantha Initiative research bundle
date: 2026-11-03
---

# Citation check, 2026-11-03

Scope: every external http/https URL in lanes A to G. Method: fetched with curl (title read), blocked
hosts re-tried with WebFetch and then confirmed by web search; arXiv IDs matched to the paper named by
the lane; GitHub licences read from the GitHub licence field and, where that said NOASSERTION or the
lane's claim was unusual, from the repo's LICENSE file; Hugging Face weight licences from the model card
metadata. No external source code was read. Internal repo paths were not checked. Edits were one-line
text edits; no analysis, verdict or structure was changed.

## Tallies

| Lane | URLs checked | Reachable | Matched the lane's claim | Corrected | Removed | Still unverified |
|---|---|---|---|---|---|---|
| A (current state) | 0 | n/a | n/a | 0 | 0 | 0 (no external URLs) |
| B (agents, self-improvement) | 33 | 33 | 33 | 0 | 0 | 0 |
| C (memory) | 56 | 56 | 56 | 0 | 0 | 0 |
| D (cognitive architecture) | 33 | 31 | 30 | 2 | 1 | 1 |
| E (conversation) | 28 | 27 | 27 | 1 | 0 | 0 |
| F (personality, proactive) | 22 | 21 | 21 | 1 | 0 | 0 |
| G (embodiment, distributed) | 26 | 26 | 26 | 0 | 0 | 0 |

Reachability notes: in D, four hosts return 403 to scripted fetches (betanews, Medium, OpenAI help,
iPhone in Canada) and were confirmed by web search only (title and gist match; page body not read). In G,
the two Home Assistant forum threads were read via WebFetch; the Hackster review returns 403 and was
confirmed by web search only (title, Hailo-10H, 40 TOPS, 8 GB, USD 130 list all match). In F, the Horvitz
page failed once and loaded on retry.

## Corrections made

- D, row 12 (Letta sleep-time agents): docs URL returned 404; replaced with the live page
  `https://docs.letta.com/guides/agents/sleep-time-agents`.
- D, row 8 (ACT-R): project site did not resolve from this host; link replaced with the official-Lisp
  mirror `https://github.com/asmaloney/ACT-R` (LGPL-2.1 verified); the project site is kept as plain text
  tagged [link unverified].
- D, row 27 (Gemini memory): the-decoder link was an article about ChatGPT memory, not Gemini; link removed,
  "Google help pages" left tagged [link unverified].
- E, Kyutai Unmute row: announcement URL returned 404; replaced with the Kyutai
  `delayed-streams-modeling` repo (the STT/TTS code, 1B en/fr model, 500 ms delay, semantic VAD all
  confirmed there). The row's "announcement read" caveat was changed to "repo page read (announcement URL 404)".
- F, Cha et al. smart-speaker opportune moments: PDF URL returned 404 and the venue was wrong (paper is
  IMWUT 2020, not CHI 2020); link replaced with `https://doi.org/10.1145/3411810` (title and journal
  confirmed through Crossref; doi.org itself blocks scripted fetches).
- F, row 1 (arXiv 2402.10962): link text updated to the paper's current title ("Instruction (In)Stability";
  first title was "Persona Drift"). Same paper, same ID.

## Licence claims

Verified and the lane text updated: Harness2 Apache-2.0, DGM Apache-2.0, SICA MIT, GEPA MIT, ACE
Apache-2.0, OpenEvolve Apache-2.0, Voyager MIT, Letta Apache-2.0, DSPy MIT, OpenHands MIT (also
mini-swe-agent and SWE-agent MIT) in B; Persona Vectors Apache-2.0 in F; Home Assistant core Apache-2.0,
ESPresense AGPL-3.0, Frigate MIT, YAMNet Apache-2.0 (tensorflow/models root LICENSE), exo Apache-2.0 in G;
Generative Agents Apache-2.0, Global Workspace Agents repo MIT, Soar BSD (LICENSE.md says "BSD License",
clause count not stated), ACT-R LGPL-2.1, Reflexion MIT, AI Scientist custom source licence, pymdp MIT,
ProactiveAgent Apache-2.0 in D; Moshi code MIT (Python) and Apache (Rust) with CC-BY-4.0 weights,
Kyutai STT/TTS weights CC-BY-4.0, PersonaPlex code MIT and weights NVIDIA Open Model License, Freeze-Omni
custom Tencent terms, VoiceActivityProjection MIT in E.

Verified, lane text already correct (no edit): all C licences (Letta, sleep-time-compute MIT, mem0,
Graphiti, Hindsight, A-MEM, Generative Agents, HippoRAG, LightRAG, GraphRAG, Cognee, MemOS, EverOS,
LightMem, LongMemEval, LoCoMo NOASSERTION); Temporal MIT, Restate BSL 1.1, Letta Apache-2.0 in D;
Smart Turn BSD-2-Clause, Chatterbox MIT, Pocket TTS MIT, Hertz-dev Apache-2.0, Sesame CSM-1B Apache-2.0,
Qwen3-Omni Apache-2.0 in E. Orpheus: repo is Apache-2.0 as the lane says; the lane's note of a conflicting
secondary source was not resolved.

Caveats removed: the "from memory" / "not verified" / "[unverified]" flags on the licences above, and the
"paper via summary (search)" flag on Harness2 (arXiv 2610.14224 resolves to Harness^2: Recursive Agent
Harnessing for an Open World).

## Still unverified

- D: ACT-R project site (act-r.psy.cmu.edu) did not resolve; replaced by the mirror, site tagged.
- D: licences not checked and left as the lane states them: LIDA framework, CogniPair, Unified Mind Model,
  Sigma, OpenCog Hyperon, semantic-entropy code, HTN/LLM+P.
- D, G (page bodies not read, existence and gist confirmed by search only): betanews, Medium (OpenCog
  Hyperon alpha), OpenAI help article, iPhone in Canada, Hackster.
- B: "not checked" licences for ADAS, Agent Workflow Memory, Reflexion/Self-Refine, Live-SWE-agent (repo is
  MIT per GitHub, lane text unchanged), LATM, RAG-MCP, tau-bench; AppWorld is Apache-2.0 per GitHub.
- E: LiveKit turn-detector model licence, the Orpheus conflict, and weights licences for the Unmute-adjacent
  models other than Kyutai's.
- G: Hailo SDK terms.
- Lane claims that quote numbers from papers (benchmarks, percentages) were not re-verified here; only the
  identity of each cited source was.
