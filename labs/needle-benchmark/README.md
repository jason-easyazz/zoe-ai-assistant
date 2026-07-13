# needle-benchmark — Cactus Needle as a tool/intent router for Zoe (LAB SPIKE)

Evaluates [cactus-compute/needle](https://github.com/cactus-compute/needle)
(26M-param single-shot function-calling model, MIT, distilled from Gemini 3.1
Flash Lite) as a fast CPU-only router in front of the Gemma 4 E4B brain (the
rock — Needle is only ever a *router*, never a brain swap).

LAB-ONLY: nothing here is wired into the voice path, `fast_tiers`, `zoe-data`,
systemd, or CI. The runtime venv lives OUTSIDE the repo (`setup.sh`), weights
come from HF at run time (`Cactus-Compute/needle`, `needle.pkl` ≈ 53 MB fp
checkpoint; the ~14 MB INT4 figure is the Cactus-runtime artifact, not usable
here), and prod services were touched only by two read-only measurements
documented below.

## VERDICT: **REJECT as-is** (fine-tune + Cactus runtime would both be required to revisit)

Corpus: 81 labeled utterances over Zoe's real 20-tool set (+`general_chat`
escape tool) — 34 canonical, 39 paraphrase/sloppy ("the ones keyword routing
misses"), 8 chat. Box: Jetson Orin NX 16GB, CPU-only, live services running.

### 1. ACCURACY — Needle loses to the current routing

| router | overall | canonical | paraphrase | chat (no-tool) |
|---|---|---|---|---|
| **Baseline** Tier-0 regex + Tier-1 bge-small (generously scored) | **61.7%** | 85.3% | 35.9% | 87.5% |
| Needle, full 20-tool block | 30.9% | 52.9% | 17.9% | **0.0%** |
| Needle, own-retrieval top-3 shortlist | 13.6% | 17.6% | 12.8% | 0.0% |
| Needle, **oracle** 3-tool shortlist (decoder best case) | 66.7% | 88.2% | **56.4%** | 25.0% |

- **Full mode is structurally broken**: Zoe's 20-tool schema is **1,924 Needle
  tokens vs the model's 1,024-token encoder cap** — 8 of 21 tools (journal,
  people, media, home, remember_fact, remember_emotional_moment, general_chat)
  are silently truncated out and can never be chosen.
- **The released checkpoint's retrieval head is dead**: `encode_contrastive`
  returns **all-zero embeddings** for every input (verified directly), so
  Needle's built-in tool-shortlisting always returns the same 3 tools.
  Shortlist-by-own-retrieval is therefore nonfunctional out of the box.
- Even with an **oracle** prefilter (expected tool guaranteed in a 3-tool
  candidate set — the ceiling for a bge-prefiltered deployment), Needle beats
  the baseline on paraphrases (56.4% vs 35.9%) but **misroutes 75% of chat
  turns into a tool call** (chat 25%). An authoritative router that hijacks
  conversational turns is disqualifying for Samantha-grade conversation.
- Baseline detail: Tier-0 regex hit 32/81 (100% precise when it hits);
  paraphrase coverage is the real gap — 21/39 paraphrases fell to the brain.

### 2. LATENCY — seconds, not the 10–50ms target (~50–100x off)

Steady-state per decision (post-XLA-compile), JAX reference runtime, CPU:

| mode | p50 | p90 |
|---|---|---|
| full block (enc 1024) | 3,612 ms | 4,387 ms |
| oracle 3-tool (enc 512) | 2,179 ms | 3,357 ms |

Decomposition (enc 512): encoder ≈ 250 ms; **each decode token ≈ 95 ms**
because the reference decoder re-runs the whole buffer per token (no KV
cache); a ~15-token call ⇒ ~1.7–2.2 s. The advertised 6,000 tok/s
prefill / 1,200 tok/s decode belongs to the **Cactus C++ INT4 runtime**
(mobile-oriented; not packaged for Jetson/Linux ARM64 here). Reaching
ms-class latency requires porting/building that runtime — untested in this
spike. RAM footprint of the JAX process: ~560 MB RSS (fits, but not free).

### 3. CLASSIFIER-CHAIN COST (what Needle would replace)

Measured in-lab (`classifier_cost.py`, `results/classifier_cost.json`):

| stage | p50 | p90 | note |
|---|---|---|---|
| Tier-0 `intent_router.detect_intent` (regex) | 0.18 ms | 0.30 ms | effectively free |
| Tier-1 `semantic_router.route` (bge-small ONNX) | 60 ms | 82 ms | in-process |
| Tier-0.5 `intent_classifier_llm` (llama-server) | 67 ms | 77 ms | **always returns None — see below** |
| Pi governor `pi_intent_classifier` | — | — | 4 s timeout declared; not timed (RAM) |

**Bug found**: Tier-0.5 POSTs to `/v1/completion`, which llama-server 404s
(valid endpoints: `/completion` or `/v1/completions`) — the stage has likely
**never worked**; every eligible turn just wastes a ~70 ms round-trip. Run
against the *correct* endpoint, the same prompt costs **1.2–1.8 s p50** on the
live Gemma server (its 2 s timeout would often trip) and it misclassified
"what time is it" as `general_question`. Conclusion: the replaceable
pre-brain chain costs ~60–80 ms when honest (regex+embed); the LLM stages are
either dead or not worth their cost — that's a cleanup, not a Needle case.

