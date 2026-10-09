---
type: Reference
title: Restraint in code (ZOE_RESTRAINT) - what Zoe leaves unsaid, decided by code
description: Register item BP1. A sensitivity class on memory rows and threads, withheld from the recall packet, the [Today] brief and the [RAISE] pick unless the turn pulls it; a spoken mute that survives; back-off from the delivery ledger; the guest rule. Flag off|shadow|enforce, default shadow. What it does, where it sits, how it is measured, what it does not cover, and the operator steps.
tags: [samantha, restraint, person-likeness, memory, proactive, voice-path, zoe-data, BP1]
timestamp: 2026-10-09T00:00:00Z
---

# Restraint in code (`restraint.py`, `ZOE_RESTRAINT`)

Spec: [person-likeness record](../research/person-likeness-2026-10-09.md) section 5 (SAL3 sensitive classes wait for a
pull, SAL6 a spoken mute, SAL7 back-off), [best-ideas register](../research/best-ideas-register-2026-10-09.md) BP1,
[blueprint](../architecture/samantha-brain-blueprint-2026-10-09.md) law L3 (**withhold, do not instruct**) and wave W2.
Measured by the P family ([samantha-person.md](samantha-person.md)): P4, P3.d, with P1 and the bar's S5 and the
day-sim's 1r / 7r / 7s as the no-regression guards.

**The rule that shapes everything: the brain is never told what not to say.** Every part below REMOVES material
from what the model sees. There is no "do not mention" line, no placeholder, no marker. A withheld row leaves no
trace in the packet, and a test pins that the filtered output holds nothing the input did not.

## The flag

`ZOE_RESTRAINT` = `off` | `shadow` (default; unset or an unrecognised value) | `enforce` (`1`/`on`/`true` too). Per-call env
read, so a restart flips it.

| mode | the decisions | what changes |
|---|---|---|
| `off` | not made | nothing; no class is stamped or stored |
| `shadow` (default) | made and logged (`RESTRAINT user= surface= mode=shadow withheld= reasons= classes=`: counts and class names, never text) | **nothing**: every row, thread and raise goes out exactly as before; a spoken mute is RECORDED but not honoured and not acknowledged; new rows are stamped with their class |
| `enforce` | made and honoured | sensitive and muted material is removed; a mute is acknowledged in one deterministic sentence |

Every entry point is fail-open (an error is no restraint, never a broken turn), except the guest rule, which is pure
code with no I/O to fail.

## 1. The sensitivity class

Six classes: `health`, `money`, `family_conflict`, `grief`, `other_member` (a row about another person) and `affect`
(an emotional moment). Decided in code, **structured signals first, a word list second** (the blueprint's union rule):

| signal | decides |
|---|---|
| `memory_type == person` or `entity_type == person` | `other_member` |
| `memory_type == emotional_moment`, a captured `affect`, a candidate `kind == emotional` | `affect` |
| a row `tags` entry (`health`, `medical`, `dental`, `finance`, `grief`, `conflict`, ...) | `health` / `money` / `grief` / `family_conflict` |
| the per-language word lists (`lexicons_data/<lang>.json` "restraint": health, money, grief, conflict, kin and possessive patterns, a first-person feeling list), compiled by `restraint_lex.py` for the language the text is written in | the fallback for everything above |
| the member's contact names (`people`), passed by the nightly pass | `other_member` for a thread that names one |

`classify_full` returns the signals beside the classes (`health:lexicon`, `affect:type`, ...), so an audit can tell a
structured decision from a word-list one.

**Stored, invalidate never delete.** A new memory row's metadata carries `sensitivity` (csv), `sensitivity_v` (the
classifier version) and `sensitivity_h` (a hash of the row's words), written in `MemoryService._build_metadata`. A
reader trusts the label only while version and hash still match; an edited row or a newer classifier makes it invalid
(ignored and recomputed, never overwritten or removed). Older rows without a label are classified at read time. Threads
(the nightly proactive candidates) are stored in `restraint_classes` (migration 0042): a changed text or version sets
`invalid_at` on the old row and inserts a new one; going back to the old words revalidates the old row.

## 2. Withhold

Three surfaces, one rule (`restraint.decide`, pure):

| surface | where | a sensitive row or thread is delivered only when |
|---|---|---|
| `packet` | `routers/memories.py memory_for_prompt`, before `_build_memory_prompt_packet` (the recall floor, the `recall_memory` tool, continuity, the legacy lane all pass here) | the owner's words share a topic with it, name its class ("what did the doctor say"), are a mood statement (feelings only: `affect`), or are an open question |
| `brief` | `brief_first_turn._prepare` via `filter_brief_ctx` (a copy; the cached context is untouched) | the turn is an open question |
| `raise_greeting` / `raise_cue` | `proactive/selector.py _prepare`, before the pick (a withheld candidate never blocks the next one) | an open question (greeting); a cue raise IS a topic pull (the owner's words matched the candidate's anchors) |

**A pull** is an open question to Zoe about what is pending: "what's up?", "how's it going?", "how are things?",
"what's new?", "anything I need to know?", "what do you know about me?" (`restraint.is_pull`, a closed phrase list after
the greeting leads and tail words are stripped). A bare "Hi Zoe" or "Good morning" is **not** a pull, and neither is
"how are you?" (that asks about Zoe). The bar's S5 and the day-sim's open turns are all pulls, so the raises they measure
still happen.

The recall packet judges the **owner's words, not the model's query**: the `recall_memory` tool call carries only what
the model typed, so `note_turn` (in `zoe_flue_client`, beside `recall_evidence.note_turn`) records the owner's turn and
the speaker gate's verdict; the previous turn within five minutes keeps its topic alive ("are you sure?" continues the
dentist). Kin words (mum / mom / mother ...) count as topics although they are short.

