---
type: architecture
title: Struggle detector - notice where Zoe struggled (Phase 1 of the self-correction loop)
status: phase-1 built (ZOE_STRUGGLE_DETECT default shadow); phases 2-3 planned
owner: services/zoe-data (struggle_signals.py, struggle_detector.py)
updated: 2026-10-14
---

# Struggle detector

Owner intent: Zoe should *recognise issues and realign* by herself. That is a five-step loop; this document is its
contract. **Phase 1 (this build) is step 1: notice.** It detects, records and reports. It changes nothing Zoe says
(in the default `shadow` mode) and nothing about how a turn is answered.

| step | what | phase | status |
|---|---|---|---|
| 1. notice | per-turn struggle signals, stored + reported | 1 | **built** |
| 2. explain overnight | night pass clusters the day's events into problem statements | 2 | planned |
| 3. propose | a cluster becomes an evolution proposal with a candidate fix | 2 | planned |
| 4. prove | the fix is replay-gated (voice corpus + the struggle replay) before it ships | 3 | planned |
| 5. apply | router retraining applied automatically **within its gates**; code changes only as PRs; **rocks never change** | 3 | planned |

## Where it sits (and why it cannot hurt a turn)

```
turn ──► semantic_router.route ──(note_route: deque append)──┐
     ──► brain_dispatch._log_lane ──(note_brain: deque append)┤   in the turn: two O(1) appends (p99 0.03 ms measured)
     ──► reply out
         └─ existing post-turn task ─► struggle_detector.observe ─► bounded queue ─► ONE worker:
              voice: routers/voice_tts._run_voice_memory_passes      build Turn (joins the notes) ─► detect(prev, cur)
              chat:  routers/chat._persist_memory_candidates          ─► struggle_events row(s) ─► STRUGGLE log line
```

* The hooks sit in the turn's EXISTING post-turn background tasks, never on the reply path. `observe` is a
  `put_nowait`; a full queue (`ZOE_STRUGGLE_QUEUE_MAX`, 256) drops and counts. Every exception is swallowed and counted
  (`stats()`: `hook_errors`, `process_errors`, `store_errors`, `dropped_full`, ...). A store write is bounded (5 s).
* A speculative voice turn that is cancelled never reaches the hook (`_spawn_bg` defers the memory passes until commit).
* Harness / test traffic is never recorded: `replay-*` / `test*` / `probe*` / `ci-` / `e2e` / `bench` panels and
  synthetic users (`user_filters.is_synthetic_user`).
* The voice-path file touched is `routers/voice_tts.py` (one call at the top of `_run_voice_memory_passes`, one kwarg
  at the streaming finaliser). Replay-gated like every voice change.

## The signals (`struggle_signals.py`, pure)

Pair signals compare the turn with the PREVIOUS turn of the same conversation (voice: the panel; chat: the session) by
the same user; on a voice panel the previous turn by another identity also counts (the panel's identity can flip
between guest and member mid-conversation) but then no words are kept. A pair event is ABOUT the previous turn (the one
that failed): its domain, path and shape are that turn's.

