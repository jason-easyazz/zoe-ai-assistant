---
type: research
title: Arousal detection on the Pi — licence and scope before any build (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or live service changed by this document; nothing downloaded, nothing loaded
description: Owner decision Q18 — "a tiny model scores each clip for arousal off the critical path; high arousal plus 'no, that's not what I said' triggers an apology and a clarify … the licence of the candidate model and the scope (owner only? every member?) need your call before any research time goes in." Jason's answer — research the licence and scope, build nothing. This record settles both. Field half — every candidate that could score arousal from a short clip on a Pi 5 CPU (Wav2Small, audEERING's Dawn, the Odyssey-2024 WavLM baseline, Vox-Profile, emotion2vec+, SenseVoice, LAION's Empathic-Insight-Voice, openSMILE) with the exact weight licence, the training-data terms behind it, size, reported arousal CCC and ARM cost, against the repo's own licence posture. The headline — Wav2Small has no published weights at all (the HF repo is a README that averages two other models, one of them non-commercial), so the "~100 KB model" is a recipe, not a download. Our half — a read-only trace with file:line of where a clip exists (Pi RAM → one POST → Jetson temp file → optional corpus copy), the #1760 shadow-thread slot that already runs a scorer off the path, the device-token claim acceptance that is the template for a per-turn score, where affect is text-only today, and the correction cue that already matches "no, that's not what I said". Then the scope call (the act for everyone, the record for consenting adults, children never, nothing by identity until the speaker gate is live), what the Q16c emotional-safety note must say, a go/no-go table with a measurement plan — and four decisions for Jason.
---

# Arousal detection on the Pi — licence and scope before any build (2026-10-04)

Research date: 2026-10-04. The idea is **P4** in
[companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md) ("arousal
on the Pi, first used for frustration repair"), which re-scopes **W4.1** (the SER bake-off) and pairs
with **W1.5** (conversational repair) in the
[Samantha plan](../architecture/samantha-evolution-plan.md); the plan's **emotional-safety policy**
(`:951-955`, "gates W4 writes") is the governance note the owner numbered **Q16c**. Sources are cited
inline and listed in §8. **[unverified]** marks a secondary source or a number not measured on our
hardware; **[extrapolated]** marks a cost estimate derived from a published number for a different
model or chip.

The owner's decision, verbatim (Q18, 2026-10-04): *"Arousal detection on the Pi (frustration repair).
A tiny model scores each clip for arousal off the critical path; high arousal plus 'no, that's not
what I said' triggers an apology and a clarify. The field evidence is good; the licence of the
candidate model and the scope (owner only? every member?) need your call before any research time
goes in."* Jason's answer: *"Research the licence and scope, build nothing yet."* This record does
exactly that: §1 is the licence call, §3 is the scope call, §4 says what would have to be true, and
nothing was built.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) are untouched;
nothing here adds Jetson RAM; no model was downloaded or loaded; no test suite was run; no live
service was run, restarted or queried; the Pi was not touched; **no audio was decoded** — the only
read of the voice corpus was WAV *headers* for durations (§2.1); no household data is quoted.

## 0. TL;DR

- **Wav2Small cannot be downloaded.** The Hugging Face repo the field audit pointed at
  ([dkounadis/wav2small](https://huggingface.co/dkounadis/wav2small)) contains two files —
  `.gitattributes` and `README.md` — per the HF API; the GitHub repo is one `README.md`. The README
  defines a *teacher*: a Python class that loads two other checkpoints and **averages** them —
  `3loi/SER-Odyssey-Baseline-WavLM-Multi-Attributes` (MIT) and
  `audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim` (CC-BY-NC-SA-4.0, "for research purpose
  only"). The CCC numbers on the card (arousal **0.762** Test-1) are the teacher's, not the 72 K
  student's (**0.66**). The student weights, the 60–120 KB ONNX and the 9 ms figure exist only in the
  paper. "Wav2Small" today is a *recipe* whose licence is the licence of its teacher — and half of
  that teacher is non-commercial.
- **The licence landscape splits cleanly in three.** *Non-commercial / research-only* (no):
  audEERING Dawn (CC-BY-NC-SA + "research purpose only" + "a commercial license … can be acquired
  with audEERING"), Vox-Profile's WavLM dimensional model (Open RAIL, "No commercial use",
  "surveillance" and "privacy-invasive applications" out of scope), openSMILE's open-source build
  ("not allowed … for any sort of commercial product"). *Permissive and dimensional* (yes, but
  **far too heavy to be resident**): the **Odyssey-2024 WavLM-large baseline (MIT, 1.27 GB)** and
  **LAION's Empathic-Insight-Voice heads (CC-BY-4.0, the arousal head alone is a 295 MB `.pth`)
  on a Whisper-Small fine-tune (CC-BY-4.0, 967 MB)** — both are judge/teacher material only.
  *Permissive and categorical* (yes, but not arousal): **emotion2vec+ base** (FunASR model licence,
  commercial permitted with attribution; 1.12 GB `.pt`, 373 MB fp32 ONNX, 9 classes) and
  **SenseVoiceSmall** (same licence, 7 emotion tags, 936 MB `.pt` / 226 MB int8, duplicates
  Moonshine). **No permissively-licensed model under the packet's 300 MB resident line that
  outputs arousal exists today; the only one under it at all is an int8 SenseVoice that does not.**
  The sub-megabyte model the owner's decision assumes has to be *made* — distilled from clean
  teachers — before it can be measured.
- **Household use would be lawful under every licence above, and that is not the point.** CC's
  "NonCommercial" means "not primarily intended for or directed towards commercial advantage or
  monetary compensation" ([CC BY-NC-SA 4.0 §1](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode));
  a wall panel in one home is that. The repo already consumes a non-commercial *data* term for
  household use (Open-Meteo's 10 k/day non-commercial tier in `routers/weather.py`,
  [self-building-skills §5](self-building-skills-2026-10-04.md)). But the repo is **MIT**
  (`LICENSE`), public, with installers; every model on the live path today is permissive (Gemma 4
  E4B-it **Apache-2.0**, Kokoro-82M **Apache-2.0**, Moonshine 0.1.5 **MIT** — the upgrade note
  records that 0.1.5 "removes the non-commercial Community License caveat. No change to Zoe's
  posture"); the speaker-gate record chose its embedder on "no model in the table is
  non-commercial"; and the W4.1 bake-off packet already says **"a non-commercial license is a kill
  criterion for prod (judge-use ok)"**
  ([samantha-evolution-packets.md:212](../architecture/samantha-evolution-packets.md)). That rule has
  never been written down as policy. This record recommends writing it down and applying it: **NC
  and research-only weights never on the live path; allowed as a lab judge; audEERING's card
  statement is narrower than its licence and is honoured as written.**
- **Our system already has the slot, the template and the cue.** The Pi's speaker-ID shadow scorer
  (#1760) runs one inference per turn on a serial daemon thread started *beside* the upload POST,
  writes a metadata-only JSONL row and never delays the turn
  (`zoe_voice_daemon.py:2149-2258`) — that is the off-critical-path slot. The server's
  `_accept_panel_voice_claim` (`voice_tts.py:5726-5751`: device-token callers only, server-side
  threshold, consent re-check) is the template for accepting a per-turn score. The brain message
  already takes labelled blocks after the user's words (`zoe_flue_client.py:1495-1540`), which is
  how Hume injects "top-3 expression labels as text". And `memory_supersede.CUES` has an
  utterance-level `correction` cue whose regex matches "no, that's not what I said" today
  (`memory_supersede.py:100-105`) — it feeds memory supersession, not dialogue repair; W1.5
  (conversational repair) is NOT STARTED. Affect is **text-only** end to end.
- **Scope call: the *act* for everyone, the *record* for consenting adults only, children never,
  and nothing by identity until the speaker gate is live.** An apology-and-clarify is the least
  harmful thing Zoe can do, and guests and children are the ones she mishears most — so the
  in-turn repair should not depend on *who* is speaking. A stored emotional measurement is
  different: it is the "kids' emotional data gets the strictest retention" case the plan already
  names, it needs the enrolment-interview opt-in (W5.3), and under the WA Surveillance Devices Act
  the discard-unknown rule is load-bearing. Since voice-ID is in shadow (the claim is discarded)
  and the only identity today is panel binding, "owner-only" cannot be enforced yet in any case:
  phase 1 is **score every clip in RAM, keep no per-clip value for anyone — only identity-free
  aggregates (histogram bins, CCC accumulators, counts) — act on the cue, write no memory**.
  Per-clip rows exist in exactly one place: an **owner-claimed labelling session** (opt-in,
  panel-bound, time-boxed, started from the owner's phone), because a row with a timestamp and a
  panel id is linkable emotional data the moment a guest or child speaks, whatever the file is
  called.
- **Go/no-go (§4): NO-GO on today's candidates, GO on a measured path.** The path is: write Q16c;
  adopt the licence rule; a bake-off (P-W4.1) in which the only *resident* candidates are
  licence-clean **and under 300 MB** — a re-distilled 72 K student whose teachers are the **MIT
  Odyssey WavLM and LAION's CC-BY heads** (a clean two-teacher average, the paper's own recipe
  with a permissive pair), emotion2vec+ embeddings in **int8** (the fp32 ONNX is 373 MB, over
  the line) plus an in-house head, and a feature-based ridge baseline — judged against a
  ~150-clip owner-labelled set with negative controls. The four conditions (licence OK, <200 ms p95 on the Pi off-path, arousal CCC ≥0.6 on
  in-house clips, zero voice-path latency change) are stated with the instrument for each.

## 1. Field — the candidates and their licences

### 1.1 The three licence questions, and why "household" does not settle them

Every candidate carries three terms, and they are not the same:

1. **The weights' licence** (what the card says: Apache-2.0, MIT, CC-BY-4.0, CC-BY-NC-SA-4.0, Open
   RAIL, FunASR model licence).
2. **The training data's terms**, which bind the *trainer*, not the user of the weights — but which
   explain why the weights carry the licence they do, and which decide whether *we* could
   re-train. The dimensional SER world is built on **MSP-Podcast**, which is distributed under an
   **academic licence** that "has to be signed by someone with signing authority in behalf of the
   university" ([MSP lab](https://lab-msp.com/MSP/MSP-Podcast.html)); the audio itself is
   CC-licensed podcasts (CC-BY 90.86 %), so the lab says "you can use it for commercial product!",
   but a household cannot *obtain* it. EmoBox's survey of the other corpora: "SAIL-IEMOCAP, CREMA-D,
   and MSP-IMPROV do not permit commercial purposes" ([EmoBox](https://arxiv.org/html/2406.07162v1)).
3. **The card's use statement**, which can be narrower than the licence. audEERING's card says the
   model "is for research purpose only" and that "a commercial license for a model that has been
   trained on much more data can be acquired with audEERING"
   ([card](https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim)); its
   open-source page repeats "Our open-source models are for research purposes only"
   ([audEERING](https://www.audeering.com/research/open-source/)). A household is not "research".

Two CC definitions matter. **NonCommercial** "means not primarily intended for or directed towards
commercial advantage or monetary compensation"; **Share** means "to provide material to the public";
and ShareAlike binds only *shared* Adapted Material
([legalcode](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode)). So: a private, unshared
fine-tune on a NC-SA model is lawful in a home; a public repo whose installer pulls NC weights makes
every downstream *commercial* user non-compliant; and a "research only" card statement is a
condition the household does not meet regardless of NC. The field's own precedent is **Omi**, whose
speaker-ID postmortem the P3 record traced — they shipped on a gated CC-BY model and paid for it in
support, not in court. Zoe's policy question is reputational and practical, not legal.

**Where the repo stands today** (read-only, worktree head `4adfe59b`):

| Component | Weights licence | Source |
|---|---|---|
| Repo code | **MIT** ("Copyright (c) 2025 Zoe AI Assistant") | `LICENSE` |
| Brain: Gemma 4 E4B-it | **Apache-2.0** (the Gemma Terms of Use applied to earlier generations only) | [HF card](https://huggingface.co/google/gemma-4-e4b-it); [Gemma terms](https://ai.google.dev/gemma/terms) |
| STT: Moonshine v2 Medium | **MIT** — 0.1.5 "makes the models MIT by default at every size and language … removes the non-commercial Community License caveat. No change to Zoe's posture" | [moonshine-0-1-5-upgrade.md:89-91](../knowledge/moonshine-0-1-5-upgrade.md) |
| TTS: Kokoro-82M | **Apache-2.0** ("Apache-licensed weights … production environments to personal projects") | [HF card](https://huggingface.co/hexgrad/Kokoro-82M) |
| Speaker ID (current + proposed) | resemblyzer Apache-2.0; CAM++ Apache-2.0; "No model in the table is non-commercial" | [speaker-gate-rebuild §1.5](speaker-gate-rebuild-2026-10-04.md) |
| W4.1 bake-off packet | "Record each model's weight license — **a non-commercial license is a kill criterion for prod (judge-use ok)**"; kill criteria >300 MB resident or >150 ms p95 | [samantha-evolution-packets.md:205-216](../architecture/samantha-evolution-packets.md) |
| Reading NC source | "PolyForm Noncommercial … fine to *read*" | [omi-integration-plan.md:96](../architecture/omi-integration-plan.md) |
| NC *data* term in use | Open-Meteo, "non-commercial 10k/day", "same provider and terms as `routers/weather.py`" | [self-building-skills §5](self-building-skills-2026-10-04.md) |

There is **no licence policy document** in `docs/governance/` (it holds infra and security records)
and nothing in `docs/CANONICAL.md` names model licences. The rule exists only as a line in a
packet. §6 asks the owner to make it policy.

### 1.2 The candidate table

"Pi cost" is for one 3–4 s clip on a Pi 5 (Cortex-A76 @ 2.4 GHz, ~5.6 GB free, ~2.7 idle cores per
the P3 record). No candidate has a published Pi 5 number; the extrapolations use two measured
anchors on the same core: an 86 M-parameter audio transformer at **9.4 s per 10 s window fp32
single-thread, 2.09 s int8 four threads** ([dev.to, AST on Pi 5](https://dev.to/syamaner/part-4-edge-deployment-of-an-86m-parameter-audio-transformer-1821)),
and SenseVoiceSmall int8 at **RTF 0.099 single-thread / 0.049 at 3–4 threads on an RK3588's
Cortex-A76** for a 5.592 s clip ([sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)).

| Candidate | Outputs | Params / size | Arousal CCC (reported) | Pi 5 cost per clip | Weights licence | Training-data terms | Household use lawful? | Repo-compatible (live path)? |
|---|---|---|---|---|---|---|---|---|
| **Wav2Small** (student, paper) | A/D/V | 72 K; 60 KB int8 ONNX (paper body) / "120KB" (abstract); 9 MB RAM | **0.66** Test-1, 0.56 IEMOCAP; valence 0.37 | 9 ms per 5 s on a Xeon Gold 6226R → **~50–100 ms** [extrapolated] | **No weights published** (HF repo = README + .gitattributes; GitHub = README) ; paper CC-BY-NC-SA-4.0 (body) vs CC-BY-SA-4.0 (arXiv abstract metadata) | MSP-Podcast v1.7 audio + AudioSet/CochlScene, human labels discarded, teacher A/D/V as ground truth | n/a | **n/a — nothing to install.** As a *recipe* it inherits its teacher's licence |
| **"wav2small" HF card** (= the teacher) | A/D/V | the two checkpoints below, averaged: **661 MB + 1.27 GB** | **0.762** Test-1 (D 0.684, V 0.676); Test-2 A 0.486 | seconds [extrapolated] | README code only; `cc-by-nc-sa-4.0` on the card; loads Dawn (NC) + Odyssey (MIT) | as its two parts | yes (NC) | **No** (half NC, "research only") — judge only |
| **audEERING Dawn** `wav2vec2-large-robust-12-ft-emotion-msp-dim` | A/D/V ≈0..1 | **661 MB** `model.safetensors` (661,375,508 B; ~0.165 B params); ONNX on Zenodo | not on the card; it is the Wav2Small teacher's stronger half [paper: "Dawn"] | **~2–5 s** fp32 [extrapolated from the 86 M anchor ×2] | **CC-BY-NC-SA-4.0**, "for research purpose only", commercial licence sold by audEERING | MSP-Podcast v1.7 (academic licence) | yes (NC) | **No** — judge only, and the card's "research only" wording argues against even that; prefer the MIT judge below |
| **Odyssey-2024 baseline** `3loi/SER-Odyssey-Baseline-WavLM-Multi-Attributes` | A/D/V | **1.27 GB** `model.safetensors` (1,274,490,516 B; 0.3 B, WavLM-large) | card: Test-3 A **0.405**, V 0.577, D 0.577 (Test-3 is the hard out-of-domain partition; the single-attribute cards report dev V 0.709 / Test-3 0.607, dev D 0.584 / Test-3 0.424) | **~5–10 s** fp32, ~2–3 s int8 [extrapolated ×3.5 of the 86 M anchor] | **MIT** | MSP-Podcast (Odyssey-2024 release; academic licence to the trainer) | yes | **Yes** as licence; **no** as resident (4× the 300 MB line even at int8) → **the lab judge and a clean teacher** |
| **Vox-Profile** `tiantiaf/wavlm-large-msp-podcast-emotion-dim` | A/V/D 0..1 | **1.26 GB** `model.safetensors` (1,264,246,784 B; 0.3 B) | not on the card | as above | **Open RAIL** — "No commercial use"; out of scope: "Surveillance", "Privacy-invasive applications", "Clinical or diagnostic" | MSP-Podcast | yes (NC) | **No** |
| **emotion2vec+ base** | 9 classes (angry, disgusted, fearful, happy, neutral, other, sad, surprised, unknown); 768-d embeddings | **1.12 GB** `model.pt` (1,118,245,678 B); community ONNX **373 MB** fp32 (373,159,295 B; ≈93 M params) → **~95 MB int8** would have to be *made* [extrapolated] | n/a (categorical) | **~1–2.5 s** int8 [extrapolated from the 86 M anchor] | **FunASR Model License** ("other / model-license" on the card): use, copy, modify, share permitted; attribution and model-name retention required; "provided for reference and learning purposes only" disclaimer; maintainers confirm commercial use | seed: EmoBox academic corpora; base: 4,788 h "filtered large-scale pseudo-labeled data" of unstated provenance | yes | **Yes** as licence; **under 300 MB only as int8** — no arousal; a 768-d embedding + an in-house arousal head is the usable shape |
| **SenseVoiceSmall** | ASR + 7 emotion tags (`HAPPY SAD ANGRY NEUTRAL FEARFUL DISGUSTED SURPRISED`) + 8 event tags | **936 MB** `model.pt` (936,291,369 B; "similar to Whisper-Small"); sherpa-onnx **226 MB int8** | n/a (categorical) | RTF 0.099 (1 thread) / 0.049 (3–4) on A76 → **~0.3–0.5 s** | code MIT; weights **FunASR Model License** (commercial OK per maintainers) | FunAudioLLM's own 400 k h, unstated | yes | **Yes** as licence; the only permissive file under 300 MB in this table; **no** as design — categorical, and it duplicates Moonshine (the plan's own "fallback only") |
| **LAION Empathic-Insight-Voice-Small** | 58 MLP heads incl. **arousal** and **valence** on Whisper-encoder embeddings | **not small**: `model_Arousal_best.pth` is **295 MB** (294,944,245 B; each of the 58 heads is ~295 MB — the documented 1500×768→64 projection alone is ~74 M params) on **BUD-E-Whisper = Whisper-Small fine-tune, 967 MB** `model.safetensors` (966,995,080 B; 241.7 M params) → **≈1.26 GB fp32, ≈315 MB even at ideal int8** | **none reported** for arousal (the paper reports 40-class alignment) | Whisper pads to 30 s → encoder cost is fixed: **~2–4 s** int8 [extrapolated] | heads **CC-BY-4.0**; backbone **CC-BY-4.0** | "LAION's Got Talent" ~5,000 h *synthetic* voice acting + ~5,000 h in-the-wild | yes | **Yes** as licence; **no** as resident (over the 300 MB line before runtime overhead) → **a second clean teacher / judge** beside Odyssey; acted/synthetic training is the domain-gap risk the field audit flagged |
| **openSMILE eGeMAPS + a ridge head** | hand-crafted prosody features → a regressor we train | KB | depends on our labels | ms | open-source build: free for "research purposes and personal use"; "not allowed … for any sort of commercial product" (audEERING dual licence) | our own labels | yes (personal) | **No** for the extractor (same NC class); the *approach* is fine with permissive feature code (librosa, ISC) |

Sources per row: Wav2Small [paper HTML](https://arxiv.org/html/2408.13920v1), [abstract](https://arxiv.org/abs/2408.13920),
[HF API](https://huggingface.co/api/models/dkounadis/wav2small), [README](https://huggingface.co/dkounadis/wav2small/raw/main/README.md),
[GitHub](https://github.com/dkounadis/wav2small); Dawn [card](https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim),
[audEERING](https://www.audeering.com/research/open-source/); Odyssey [multi-attribute card](https://huggingface.co/3loi/SER-Odyssey-Baseline-WavLM-Multi-Attributes),
[valence card](https://huggingface.co/3loi/SER-Odyssey-Baseline-WavLM-Valence), [dominance card](https://huggingface.co/3loi/SER-Odyssey-Baseline-WavLM-Dominance),
[challenge paper](https://www.isca-archive.org/odyssey_2024/goncalves24_odyssey.pdf); Vox-Profile [card](https://huggingface.co/tiantiaf/wavlm-large-msp-podcast-emotion-dim),
[paper](https://arxiv.org/pdf/2505.14648); emotion2vec+ [card](https://huggingface.co/emotion2vec/emotion2vec_plus_base),
[README front-matter](https://huggingface.co/emotion2vec/emotion2vec_plus_base/raw/main/README.md), [repo](https://github.com/ddlBoJack/emotion2vec),
[FunASR MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE), [community ONNX](https://huggingface.co/pankotaro/emotion2vec-plus-base-onnx);
SenseVoice [repo](https://github.com/FunAudioLLM/SenseVoice), [card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall),
[sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html); LAION [heads](https://huggingface.co/laion/Empathic-Insight-Voice-Small),
[backbone](https://huggingface.co/laion/BUD-E-Whisper), [EmoNet-Voice paper](https://arxiv.org/html/2506.09827v2);
openSMILE [about](https://audeering.github.io/opensmile/about.html), [Wikipedia](https://en.wikipedia.org/wiki/OpenSMILE) [secondary].
File sizes are the published checkpoints as listed by the HF API (`?blobs=true`) on 2026-10-04:
[Dawn](https://huggingface.co/api/models/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim?blobs=true),
[Odyssey](https://huggingface.co/api/models/3loi/SER-Odyssey-Baseline-WavLM-Multi-Attributes?blobs=true),
[Vox-Profile](https://huggingface.co/api/models/tiantiaf/wavlm-large-msp-podcast-emotion-dim?blobs=true),
[emotion2vec+ base](https://huggingface.co/api/models/emotion2vec/emotion2vec_plus_base?blobs=true),
[emotion2vec+ ONNX](https://huggingface.co/api/models/pankotaro/emotion2vec-plus-base-onnx?blobs=true),
[SenseVoiceSmall](https://huggingface.co/api/models/FunAudioLLM/SenseVoiceSmall?blobs=true),
[LAION heads](https://huggingface.co/api/models/laion/Empathic-Insight-Voice-Small?blobs=true),
[BUD-E-Whisper](https://huggingface.co/api/models/laion/BUD-E-Whisper?blobs=true). "int8" sizes are
quarter-of-fp32 estimates unless a published int8 file exists (only SenseVoice's does).

### 1.3 What the table says

- **The owner's premise — "a tiny model" — has no licence-clean instance yet.** The only sub-MB
  dimensional model in the literature is Wav2Small, and it is unpublished; its 0.66 was reached by
  distilling from a 0.762 teacher that is half non-commercial. Everything that *is* downloadable and
  outputs arousal is 0.66–1.27 GB on disk and seconds per clip on the Pi.
- **Permissive and dimensional exists in exactly two places, and neither fits in 300 MB**: the
  Odyssey MIT baseline (1.27 GB) and LAION's CC-BY heads (295 MB for the arousal head *alone*, on a
  967 MB backbone — ≈315 MB even at ideal int8, before runtime overhead). Both are **judge and
  teacher material**, which is good news for the recipe (§1.4: a clean two-teacher average) and
  bad news for "ready now": nothing permissive and dimensional can be resident on the Pi as
  published. LAION's heads carry the extra caveat that they were trained on synthetic acting with
  no published arousal CCC — the companion-field record's warning that acted data generalises
  poorly to natural short commands applies in full.
- **Permissive and small exists only as categorical** (emotion2vec+, SenseVoice), and a
  category→arousal mapping (angry/fearful/surprised high, sad/neutral low) is a guess the measurement
  plan would have to validate, not a model.
- **The field evidence for the *use* is unchanged**: acoustic features predict arousal and
  dominance well and valence poorly ([Springer 2025 survey](https://link.springer.com/article/10.1007/s12243-025-01069-1);
  Interspeech 2025 challenge: the valence gains came from *text* models, baseline valence CCC 0.638
  → 0.734 with a text ensemble, [paper](https://www.isca-archive.org/interspeech_2025/naini25_interspeech.pdf));
  Alexa's shipped frustration detector is "one algorithm analyzes your words and looks for words
  associated with frustration such as 'no', another … your tone of voice, and a third … their
  combined data", first for music errors in 2020, and "No, Alexa" makes her "apologize and ask you
  to clarify" ([OneZero](https://onezero.medium.com/heres-how-amazon-alexa-will-recognize-when-you-re-frustrated-a9e31751daf7),
  [KnowTechie](https://knowtechie.com/amazons-alexa-knows-when-youre-pissed-off-with-its-results/) — both
  secondary); Hume EVI feeds "the top-3 expression labels as text" into the LLM's user message
  ([FAQ](https://dev.hume.ai/docs/speech-to-speech-evi/faq)). The design in Q18 is the Alexa
  combiner with Hume's text injection, and both are right.
- **The corpus is shorter than the benchmarks.** MSP-Podcast clips are 2.75–11 s; a header-only read
  of Zoe's 1,312-WAV corpus gives **p50 3.04 s, p25 2.24 s, p90 5.92 s, 43 % under 2.75 s** (§2.1).
  Any CCC we measure on in-house clips will be lower than the paper's for that reason alone, which is
  why §4 sets the bar on *our* clips and not on MSP-Podcast.

### 1.4 The licence call

1. **No non-commercial or research-only weights on the live path.** This is the packet's kill
   criterion made policy. It excludes Dawn, the "wav2small" teacher ensemble, Vox-Profile and the
   open-source openSMILE build from anything the panel runs. Household use would be lawful; it is
   still the wrong posture for a public MIT repo with installers, and it contradicts audEERING's own
   stated intent.
2. **NC and research-only models may be lab judges, with the card's wording honoured.** The packet
   allows "judge-use". Because audEERING's card says "research purpose only" and our bake-off *is*
   research, Dawn as a judge is defensible — but the **MIT Odyssey baseline is the better judge and
   the only acceptable teacher**, because a student distilled from Dawn's outputs inherits an
   argument about whether model outputs are "Adapted Material" that nobody needs to have.
3. **A re-distilled student is licence-clean only if the teacher and the audio are.** The paper's
   recipe discards MSP-Podcast's human labels and uses the teacher's A/D/V on *any* audio
   (MSP-Podcast + AudioSet + CochlScene). We cannot obtain MSP-Podcast (academic signature), but the
   recipe does not need it: permissively licensed speech (LibriSpeech CC-BY-4.0, Common Voice
   CC0, the in-house corpus) plus permissive teachers yields a student whose weights we own. The
   paper's teacher was itself an *average of two models*; the clean equivalent is the **MIT
   Odyssey baseline averaged with LAION's CC-BY arousal head** — both too big to be resident
   (§1.2), both fine on the Orin in a brain-stopped training window or on a laptop. The risk is
   quality — the paper's 0.66 came from a 0.762 teacher; the MIT model alone reports Test-3
   0.405 and LAION reports nothing for arousal (and whatever they score on our clips is what §4
   measures first).
4. **The training-data terms are a reason, not a blocker.** They explain why every dimensional
   model is either NC or trained by someone who signed the academic licence; they do not bind a user
   of MIT weights.

## 2. Our system — read-only trace (file:line, worktree head `4adfe59b`)

### 2.1 Where a clip exists, and for how long

- **Pi, capture.** `record_command` (`scripts/setup/zoe_voice_daemon.py:1631-1699`) records at
  `SAMPLE_RATE` 16 kHz (`:132`), drops anything under 0.3 s (`:1696`) and returns WAV **bytes** via
  `_frames_to_wav` (`:1621-1629`). The bytes live in daemon memory for the turn; nothing on the Pi
  writes them to disk (the barge-in seed is also in-memory, `:1213`). The ambient thread's own rule
  is explicit: "Raw audio is never stored — only transcripts are kept" (`:1425-1433`).
- **Pi, upload.** `_do_single_turn_stream` (`:2469-2516`) base64-encodes the WAV and POSTs
  `{audio_base64, panel_id[, speculative, turn_id][, conversation][, voice_user_id, voice_score]}`
  to `/api/voice/turn_stream` (`:2500-2516`). The daemon talks to exactly these server endpoints:
  `/api/voice/wake`, `/transcribe`, `/turn`, `/turn_stream`, `/turn_stream/speculation`,
  `/identify`, `/speak`, `/ambient` — there is no per-turn metadata side channel today.
- **Pi, the off-path slot (#1760).** The speaker-ID shadow scorer is the pattern Q18 asks for.
  `_speaker_claim_to_attach` (`:2246-2258`) hands the WAV to `_start_shadow_scoring`
  (`:2168-2225`): one daemon thread per turn, each joining its predecessor so "there is only ever ONE
  resemblyzer inference in flight" and rows "land in turn order"; started "just before the POST"
  because scoring *before* it used to cost "a median 0.54 s (max 1.12 s) of dead time before every
  reply" (`:2149-2156`, [panel-ttfa-breakdown](../knowledge/panel-ttfa-breakdown-2026-09-28.md));
  a bounded 3 s drain at shutdown (`:2165`, `_drain_shadow_scoring`); inline fallback with a 10 s
  cap if a thread cannot start (`:2168-2171`). The row it writes
  (`~/.zoe-voice/speaker_shadow_metrics.jsonl`, `:262-265`) carries
  `{boot, seq, ts, panel_id, user_id, score, n_profiles, source, truth}` — "metadata ONLY, never
  audio bytes or embeddings" (`:2018-2028`). **The one thing the slot cannot do is deliver its
  result into the same turn's payload**: "there is no follow-up path to deliver a late claim"
  (`:2252-2256`). That constraint shapes the repair design (§2.4).
- **Jetson, STT.** `/turn_stream` (`services/zoe-data/routers/voice_tts.py:4977`) decodes the
  base64 into a `NamedTemporaryFile`, measures its duration, calls `_transcribe_audio` and unlinks
  the file in `finally` (`:5030-5040`). `_transcribe_audio` (`:2683-2694`) runs Moonshine and then
  `_maybe_capture_stt` (`:2660-2681`): **only when `ZOE_VOICE_SAVE_AUDIO` is set**, the WAV is
  copied to `ZOE_VOICE_SAMPLE_DIR` or `/home/zoe/.zoe-voice-samples` as `HHMMSS_mmm.wav` and a
  `STT_CAPTURE file= moonshine=` line is logged; instrument callers (`replay-` panel ids) pass
  `capture=False` so the corpus does not re-ingest its own replays. That directory is the
  "permanent regression corpus" the memory rule protects. A header-only read on 2026-10-04 (no
  audio decoded): **1,312 files, 16 kHz and 24 kHz, p10 1.84 s / p25 2.24 s / p50 3.04 s /
  p75 4.08 s / p90 5.92 s / max 9.60 s; 13 under 1 s, 191 under 2 s, 565 (43 %) under 2.75 s, 202 at
  or over 5 s.** The P3 record adds that it holds two acoustic clusters (A ≈78 % near-field owner
  commands; B mostly other voices and TV) — the ready-made negative control.
- **So:** a clip is in Pi RAM for the turn, in a Jetson temp file for the STT call, and on the
  Jetson's disk *only* under an operator flag that exists for regression replay. There is no
  per-member clip store, and the biometric policy forbids one for identity ("No raw audio",
  [biometric-retention-policy §1](../knowledge/biometric-retention-policy.md)).

### 2.2 Where per-turn metadata reaches the brain

- **Accepting a panel-computed score.** `voice_command` (`voice_tts.py:3020-3041`) reads
  `identified_user_id` or, failing that, `_accept_panel_voice_claim(payload, caller)`
  (`:5726-5751`): honoured only from `caller["source"] == "device"` (a device token, never a browser
  session), the raw score is compared to the **server's** threshold ("a panel cannot make itself
  more trusted than the server allows"), and the claimed user must still hold consent in the DB
  ("fail closed"). `/turn` and `/turn_stream` forward only the `voice_user_id`/`voice_score` keys
  (`:4957`, `:5179`). A `voice_arousal` field would be accepted by the same three rules.
- **The deterministic tiers run first.** After the opener/ender fast-path (`:5107-5140`),
  `fast_tiers.resolve(text, effective_user, session_id, channel="voice", …)` (`:4155-4165`) answers
  weather/time/lists/calendar without the brain; only a miss reaches the brain lane. A repair cue
  must be checked *before* this (a misheard "turn off the lights" that gets executed again is the
  failure the repair exists to stop).
- **The brain message.** `zoe_flue_client.py:1495-1540` assembles, in order: the recall block, the
  pending-offer block, the user's words, the **continuity block** (after the words — "closest to the
  reply, where a 4B model acts on it"), the day brief, the raise block. Each labelled pair is
  elided from history by the tables the pull-not-push record lists
  (`zoe_flue_client.py:654`, `labs/flue-zoe-brain-2x/src/context-blocks.ts:32`). A `[Voice]` line
  ("the person sounded tense") is one more pair, Hume-style. The core lane's marker is
  `[The user just said]` (`zoe_core_client.py:800`).
- **Identity on the turn** is the chain the P3 record traced: daemon claim (after threshold +
  consent) → `_bound_user` → `_panel_recent_user` → `_panel_default_user` → caller → guest
  (`voice_tts.py:3198-3236`). With speaker ID in shadow the claim is discarded, so **today "who" is
  the panel binding** — which in one household with one panel is the owner in practice and nobody
  in particular in principle.

### 2.3 Affect today is text-only, end to end

- **Turn time.** `memory_gate.extract_affect` (`memory_gate.py:440-456`) is a regex over the user's
  *own* words for a first-person feeling ("anxious", …) and returns the sentence it came from.
  `memory_digest._affect_for_fact` (`memory_digest.py:403-431`) keeps that feeling on the fact drawn
  from that sentence (`metadata={"affect": …}`, `:617-651`) — the "affect-keeping digest". The
  **bare-mood rule** `_names_a_thing` (`:390-400`) distinguishes "anxious about the job interview"
  (a thing a check-in can ask about) from "feeling a bit rough lately" (what the person is saying
  right now; never a check-in focus). The `correction` cue is read here too (`:440`,
  `memory_supersede.utterance_cue`).
- **Nightly.** The emotional pass (`:895-935`) asks the brain for moments with `emotion ∈ {joy,
  excitement, anxiety, sadness, frustration, pride, relief, love, grief, other}` and
  `significance ≥ 2`, and ingests them as `memory_type="emotional_moment"` with
  `tags=["emotional", emotion]`. The emotional follow-up trigger
  (`proactive/triggers/emotional_followup.py:33-52`) acts on `valence ∈ {neg, mixed}`,
  `intensity ≥ 0.6`, 20 h–7 d old, once per moment ever, flag `ZOE_EMOTIONAL_FOLLOWUP_ENABLED`.
- **Continuity.** `_continuity_context_block` (`zoe_flue_client.py:982-996`) fires only when
  `is_continuity_turn` says the message is a first-person emotional/state statement, for a real
  (non-guest) user id, and never beside a recall block.
- **Consequence for Q18:** an arousal score has three possible consumers — (a) the in-turn repair
  (this decision), (b) a feature of `is_continuity_turn` / the "flat 'I'm fine' = low arousal +
  neutral text" fusion (W4 proper, *writes* memory), (c) the W11 delivery profile. Only (a) is in
  scope here, and (a) **writes nothing**. That is what makes the scope call in §3 possible.

### 2.4 Where "apology + clarify" would attach

- **The cue exists.** `memory_supersede.CUES` (`memory_supersede.py:75-105`) ends with
  `correction` — utterance-only (`fact_level=False`), pattern
  `^\s*(?:actually|wait|sorry|no)\b[^.!?]{0,80}?\b(?:wrong|meant|mistake|not(?!\s+(?:sure|really|…)))\b
  | i (?:got|had) (?:that|it) wrong | i was wrong | my (?:mistake|bad) | that'?s (?:wrong|not right|incorrect)
  | i meant | correction`. "No, that's not what I said" matches on `no` + `not what`. It was added
  2026-10-04 because the word-overlap dedup "dropped the corrected fact as a duplicate of the one it
  replaces" — it is tuned for **memory supersession**, and its one consumer is `memory_digest.py:440`.
  A repair needs a sibling table, not this one: "no, the *other* one", "that's not it", "I said
  *kitchen*" are repair cues with no memory meaning, and "no, not that" is excluded here by design
  (`not that` is in the negative lookahead).
- **W1.5 is the tracked home and is NOT STARTED** ([plan :936-940](../architecture/samantha-evolution-plan.md),
  `:1033`): "The voice path currently hardcodes `confidence=1.0` — no mishearing signal exists …
  have Zoe **ask** ('say that again?') instead of mis-executing." Q18 adds the second input the plan
  did not have: the voice itself.
- **The shape that exists for a deterministic spoken ack before the brain** is the conversation
  opener fast-path (`voice_tts.py:5107-5140`): a phrase synthesised through
  `_synthesize_kokoro_sidecar` and streamed as the first chunk, then flags on the done frame. An
  apology ack ("sorry — say that again?") is that shape. But a *clarify* needs the previous turn
  (what Zoe did, what she heard), so the repair is **not** a brain-skipping fast path; it is a
  short ack plus a `[REPAIR]` block on the brain message carrying Zoe's last reply and transcript,
  and a hold on the deterministic tiers for that turn.
- **The timing problem the off-path slot creates.** The Pi's scorer result cannot ride the payload
  it was started beside (§2.1). Three honest options for a future build record, none chosen here:
  (1) score **before** the POST — the #1760 regression, acceptable only if the measured p95 is
  ≤30 ms; (2) a side channel `POST /api/voice/turn_meta {turn_id, arousal}` that the server joins
  by `turn_id` (the speculation path already carries one, `zoe_voice_daemon.py:2503`) with a budget
  of **zero extra waiting** — STT takes ~0.3 s and the brain ~1–2 s, so a 100 ms score arrives in
  time or the turn proceeds without it; (3) score on the **Jetson** from the uploaded WAV — a 60 KB
  model would cost nothing there, but the plan's "nothing new in-process in zoe-data" rule and the
  W3 RAM gate are exactly why Q18 says "on the Pi". Option (2) keeps the owner's "off the critical
  path" literally true.
- **The repair fires on a follow-up turn only.** "No, that's not what I said" is a reply to Zoe's
  previous reply; the daemon knows it is inside the 5 s follow-up window (`FOLLOW_UP_LISTEN_S`,
  `:322-326`) or an open conversation (`payload["conversation"]`). A cold-wake "no …" is not a
  repair.

## 3. Scope — owner only, or every member?

### 3.1 What each choice implies

| | **Owner only** | **Every member** | **Everyone who speaks (incl. guests, kids)** |
|---|---|---|---|
| Enrolment / identity needed | yes — but the speaker gate is in **shadow** (claim discarded, [P3 §2](speaker-gate-rebuild-2026-10-04.md)); "owner" today = whoever the panel is bound to (`voice_tts.py:3198-3236`). Enforceable only after P3 graduates | yes, per member, with the enrolment interview (W5.3, NOT STARTED) as the consent moment | none — which is the point: the repair helps most where identity is weakest |
| Consent / privacy | the owner's own call | per-member opt-in, revocable, the biometric deletion path reused | guests never consented; **WA Surveillance Devices Act 1998** s5 (listening device + private conversation; consent of all principal parties) makes the discard-unknown rule "load-bearing legally, not only ethically" ([omi plan :268-281](../architecture/omi-integration-plan.md)); a transient in-RAM score that is never retained is the question the plan says to put to a lawyer, not to assume |
| Children | n/a | "kids' emotional data gets the strictest retention" (plan `:951-955`); kid mode (W5.4) NOT STARTED, waits on W5 | a child who is misheard benefits most from an apology; a child's stored arousal trace is the thing the policy must forbid |
| Retention | scores, never clips; the corpus flag is an operator instrument, not a member store | per-member scores only with consent; never clips | **nothing** by identity |
| What Q16c must cover | little | opt-in wording, deletion, kids | the act/record split, the guest rule, the legal question |

### 3.2 The recommendation: split the *act* from the *record*

- **The act — apologise and clarify in the turn — is for everyone.** It stores nothing, it is
  the least harmful response Zoe has, and the people she mishears most (guests, children, anyone
  far from the mic) are the ones "owner only" would exclude. An apology does not need to know who
  you are. (Apple's HomePod says "Okay <name>" before *personal data*, not before saying sorry.)
- **The record — any score kept past the turn, any feed into emotional memory, continuity or
  the follow-up trigger — is for consenting adult members only, and for nobody until the speaker
  gate is live.** Children's scores are never persisted and never reach memory, whatever kid mode
  later allows. Guests' scores are never persisted (the discard-unknown rule). This is the
  biometric policy's own shape — embeddings only, kept until deleted, consent revocable, matching
  filters non-consenting rows in SQL — applied to a non-biometric but emotional datum.
- **Phase 1 therefore is: score every clip on the Pi, in RAM; keep no per-clip value for anyone;
  act on the cue; write no memory.** What the shadow phase *may* keep is identity-free by
  construction: per-panel **aggregates** updated in place — a 10-bin arousal histogram, running
  CCC/correlation accumulators against the repair cue, and counts (`turns`, `cue_fired`,
  `repair_fired`, `scorer_skipped`) — with no timestamp, no sequence number and no row per turn.
  A per-clip row (`seq, ts, panel_id, duration_s, arousal, …`) is **linkable retained emotional
  data the moment a guest or a child speaks**, because the speaker gate cannot yet tell them from
  the owner; calling the file "anonymous" would not make it so, and it would contradict the rule
  two bullets up. Per-clip rows are therefore allowed in exactly one place: an **owner-claimed
  labelling session** — opt-in, started from the owner's phone, bound to the one panel, time-boxed
  (≤2 h), rows written only while the claim is open, and the owner told on the panel that it is
  open. That session is also where the §4 label set comes from. Per-clip rows for anyone else wait
  until identity *and* consent are enforceable (P3 live + W5.3 opt-in).
- **"Owner only" as the owner phrased it is the right instinct for the *record* and the wrong
  boundary for the *act*.** That is the one place this record disagrees with the question as
  asked, and §6 puts it to Jason plainly.

### 3.3 What the Q16c emotional-safety note must say

The plan requires "a short normative doc under `docs/governance/`, referenced by the W4/W6 gates";
none exists ([personality-identity-layer §3.4](personality-identity-layer-2026-10-04.md) confirms
`docs/governance/` holds infra and security records only). For arousal it must state, in this order:

1. **What the signal is and is not.** Arousal from prosody — activation, not emotion, not truth,
   not diagnosis. Never a lie detector, never a crisis detector, never used for surveillance of
   anyone (the Vox-Profile card's own exclusions, adopted as ours regardless of model).
2. **The act/record split** (§3.2), verbatim: repair for anyone; retention and memory for
   consenting adults only; children never; guests never.
3. **Retention.** Scores, never clips; **no per-clip score is kept for anyone** before identity and
   consent are enforceable — the shadow phase keeps only per-panel aggregates with no timestamps;
   per-clip rows exist only inside an owner-claimed, time-boxed labelling session the owner started
   and can see is open; deletion follows the biometric path (one request removes everything
   derived); the `ZOE_VOICE_SAVE_AUDIO` corpus is an operator instrument outside this policy and
   stays owner-only.
4. **Consent.** Opt-in in the enrolment interview (W5.3), per member, revocable, visible in
   settings beside Speaker Identity; absence of consent = the act only.
5. **No therapy claims; crisis language takes the deterministic escalate-to-human path** (the
   plan's words) — and an arousal score **never** triggers that path on its own.
6. **Transparency.** A member can ask what Zoe heard ("did I sound annoyed?") and see their own
   counters on the W16 scoreboard; nothing is inferred silently about a person who cannot see it.
7. **The licence rule** (§1.4): no non-commercial or research-only weights on the live path; judges
   in the lab only.
8. **The legal question to put to counsel before any ambient extension**: whether a transient,
   unretained in-RAM score on a non-consenting speaker is "recording" under the WA SDA. Until
   answered, scoring runs only on wake-word turns (a party who addressed the device), never on
   ambient audio.

## 4. Go / no-go — what would have to be true, and how it would be measured (BUILD NOTHING)

| Condition | Instrument | Pass | Negative control (must go RED) | Status today |
|---|---|---|---|---|
| **Licence OK** | the §1.2 table; the rule in §1.4 written into `docs/governance/` | resident weights permissive (MIT / Apache / CC-BY / FunASR); card carries no "research only"; teacher of any distilled student permissive; feature code permissive | a Dawn-distilled student submitted to the gate is rejected by the rule as written | **NO-GO** for every downloadable dimensional model; GO path = Odyssey-MIT teacher, LAION CC-BY heads, emotion2vec+ embeddings, librosa features |
| **<200 ms p95 on the Pi, off-path** | P-W4.1 bake-off harness on the Pi (not the Orin): per-clip p50/p95 over the corpus, steady RSS, threads pinned to 1; the shadow-thread chain pattern | p95 < 200 ms *and* RSS < 300 MB (the packet's kill line) | run the same harness with the model replaced by a 0.3 B judge → must fail the latency line | unmeasured; extrapolations say the 72 K student passes by 2–4×, LAION/emotion2vec+ fail by 10× unless int8 and multi-threaded |
| **Arousal CCC ≥0.6 on in-house clips** | a label set: ~150 corpus clips from cluster A rated 1–5 for arousal by the owner (one sitting, on the phone, never on the panel — VISION 8), plus the MIT judge's score on the same clips; CCC human-vs-judge reported **first** | candidate CCC vs human ≥0.6; and candidate ≥ judge − 0.05 | shuffle the human labels → CCC ≈ 0; score the cluster-B/TV clips → the distribution must differ from cluster A, or the model is reading channel, not voice | unmeasurable until the label set exists — **this is the chore the owner has to say yes to** (§6). If human-vs-judge CCC itself is <0.6 on 3 s commands, the bar is restated as AUC for "high arousal" (binary), honestly |
| **Zero voice-path latency** | the panel TTFA breakdown and the replay gate with the scorer on vs off (same corpus, same day); `voice_stage_seconds` p50/p95 | TTFA p50 and p95 within noise (±20 ms) of off; no `skipped (predecessor still running)` rows | start the scorer *before* the POST (option 1 in §2.4) → TTFA must rise by the scorer's latency, proving the instrument sees it | the #1760 slot already proves the pattern for resemblyzer (0.54 s moved off-path) |
| **The repair cue is precise** | a repair-cue table with a unit test over the corpus transcripts: cue rate per 100 turns, and the day-sim's "misheard command" seed | ≤1 false repair per 100 non-correction turns; ≥80 % of seeded corrections caught | remove the arousal term → the cue alone must over-fire on "no" turns (that is the Alexa reason for the combiner) | not built; the memory cue is the wrong table (§2.4) |
| **Q16c exists and is referenced by the W4 gate** | the governance doc; the plan's checklist line `:1037` | merged before any flag is created | — | NOT STARTED |

**Measurement plan, in order, no code on the live path:** (1) Q16c + the licence rule
(docs only); (2) the owner's 150-clip label set (a one-off, ~30 min); (3) the bake-off on the
**Pi** in an isolated venv — **resident** candidates (all permissive, all under 300 MB): *a* a 72 K
student re-distilled with the **Odyssey-MIT + LAION-CC-BY two-teacher average** on permissive
audio (this needs a training window; the dev-box rule allows stopping the brain for it, or it runs
on a laptop), *b* emotion2vec+ base embeddings **quantised to int8** (the 373 MB fp32 ONNX is over
the line and the int8 file does not exist yet) + a ridge head fitted on the label set
(cross-validated), *c* librosa prosody features + ridge (the floor); **judges** (not resident) =
Odyssey WavLM-large (MIT, 1.27 GB) and the LAION arousal head (CC-BY, 295 MB + 967 MB); report
CCC/AUC, p95, RSS per candidate with the negative controls; (4) only if one candidate passes all
four lines, a **build record** for P4 + W1.5 (flags `ZOE_VOICE_AROUSAL=off|shadow|active`,
`ZOE_VOICE_REPAIR`, a one-week shadow window that writes **aggregates only** — per-clip rows only
inside an owner-claimed labelling session (§3.2) — then the repair).

## 5. Go / no-go against VISION

| Principle | Verdict | Why |
|---|---|---|
| 1 Rocks fixed | GO | no change to Gemma / Moonshine / Kokoro; the score is a text line to the brain |
| 2 Local, private, fast | GO with the §3 split | on the Pi, in RAM, off-path; nothing retained by identity; Hume/Gemini-style cloud affect is excluded by this principle alone |
| 3 Lab-prove before prod | GO | bake-off on the Pi; label set with negative controls; flag-dark; shadow week |
| 4 Build it to STICK | GO | the four conditions each have an instrument and a control; the licence rule becomes CI-visible policy |
| 5 Capture, don't lose | this record | the finding that Wav2Small is a recipe is pinned here so no bake-off chases a download that does not exist |
| 6 Borrow the piece | GO | Alexa's combiner, Hume's text injection, the paper's distillation recipe — not audEERING's weights |
| 7 Right tool | GO | the #1760 slot, `_accept_panel_voice_claim`, the block tables, the replay gate |
| 8 Voice first, no keyboard | GO | the repair is spoken; the one typed thing (labelling 150 clips) is a lab chore on the phone |
| 9 Understand before you change | this record | chain traced with file:line; nothing built |
| Owner rule: never speaks unprompted | GO | the repair answers a turn; it never initiates |

**No-go inside the idea:** any NC/research-only weights on the panel; any resident model over the
packet's 300 MB line (which today is every permissive dimensional model as published); a per-clip
score row outside an owner-claimed labelling session; a per-member clip store; a
score on ambient audio; scoring before the POST unless measured ≤30 ms; feeding arousal into
memory or continuity before Q16c and consent exist; a categorical→arousal mapping used without the
label-set validation.

## 6. Decisions for Jason (plain language)

1. **Licence rule — make it policy?** "No non-commercial or research-only model weights ever run
   on the live path; they may be lab judges." This rules out the Wav2Small teacher and audEERING's
   model on the panel, even though using them at home would be legal. Yes / no.
2. **Scope — accept the split?** Zoe may *apologise and clarify* for anyone who sounds frustrated
   (guests and kids included, nothing kept), but she *keeps* an emotional score only for adults who
   opted in at enrolment, never for children, and for no one until voice-ID is live. If you want
   "owner only" for the apology too, say so — it is simpler and loses the guests and kids.
3. **Which path to measure first?** (a) build our own tiny model from the two permissive teachers
   (MIT Odyssey + LAION CC-BY; needs a training window — brain stopped on the Orin for an
   afternoon, or a laptop), (b) emotion2vec+ shrunk to int8 with a small head we fit ourselves
   (no training window, but ~1–2 s per clip and a quantisation step nobody has published), (c)
   both in one bake-off. Nothing permissive that outputs arousal is small enough to just install —
   the LAION model turned out to be 295 MB for the arousal head alone. Default here: (c).
4. **The labelling chore.** About 150 of your own command clips, rated 1–5 for "how worked up did I
   sound", on your phone, ~30 minutes, one sitting. Without it the "≥0.6 on our clips" bar cannot
   be measured and the whole thing stays a guess. Yes / no.

## 7. Next steps if GO

1. Write `docs/governance/emotional-safety-policy.md` (Q16c, §3.3) and add the licence rule to it
   and to `docs/CANONICAL.md`; reference both from the plan's W4 gate (`:951-955`, `:1037`).
2. Build the label set: a phone page or a plain CSV over 150 cluster-A corpus clips (operator
   instrument; never the panel); record human-vs-judge CCC first.
3. Run P-W4.1 as rewritten in §4 on the **Pi**, isolated venv, resident candidates *a–c*, judges =
   Odyssey MIT + LAION CC-BY; OKF record `docs/knowledge/ser-bakeoff.md` with the kill criteria
   and the negative controls.
4. If a candidate passes: a build record for **P4 + W1.5** — repair-cue table, `turn_meta` side
   channel joined by `turn_id`, `[REPAIR]` block pair in the elide tables, aggregate-only shadow
   counters plus the owner-claimed labelling session, day-sim ask for the misheard-command seed,
   replay-gated flags, one-week shadow before the repair fires. Still no memory write — that is W4
   proper, gated on Q16c consent and W3.
5. ~~Fold the findings into the plan~~ — **done in this PR (#1838)**: the plan's W4 shortlist
   (`samantha-evolution-plan.md` §3 W4, §8.4, the W4.1 checklist line) and the P-W4.1 packet now
   say Wav2Small = recipe/teacher only (teacher half non-commercial), emotion2vec weights = FunASR
   Model License, SenseVoice categorical, and point here for the go/no-go.

## 8. Sources

Primary unless marked.

**Candidate models and licences**
- Wav2Small paper — https://arxiv.org/abs/2408.13920 ; HTML v1 (results, 60 KB / 9 MB / 9 ms, CC BY-NC-SA line) — https://arxiv.org/html/2408.13920v1 ; HF repo API (two files, no weights) — https://huggingface.co/api/models/dkounadis/wav2small ; HF README (teacher = average of Odyssey WavLM + Dawn; teacher CCC) — https://huggingface.co/dkounadis/wav2small/raw/main/README.md ; GitHub (README only) — https://github.com/dkounadis/wav2small
- audEERING Dawn — https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim ; audEERING open-source page ("research purposes only") — https://www.audeering.com/research/open-source/ ; w2v2-how-to — https://github.com/audeering/w2v2-how-to
- Odyssey-2024 baseline (MIT) — https://huggingface.co/3loi/SER-Odyssey-Baseline-WavLM-Multi-Attributes ; valence card — https://huggingface.co/3loi/SER-Odyssey-Baseline-WavLM-Valence ; dominance card — https://huggingface.co/3loi/SER-Odyssey-Baseline-WavLM-Dominance ; challenge paper — https://www.isca-archive.org/odyssey_2024/goncalves24_odyssey.pdf
- Vox-Profile (Open RAIL, no commercial use) — https://huggingface.co/tiantiaf/wavlm-large-msp-podcast-emotion-dim ; paper — https://arxiv.org/pdf/2505.14648
- emotion2vec+ base — https://huggingface.co/emotion2vec/emotion2vec_plus_base ; front-matter (license: other / model-license → FunASR) — https://huggingface.co/emotion2vec/emotion2vec_plus_base/raw/main/README.md ; repo — https://github.com/ddlBoJack/emotion2vec ; FunASR MODEL_LICENSE — https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE ; community ONNX — https://huggingface.co/pankotaro/emotion2vec-plus-base-onnx
- SenseVoice — https://github.com/FunAudioLLM/SenseVoice ; card — https://huggingface.co/FunAudioLLM/SenseVoiceSmall ; sherpa-onnx (sizes, RK3588 RTF, `emotion` field) — https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html
- LAION Empathic-Insight-Voice-Small (CC-BY-4.0; 58 heads, `model_Arousal_best.pth` 294,944,245 B) — https://huggingface.co/laion/Empathic-Insight-Voice-Small ; file listing — https://huggingface.co/api/models/laion/Empathic-Insight-Voice-Small?blobs=true ; BUD-E-Whisper (Whisper-Small fine-tune, 241.7 M params, 966,995,080 B, CC-BY-4.0) — https://huggingface.co/laion/BUD-E-Whisper ; EmoNet-Voice — https://arxiv.org/html/2506.09827v2
- Checkpoint sizes (HF API `?blobs=true`, 2026-10-04): Dawn 661,375,508 B; Odyssey multi-attribute 1,274,490,516 B; Vox-Profile 1,264,246,784 B; emotion2vec+ base `model.pt` 1,118,245,678 B; emotion2vec+ community ONNX 373,159,295 B; SenseVoiceSmall `model.pt` 936,291,369 B (URLs in §1.2)
- openSMILE licence [secondary] — https://audeering.github.io/opensmile/about.html ; https://en.wikipedia.org/wiki/OpenSMILE
- Pi 5 anchor: 86 M AST on Cortex-A76 (9.4 s fp32 / 2.09 s int8 per 10 s) — https://dev.to/syamaner/part-4-edge-deployment-of-an-86m-parameter-audio-transformer-1821

**Datasets and licence texts**
- MSP-Podcast (academic licence, institutional signature; CC podcasts) — https://lab-msp.com/MSP/MSP-Podcast.html ; corpus paper — https://www.lab-msp.com/MSP/publications/Busso_2025.pdf
- EmoBox (which corpora permit commercial use) — https://arxiv.org/html/2406.07162v1
- CC BY-NC-SA 4.0 legal code (NonCommercial, Share, ShareAlike) — https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode
- Gemma 4 E4B-it (Apache-2.0) — https://huggingface.co/google/gemma-4-e4b-it ; Gemma terms (earlier generations) — https://ai.google.dev/gemma/terms
- Kokoro-82M (Apache-2.0) — https://huggingface.co/hexgrad/Kokoro-82M

**Field evidence for the use**
- Audio→arousal, text→valence: Springer 2025 survey — https://link.springer.com/article/10.1007/s12243-025-01069-1 ; Interspeech 2025 SER challenge — https://www.isca-archive.org/interspeech_2025/naini25_interspeech.pdf
- Alexa frustration detector [secondary] — https://onezero.medium.com/heres-how-amazon-alexa-will-recognize-when-you-re-frustrated-a9e31751daf7 ; https://knowtechie.com/amazons-alexa-knows-when-youre-pissed-off-with-its-results/
- Hume EVI FAQ (top-3 labels as text) — https://dev.hume.ai/docs/speech-to-speech-evi/faq

**Our system (read-only, worktree head 4adfe59b)**
- `scripts/setup/zoe_voice_daemon.py` (capture `:1631-1699`, WAV `:1621`, upload `:2469-2516`, shadow slot `:2149-2258`, metrics row `:2018-2028`, follow-up window `:322-326`, ambient `:1425-1433`)
- `services/zoe-data/routers/voice_tts.py` (`/turn_stream` `:4977-5040`, `_maybe_capture_stt` `:2660-2681`, `_transcribe_audio` `:2683`, claim acceptance `:3020-3041`, `:5726-5751`, fast tiers `:4155`, opener fast-path `:5107-5140`, identity chain `:3198-3236`, memory passes `:2940`)
- `services/zoe-data/zoe_flue_client.py` (`:982-996`, `:1495-1540`); `zoe_core_client.py:800`
- `services/zoe-data/memory_gate.py:440-456`; `memory_digest.py:390-431`, `:535-651`, `:895-935`; `memory_supersede.py:75-135`; `proactive/triggers/emotional_followup.py:33-52`
- `LICENSE`; `docs/knowledge/biometric-retention-policy.md`; `docs/knowledge/moonshine-0-1-5-upgrade.md:89-91`; `docs/knowledge/panel-ttfa-breakdown-2026-09-28.md`; `docs/architecture/samantha-evolution-plan.md` (W4 `:185-222`, `:630-660`; W1.5 `:936-940`; W5.3/W5.4 + emotional-safety `:944-955`; checklist `:1033-1037`); `docs/architecture/samantha-evolution-packets.md:205-216`; `docs/architecture/omi-integration-plan.md:96`, `:268-281`; `docs/research/companion-field-vs-samantha-2026-10-03.md` (§1 "Hears mood", W4 shape, P4); `docs/research/speaker-gate-rebuild-2026-10-04.md` (§1.5 licences, §2 identity chain, §2.3 clusters); `docs/research/personality-identity-layer-2026-10-04.md` (§3.4, §6); `docs/research/self-building-skills-2026-10-04.md` (Open-Meteo terms); `~/.zoe-voice-samples` WAV headers only (1,312 files, 2026-10-04)