Not filtered, by design: calendar events (the day, not a thread), an `event` raise (only the guest rule holds it) and the
relational "People and important dates" block (only built when the owner asks). The exact-words block is filtered at its source
(`exact_words.lookup` -> `restraint.filter_extra`: the guest wall and the mutes; the owner asking "what exactly did I say about ..." is
the pull). The always-present user-model **card** is filtered at serve time (next section).

## 2c. The user-model card (`surface=card`)

The card (`user_model_card.py`) is in front of the model on every turn, so no pull could ever release a line of it. In enforce,
`load_card_block` takes off it, over the stored items and at serve time (flipping the flag needs no rebuild; the served bytes are
deterministic per items and flag, so the prefix cache is stable), (a) every item the classifier calls `money`, `grief` or
`family_conflict`, and (b) every line of the **Health** category that is not safety information. Safety information is the
lexicon's `health_safety` list (allergy, anaphylaxis, EpiPen, asthma, diabetes, epilepsy, medication, pregnancy, access needs: deaf,
wheelchair, disability): cooking and advice need those on every turn. A migraine, a knee, a surgery, insomnia, ADHD wait for the
recall packet, which the owner's own question pulls. `off` and `shadow` serve the stored text byte for byte (shadow logs
`RESTRAINT ... surface=card withheld=N`). Not covered: a guest voice (`verdict False`) still hears the card the member would; the card
is not rebuilt per voice.

## 3. The spoken mute

`restraint.handle_turn` runs at the top of `run_flue_brain_streaming`, before the brain, the brief and the raise:

* mute: "don't mention that again", "stop bringing that up", "leave it", "please don't bring up the dentist anymore", "stop
  asking me about my knee", "I don't want to talk about it anymore", "that's enough about the interview", "never mention
  Dana's surgery again", "leave the dentist alone", "lets not talk about the dentist" and their polite forms ("could you
  not ...", "I'd like you to stop ..."). A bare "leave it" is a mute only when something was just raised (otherwise it is
  an ordinary turn); "leave it on", "stop", "stop the music", "leave me alone" are never mutes.
* release: "you can mention the dentist again", "feel free to bring that up again", "it's okay to talk about it again".
* **what "that" means:** the thread raised or marked by the brief in this conversation, else the member's most recent raise
  within 12 hours, else the item a continuity turn just checked in about. If there is none, a clear mute phrase asks which
  one ("Which one should I leave alone?") and records nothing.
* **a row with provenance, never the owner's words** (`restraint_mutes`): `topic_key` (stems), `thread_ref`, `session_id`,
  `turn_key` (a digest of the utterance), `phrase` (the pattern id), `created_at`. A release sets `status = released`,
  `released_at`, `released_turn_key`; the row stays.
* honoured everywhere: the raise and the brief never carry a muted thread (even on a pull or a cue); the packet withholds
  it from a mood turn or a task turn but still answers the owner's own question about it (a mute is about volunteering).
* enforce speaks `Okay, I won't bring that up again.` / `Okay, I can mention that again.` from code, in the language the owner
  spoke (the lexicon's `ack_*`; English when that language has none), the verify-on-challenge pattern: no model call. Shadow records and
  lets the brain reply. **Both brain lanes** record and speak it: `run_flue_brain_streaming` and, since this PR, `run_zoe_core_streaming`. The replay harness
  (`replay_isolation`) never writes a mute; a guest has no standing to set one.
* erased with the member (`MemoryService.delete_user`) and with a forget of the entity the topic names
  (`memory_forget_cascade`).

## 4. Back-off

The delivery ledger's `outcome` column is the evidence (`ZOE_PROACTIVE_LEDGER` must be on; with it dark there is no
evidence and no back-off). Per kind: each consecutive `ignored` raise doubles the member gap for that kind (2 h base,
x2 per ignored raise, capped at x32 and 7 days); `unknown` / `undelivered` rows are skipped (the person may never have
heard it) and an `accepted` one ends the run. **Three unanswered raises in a row of any kind pause raising for 72 hours.**
This sits beside `MAX_SURFACED = 2` (raised twice, never again), the 3-day cooldown and the 2 a day cap; none of them changed.

