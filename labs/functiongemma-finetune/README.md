# functiongemma-finetune — fine-tune FunctionGemma-270M as Zoe's complete-call router (LAB SPIKE)

Follow-up to `labs/functiongemma-feasibility/` (PR #1283, verdict **GO**):
stock FunctionGemma-270M routes at 33.3% on the full 21-tool block but 93.8%
on a 3-tool shortlist — the gap is tool *selection*, exactly what fine-tuning
targets. This lab builds the training set, the two training variants
(functional-token vs plain — the headline A/B), the GGUF export, and the
held-out eval harness.

LAB-ONLY: nothing wired into the voice path, `fast_tiers`, `zoe-data`,
systemd, or CI. Eval runs on **:11435**, never the live brain port.

## STATUS: functok TRAINED + EVALUATED on-box (CPU); plain leg reduced-scope

The functok variant was trained **on this box, CPU-only** (nice 19,
OOM-shielded, 2 GB memory gate enforced throughout; 1 epoch, ~2.4 h at
~50 s/step) and evaluated on the held-out corpus — headline numbers below.
The plain variant is CPU-hostile (~2.3k-token prompts ≈ 25× the FLOPs per
example → ~45 h/epoch full-set), so it runs at reduced scope: a stratified
414-example subsample (18/tool + 54 chat), 1 epoch, with a windowed-loss
trainer (`logits_to_keep`) because full-sequence logits alone are
~2.5 GB fp32 *per example* at a 262k vocab. Off-box packet below remains the
path to the full-strength A/B (3 epochs, full set, both variants).

On-box engineering record (why things are the way they are):
- **GPU training is not usable on this box**: even with the brain stopped
  (~5.9 GB free) both bs2 and bs16 runs died in backward with Jetson NvMap
  ENOMEM → torch `CUDACachingAllocator.cpp:1131` NVML assert (torch 2.8.0
  aarch64). Not batch-size-dependent; likely Jetson-specific allocator issue.
- **bf16 on aarch64 CPU is emulated** and pathologically slow (no step in
  10 min); fp32 + `TRAIN_THREADS=8` runs ~45–55 s/step (functok, bs8).
- The first plain attempt (no checkpointing, full logits) swap-thrashed the
  box to 164 MB available and was killed; gradient checkpointing + the
  windowed loss fixed it (memory stays >4 GB).

## The A/B being tested (headline deliverable)

One canonical dataset, two rendered variants (`train_lora.py --variant …`):

| variant | prompt | target |
|---|---|---|
| `plain` | full 21-tool declaration block (stock FunctionGemma usage, ~2,240 tokens) | `<start_function_call>call:NAME{args}<end_function_call>` |
| `functok` | **no tool declarations** — 1-line router prompt (~50 tokens) | opens with a functional token, then the same call syntax |

The Octopus-v2 twist: functional tokens `<zoe_tool_0>…<zoe_tool_19>` +
`<zoe_no_tool>` are mapped onto Gemma's reserved `<unused0>…<unused20>` vocab
rows — **no tokenizer resize, GGUF-safe**, already single tokens in llama.cpp.
`<zoe_no_tool>` (= `<unused20>` = `general_chat`) ends the turn immediately.
For `functok`, `embed_tokens` is fully trained (`modules_to_save` +
`ensure_weight_tying=True`, peft 0.18) so the tied lm_head learns to emit the
tokens; targets are rendered through the model's own chat template, never
hand-built. If functok holds accuracy, it also deletes the 2,235-token schema
prompt → the feasibility latency numbers say that's ~768 ms → ~400 ms p50 CPU.

## Dataset (`data/train.jsonl`, committed — 2,950 examples)

- **2,400 template examples** — 120 per tool × 20 tools, hand-authored
  utterance templates × slot pools (`build_dataset.py`); args are correct *by
  construction* (no LLM labeling error). Generic voice-style wrappers
  ("hey zoe …", "… please") multiply phrasing variety.
- **320 chat negatives** — seeded from `labs/setfit-router` `train.jsonl`
  `chat` rows (1,121-row corpus, label-safe reuse: chat needs no args) + a
  hand chat bank.
- **230 brain paraphrases** — `gen_paraphrases.py` rewrote a sample via the
  live brain (:11434, serial + paused, gentle); kept only rewrites where every
  anchor arg value (item/name/fact/…) survives verbatim, so labels stay true.
- **Held-out guard**: any text normalizing equal to one of the 81
  `labs/needle-benchmark/corpus.jsonl` cases is dropped. We never train on
  the eval set. (Voice-replay corpus: `~/.zoe-voice-samples` has WAVs but no
  transcript manifest — noted, not used.)

Rebuild: `python3 build_dataset.py --setfit-train <setfit train.jsonl>`
(merges `data/paraphrases.jsonl` if present).

## Training

```
python3 labs/functiongemma-finetune/train_lora.py --variant functok   # then
python3 labs/functiongemma-finetune/train_lora.py --variant plain
```

LoRA r=16/α=32 on attn+MLP, bf16, Adafactor, gradient checkpointing, 3
epochs, lr 2e-4 (cosine); functok adds trained (tied) `embed_tokens`. Safety:
refuses to start under 2 GB `MemAvailable`, aborts mid-run under 600 MB.

### Off-box packet (consumer GPU)

- Inputs: this directory + base model `unsloth/functiongemma-270m-it`
  (ungated HF mirror; pass `--model-dir unsloth/functiongemma-270m-it`).
- Versions used to build/verify the pipeline on-box: python 3.10, torch 2.8.0,
  transformers 5.5.0, peft 0.18.1 (needs `ensure_weight_tying`), datasets;
  any CUDA box works — Unsloth optional (this script is plain HF+peft;
  Unsloth has no aarch64 wheels, which is one reason training is off-box).
- Expected runtime on one consumer GPU (e.g. RTX 3060/4070): `functok`
  (~50-token prompts, 2,802 train examples, 3 epochs) ≈ **10–20 min**;
  `plain` (~2,300-token prompts) ≈ **2–4 h** at batch 4 (raise batch on
  bigger cards).
- Outputs: `runs/<variant>/adapter` + `runs/<variant>/merged`; convert with
  `LLAMA_CPP=<llama.cpp checkout> ./export_gguf.sh runs/<variant>/merged
  functok-q8.gguf` (validated; needs gemma3 support) and copy the GGUF back
  to `/home/zoe/models/lab/`. Off-box eval likewise needs
  `LLAMA_SERVER=<path to llama-server>` (defaults are this box's paths).

## Eval (held-out 81-case corpus, :11435)

```
python3 labs/functiongemma-finetune/run_eval.py --gguf <path> --variant plain   --tag plain-q8-cpu
python3 labs/functiongemma-finetune/run_eval.py --gguf <path> --variant functok --tag functok-q8-cpu
```

Same discipline as the feasibility harness: 2 GB memory gate, port-free +
`/props` identity check, always kills its server. `plain` benches the full
block **and** the 3-tool shortlist (two-stage config); `functok` benches with
no schema in the prompt (empty output ≡ no-call ≡ chat).

## Reference points (all measured previously, same corpus + scoring)

| config | overall | paraphrase | chat-FP |
|---|---|---|---|
| Tier-0 regex + Tier-1 bge baseline | 61.7% | 35.9% | 12.5% |
| SetFit mlp head (`labs/setfit-router`) | 80.2% | 64.1% | 12.5% |
| stock FunctionGemma Q8 CPU, full block | 33.3% | 12.8% | 0.0% |
| stock FunctionGemma Q8 CPU, 3-tool shortlist (16-case subset) | 93.8% | 75.0% | 0.0% |
| **fine-tuned targets** | **≥85%** | — | ≤12.5% (ideally ~0) |

### Fine-tuned results (measured, Q8_0 GGUF, CPU `-ngl 0`, :11435)

| config | overall | canonical | paraphrase | chat-FP | prompt toks p50 | p50 | p90 |
|---|---|---|---|---|---|---|---|
| **functok, no schema in prompt** (1 epoch CPU) | **74.1%** | 91.2% | 53.8% | **0.0%** | **47** | **360 ms** | 483 ms |
| plain fine-tuned (reduced-scope leg) | *see results/plain-q8-cpu.json* | | | | | | |

functok highlights vs the reference points:
- **+40.8 pts over stock full-block** (33.3% → 74.1%) with the 2,235-token
  schema prompt REMOVED (47 prompt tokens): the Octopus-style functional-token
  trick works at 270M scale.
- **Beats the 61.7% live baseline** by 12.4 pts with 0% chat false-positives
  (baseline: 12.5%) at roughly half the stock full-block latency
  (360 ms vs 768 ms p50 CPU).
- **Below the 80.2% SetFit head** (and the ≥85% target) after ONE epoch on
  1 CPU: the gap is paraphrase (53.8%); failures are mostly paraphrase-style
  confusions and occasional hallucinated tool names (`get_media`,
  `shopping_list_query`) — classic under-training signals. The off-box
  3-epoch run is the expected fix, not more data.

## Recommendation (as of this spike)

1. **Ship the off-box packet** — on-box training is gated out by the box's
   steady-state memory pressure, not by model size; the pipeline itself is
   ready end-to-end.
2. Until a fine-tuned checkpoint lands, the **two-stage config remains the
   strongest measured option**: bge/SetFit top-3 shortlist → stock
   FunctionGemma complete-call decode (93.8% on the shortlist subset,
   0% chat-FP, ~390 ms p50 CPU).
3. The functok-vs-plain A/B decides the endgame: if functok ≥ plain, adopt
   the schema-free single-stage router (fastest possible: no 2,235-token
   prompt, single-token selection); if plain wins, keep two-stage with the
   fine-tuned decoder.