| signal | fires when | precision guards (each pinned by a hard-negative test + a negative control) |
|---|---|---|
| RE_ASK | the same request again within `ZOE_STRUGGLE_WINDOW_S` (45 s) after normalisation (wake word, contractions, number words, plurals, fillers) or with only a polite lead-in dropped | repeatable commands (next / louder / yes ...); changed slot (time, number, place, name, on/off) |
| REPHRASE | same domain, other words, no new slot: shared content words, a pure refinement, or the same lead + time anchor + time frame | follow-up openers ("and", "what about"); different fact; added time slot |
| CORRECTION | a repair cue opens the next turn ("no, I said ...", "I meant ...", "not that one", "you misheard"), or opens its second sentence | "no worries / no thanks / no, it's fine"; a bare "no" answering Zoe's question; "I meant to ..." |
| NEGATIVE_FEEDBACK | `conversation_feedback.classify` says thumbs_down / correction, or a complaint about the exchange ("you're not listening", "that doesn't make sense") | kind verdicts; sentences merely containing "wrong" |
| CANT_DO | the reply is a capability disclaimer (superset of the replay gate's `_CANT_DO_RE`) on a routed domain, or the brain's degraded fallback (ERROR, any domain) | policy refusals (guest / PIN / privacy); "I can't wait"; unsupported asks (no domain) |
| FALLTHROUGH | the router placed the turn in a fast domain and the brain (or ANOTHER fast domain) answered | two witnesses: the two-stage domain AND its similarity score >= 0.70 (or head >= 0.9 for domains without examples); a timed-out head needs similarity >= 0.65; similarity alone needs >= 0.80 |
| VERBOSE | a short factual question (<= 12 tokens) got a reply over budget (voice 280 chars / 200 on a fast domain; chat 700 / 500) | open requests (story, explain, why, recipe, tell me about ...) |
| MISHEAR | a wake-word-only transcript, or a voice corrected repeat within 30 s (same frame, one or two words swapped for sound-alikes, or re-ordered, or a garbled lead-in dropped) | a different word that does not sound alike ("milk" -> "eggs"); polarity / number / place swaps; typed text (a typo fix is a RE_ASK) |
| ABANDON | the streamed voice reply ended early with words already heard (barge-in or disconnect - indistinguishable server-side) | an unheard / complete reply |

Each event carries: ts, channel, panel, user scope, prev + current turn ids (detector-minted, `t-<hash>`), signal,
confidence, fix type, domain predicted vs served, path served (`fast` / `brain:<lane>`), latency (route note to
completion), a text-free `pattern` (`<domain>|<lead>:<time frame>:<anchor>`, e.g. `weather|what:future:today`) and
JSON reason codes. Suggested fix types: `router_training`, `router_latency`, `capability_gap`, `stt`, `verbosity`,
`answer_quality`.

## Privacy

* A member's row may carry `summary` = the PII-scrubbed (`memory_service.scrub_pii`, on the RAW words) display form (lower case, wake word stripped, never folded, so a forget's name pattern finds it) of the user words, <= 160
  chars - never Zoe's reply. It is the same text `chat_messages` already keeps for that member.
