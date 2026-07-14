# Two-stage router eval — the HONEST end-to-end number (lab only)

The previously-quoted **93.8%** for "SetFit shortlist → FunctionGemma decode"
was measured with an **oracle** shortlist on a **16-case easy subset**
(time/timer/chat only). This lab runs the REAL two-stage pipeline —
SetFit head top-3 domain shortlist + chat gate → stock FunctionGemma-270M Q8
decoding from only the shortlisted tools — end-to-end on the complete 81-case
needle-benchmark corpus, scored identically to every prior eval.

**Headline: the real two-stage scores 35.8%. It does NOT survive contact.**

## Results (all rows scored on the same 81-case corpus + scorer)

| Config | Overall | Canonical | Paraphrase | Chat-FP | p50 | p90 |
|---|---|---|---|---|---|---|
| **TWO-STAGE A**: logreg top-3, gate 0.4 → stock FG shortlist block | **35.8%** | 50.0% | 10.3% | **0.0%** | 469 ms | 907 ms |
| **TWO-STAGE B**: mlp top-3, gate 0.7 → stock FG shortlist block | 33.3% | 47.1% | 7.7% | **0.0%** | 600 ms | 1228 ms |
| Single-stage **functok fine-tune** (no schema, 47-token prompt) — re-run, matches reference | **74.1%** | 91.2% | 53.8% | **0.0%** | 367 ms | 487 ms |
| *ref:* SetFit mlp head alone (labs/setfit-router) — **domain-level**, see caveat | **80.2%** | 97.1% | 64.1% | 12.5% | +14 ms on prod embed | — |
| *ref:* Tier-0 regex + Tier-1 bge baseline (PR #1276) | 61.7% | 85.3% | 35.9% | 12.5% | — | — |
| *ref:* stock FG Q8 CPU, full 20+1-tool block | 33.3% | 41.2% | 12.8% | 0.0% | — | — |
| *ref:* stock FG Q8 CPU, **oracle** 3-tool block, 16-case subset | 93.8% | 100% | 75.0% | 0.0% | — | — |

**Scoring caveat (not apples-to-apples on one axis):** the SetFit-alone row
is **domain-level** accuracy (13-way domain incl. chat, per
`labs/setfit-router/eval.py`); the two-stage and FunctionGemma rows are
**tool-level** (exact tool name in the case's `expected` list, 20+1 tools).
A domain hit still needs a domain→tool dispatch step to become an executed
call, so 80.2% is an upper bound on what SetFit-alone delivers end-to-end —
but the two-stage was built precisely to BE that dispatch step, and it turns
80.2%-worth of near-perfect shortlists into 35.8% executed calls. The
directional verdict (two-stage with a stock decoder is a large net loss)
does not depend on the caveat; the strongest measured tool-level system
remains the functok fine-tune at 74.1%.

End-to-end two-stage latency (what production would pay, embedding included):
config A p50 **469 ms** / p90 **907 ms** per decision — stage 1
(bge-small embed + head) is only p50 10 ms / p90 21 ms of that; the FG decode
on a ~800-token shortlist prompt (range 402–1099 tokens) is the rest.

## Error attribution — stage 2 is the bottleneck, not stage 1

Config A failures (52 of 81):

| Failure mode | Count | Meaning |
|---|---|---|
| stage-2 wrong pick | 35 | gold tool WAS in the offered schema block; stock FG picked a sibling tool, called general_chat, or refused in prose ("I apologize, but I cannot assist…") |
| stage-1 gate ate a tool turn | 17 | logreg confidence < 0.4 (or predicted chat) → abstained on a real command |
| stage-1 shortlist miss | 0 | top-3 domains always contained the gold domain when the gate fired |

Config B: 43 stage-2 wrong picks, 10 gate abstentions, 1 shortlist miss.

So the SetFit shortlist itself is essentially perfect (0–1 misses in 81) and
the chat gate is airtight (0.0% chat-FP in both configs) — **stock
FunctionGemma-270M then destroys the routing on realistic 5–9-tool blocks**:
it hijacks sibling tools (`show_list`→`list_remove`, `remember_fact`→
`remember_emotional_moment`), bails to `general_chat`, or answers in refusal
prose instead of calling anything. The oracle 93.8% came from an easy 3-tool
menu (get_time / set_timer / general_chat) on 16 easy cases; with real
same-domain sibling tools in the block, the decoder's discrimination
collapses (paraphrase slice: 10.3%).

## Verdict

- **The real two-stage does NOT beat SetFit-alone.** 35.8% tool-level vs
  80.2% domain-level (an upper bound on SetFit-alone; different scoring
  axes, so read it as a directional loss, not an exact 44-point gap —
  see the scoring caveat). On the SAME tool-level axis it is worse than
  the 74.1% functok fine-tune and the 61.7% live baseline, and B literally
  ties the 33.3% stock-full-block row it was meant to fix.
- **The honest number to plan around for shadow-mode is SetFit-alone 80.2%
  domain-level** (+14 ms on the already-computed prod embedding; tool
  dispatch within the domain still needed — see the scoring caveat above),
  with the functok
  fine-tune's 74.1% (367 ms p50, args included, 0% chat-FP) as the only
  viable FG-based alternative today.
- If a two-stage is still wanted, stage 2 must be a **fine-tuned** decoder
  (or more functok epochs — one is training now), not stock FG; re-run this
  harness (`--config a`) with the new GGUF before believing any composite
  number again.

## Files

- `run_two_stage.py` — the harness (configs `a`, `b`, `functok`); same
  discipline as `labs/functiongemma-feasibility/run_feasibility.py`
  (memory gate ≥1500 MB, port-free + `/props` identity check on :11435,
  CPU-only `-ngl 0`, always kills its server, never touches :11434).
  On hosts where the llama-server build or GGUFs live at different paths,
  override all that differ, e.g.
  `LLAMA_SERVER=/path/to/llama-server GGUF_STOCK=/path/to/stock.gguf
  GGUF_FUNCTOK=/path/to/functok.gguf python3
  labs/two-stage-router-eval/run_two_stage.py --config a`
  (setting `LLAMA_SERVER` alone is not enough if the models moved too).
- `results/a.json`, `results/b.json`, `results/functok.json` — full per-case
  records incl. shortlist, confidence, raw model output, failure attribution.

Corpus/scorer: `labs/needle-benchmark/corpus.jsonl` (81 cases: 34 canonical /
39 paraphrase / 8 chat), tools `labs/functiongemma-finetune/zoe_tools.json`
(+ `general_chat` escape hatch), predicted-tool-in-`expected` scoring —
identical to the feasibility/finetune evals. Stage-1 artifacts:
`labs/setfit-router/artifacts/head_{logreg,mlp}.joblib` on pinned
BAAI/bge-small-en-v1.5 (fastembed).

Run discipline: the idle trainer was SIGSTOPped (supervisor first, then the
training pgid) for the duration and SIGCONTed after (trap-guarded); verified
resumed. Live brain :11434 untouched and healthy after the run.
