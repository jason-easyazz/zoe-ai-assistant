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
| the English word list (`restraint._HEALTH`, `_MONEY`, `_GRIEF`, `_FAMILY_CONFLICT`, kin and possessive-name patterns, a first-person feeling list) | the fallback for everything above |
| the member's contact names (`people`), passed by the nightly pass | `other_member` for a thread that names one |

`classify_full` returns the signals beside the classes (`health:lexicon`, `affect:type`, ...), so an audit can tell a
structured decision from a word-list one.

**Stored, invalidate never delete.** A new memory row's metadata carries `sensitivity` (csv), `sensitivity_v` (the
classifier version) and `sensitivity_h` (a hash of the row's words), written in `MemoryService._build_metadata`. A
reader trusts the label only while version and hash still match; an edited row or a newer classifier makes it invalid
(ignored and recomputed, never overwritten or removed). Older rows without a label are classified at read time. Threads
(the nightly proactive candidates) are stored in `restraint_classes` (migration 0040): a changed text or version sets
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

Not filtered, by design: calendar events (the day, not a thread), an `event` raise (only the guest rule holds it), the
relational "People and important dates" block and the exact-words block (both are only built when the owner asks), and the
always-present user-model **card** (see limits).

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
* enforce speaks `Okay, I won't bring that up again.` / `Okay, I can mention that again.` from code (the
  verify-on-challenge pattern: no model call). Shadow records and lets the brain reply. The replay harness
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

Migration `0040_restraint.py` (two new tables, no change to any existing one): `restraint_mutes`, `restraint_classes`. A
deploy that has not run it degrades to no mutes and classes recomputed from the text. If the night-mind migration (also
numbered 0040, PR #1930) lands first, renumber this one to 0041 / `down_revision = "0040"`.

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

**Not measured live.** The live brain was inside a 12B trial window (the harness lock was held) when this was built, so
the live P2 / S5 / day-sim runs and the replay gate were NOT run. What was done instead: every bar and day-sim ask, paired
with the sentence it was written to find, is checked offline to still DELIVER its row under enforce
(`tests/unit/test_samantha_person_checkout.py`), S5's and the day-sim's open turns are checked to be pulls, and the
selector tests replay the 1r / 7r / 7s shapes (one raise, minutes apart no second, the next thread not the same one).

## Limits, stated

* **The word list is English and a paraphrase it never saw is missed.** Held out set of 32 sensitive and 21 plain
  sentences written before the vocabulary was widened: class-exact recall 20 / 32 (any class 22 / 32), 0 plain false
  positives; after widening general vocabulary 29 / 32 on the same sentences (no longer held out), 0 false positives.
  `tests/test_restraint.py` pins it. Structured signals (type, entity, tags, captured affect) carry the cases the list
  cannot; the stage-2 `kind = health` enum of the night mind plugs into the same union.
* **`other_member` needs a signal.** A row about "Teodor" with no `entity_type=person`, no kin or possessive and no
  contact name in the list is not classed; the nightly pass passes the member's contact names for threads, ingest does not
  have them for rows.
* **The user-model card is not filtered.** It is the always-present block of current facts, it carries allergies and
  medication on purpose (cooking suggestions need them), is built per night and prefix-cache stable. Filtering it
  per turn would change its bytes and its health line is safety information; it is listed in
  [open-problems.md](open-problems.md).
* **The mute is captured on the Flue lane** (the live brain). The dormant core lane honours existing mutes (the filters live
  in shared code) but does not record a new one.
* **Back-off needs `ZOE_PROACTIVE_LEDGER`.** Its rows are the evidence.
* **Speaker verdict only reaches voice turns** that carry the gate's block; typed chat has none (`None`).

## Operator steps (nothing here is done by the PR)

1. Merge; `alembic upgrade head` (0040) runs with the deploy; restart zoe-data. `ZOE_RESTRAINT` unset = shadow.
2. Watch `grep RESTRAINT ~/.zoe-logs/*` for a week: `withheld=` counts per surface and class are what enforce would remove.
3. Voice path: this PR touches `zoe_flue_client.py`, `routers/memories.py` and one line of `routers/voice_tts.py`; the replay
   gate (`voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock`) must pass against a checkout of the merged commit.
4. Before the flip: run the bar (S1-S22 with S5), the day-sim, and `samantha_person.py` (P2, P3, P4, P9) once with the service
   in `enforce`, and compare with the shadow baseline. Flip with `ZOE_RESTRAINT=enforce` in the service `.env`.
5. Owner decisions that remain: which classes wait for a pull (the six above), and whether an open question like "how's it
   going?" is a pull (it is here: the day-sim and bar need it).