* No words for: guests (and a voice turn whose speaker the gate REJECTED, recorded as a guest's); a turn queued before a forget of that user (before a delete it is not recorded at all); off-the-record / distress turns (`memory_provenance.is_off_record`); turns naming a forgotten
  entity (tombstone + `memory_forgotten.matches`); a pair across two identities; any lookup failure.
* Reports (the report half, below) show example words only from the VIEWER's own rows; another member's words never
  appear in anyone else's report and guest rows have none to show.
* Forget: `memory_forget_entity` -> `struggle_detector.erase_entity` (after the night mind, fail closed: a store failure
  answers "I can't say it's forgotten yet"). User delete: `MemoryService.delete_user` -> `struggle_detector.delete_user`
  (fail closed). Only a missing table counts as "nothing stored".
* Retention: rows older than `ZOE_STRUGGLE_RETENTION_DAYS` (90) are swept by the worker at most once an hour.

## Flags

| flag | default | meaning |
|---|---|---|
| `ZOE_STRUGGLE_DETECT` | `shadow` | `off` = hooks return at once; `shadow` = detect + store, nothing user-facing; `on` = reserved for the report half's user-facing pieces, behaves as `shadow` until they land |
| `ZOE_STRUGGLE_WINDOW_S` | 45 | pair-signal window (clamped 5..300) |
| `ZOE_STRUGGLE_RETENTION_DAYS` | 90 | row retention |
| `ZOE_STRUGGLE_QUEUE_MAX` | 256 | worker queue bound |

## Reports

This half records: every event is a `struggle_events` row plus one `STRUGGLE signal=... domain=... path=...` log line
(labels only), and `struggle_detector.stats()` holds the counters. The read side - the clustered digest, the admin
endpoint, the operator CLI, the nightly `STRUGGLE_SUMMARY` line and the `on`-only pieces (the admin morning-brief line,
a one-line ack of a bare "that was wrong") - is the second, serial PR of this workstream.

## Evaluation (2026-10-14)

Offline replay of the app log (`scripts/maintenance/struggle_replay_eval.py`, read-only, LOCAL ONLY; the labelling
sheet and labels live outside the repo). The log held 108 real-panel voice turns in the window (the log only reaches
back to 2026-10-04); every one of the 107 with a predecessor was hand-labelled. Numbers only:

| signal | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| MISHEAR | 6 | 0 | 2 | 1.00 | 0.75 |
| FALLTHROUGH | 4 | 0 | 2 | 1.00 | 0.67 |
| CORRECTION | 3 | 0 | 1 | 1.00 | 0.75 |
| REPHRASE | 3 | 0 | 3 | 1.00 | 0.50 |
| NEGATIVE_FEEDBACK | 2 | 0 | 1 | 1.00 | 0.67 |
| ABANDON | 1 | 0 | 0 | 1.00 | 1.00 |
| RE_ASK, CANT_DO, VERBOSE | 0 | 0 | 0 | n/a | n/a |

Honest caveats: these are IN-SAMPLE (the thresholds were tuned on this same sheet - before tuning FALLTHROUGH was 4/8
and a polarity swap produced a false RE_ASK); the counts are small; CANT_DO and VERBOSE cannot be judged from the log
(brain reply text is not logged) and chat turns are not in it. The weather-forecast episode of 2026-10-14 (a forecast
question answered with current conditions, then a re-ask about the day's high) is caught: FALLTHROUGH + REPHRASE on the
re-ask, MISHEAR on the corrected repeat. Re-run after a week of shadow data with the stored rows as the sheet.

## Phase 2 / 3 plan (out of scope for Phase 1)

* **Phase 2 - explain + propose.** A `night_mind`-adjacent pass (inside `run_memory_digest`, after the reject summary)
  reads the day's `struggle_events`, clusters by `(signal, domain, pattern)`, and writes ONE problem statement per
  cluster above a count threshold as an `EvolutionSignal` / proposal through `zoe_evolution_proposal.py`
  ([zoe-evolution-proposal-contract.md](zoe-evolution-proposal-contract.md)); `evidence` = event ids + counts, never
  words. `routers/chat._check_frustration` (an older in-process exact-repeat counter feeding `evolution_notice`) folds
  into this - one notice source, not two.
* **Phase 3 - prove + apply.** `router_training` clusters feed the router self-train loop (`ZOE_ROUTER_SELFTRAIN`) as
  candidate examples from MEMBERS' rows only, labelled with the domain the user's follow-up resolved to; promotion stays
  behind that loop's replay ratchet. `stt` clusters become homophone-fix candidates (`_normalize_voice_command_text`)
  proposed as PRs. `capability_gap` / `verbosity` / `answer_quality` become PR proposals only. Every change is
  replay-gated against the voice corpus AND re-scored by `struggle_replay_eval.py` (the cluster must shrink, no other
  signal may grow). The rocks (Gemma brain, Moonshine STT, Kokoro TTS) are never a fix target.

## Known gaps (also in docs/knowledge/open-problems.md)

* No STT confidence: Moonshine's word confidences are off (`word_timestamps` not enabled), so a garbled transcript that
  is not repeated is invisible.
* ABANDON cannot tell a barge-in from a client disconnect (no server-side barge-in signal).
* Turn ids are detector-minted, not `chat_messages` ids (the voice save does not return its row id).
* Recall is deliberately traded for precision (REPHRASE 0.50 in-sample): rewordings with no shared words and no time
  anchor ("how are you" -> "I want to know how you are") are not caught.
