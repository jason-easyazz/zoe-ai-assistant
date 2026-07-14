# router-90-campaign — push the tool-level router to ≥90% (LAB)

Campaign lab: grammar-constrained decoding + sibling-discrimination training
data on top of `labs/functiongemma-finetune` (functok fine-tune) and
`labs/two-stage-router-eval` (SetFit stage-1). All numbers are on the frozen
81-case `labs/needle-benchmark/corpus.jsonl` with the identical scorer
(predicted tool ∈ `expected`; no-call/general_chat correct iff expected).

LAB-ONLY: nothing wired into the voice path; eval on :11435, never :11434.

## Scoreboard (target: ≥90% overall tool-level)

| config | overall | canonical | paraphrase | chat-FP | status |
|---|---|---|---|---|---|
| Tier-0/1 live baseline | 61.7% | — | 35.9% | 12.5% | reference |
| SetFit mlp head (domain-level) | 80.2% | — | 64.1% | 12.5% | reference (not tool-level) |
| stock FG two-stage config-a, no grammar | 35.8% | 50.0% | 10.3% | 0% | DEBUNKED |
| **stock FG two-stage + grammar(b) shortlist** | **42.0%** | 55.9% | 17.9% | 0% | +6.2 vs 35.8 — still dead; stock decoder is the problem |
| functok e1 (1 epoch), no grammar | 74.1% | 91.2% | 53.8% | 0% | reference (#1317-era) |
| **functok e1 + grammar(a) all-21** | **74.1%** | 91.2% | 53.8% | 0% | Δ=0: grammar kills hallucinated names, but those cases fall to legal siblings |
| **functok e1 + SetFit shortlist + grammar(b) [HYBRID]** | **75.3%** | 91.2% | 56.4% | 0% | best measured; +1.2 |
| functok e3 (3 epochs, old data) | pending (training, ETA ~15–20 h @ ~250 s/step nice-19 CPU) | | | | eval w/ + w/o grammar when done |
| functok definitive (expanded data, `run_definitive_training.sh`) | pending | | | | **the credible shot at 90%** |

Latency note: grammar-run latencies in `results/*.json` (~2.5 s p50) are
NOT comparable to the earlier ~360 ms numbers — the e3 CPU trainer was
running (5 cores) during these evals. Accuracy is unaffected.

## Error attribution (measured)

- **grammar(a), functok e1** — 21 fails: all 21 are *legal-name* errors the
  grammar cannot fix — sibling picks (show_list/list_remove→shopping_list_add
  ×5, cal-show→list_reminders/people, cal-add↔add_reminder,
  add_reminder↔remember_fact, note_search→recall_memory/create_note) and
  7 paraphrase→no-call (media ×2, weather, cal-show, people, recall).
  Hallucinated tool names (`get_media`, `shopping_list_query`) are gone but
  those 2 cases just became different wrong answers.
- **hybrid grammar(b)** — 20 fails: 17 stage2 wrong picks *within* the
  shortlist (same sibling families), 3 stage-1 shortlist misses
  (cal-add-p2, media-p1, fact-p1 — note: the earlier "zero stage-1 misses"
  claim does not hold on this gate-off logreg run).
- **stock-gb** — 44 stage2 wrong picks: the stock decoder prefers
  general_chat/siblings even when grammar-cornered. Confirms DEBUNKED.

**Verdict of the grammar lever:** grammar constraints are worth keeping
(they guarantee syntactically legal calls and delete hallucinated names for
free) but they are worth ~0–1.5 pts, not the 90% lever. The measured gap is
*sibling discrimination inside the legal set* + paraphrase no-calls — a
training-data problem.

## The plan to 90% (honest best shot)

1. `labs/functiongemma-finetune/build_sibling_dataset.py` → 275 contrastive
   examples (182 tool + 93 hard chat negatives) aimed at exactly the
   measured confusion families above; held-out guard vs the eval corpus.
2. e3 (2 more epochs, old data) is mid-flight and classifier-protected —
   let it finish, eval it (± grammar) for the epochs-vs-data attribution.
3. `run_definitive_training.sh`: +2 epochs on original+sibling data from the
   best available merged checkpoint, full-speed CPU (~5–6 h uncontended).
4. Final eval = `run_grammar_eval.py --config functok-gb` (hybrid) with the
   definitive GGUF. Expectation: e1's canonical is already 91.2%; the loss
   is concentrated in 18 paraphrase cases whose families the sibling set targets
   directly. 90% needs ≥73/81 — i.e. recovering ~12 of the 20 hybrid fails.
   Plausible but not guaranteed; if it lands 85–89%, the remaining lever is
   brain-paraphrase augmentation of the sibling families
   (`gen_paraphrases.py`, needs :11434 up).

## Files

- `run_grammar_eval.py` — grammar-constrained eval harness (configs
  `functok-ga` / `functok-gb` hybrid / `stock-gb`; `--gate` optional).
  GBNF via `"grammar"` on `/v1/chat/completions` (no tools), or
  `/apply-template` + `/completion` for the stock model, because
  llama-server rejects custom grammar together with `tools`
  (`server-common.cpp`). Verified against build b9733 on this box.
- `run_definitive_training.sh` — the queued definitive run (see HANDOFF.md).
- `results/` — raw per-case JSON for every configuration.
- `../functiongemma-finetune/build_sibling_dataset.py` +
  `data/train_sibling.jsonl` — the sibling-discrimination dataset.

## HANDOFF

See `HANDOFF.md` for the exact next commands (e3 poll → export → eval →
definitive run → final eval), written so any session can finish the campaign.