### 4. TOOL-PREFILTER VALUE — real, but doesn't need Needle

Gemma tokens (live `/tokenize`): full 20-tool block = **1,661 tokens**;
top-3 shortlist ≈ **252 tokens avg** (range 149–480) ⇒ **~1,409 tokens
(84.8%) saved** per brain turn. At Orin-class prefill this is a meaningful
slice of the 4.8 s brain turn — but the shortlisting can be done by the
**already-live bge-small router** (60–80 ms); Needle adds nothing here,
especially with its retrieval head dead.

## Recommendation

**Reject as-is.** Every adoption path is blocked out of the box: full-router
mode can't fit Zoe's tool set in the encoder, the native prefilter head is
zero-weights, oracle-best accuracy still hijacks chat turns, and the runnable
(JAX) runtime is ~2–4 s/decision on this box. Revisiting would require BOTH
local fine-tuning on a Zoe-shaped corpus (Needle supports it; explicitly out
of scope for this spike) AND porting the Cactus INT4 runtime to Jetson —
two efforts for a role the box nearly covers already.

**Better ROI captured by this spike** (cheap, uses what's live):
1. Fix or remove the dead Tier-0.5 classifier endpoint (chip spawned).
2. `semantic_router.ROUTES` has **no music or smart_home domains** — every
   sloppy media/home utterance ("chuck on something chill", "kill everything
   downstairs") falls to the 4.8 s brain lane today. Adding those two domains
   + paraphrase exemplars attacks the same coverage gap Needle targeted, at
   ~70 ms and zero new runtimes.
3. Use bge-small for the 84.8% tool-block prefilter into Gemma's context
   (the vector Tool-RAG idea already on the abilities.ts roadmap).

## Files

| file | role |
|---|---|
| `setup.sh` | builds the isolated venv + patched needle source OUTSIDE the repo |
| `extract_tools.py` | Zoe's real 20-tool set (names/descriptions from `labs/flue-zoe-brain/src/tools/zoe-tools.ts`) → `zoe_tools.json` |
| `zoe_tools.json` | committed extraction artifact (regenerable) |
| `corpus.jsonl` | 81 labeled utterances: canonical / paraphrase-sloppy / chat |
| `needle_bench.py` | Needle accuracy + latency (`--mode full \| shortlist \| retrieval \| oracle`) |
| `baseline_bench.py` | current Tier-0 regex + Tier-1 embedding routing, same corpus |
| `classifier_cost.py` | Q3 — cost of the pre-brain classifier chain |
| `token_savings.py` | Q4 — Gemma tokens: full tool block vs top-3 shortlist |
| `results/*.json` | committed run outputs backing every number above |

## Method notes / caveats

- **Baseline is scored generously**: any Tier-0 regex intent hit counts (the
  live path only short-circuits *read* intents), and Tier-1 embedding routing
  is credited at *domain* level. Real live rescue is lower — the true
  coverage delta favours Needle less than these tables suggest, which only
  strengthens the verdict.
- **Needle got one extra tool**: `general_chat` (explicit no-tool escape) —
  single-shot FC models always emit a call; standard router design.
- Expected labels allow multiple correct tools where Zoe genuinely overlaps
  (e.g. `shopping_list_add` vs `add_to_list`).
- `needle_bench.py` wraps needle's `generate()` with fixed-shape padding and
  a 64-slot decode buffer; stock `generate()` recompiles XLA per query shape
  (~6 s/call observed) and decodes over a 512-slot buffer.
- Prod-adjacent measurements were read-only: a handful of classification-size
  completions to `:11434` (the measurement subject; far lighter than one brain
  turn) and pure `/tokenize` calls. No writes, no chat turns, no restarts.

## Reproduce

```bash
WORK=~/needle-work bash labs/needle-benchmark/setup.sh
cd $WORK
B=/home/zoe/assistant/labs/needle-benchmark
PYTHONPATH=$WORK ./venv/bin/python $B/needle_bench.py --ckpt ckpt/needle.pkl --mode full      --out $B/results/needle_full.json
PYTHONPATH=$WORK ./venv/bin/python $B/needle_bench.py --ckpt ckpt/needle.pkl --mode retrieval --out $B/results/needle_retrieval.json
PYTHONPATH=$WORK ./venv/bin/python $B/needle_bench.py --ckpt ckpt/needle.pkl --mode shortlist --out $B/results/needle_shortlist.json
PYTHONPATH=$WORK ./venv/bin/python $B/needle_bench.py --ckpt ckpt/needle.pkl --mode oracle    --out $B/results/needle_oracle.json
python3 $B/baseline_bench.py --out $B/results/baseline.json          # system python (zoe-data env)
python3 $B/classifier_cost.py --out $B/results/classifier_cost.json  # quiet window; --no-llm to skip :11434
python3 $B/token_savings.py --out $B/results/token_savings.json
```

Pinned: needle @ opensrc cache `cactus-compute/needle` (main); jax==0.6.2 +
flax==0.10.6 (latest jax 0.10 removed remat `concrete`, which needle's flax
scan still uses — this pair is the proven CPU/ARM64 combo).
