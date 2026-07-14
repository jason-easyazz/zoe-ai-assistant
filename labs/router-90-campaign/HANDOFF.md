# HANDOFF — router-90 campaign: TARGET HIT

State as of 2026-07-14 evening:

- **DONE: 90.1% overall (73/81) / 100% canonical / 79.5% paraphrase /
  0% chat-FP / 424 ms p50** — `results/r2-gb-mlp-g0.5.json`. Config:
  SetFit **mlp** head top-3 shortlist with chat gate **0.5** + functok
  **r2** decoder + shortlist GBNF grammar
  (`run_grammar_eval.py --config functok-gb --head mlp --gate 0.5`).
- Ungated r2 = 92.6% but 12.5% chat-FP (`results/r2-gb-mlp.json`);
  gate 0.7 = 84.0% (too strict). 0.5 is the ship point.
- Model: `/home/zoe/models/lab/functiongemma-270m-zoe-functok-r2-Q8_0.gguf`
  (Q8 GGUF, ~292 MB). Training: warm-start from
  `runs/functok-definitive/merged`, 2 epochs on
  train_expanded + round-2 families ×2 (batch 8, accum 2, CPU ~3.5 h).
  Checkpoints/GGUFs are NOT in git (`labs/functiongemma-finetune/.gitignore`).
- llama-server (:11434 brain) is RESTARTED and healthy — leave it up.
- EXPORT GOTCHA (cost an hour): transformers `save_pretrained` re-serializes
  the tokenizer in a way that breaks `convert_hf_to_gguf.py`
  (assert max id < vocab_size). Before export, copy the pristine upstream
  tokenizer files from `/home/zoe/models/lab/functiongemma-270m-it-hf/`
  (tokenizer.json, tokenizer.model, tokenizer_config.json,
  special_tokens_map.json, added_tokens.json, generation_config.json,
  chat_template.jinja) over `<merged>/`, then
  `LLAMA_CPP=/home/zoe/llama.cpp ./export_gguf.sh <merged> <out>.gguf`.

## Next (a fresh session picks up here)

1. **Shadow-mode integration**: wire the winning two-stage config
   (mlp shortlist + gate 0.5 + r2 GGUF + shortlist grammar) behind a
   default-off flag next to the SetFit shadow head (#1318,
   `ZOE_ROUTER_HEAD`); log said-vs-did against the live Tier-0/1 router
   before any cutover. Replay-gate per the voice-path mandate.
2. Optional accuracy headroom: 3 stage-1 misses + gate abstentions are the
   remaining gap; brain-paraphrase augmentation of the sibling families
   (`gen_paraphrases.py`, needs :11434) or a SetFit head retrain on the
   round-2 texts.
3. The committed round-2 dataset carries corrected arg keys (`list_type`,
   media dispatcher shape, home `room`); the shipped r2 GGUF was trained
   before those key fixes (tool-choice scoring unaffected — args-only).
   Fold them in at the next retrain.