## 5. The guest rule

`speaker_verified is False` (the speaker gate ran and did not confirm a member) is bound in `routers/voice_tts.py
voice_command` (`restraint.bind_verdict`, a context variable) and captured per turn by `note_turn`. With it, a sensitive
class is withheld on every surface **whatever the turn pulls**, and a sensitive calendar title waits too. `None` (the gate
off, in shadow, or a typed turn) applies the normal pull rule to everyone, which is the "sensitive classes wait for a pull
for everyone until the speaker gate enforces identity" default of the register.

## Data

Migration `0042_restraint.py` (two new tables, no change to any existing one): `restraint_mutes`, `restraint_classes`. A
deploy that has not run it degrades to no mutes and classes recomputed from the text. The chain is
0039 -> 0040 (night mind, #1930) -> 0041 (proactive ledger lines, #1935) -> 0042 (this one): renumbered in the wave-1 batch.

## Measurement

`python3 scripts/perf/samantha_person.py --checkout --seeds zmb-v1,fresh-a,fresh-b` runs the in-process tiers against
this tree with no network, no live service, no lock and no live database (the mute probe replaces
`db_compat.get_compat_db` and the memory service before its first call and builds a SQLite file from the real migrations).
Results 2026-10-09, three worlds (permuted names, weekdays and ailments), enforce:

| half | bar | system | restraint off + mute never honoured (control) |
|---|---|---|---|
| P4.a no sensitive item to an unconfirmed voice | every ask | **60/60 PASS** | 0/60 FAIL |
| P4.b the plain item is still raised | >= 18/20 | **60/60 PASS** | 0/60 FAIL |
| P4.c an open question delivers the sensitive item | >= 18/20 | **60/60 PASS** | 60/60 (the pull works: nothing was held back) |
| P4.d a bare greeting raises nothing sensitive | every ask | **60/60 PASS** | 0/60 FAIL |
| P3.d five natural mute phrasings, gone four days later | every ask | **5/5 PASS** (the acknowledgement is spoken) | 0/5 FAIL (`mute_off` and a bypassed selector) |
| P1.a-d salience | as before | 60/60 each, unchanged | 60/60 each |

Before this work the live baseline (2026-10-09, unchanged stack) had P4.a / P4.b at 0/20 and P3.d at 0/1, all marked
EXPECTED FAIL targets ([samantha-person.md](samantha-person.md)). They are ordinary gating halves now. The pre-registration
sha changed with them (`4099844a...`), with this table as the baseline.

**Live, in-process (2026-10-09, after the enforce-ready PR).** The live 4B ran through `scripts/perf/person_half_enforce_ab.py` with
`ZOE_RESTRAINT=enforce` in one arm and `shadow` in the other (no live flag touched, write-isolated, no database; the packet is the real
`memory_for_prompt` -> `restraint.apply_to_packet` output forced onto each task turn, the worst case): P2.a 20/20 in both arms, P2.b 17/20 -> 20/20,
P2.c 20/20 in both, 40 of 40 prompts differ. **Still not measured live:** P9, S5 and the day-sim (they need the live database: seeds, candidates, the brief)
and the voice replay gate. The offline stand-ins stay: `tests/unit/test_samantha_person_checkout.py` (every bar and day-sim ask still gets the row it was written to
find; S5's and the day-sim's open turns are pulls) and the selector tests that replay the 1r / 7r / 7s shapes. The full flip block and the
verdict (DO NOT FLIP until the 90 % word-recall bar or a time-boxed live trial is decided) are in
[person-half-enforce-pack-2026-10-09.md](person-half-enforce-pack-2026-10-09.md).

## Languages

Every word restraint decides by is data: `lexicons_data/<lang>.json` under `"restraint"` (health, money, grief, conflict, kin and
possessive patterns, feelings, the stop words, the "what's up?" phrases, the spoken-mute grammar as whole regexes, the spoken
acknowledgements), compiled per language by `restraint_lex.py`. No English word list is left in `restraint.py` and a test pins it.

* **The language is the text's own** (`lexicons.detect`: kana -> ja, Han -> zh, else the Latin lexicon whose function words match most,
  else en). A language with no `restraint` entry classes nothing by words (the structured signals still decide) and is **never guessed
  from English**. Closed phrase lists (a pull, a mute) are tried in the text's language first and then in every other language that has
  them, because a four-word utterance carries too few function words to detect; the open class lists never cross languages (`pain` is
  health in English and bread in French).
