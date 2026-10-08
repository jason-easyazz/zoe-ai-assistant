---
type: Research
title: Structural, language-independent memory floors - measured options (Samantha program)
description: Why the lexical memory floors (memory_authority polarity/contrast/retraction rules, role_guess_guard, people_roles) leak a new English phrasing every review round and cannot serve a second language; a 337-item labelled set built from the ledger phrasings (PR #1912/#1913/#1916 review threads) plus held-out English and es/fr/de/zh/ja translations; measured size, RAM, latency and accuracy of six ONNX NLI cross-encoders, the live 4B as a constrained yes/no judge, and a fine-tuned small multilingual cross-encoder; the recommended structural design (structured claim rows with a verbatim quote, polarity stored once, retractions by id, role claims as id triples, lexicons as per-language data), a migration plan that keeps every red-when-removed test, and a ranked experiment list with pass bars.
tags: [memory, authority, multilingual, nli, floors, role-guard, language-independence, samantha-bar, zmb, stt, research]
timestamp: 2026-10-09T02:00:00Z
---

# Structural, language-independent memory floors - 2026-10-09

Evidence labels. **[measured]** = run on this Orin today (2026-10-09, `main` @ `b2ec3ef5`; nothing live changed,
nothing committed except this note and the labelled set). **[src]** = read in this worktree. **[doc]** = a URL
fetched today. **[est.]** = arithmetic from published numbers, not run. **[unverified]** = not read or not run.
All person names are synthetic. Every "n=" below is small: read the numbers as a ranking, not a rate.

## 0. Summary

1. **The leak is structural, not a shortage of rules.** The three floors (`memory_authority.py` 1,385 lines,
   `role_guess_guard.py` 441, `people_roles.py` 273 **[src]**) decide polarity, contrast, retraction, role and
   owner by regex over English prose. This week's three review threads raised **33 findings** (#1913: 9,
   #1912: 22, #1916: 2 **[measured, `gh api`]**); every fix was another English rule except one finding that was
   **declined** because "lexical polarity cannot separate it" (`close to Bendigo but not within the city limits`).
   On the labelled set the lexical stack scores **0.98 balanced accuracy on the phrasings it was tuned on, 0.59 on
   43 new English phrasings (including 2 false promotions), 0.50 on every non-English item** (it never fires:
   recall 0 of 25).
2. **Off-the-shelf multilingual NLI is not the answer.** Six ONNX cross-encoders (90-430 MB on disk, 260-750 MB RSS,
   7-54 ms at 2 threads) score **0.47-0.80 balanced accuracy** on the support decision with **23-30 false
   promotions on the 108 ledger rows** for the five usable ones - the unsafe direction. A threshold tuned on the
   ledger does not transfer (best 0.82 on the held-out splits). The "small" 107 MB int8 MiniLM **collapsed under
   int8 quantisation** (AUC 0.55 vs 0.85 in fp32).
3. **Measured winner among zero-shot options: the live 4B as a constrained yes/no judge**
   (`grammar: root ::= "yes" | "no"`, ~119 prompt tokens, 2 output tokens): **0.88 / 0.89 / 0.82** balanced
   accuracy on ledger-sample / held-out EN / es+zh+ja, false promotions **3 / 0 / 1**; role decision
   **0.93 / 0.85 / 0.83**. **280 ms p50** (329 p95): an **off-path** verifier only, because it shares the brain's
   single slot.
4. **Measured best deployable direction: a small multilingual cross-encoder fine-tuned on the decision (E1).**
   Trained for 20 CPU-minutes on 4,368 templated pairs in en/es/fr/de (embeddings frozen, vocabulary pruned),
   it scores **0.98 on the 90 non-English items with 0 false promotions, including zh 1.00 and ja 0.90 which it
   never saw**, but only **0.74 on the held-out English paraphrases (1 false promotion, 9 misses)** and 0.82 on the
   ledger. Reading: language transfer works; paraphrase coverage is a data problem that more varied training data
   solves, and its errors are mostly misses (fail-closed). The non-English number is optimistic (section 3.5).
5. **Recommended architecture (section 5): move language out of the floor, keep the floor.** The extractor (already
   a 4B call) emits a *structured claim* - subject, predicate code, polarity, modality, tense and **a verbatim quote
   of the owner's turn**. The floor then checks what is language-independent by construction: the quote is a
   substring of the owner's turn (NFKC/casefold), the value is in the quote (edit distance, not stemming), the turn
   is not a pasted/third-party block, the speaker is verified, and an *independent* verifier (distilled
   cross-encoder; the 4B off-path while that is being built) agrees that quote entails fact. Polarity is decided
   once and stored; a retraction retires rows **by id** through a structured key. Role claims are supported only
   by `person_relationships` rows (person id, language-neutral kin code, owner id) and the guard becomes set
   membership over id triples. Lexicons survive as per-language **data** used as pre-filters, never as authority,
   and a language is "on" only when its fixture rows pass.
6. **Migration keeps every red-when-removed test** (section 6): parametrized tables become fixture rows, each
   negative control becomes "swap the verifier for accept-all and rows of that kind must go red", and a meta-test
   proves every row *kind* has a control.
7. **A second spoken language is a separate, smaller problem** (section 7): Moonshine ships per-language models
   (es/de/zh/ja at 34 M-123 M parameters, MIT) and the household member's language should select the model; RAM,
   not accuracy, is the limit.

## 1. What the lexical floors are, and why they leak

### 1.1 Inventory **[src]**

| module | what it decides | lexical machinery |
|---|---|---|
| `memory_authority.supports` / `entailing_span` / `_window_is_a_statement_about_the_user` | does the owner's own sentence entail the stored fact (promotion to `user_stated_derived`, supersession) | English suffix stemmer with special cases (`_stem`, `_NO_PLURAL`, consonant un-doubling), stop words, attribute cue classes (`_CUE_WORDS`: ~100 English words in 7 classes), negation (`_NEG_RE`), end-state verbs (`_ENDED_RE`, `_ENDED_BASE`), contrast delimiter (`_CONTRAST_RE`, `_NOT_A_CONTRAST`, `_TEMPORAL`, `_ANAPHORA`), hedges, hypotheticals, questions, lead-in labels, first-person pronoun set |
| `role_guess_guard` | does a reply assign a family/partner role the evidence does not state, for that person and that owner | `GUESS_ROLES` (~55 English words), determiner/adverb/modal-verb regexes (`_DET`, `_ADV`, `_VERB`), owner-chain parsing |
| `people_roles` | does an extracted fact make NAME the holder of a role nobody stated | `_ROLE_ALT`, `_CLAIM_RES`, `PET_WORDS`, `_NAME` (`[A-Z][a-z]...`: ASCII-capitalised names only) |

The fact itself is an English third-person sentence (`_TURN_EXTRACTION_PROMPT`: `"fact": a single concise
sentence in third-person` **[src]**), so a Spanish turn is compared with an English-shaped fact by
English-shaped rules.

### 1.2 The review loop has a countable shape **[measured, `gh api`]**

PR #1913 ("X, not Y"): 4 rounds, 9 findings - pronoun denial, attached dash, negated end-state verb, temporal
denial, bare "but not", descriptive location, base-form verb, negation binding, verbs in the global stop list.
PR #1912 (role guard): 6 rounds, 22 findings - role-first copula, owner of the role, full names vs first names,
complete owner (not a shared token), nested possessives, hedges/modals, guard people even when another role is
known, questions are not evidence, pronouns, lowercase owners after "of", possessive relatives. PR #1916: 2
findings. Each fix is right for the phrasing it was written for; the next phrasing is a different English
construction (section 3.2), and none of it transfers to another language.

### 1.3 The requirement

The owner asked on 2026-10-08 that Zoe not be English-only by construction. A lexical floor cannot meet that:
adding a Spanish `_NEG_RE` and a French `_stem` multiplies the loop by the number of languages.

## 2. Method

### 2.1 The labelled set (committed)

`services/zoe-data/tests/fixtures/structural_floors_labelled_set.json` - 337 items, synthetic names (`.gitignore`
ignores `*.json` except under `services/zoe-data/tests/fixtures/`, hence that path).

| task | label True means | split | n | source |
|---|---|---|---|---|
| `support` | the owner's own sentence **entails** the stored fact (the `entailing_span`/`supports` contract) | `ledger` | 108 | AST-extracted from `test_user_correction_contrast.py` (#1913 rounds 1-4, ~70 rows), `test_owner_retraction_lands.py` (#1916), `test_memory_authority.py` anchoring tables, `test_memory_own_change_of_mind.py` C1/C5, plus the declined `within the city limits` case |
| `support` | same | `heldout_en` | 43 | **authored today**: phrasings the loop has not produced yet (rather than, kicked the habit, called time on, pulled out, the seventh of August, hives, "or so she tells me", moving next month, ...) |
| `support` | same | `xling` | 108 | 18 core cases x {en, es, fr, de, zh, ja}; **author-translated, not native-reviewed** |
| `role` | the reply clause is an unsupported role **guess** (the `neutralise` contract) | `ledger` | 34 | `test_role_guess_guard.py`, `test_s22_role_guess_guard.py` (#1912) |
| `role` | same | `heldout_en` | 14 | authored today (periphrasis, elliptical, vague "family", compound role, negated claim) |
| `role` | same | `xling` | 30 | 6 cases x {es, fr, de, zh, ja}; evidence rows stay English (the store), reply and user turn in the language |

Limits that matter. The **ledger split is the lexical stack's training set** (each phrase was added because a test
pinned it): an upper bound for lexical, a fair test for everything else. Held-out and cross-lingual splits are the
author's, written by the person who read the ledger. Translations are mine and unreviewed. 14-108 items per split
means confidence intervals of roughly +/-0.1 to 0.2. Balanced accuracy (mean of recall and specificity) is reported
because always-"no" scores 0.72 plain accuracy on the non-English support split; `(FP/FN)` counts sit beside it.
**For `support` FP = false promotion (unsafe). For `role` FN = a guess let through (unsafe).**

### 2.2 Instruments, and how they were checked

* ONNX Runtime 1.23.2 CPU, one process per model under `systemd-run --user --scope -p MemoryMax=... -p
  MemorySwapMax=0`, RSS from `/proc/self/status` after the run, batch 1, 20-35 tokens per pair, 327 pairs. Latency
  was re-measured after the other jobs finished (load average fell 15 -> 4.5 during it): treat as an upper bound.
* **Sanity probe before trusting any model**: `A man is playing a guitar -> A person plays an instrument` (entail
  0.55) and `-> A man is sleeping` (contradiction 0.57); `I live in Perth` vs `I don't live in Perth` flips to
  contradiction 0.67-0.87. Label order was read from each `config.json` `id2label` (the English DeBERTa-xsmall
  orders contradiction/entailment/neutral differently from the others).
* **Instrument negative control**: the production lexical rules run through the same harness reproduce 0.98 / 0.96
  on the ledger, i.e. the fixture labels agree with the tests that produced them (two residual disagreements
  listed in 3.2).
* Hypothesis wording was measured rather than assumed (3.3 item 4): F1 = premise is the sentence, hypothesis is the
  fact with `User` -> `The speaker`; F2 = premise `User: "<sentence>"`, hypothesis the fact verbatim. F2 is
  language-neutral and better on 5 of 6 models, so the tables use F2.
* The 4B judge ran against the live brain endpoint (read-only, synthetic text, serial, 183 requests at ~01:30 local,
  single slot, `temperature 0`). **This is the one place a live service was touched; nothing was written.** The
  prompt was written once and not tuned on the labels; one format change (JSON schema -> a two-word grammar) was
  made after seeing latency, not accuracy.

## 3. Results

### 3.1 The measured table

Balanced accuracy, with `(false-promotions / misses)` in parentheses. Support splits: ledger n=108, held-out EN
n=43, non-English n=90 (es, fr, de, zh, ja). Role splits: ledger n=34, held-out EN n=14, non-English n=30. **Role
columns use the hand-written claim as the hypothesis ("oracle claim")**: they measure step 2 (claim vs evidence)
only; step 1 (reading a reply into a claim) is in 3.3 item 5. p50 is per cross-encoder pair.

| model (artifact) | disk MB | RSS MB | p50 ms, 2 thr / 4 thr | support: ledger | support: held-out EN | support: non-EN | role: ledger | role: held-out EN | role: non-EN |
|---|---|---|---|---|---|---|---|---|---|
| Production lexical rules (`entailing_span`/`supports`, `neutralise`) | - | 0 | 0.13 (CPU, stdlib) | 0.98 (1/1) | 0.59 (2/14) | 0.50 (0/25) | 0.96 (1/0) | 0.67 (1/4) | 0.50 (0/15) |
| mmBERT-small NLI q8 (`Nicolassuez/mmbert-small-nli-onnx-q8`) | 141.5 | 521 | 24.9 / 19.0 | 0.73 (25/5) | 0.79 (9/1) | 0.68 (37/2) | 0.84 (1/5) | 0.83 (2/0) | 0.93 (2/0) |
| DeBERTa-v3-xsmall NLI int8, **English only** (`Xenova/nli-deberta-v3-xsmall`) | 90.4 | 264 | 20.8 / 17.5 | 0.80 (23/0) | 0.78 (7/3) | 0.66 (26/7) | 0.91 (1/2) | 0.83 (2/0) | 0.83 (4/1) |
| mDeBERTa-v3-base xnli int8 (`onnx-community/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7-ONNX`) | 338.3 | 594 | 52.0 / 36.6 | 0.73 (23/7) | 0.66 (5/9) | 0.72 (26/4) | 0.89 (0/5) | 0.83 (2/0) | 0.83 (0/5) |
| Horizon mmBERT-small zero-shot q8, 2-class (`Horizon-Labs/multilingual-zeroshot-small`) | 268.4 | 633 | 54.0 / 33.8 | 0.76 (28/0) | 0.76 (9/2) | 0.75 (32/0) | 0.77 (4/3) | 0.67 (4/0) | 0.70 (4/5) |
| multilingual-MiniLMv2-L6 xnli fp32 (`onnx-community/multilingual-MiniLMv2-L6-mnli-xnli-ONNX`) | 428.2 | 750 | 18.4 / 9.1 | 0.73 (30/1) | 0.62 (13/4) | 0.66 (41/1) | 0.69 (3/8) | 0.67 (4/0) | 0.73 (3/5) |
| same, int8 | 107.4 | 447 | 7.3 / 4.3 | 0.49 (14/39) | 0.54 (8/11) | 0.47 (27/16) | 0.67 (8/0) | 0.58 (5/0) | 0.67 (10/0) |
| **E1**: that MiniLMv2-L6, fine-tuned on 4,368 templated en/es/fr/de pairs, vocab pruned to 846 tokens (support only; torch fp32 eval) | ~50 (est., see 3.5) | ~120 (est.) | ~18 / ~9 (est.: same graph as the fp32 row) | 0.82 (7/12) | 0.74 (1/9) | 0.98 (0/1) | - | - | - |

The 4B was run on a stratified subset (all held-out, 40 random ledger rows, es+zh+ja cross-lingual, 20 random role
ledger rows, es+zh role) to hold the live brain for four minutes, so here is the like-for-like table on that subset:

| decision | split (n) | lexical | 4B yes/no (zero-shot) | E1 (support only) |
|---|---|---|---|---|
| support | ledger sample (40) | 1.00 (0/0) | 0.88 (3/2) | 0.77 (4/5) |
| support | held-out EN (43) | 0.59 (2/14) | 0.89 (0/4) | 0.74 (1/9) |
| support | es+zh+ja (54) | 0.50 (0/15) | 0.82 (1/5) | 0.97 (0/1) |
| role | ledger sample (20) | 1.00 (0/0) | 0.93 (0/2) | - |
| role | held-out EN (14) | 0.67 (1/4) | 0.85 (1/1) | - |
| role | es+zh (12) | 0.50 (0/6) | 0.83 (0/2) | - |

4B cost per decision: median prompt 119 tokens (of which most are uncached: ~206 ms prefill median), 2 output
tokens (~64 ms), **p50 280 ms / p95 329 ms wall**.

### 3.2 The lexical stack on the new phrasings: what the loop will find next

Held-out support misses (all FN: the fact stays `model_from_turn`/`disputed`, the "stale fact served" class of
the day-sim incident): `My mum lives in Bendigo, definitely not Ballarat`; `Correction: it's Bendigo where my mum
lives, Ballarat was wrong`; `Mum's place is in Bendigo`; `Perth is home for me`; `I've kicked the habit, no more
cigarettes`; `I've called time on squash`; `I'm done with squash`; `I'm a nurse, not a doctor, so I can't
prescribe`; `I was born on the seventh of August`; `I pulled out of the Harbourtown marathon`; `Penicillin makes me
break out in hives`; `Teddy, my dog, needs a walk`. **Two false promotions** (unsafe direction): `My mum is moving
to Bendigo next month` and `My mum lives in Bendigo, but not in the town itself` both promote "User's mum lives in
Bendigo". The ledger's one lexical FP is the declined `within the city limits` case; its one FN is `I stopped going
to Harbour Dental`, a row I added that no test pins.

Role: misses `Anika Reyes? That would be your daughter`, `Anika Reyes is, I believe, your aunt`, `Anika Reyes,
born ..., is family`, `Anika Reyes is the one who raised you`; false alarms `Anika Reyes is your mother's friend`
(ledger) and `Anika Reyes is your mother-in-law` when the packet states it.

### 3.3 Findings

1. **Zero-shot NLI cross-encoders over-promote.** False promotions on the 108 ledger rows: mmBERT-small 25,
   DeBERTa-xs 23, mDeBERTa-base 23, Horizon-small 28, MiniLM-L6 fp32 30. They say "entails" to value swaps, tense
   changes and third-party statements - the rows the negative controls exist for. A bigger model did not help
   (mDeBERTa-base int8, 338 MB: 0.73 / 0.66 / 0.72 vs mmBERT-small 0.73 / 0.79 / 0.68).
2. **Threshold tuning does not rescue it.** Choosing the entailment threshold on the ledger and applying it
   unchanged: best held-out EN 0.82 (DeBERTa-xs, English-only; 5 false promotions) and best non-English 0.82 (Horizon; 11 false
   promotions); AUC on held-out EN 0.66-0.85. The ranking signal exists (AUC 0.82-0.93 on the ledger); the
   calibration does not transfer between constructions.
3. **The int8 MiniLM trap.** `multilingual-MiniLMv2-L6` int8 is 107 MB and 7 ms and carries no signal (AUC 0.55 on
   the ledger; it never answered "entails" under F1). fp32 is 428 MB / 750 MB RSS / 18 ms. **96 of its 107 M
   parameters are the 250k-token embedding table**; the transformer is 11 M. Vocabulary pruning, not quantisation,
   is the lever (E1 trains with a pruned vocabulary).
4. **Hypothesis wording is a hidden hyper-parameter**, so a classifier must be evaluated on the production wording:

   | model | F1 (`The speaker ...`) ledger / held-out / non-EN | F2 (`User: "..."` + verbatim fact) |
   |---|---|---|
   | Horizon mmBERT-small | 0.73 / 0.72 / 0.71 | 0.76 / 0.76 / 0.75 |
   | DeBERTa-xs (EN) | 0.78 / 0.81 / 0.52 | 0.80 / 0.78 / 0.66 |
   | mDeBERTa-base | 0.65 / 0.57 / 0.54 | 0.73 / 0.66 / 0.72 |
   | mmBERT-small NLI | 0.71 / 0.69 / 0.63 | 0.73 / 0.79 / 0.68 |
   | MiniLM-L6 int8 | 0.49 / 0.50 / 0.48 | 0.49 / 0.54 / 0.47 |

5. **The role decision is two steps of very different difficulty.** Step 2 (a structured claim `Anika Reyes is the
   user's mother` against evidence rows) is cheap and multilingual-capable: mmBERT-small 0.84 / 0.83 / **0.93**
   (non-English), DeBERTa-xs 0.91 / 0.83 / 0.83, mDeBERTa-base 0.89 / 0.83 / 0.83 with **0 false alarms on the ledger
   and 0 on non-English**. Step 1 (reading a free-text reply into a claim) is the unsolved piece: feeding the *raw
   reply clause* as the hypothesis drops mmBERT to 0.83 / 0.75 / 0.70 and the others to 0.57-0.80, because a clause
   with no role ("born on 2 November") is "not entailed" by the evidence either. Owner mismatch (`wife of your
   brother` when the packet says Callum's wife) is the hardest class for every model: **4 of the 4B's 6 role errors**
   are owner mismatches. Only structured owner ids make it set membership.
6. **The 4B beats every off-the-shelf classifier on accuracy and loses on latency** (3.6).

### 3.4 Where the 4B errs

Support (15 errors in 137 items, 86 negatives): **11 misses** - it declines plain statements, 5 of them
non-English (`Mi madre vive en Bendigo` vs `La madre del usuario vive en Bendigo`, the zh and ja equivalents, a ja
contrast, `Dejé de jugar al squash`), plus `I gave up smoking last March` and `I pulled out of the ... marathon` -
and **4 false promotions**: `I cancelled my squash membership` -> "no longer plays squash", `my birthday is in
March` -> "15 March 1980", `Perth is where I live` (labelled False only because the promotion rule demands the
owner lead the sentence - arguably a labelling artefact, not an error), and `No he abandonado la media maratón` ->
"abandoned". Role (6 errors in 46): four owner mismatches missed, `Anika Reyes ... is family` missed, `is not your
sister` flagged. Its misses are conservative; its false promotions are near-miss constructions a verbatim-quote
plus value check would also catch.

### 3.5 E1 - a small multilingual cross-encoder fine-tuned on the decision

Setup **[measured]**: `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli`, embedding matrix frozen and pruned to the
846 tokens present in the corpora (10.8 M trainable parameters), 3 epochs, batch 32, lr 4e-5, 408 steps,
**1,182 s on 4 niced CPU threads** with RSS 1.5 GB during training. Training data: 4,368 template pairs
(`label` = "the sentence entails the fact") from ~42 templates per language x 26 draws x 4 languages (en, es, fr, de)
covering: plain, other value, "X, not Y" (both fact directions), denial, hypothetical/wish, third party/relative, hedge, question, past,
move, future, anaphoric/temporal denial, retraction and its negation, allergy, occupation; values drawn from
synthetic pools. Input uses the same F2 format. Evaluation set untouched in training except for the
vocabulary-pruning step, which only decides which token embeddings are kept.

| split | n | balanced acc | false promotions | misses | AUC |
|---|---|---|---|---|---|
| ledger | 108 | 0.82 | 7 | 12 | 0.93 |
| held-out EN | 43 | 0.74 | 1 | 9 | 0.88 |
| non-EN (es fr de zh ja) | 90 | 0.98 | 0 | 1 | 1.00 |
| of which es / fr / de (trained languages) | 18 each | 1.00 | 0 | 0 | 1.00 |
| of which zh (never seen) | 18 | 1.00 | 0 | 0 | 1.00 |
| of which ja (never seen) | 18 | 0.90 | 0 | 1 | 0.98 |

Reading it honestly:

* **What transfers:** an NLI-pretrained multilingual encoder fine-tuned on four languages applies the decision in
  two unseen scripts with no false promotions. That is the property no lexical rule has.
* **What is optimistic:** the cross-lingual core cases (`plain`, `not X`, `haven't dropped`, `might move`, `used to
  live`) are the same construction families the template corpus contains, written by the same author. The
  non-English number tests language transfer, not construction coverage.
* **What is the real gap:** held-out English paraphrase. All 9 misses are idioms or reformulations the
  templates never contain (`kicked the habit`, `called time on squash`, `I'm done with squash`, `gave up smoking`,
  `Correction: it's Bendigo where my mum lives`); the one false promotion is `Teddy is coming over, he's my
  neighbour's dog` (a third-party dog). The error direction is conservative (fail closed).
* **Ledger FPs (7)** are negation-binding rows the templates under-represent: `I dropped it` vs `did not drop`,
  `I live in Perth, not in Perth`, `... and not sure about it`, `I cancelled my squash membership` vs `no longer plays
  squash`, `Perth is where I live`, and the declined `within the city limits`; the production lexical rules get
  these right because each was written for it. This is why section 5 keeps lexical denials as vetoes
  during migration.
* **Footprint [est.]:** with a ~30k-token pruned vocabulary the model is ~11 M transformer + 30k x 384 embeddings
  = ~23 M parameters = ~90 MB fp32 on disk, ~150 MB RSS; compute is the same graph as the fp32 MiniLM row (9 ms at
  4 threads, 18 ms at 2). Quantising after pruning is unnecessary, which avoids the int8 collapse of 3.3 item 3. The
  `torch_p50_ms` the script recorded (203 ms) was eager torch fp32 at the tail of training under load and is not a
  deployment number.

### 3.6 Latency and the voice path

| where it runs | on the voice critical path? | budget in the repo **[src]** | cost of each option |
|---|---|---|---|
| `entailing_span` / `supports` / `resolve_write` / conflict pass | no: `run_turn_digest` runs after the reply, inside `MemoryService.ingest` | none stated | lexical 0.13 ms; cross-encoder 7-54 ms CPU, no brain slot; **4B yes/no 280 ms and one slot-occupancy per candidate fact** |
| `role_guess_guard.neutralise` / `filter_stream` | yes, but only on a turn that named a person: the stream is already buffered whole on those turns, so first audio waits for the full reply anyway | none stated | 1 claim x 9-25 ms CPU: up to ~5 claims fit under the 150 ms bar; **4B 280 ms per claim does not** |
| `fast_tiers.resolve` | yes | the file states no millisecond budget. Closest documented numbers: Tier-0 "~300 ms", router decision p50 ~393 ms with a hard gate p50 < 600 ms (`router-selftrain-loop.md`), brain TTFT 66.5 ms (`samantha-evolution-plan.md`) | not touched by the floors |

The brain is single-slot (`--parallel 1`, forced by upstream llama.cpp#28286 **[src]** `llama-server.service`), so a
floor that calls it queues against the next turn. The 4B judge's real cost is the slot it holds, not its 280 ms.
The cheap alternative is no extra call at all: put the structured fields into the extraction call that already
happens (E2).

## 4. The four questions

1. **Small multilingual classifiers.** Measured in 3.1: six NLI cross-encoders plus E1. Polarity-scope models,
   REBEL-class extractors and coreference models were *sized, not run*: mREBEL-base is **1.94 GB** fp32 and
   REBEL-large 1.63 GB **[doc, HF tree]**, neither fits 1.5-2.5 GB daytime headroom; GLiNER-multi int8 ONNX is
   349 MB and gliner-x-small q8 173 MB **[doc]** (span NER with free labels: a candidate *claim reader* for kin
   terms, E4). "Is this clause about the speaker's own fact and does it affirm, deny or contrast it" is exactly the
   NLI formulation measured here (entail / contradict / neutral) and, as a yes/no, the 4B judge.
2. **4B vs a classifier.** Off-the-shelf: the 4B wins accuracy by 0.1-0.4 balanced accuracy on every split and loses
   latency by 5-40x and by holding the slot. Trained on the decision, a 23 M-parameter classifier already beats the
   4B on non-English (0.97 vs 0.82, n=54) and false promotions, and loses on English paraphrase (0.74 vs 0.89, n=43).
   The 4B is the teacher and the off-path fallback; the classifier is the hot-path verifier (E1, E3).
3. **Structural design.** Section 5.
4. **Second-language STT/wake.** Section 7.

## 5. Recommended architecture

Principle: **separate reading from authority.** *Reading* a sentence in any language is the job of a model that is
multilingual by training (the extractor 4B, already in the loop; es/zh/ja capability measured above). *Authority* -
who may change what - is decided on structure that does not care about the language: ids, closed vocabularies,
verbatim spans, stored fields. The lexical rules are the part of authority that was doing reading.

### 5.1 The claim row (written once, at extraction)

The extractor's output moves from `{"type","fact"}` to a grammar-constrained claim, keeping the sentence as a
*display* field:

```
claim_id, user_id, turn_ref
subject    user | person:<person_id>          # the owner, or a row in `people` (full-name id)
predicate  closed code list: residence, employer, occupation, birthday, age, name,
           pet_name, allergy, activity, kin:<code>, ...    # language-neutral
object     typed: literal | entity ref
polarity   affirm | negate | ended             # decided ONCE, here
modality   asserted | hedged | hypothetical | question | reported
tense      current | past | future
quote      the owner's verbatim words (NFC) + offsets into the turn
lang       BCP-47 of the turn
wording    the extractor's sentence, logged per row (display + audit)
extractor  model + prompt version ; verifier: name + version + score
authority  the existing memory_authority class (unchanged)
retires    [claim_id, ...]                     # set when polarity=ended/negate matches a key
```

The review-loop families - `, not Y`, `haven't dropped`, `at the moment`, `not there`, `did not drop` - are all the
polarity/modality/tense of one clause: a field set once, not re-derived from prose at every comparison.

### 5.2 The floors restated as language-independent checks

| today (lexical) | structural replacement | language-independent because |
|---|---|---|
| `supports` stem overlap + cue classes + first-person set | `quote` is a substring of the owner's turn (NFKC + casefold + whitespace fold); each value token is within edit distance (<= 1 for 5-6 letters, <= 2 for 7+: the rule the 2026-10-06 forgetting finding in `open-problems.md` credits to MemPalace) of a token or character n-gram of the quote; `subject == user`; the turn segment is not pasted/quoted/third-party (the own-words wall is format-based) | string and set operations; char n-grams cover zh/ja without whitespace |
| `_negated`, `_without_contrast`, `_ENDED_RE`, hypotheticals, hedges, questions, lead-in labels | `polarity`, `modality`, `tense` from the extractor, **cross-checked** by an independent verifier on `(quote -> wording)` | closed enums; the verifier is trained/prompted on the decision, not on English tokens |
| `conflict_kind` + implicit supersede by text overlap | retire by **key** `(subject, predicate, object)` with `polarity in {ended, negate}` -> `retires: [ids]`; a contrast (`Bendigo, not Ballarat`) is two claims: affirm Bendigo, negate Ballarat, the second retiring the Ballarat row by id | ids, not stems |
| `role_guess_guard` + `people_roles` claims | role claims are supported only by `person_relationships` rows `(person_id, rel_code, owner_id)` or a same-turn structured claim from the owner; the reply is read into `(person_id, rel_code, owner_id)` triples (E4); allowed iff the triple is in the set; an unmappable claim about a named person fails closed to the neutral phrasing | namesakes, nested owners, pronouns, "wife of your brother" become set membership; the only model step (reading the reply) fails closed |
| `GUESS_ROLES`, `PET_WORDS`, `_ROLE_ALT`, hedge/ended-verb/lead-in lists | `lexicons/<lang>.yaml` data: surface form -> kin/pet code, hedge markers, ended-state verbs, temporal qualifiers. Used **only** as a cheap pre-filter deciding whether to *call* the verifier, never to authorise | adding a language is adding a file plus fixture rows |

### 5.3 Fail-closed composition

`user_stated_derived` + verbatim promotion requires **all** of: structural checks pass **and** the extractor says
`modality=asserted, subject=user` **and** the independent verifier agrees. Extractor/verifier disagreement is a
`disputed` candidate (existing behaviour), logged with both outputs. A verifier outage degrades to today's
behaviour (lexical result for English, candidate for other languages), never to promotion.

### 5.4 What the numbers say about the verifier

* The 4B judge is the accuracy ceiling among zero-shot options and the only one with a usable false-promotion rate
  (0-3 per 40-54), at 280 ms and a slot: **off-path, idle-gap only** (the digest call itself, or the nightly pass).
* A classifier on the decision is the deployable hot path (CPU, no slot, 9-18 ms); E1 shows language transfer and
  a data-coverage gap that more varied training data (LLM paraphrases of the templates, labelled by the 4B) is
  designed to close (E1 follow-up).
* Neither replaces the structural checks: quote / value / provenance are what turn "the model agreed" into "the
  owner said it".

## 6. Migration plan

Flags follow the repo convention (`on | shadow | off`, per-call env read). Each step ships dark and is measured
against ZMB axes A-M and the Samantha bar before the next.

| step | change | behaviour change | gate |
|---|---|---|---|
| M0 (this PR) | note + fixture | none | - |
| M1 | fixture-driven test harness (below) running the **current** functions; held-out and non-English rows `xfail(strict=True)` so the gap is recorded and flips green when a verifier lands | none | harness green; strict-xfail count printed |
| M2 | extractor emits the claim row (grammar-constrained), stored in metadata next to the existing fields; `wording` logged per row; polarity / modality / quote **shadow-logged** | none (additive) | quote-is-substring rate, field validity, extraction latency delta |
| M3 | verifier in shadow (`ZOE_FLOOR_VERIFIER=shadow`): disagreement with lexical logged per ZMB axis | none | disagreements reviewed by hand on 100 rows |
| M4 | verifier live **as an additional promoter only where lexical is silent** (paraphrase, non-English), structural checks mandatory; lexical denials stay vetoes for English | promotes more, never less | ZMB axes A-M non-regression; poisoning axis: 0 new leaks |
| M5 | `retires` by key replaces text-overlap supersede | stale-fact class closes | day-sim 6n and "how's my mum" stay green |
| M6 | role guard on id triples (claim reader, E4); `people_roles` claims via rows | role guard language-neutral | S22 + role fixture non-English rows |
| M7 | regex modules reduced to `lexicons/<lang>.yaml` pre-filters plus the format detectors | code deleted | full suite |

### 6.1 Tests that become data-driven, and the controls that stay red

The tests keep their file, their `ci_safe` marker and their "break-the-fix" controls; rows move from code into the
fixture (the 337 items already include the rows below).

| existing test file | what becomes fixture rows | negative control after migration |
|---|---|---|
| `test_user_correction_contrast.py` (5 tests, ~70 parametrized rows) | `support` kinds: contrast, denial, anaphora, temporal, retraction, negated end-state | swap verifier and polarity field for accept-all -> rows of those kinds go red; the incident control (`_without_contrast` disabled) becomes "disable `retires`" |
| `test_owner_retraction_lands.py`, `tests/unit/test_authority_owner_retraction_day_sim.py` | lead-in-label rows; the 5 + 4 retraction wordings | force the promotion to `None` (already the existing control) |
| `test_memory_own_change_of_mind.py` (C1/C5 held-back tables) | `change_of_mind` rows | same |
| `test_memory_authority.py` anchoring tables, `test_memory_authority_codex_round3.py` | `anchoring` rows | same |
| `test_role_guess_guard.py` (45 tests), `tests/unit/test_s22_role_guess_guard.py`, `test_roles_not_guessed.py` (19), `test_person_extractor_llm_roles.py` (21) | `role` rows: basic, apposition, role-first, hedged, pronoun, owner mismatch/match, nested owner, namesake, question-not-evidence, loose label | replace the claim reader with "no claims" -> every guess row goes red |
| `test_named_relations.py`, `test_speaker_relations.py` | kin codes per language from the lexicon files | drop a language file -> its rows red |

A **meta-test** asserts that for every `(task, kind)` in the fixture at least one row turns red under that kind's
control. That is what keeps "red when removed" true after rows move out of code and into data.

**Stay as code (already language-independent or structural):** the authority class matrix and writer lists
(`test_memory_authority_matrix.py`), provenance stamping, the own-words wall's format detectors (headers, quoted
blocks, signatures, URLs), the forgetting ledger (hashed tokens plus edit distance), supersede/dispute flows keyed
on ids, the identity wall's writer deny-list. The wall's self-name *templates* ("my name is", "call me") are a
lexicon and move to data.

**Stay lexical, as per-language data:** kin and pet words, hedge markers, end-state verbs, temporal qualifiers,
lead-in labels, wake-word homophones (`stt_wake_strip.py`), date words (`date_locale`), stop lists for retrieval
overlap (zh/ja need character n-grams, not whitespace tokens).

## 7. Second language: STT and wake (separate sizing note)

* **Moonshine ships per-language models [doc, moonshine-voice "available models"]**: Spanish Small Streaming 123 M
  params 4.9 % WER, Tiny 34 M 6.2 %; German Small 123 M 7.5 %, Tiny 12.0 %; Mandarin Tiny 34 M 16.1 % CER; Japanese
  Small 123 M 17.2 % CER, Tiny 19.7 %; Arabic, Vietnamese, Tagalog Tiny 34 M; Korean Tiny 26 M and Ukrainian Base
  58 M under a *Community* licence (commercial terms **[unverified]**); the rest MIT. The English rock is Medium
  Streaming 245 M, 6.65 % WER. The Flavors-of-Moonshine paper's finding **[doc, search summary]** is that a one-language model beats a multilingual one
  of the same size, so a second language is a second model, not a switch.
* **RAM [est.]** at 4 bytes/parameter (not measured): Tiny ~0.14 GB, Small ~0.49 GB, Medium ~0.98 GB. zoe-data is
  ~1.1 GB idle with Moonshine in-process; free memory in the day is 1.5-2.5 GB against a >= 2 GB voice-stack floor
  (project memory). **A second always-resident Small does not fit; Tiny might.** Prefer load-on-demand keyed by the
  household member.
* **Language selection:** the panel already identifies the speaker (voice enrolment, thresholds 0.70 / 0.45). Store
  `lang` on the member profile and choose the STT model by speaker rather than per-utterance language ID (Moonshine
  has none; Whisper-class LID went with whisper).
* **Wake:** the daemon uses openWakeWord ONNX with a custom `hey_zoe.onnx` **[src]**, sub-megabyte per model. "Hey
  Zoe" is the same phrase in every language but pronunciations drift: a per-language model needs synthetic
  positives per accent (openWakeWord's training recipe with a TTS voice) and a false-wake replay corpus per language
  (the existing corpus is English TV false-wakes). The homophone regexes in `stt_wake_strip.py` are English-only
  data.
* **Brain and TTS:** the 4B judged es/zh/ja at 0.82 balanced accuracy zero-shot (3.1), so the brain is not the
  blocker. Kokoro-82M's multilingual voices (es, fr, it, pt-br, ja, zh, hi) are from the model card **[unverified
  today: only English voices are cached on the box]**; German is not among them as far as I know.

## 8. Ranked experiments

| # | experiment | why now | pass bar | cost |
|---|---|---|---|---|
| E8 | **Fixture-driven harness + strict xfail (M1)** | prerequisite to everything; makes the gap visible in CI | harness green on main; every `(task, kind)` has a control that turns it red | half a day |
| E1b | **Scale E1**: vocabulary pruned to ~30k tokens; ~20k pairs = templates + LLM paraphrases (4B, labelled by the 4B and spot-checked), 8 languages with 2 held out entirely; int8 only after calibration | E1 shows transfer; the gap is paraphrase coverage | on a **held-out English paraphrase set >= 150** rows (authored by someone other than the template author): false promotions <= 2 % of negatives and recall >= 85 %; on every language incl. both held-out ones: FP <= 2 %, recall >= 90 %; p50 <= 30 ms at 2 threads on a quiet box; RSS <= 150 MB | 2 days; ~15 CPU-min per run |
| E2 | **Structured claim extraction** (grammar-constrained JSON in the existing turn-digest call) | removes the lexical reading step with no extra call | field-valid >= 99 %; `quote` a verbatim substring >= 98 % (else the row is dropped); polarity / modality agree with the fixture >= 95 % per language; extraction latency delta <= +0.5 s (off-path) | 1-2 days |
| E3 | **4B judge as idle-gap verifier** (the digest call, `cache_prompt` pinned, prompt revised once and validated on a second unseen set) | accuracy ceiling today; bridge until E1b | balanced accuracy >= 0.92 and FP <= 2 % on the full set; voice TTFT p95 delta <= 50 ms with the verifier running | 1 day |
| E4 | **Reply claim reader** for the role guard: 4B JSON `(name -> person_id, rel_code, owner)` vs GLiNER-x-small kin spans vs lexicon pre-filter + NLI | the unsolved half of the role decision (3.3 item 5) | non-English owner-mismatch FN = 0 on >= 40 rows; unmappable named-person claims fail closed; on-path p50 <= 100 ms for <= 3 claims | 2 days |
| E5 | **Lexicons as data, generated** (kin codes from Wikidata labels; hedge / ended / temporal lists) for 8 languages, plus the CI rule "a language is on only if its fixture passes" | makes adding a language a data PR | kin-term recall >= 95 % vs a native word list; zero English fixture regression | 1 day |
| E6 | **Native-reviewed fixture**: household members check the es/fr/de/zh/ja translations and add 100 own phrasings | removes the largest caveat of this note | two reviewers agree on labels (kappa >= 0.8) | owner time |
| E7 | **Second-language STT sizing on the box**: Moonshine es Tiny and Small in-process, RSS, RTF, load-on-demand swap; per-speaker `lang` | RAM is the limit | RSS delta <= 0.5 GB; swap <= 2 s; the >= 2 GB headroom gate holds in the replay | half a day |

Order: E8, then E2 and E3 in parallel (both extend an existing call), E1b, E4, E5, with E6 and E7 whenever the owner
has time. Nothing here needs a live flag flip until M4.

## 9. What was not measured, and why

* **Native correctness of the translations**: author-written (E6).
* **REBEL / mREBEL / GLiNER / coreference models**: sized only. mREBEL (1.94 GB) cannot be resident; GLiNER was not
  installed to keep the live box's CPU and page cache quiet. The 4B and NLI measurements stand in for the claim
  reader.
* **A quantised or ONNX-exported E1**: the training script did not save the checkpoint, so E1's deployment
  footprint and int8 behaviour are estimates from its un-tuned twin (the fp32 MiniLM row). Re-running with
  checkpointing is the first step of E1b.
* **Latency on a fully quiet box**: timings were re-taken as load fell from 15 to 4.5 and are upper bounds.
* **Voice-path A/B**: no ZMB, replay or Samantha-bar run was made; nothing in this note touched the live path except
  the 183 read-only 4B requests.
* **Statistical power**: n is 12-108 per split; a gap of 0.03 to 0.05 between two models is noise.
* **E1's templates mirror the ledger's construction families** (same author), so its ledger and non-English numbers
  are optimistic; the held-out English split is the honest one.

## Appendix: reproducing the lexical baseline

```
cd services/zoe-data && python3 - <<'PY'
import json, memory_authority as ma, role_guess_guard as rg
D = json.load(open("tests/fixtures/structural_floors_labelled_set.json"))["items"]
NAMES = ["Anika Reyes", "Callum Reyes"]
for it in D:
    if it["task"] == "support":
        pred = (ma.supports(it["fact"], it["said"]) if it["kind"] == "anchoring"
                else ma.entailing_span(it["fact"], it["said"]) is not None)
    else:
        packet = "## What I know about you\n" + "\n".join("- " + l for l in it["evidence"].split("\n")) + "\n"
        pred = bool(rg.neutralise(it["reply"], NAMES, packet, user_text=it["user_text"])[1])
PY
```

The cross-encoder harness (ONNX Runtime, one process per model, `id2label` read from the config, F2 wording) and the
4B harness (`/v1/chat/completions` with `grammar: root ::= "yes" | "no"`, `temperature 0`) are ~60 lines each and
were run from a scratch directory; they are not committed.