* **English is the pre-move list plus a widening.** `tests/fixtures/restraint_pre_move_en.json` holds the pre-move sources: every
  pre-move fragment is still there in order, the mute grammar and the word sets are byte-identical, and the one fragment that was
  narrowed (`broke`, which also matched "broke her arm") is named in the test. Adding a word is a data change that a held-out set measures.
* **es, fr, de, zh, ja are author-written, `reviewed: false`.** Labelled sentences for every class, a pull, a mute, a release and the
  acknowledgement are in `tests/test_restraint_lexicon.py`; taking a language's entry away turns its test red. A native reader's review
  is owed before anyone relies on them for a household that speaks them (a CJK topic is the character bigrams minus particles).
* **Measured (2026-10-09, classifier = words only, no structured signal).** Three sets written before their first run, English:

| set | sensitive, class-exact on its first run | any class | plain false positives |
|---|---|---|---|
| 1 (60 sentences) | 35 / 60 | 43 / 60 | 0 / 31 |
| 2 (60), after widening from set 1's misses | 40 / 60 | 46 / 60 | 0 / 32 |
| **3 (54), after the vocabulary was final: the blind number** | **34 / 54 (63 %)** | **41 / 54 (76 %)** | **0 / 25** |

  Sets 1 and 2 are training data once their misses were read (59 / 60 and 56 / 60 now) and are regression floors in the test. The
  ledger's bar was ">= 90 % class-exact on a fresh set with 0 plain false positives": **0 plain false positives holds (0 of 88); the
  90 % does not, and a hand-widened word list will not reach it** (a paraphrase it never saw is missed: "I still can't believe she's
  gone", "twisted their ankle", "a colonoscopy"). What a miss costs: the row is not withheld, which is exactly shadow's behaviour; what
  restraint can never do through this list is over-withhold ordinary life. The rest is carried by the structured signals (type, entity,
  tags, captured affect), the night mind's stage-2 `kind = health` enum and the contact names; a `other_member` row about a bare first
  name still needs `entity_type = person` or a contact name (ingest has neither for a free-text row).

## Limits, stated

* **The word list is a fallback, not a classifier.** See "Languages": 63 % class-exact on an unseen English set, 0 false positives.
* **`other_member` needs a signal.** A row about "Teodor" with no `entity_type=person`, no kin or possessive and no
  contact name in the list is not classed; the nightly pass passes the member's contact names for threads, ingest does not
  have them for rows.
* **The user-model card is filtered at serve time in enforce** (section 2c); a guest voice at a member panel still hears the member's card.
* **The mute is captured on both brain lanes** (Flue and the dormant core lane).
* **Back-off needs `ZOE_PROACTIVE_LEDGER` outcome rows.** The flag is ON on the live service, the sweep (`proactive/engine.py` step 4)
  is wired, and the live log shows 58 `PROACTIVE_LEDGER` rows written since 2026-10-06 and **not one `PROACTIVE_LEDGER_OUTCOME`**.
  That is unexplained from code alone (the sweep closes an open row to `unknown` after 24 h whatever the evidence), so the sweep now
  logs `PROACTIVE_LEDGER_SWEEP open=N closed=M deferred=K` whenever it has anything to judge; read it after the next restart. Until
  `ignored` rows exist back-off is inert in enforce (shadow-equivalent), and nothing else depends on it.
* **Speaker verdict only reaches voice turns** that carry the gate's block; typed chat has none (`None`).
* **The turn mark is per member, not per turn** (two overlapping sessions of one member share it): ledgered.

## Operator steps (nothing here is done by the PR)

1. Merge; `alembic upgrade head` (0042) runs with the deploy; restart zoe-data. `ZOE_RESTRAINT` unset = shadow.
2. Watch `grep RESTRAINT ~/.zoe-logs/*` for a week: `withheld=` counts per surface and class are what enforce would remove.
3. Voice path: that PR touched `zoe_flue_client.py`, `routers/memories.py` and one line of `routers/voice_tts.py` (the enforce-ready PR adds
   `zoe_core_client.py`, `exact_words.py`, `user_model_card.py`); the replay
   gate (`voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock`) must pass against a checkout of the merged commit.
4. Before the flip: run the bar (S1-S22 with S5), the day-sim, and `samantha_person.py` (P2, P3, P4, P9) once with the service
   in `enforce`, and compare with the shadow baseline. Flip with `ZOE_RESTRAINT=enforce` in the service `.env`.
5. Owner decisions that remain: which classes wait for a pull (the six above), and whether an open question like "how's it
   going?" is a pull (it is here: the day-sim and bar need it).
