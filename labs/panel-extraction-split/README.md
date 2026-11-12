---
type: lab-record
title: "Panel extraction split - where the idle-consolidation path can lose facts on panel-style turns"
date: 2026-11-12
code: origin/main (rebased 2026-11-12, after 672f6fb69)
status: lab only; synthetic data; nothing live changed; no file under services/ changed
---

# Panel extraction split

Labels: [Proven] measured directly; [Experimental] measured on synthetic data or through a proxy; [Speculative] a reading.

## Question

Live, 11-01..11-11: idle consolidation ran 25 passes over 224 member panel turns. Every pass logged
`stored=0 rejected=0 skipped=0 dropped=0`, and one durable row was written in 9 days (lane 7,
`docs/research/samantha-frontier-2026-11-11/`). Is that (A) panel speech with nothing worth keeping, or (B) an extraction
path that fails on panel-style transcripts?

## Result (what was measured, and what it does not settle)

1. **On synthetic sessions** [Experimental]: the deterministic gates dropped **0 of 20** sessions before the model, in both
   transcript styles (40 replays). The real extractor returned at least one item on **12 of 14** fact-bearing sessions
   (7/8 durable, 5/6 oblique). The expected fact was a raw model hit on 9 of 14 (5/8 durable, 4/6 oblique), survived the
   real deterministic gates on 8 of 14 (4/8, 4/6), and was handed to the ingest recorder on 8 of 14. One more item reached
   the recorder with the wrong owner (O6). Of the 6 command-only sessions, **3 were sampled** (C1, C4, C5) and all 3
   returned `[]`; C2, C3 and C6 were not sent to the model.
2. **The live counters** [Proven counts] are **consistent with little to extract, but cannot by themselves tell an absence
   of facts from model misses.** `rejected`/`skipped`/`dropped` were 0 on every panel pass since 11-01, so the model
   returned no item at all on those passes. A model that misses a spoken fact (D1 here) also leaves every counter at 0.
   The one pass logged since #2410 reads `reason=extractor_returned_no_facts user_turns=3->3 user_chars=49` (about 16
   characters per user turn). The same code path stored 1 to 4 rows on 11 of 38 real panel passes between 10-12 and 10-28.
   Between 10-24 and 11-12 the only commit on the extractor path is #2279, and it returns before the
   `MEMORY_IDLE_CONSOLIDATE` line is logged.
3. **What would settle it:** an owner-planted fact. The owner says one durable fact to the panel in passing (for example
   "my brother lives in X"), and the next pass for that session is checked for `stored>=1`. A pass with
   `extractor_returned_no_facts` on that session would mean B on real ASR text. A store would mean the path works there,
   and the zero is the speech.

The lab also found two deterministic defects [Proven] and one model miss [Experimental] that lose or misattribute facts
when facts are spoken (see "Defects"). Only the model miss can produce a pass where every counter is 0, which is why
point 2 cannot separate A from B.

## Method

1. `fixtures.py`: 20 INVENTED sessions (the fictional Quillfeather household): 8 with durable facts stated in passing
   (D1-D8), 6 oblique / implicit (O1-O6), 6 pure commands (C1-C6). Each session carries the facts a good extractor should
   produce (`must` tokens for lenient scoring) and, for O6, a `foreign_spans` label: words that belong to someone else.
   Assistant turns are included, as on the panel.
2. `run_split.py` drives the REAL `memory_idle_consolidation.consolidate_session` with a fake connection. Rows are shaped
   as `routers/chat._save_chat_message` writes them (`metadata={"user_id", "traffic_class"}`). The forget ledger is an
   empty stand-in and `_ingest_or_supersede` is a recorder.
3. **Fail-closed isolation**, installed before any production import:
   - Every lab directory (stores, output) is created under the fixed root `/tmp`, passed explicitly, so `TMPDIR` cannot
     move it. A directory whose resolved path is inside the live checkout, this repository or any git work tree is
     refused (and removed).
   - The store / DB env names point at the store dir, which is removed at exit, or at an invalid DSN. Names only:
     `POSTGRES_URL`, `DATABASE_URL`, `ZOE_DATABASE_URL`, `POSTGRES_DSN`, `MEMPALACE_DATA_DIR`, every store path the
     zoe-data test conftest pins, `ZOE_HARNESS=1` and `GEMMA_SERVER_URL`.
   - Guards raise a `BaseException` and record the attempt if anything builds an asyncpg pool or connection, any chromadb
     client, or a sqlite3 file outside the temp dir, or opens any outbound socket (TCP or AF_UNIX). The one exception is
     127.0.0.1:11434, and only inside a model call.
   - The isolation check is an EXIT assertion. Every mode runs inside `_run_guarded`, which on every path (normal
     return, a refusal, an uncaught `GuardTrip`, any other exception) asserts that no guard tripped and that
     `db_pool._pool`, `memory_service._service_singleton` and `memory_service._AUDIT_CLIENTS` are empty. It prints
     `exit-isolation=...`, and a failure makes the exit code 4.
   - `--selftest` passes 29/29:
     - every guard trips, including on the production `db_pool.init_pool()` and `memory_service._palace_client()`, and
       in-memory sqlite still works;
     - `--out` refuses paths, `..`, hidden names and the empty name `""`, and the writer refuses a planted symlink and
       an existing file;
     - a managed dir lands in `/tmp` even with `TMPDIR` set to the repo, and the unsafe-dir check refuses the repo, a
       subdir of it and the live checkout;
     - the exit assertion returns 4 for a run that swallowed a trip and then crashed, and for an uncaught trip; it
       keeps a clean refusal's code (3) and passes a clean run (0).
   - The self-test trips guards on purpose. It resets the trip record after those deliberate trips, and its own final
     `exit-isolation=clean` covers only what ran after the reset. It is not evidence that the deliberate part was
     clean.
   - The gates replay and the rescore each ended `exit-isolation=clean`, and so did a rescore run with `TMPDIR` set to
     the repo, which still wrote under `/tmp`. The model stage ran
     on 2026-11-12 with the earlier version of this runner, which unset the DSN names and replaced the stores after
     import, without these guards. Its outputs are reused below; they were not re-run.
4. **Model stage** (`--model`, run once on 2026-11-12):
   - The real `_extract_facts_with_gemma` -> `_facts_call` against the local Gemma 4 E4B, with the real prompt.
   - 20 calls in total (cap 20): 14 fact sessions, 3 sampled command sessions and 3 lower-case reruns.
   - Niced, under `flock /tmp/zoe-voice-harness.lock`, with a preflight before every call (`night_busy.sh voice`
     non-zero, no `land_voice_pr.sh`). One run was refused and waited for a landing to finish.
   - Selection rules, fixed by hand before the calls:
     - commands: one light command (C1), the one multi-turn music command (C4) and the list edit (C5);
     - lower-case reruns: the one durable miss (D1) and two durable hits (D3, D7).
5. **Scoring** (`--rescore`, no model): three separate numbers per expected fact, from the real functions.
   - raw = a model item carries the `must` tokens;
   - gated = such an item's source turn is kept by `own_words.filter_turns` AND its text passes `_fact_text` and
     `memory_quality.is_storable_fact`;
   - ingest = such an item was handed to the ingest recorder.
   - The model run predates the recorder storing texts, so ingest is DERIVED from the loop's rule (every non-empty,
     storable item goes to ingest). The derived count is cross-checked against the per-session count that the real loop
     handed to the recorder in that run: 0 mismatches.
   - All tables below are re-derived from the stored run, and no number needed a re-run.
   - The stored run is committed as `stored_run.py`. It is sanitized to the fields the rescore reads: per item the fact,
     the quote and the run-time `is_storable_fact` verdict; per session the count handed to the ingest recorder.
   - It holds only fixture lines and the model's output about the invented household. `--rescore` refuses it unless
     every stored quote is found in a `fixtures.py` user turn.
   - `python3 labs/panel-extraction-split/run_split.py --rescore` (zoe-data interpreter, no model call) reproduces the
     tables below.

Run (all under `flock -x -w 3000 /tmp/zoe-test-lane.lock`, interpreter `~/.zoe/venvs/zoe-data-py312/bin/python`):
`run_split.py --selftest`; `run_split.py --variants asr,lower`; `run_split.py --rescore` (reads `stored_run.py`); the
model stage adds `--model --only <ids>`. `--out` takes a file NAME, never a path; omitted, a name is generated, and an
empty name is refused. The JSON goes into a newly created `/tmp/panel-split-out-*` directory with an exclusive,
no-follow create (it refuses an existing file and a symlink), and the script prints where it wrote. `--selftest` covers
these refusals.

## Gates between "turns exist" and "candidate written"

| # | gate | file:line | exercised | dropped (20 sessions x 2 styles) |
|---|---|---|---|---|
| 1 | session selection: turns in last `LOOKBACK` (3600 s), idle `IDLE` (180 s), `n >= MIN_TURNS` (2) | `memory_idle_consolidation.py:196-227` | no (needs Postgres + clock) | n/a; MIN_TURNS re-checked at gate 4 |
| 2 | owner resolved from per-turn metadata, else skip | `:232-234`, `:54-79` | yes (`_resolve_owner`) | 0 |
| 3 | `off_record` rows excluded at fetch | `:261` | no (SQL) | n/a (fixtures carry no off_record) |
| 4 | `len(rows) < MIN_TURNS` -> return | `:335` | yes | 0 |
| 5 | owner re-resolved, else skip | `:341-344` | yes | 0 |
| 6 | role == user, non-empty content | `:350-351` | yes | 0 sessions (assistant rows removed, by design) |
| 7 | own_words: pasted / third-person speech removed | `:357`, `own_words.py:501` | yes | 0 (it SHOULD have trimmed O6: defect 2) |
| 8 | forget ledger: turns naming a forgotten entity skipped (fail-closed) | `:363-369`, `memory_digest.py:1928` | empty stand-in | 0 by construction; live log: 0 `idle_consolidation skipped` lines |
| 9 | empty transcript -> `[]` without a call | `:378` | yes | 0 |
| 10 | pack into chunks, numeric dates normalised | `memory_digest.py:2186-2195` | yes | 0 (each session is one chunk, byte for byte) |
| 11 | model call; reply must contain a JSON array | `memory_digest.py:2071-2104` | `--model` | see results |
| 12 | merge / cap per chunk | `memory_digest.py:2214` | yes | 0 |
| 13 | item without fact text skipped (uncounted) | `memory_idle_consolidation.py:399-401` | yes | 0 |
| 14 | `is_storable_fact` -> `rejected` | `:403-413`, `memory_quality.py:539-540` | yes | 1 expected fact (D2, false reject: defect 1) |
| 15 | ingest (identity, foreign voice, PII, forgotten, dedup, authority) -> `stored/skip/dropped` | `:418-432`, `memory_service.py:1505+` | recorder only | n/a |

Before the model, 40 of 40 replays reached the extractor, and every expected fact's key words were in the prompt [Experimental].

## Model results, per class [Experimental; synthetic; one sample per session; temperature 0.1; ASR style]

| class | sessions sent | with >= 1 item | expected facts | raw model hit | survives deterministic gates | handed to ingest | wrong owner |
|---|---|---|---|---|---|---|---|
| durable (D) | 8 of 8 | 7 | 8 | 5 | 4 | 4 | 0 |
| oblique (O) | 6 of 6 | 5 | 6 | 4 | 4 | 4 | 1 |
| command (C) | 3 sampled of 6 | 0 | 0 | - | - | - | 0 |

Per session:

| session | items | raw | gated | ingest | notes |
|---|---|---|---|---|---|
| D1 cashew allergy ("Not that one, I'm allergic to cashews.") | 0 | no | no | no | model miss; also 0 items in the lower-case rerun |
| D2 daughter's birthday | 1 | **yes** | **no** | no | raw hit, rejected by `is_storable_fact` as `meta_rambling` (defect 1) |
| D3 new job | 1 | yes | yes | yes | same in the lower-case rerun |
| D4 vegan | 1 | yes | yes | yes | |
| D5 brother's town | 1 | yes | yes | yes | |
| D6 dog is a kelpie | 1 | no | no | no | item "has a dog named Pickle": partial, breed lost (that item does reach ingest) |
| D7 Thursday football | 1 | yes | yes | yes | same in the lower-case rerun |
| D8 partner works nights | 1 | no | no | no | item "has a partner named Odalys": partial, job lost (that item does reach ingest) |
| O1 coeliac | 1 | yes | yes | yes | |
| O2 mum's birthday Saturday | 1 | yes | yes | yes | |
| O3 bakery early shift | 1 | yes | yes | yes | |
| O4 child needs rain sounds | 0 | no | no | no | about another person, implicit |
| O5 knee surgery | 2 | yes | yes | yes | plus a reminder restated as a fact |
| O6 brother quoted ("Corvin rang and said, \"I'm moving ...\"") | 1 | no | no | no | **wrong owner**: "User is moving to Halvercombe next month." passes every gate and reaches ingest (defect 2) |
| C1, C4, C5 (sampled commands) | 0 | - | - | - | correct; C2, C3, C6 not sampled |

`memory_authority.check_observation` (an approximation of ingest grading; idle consolidation does not call it) called 6
of the stored items `unsupported`: inferential wording, such as "is vegan" from "I'm vegan now".

## Defects

1. **`is_storable_fact` rejects any fact that starts "The user's ..." as LLM meta-rambling** [Proven, deterministic].
   - Where: `memory_quality.py:97`, the `user'?s` alternative of `the\s+(...)` in `_META_OPENER_RE`.
   - It matches "The user's daughter Tamsin turns eight on 14 March." -> `(False, 'meta_rambling')`, while "User's daughter
     ..." passes. Gemma writes relationship facts as "The user's X".
   - Smallest fix: restrict the alternative to meta nouns, for example
     `user'?s\s+(?:statement|question|message|request|response|input|query|words)\b`, with a test pair: the D2 fact
     passes, and "The user's statement suggests ..." is still rejected.
2. **Quoted third-person speech with an elided subject is read as the owner's** [Proven, deterministic].
   - Symptom: `own_words.analyze('Corvin rang and said, "I\'m moving to Halvercombe next month."')` comes back
     unchanged, while `Corvin said, "..."` and `Corvin rang and said "..."` are caught.
   - Where: `own_words.py:152`. The `who3` alternative needs the opening quote immediately after the verb, so a comma
     between them (`said, "`) slips through, and `and` is not a `_WHO`.
   - Smallest fix: allow an optional comma by inserting `,?[ \t]*` between the verb and the opening-quote group `q3`.
   - Effect in this lab: the owner was recorded as moving house.
3. **Model miss on a fact inside a refusal** [Experimental]: D1 "Not that one, I'm allergic to cashews." returned `[]` in
   both styles. One case, so it cannot be generalised. A prompt line ("facts stated in passing inside a request or a
   correction count") is the next experiment, not a fix to ship blind.

Defects 1 and 2 are already being fixed on branch `feature/recall-similar`.

Also seen [Experimental]: one fact per sentence. The second clause was lost in D6 (breed) and D8 (job).

Side findings:
- [Proven, code] Idle consolidation passes no `speaker_verified` to ingest (`memory_idle_consolidation.py:418-423`), and
  it does not read the `speaker_verified: false` mark the voice lane writes when the speaker gate rejects a turn. A turn
  the gate refused is therefore mined as the member's own words.
- [Speculative] Because of the 3600 s lookback (`:204`), a session whose sweeps failed for more than an hour is re-read
  only if it gets a new turn later. There were 55 sweep failures live, 33 of them "db pool exhausted".

## Limits

- Synthetic: one invented world written by the experimenter, 20 sessions, one model sample each at temperature 0.1.
  Nothing here measures what real household sessions contain.
- Only 3 of the 6 command sessions were sent to the model. The lower-case style was sent for 3 sessions.
- The model stage ran before the fail-closed guards existed (method item 3). Its process used the earlier stand-ins: DSN
  names unset, stores replaced after import.
- The real ASR's casing and punctuation were not read (privacy rail).
- Not exercised, because they need live stores: the session-selection SQL, the off-record fetch filter, a forget ledger
  with real entries, and the real ingest (MemPalace, authority grading, dedup).
- The live numbers (11 of 38 pre-11-01 storing passes, one `ALLZERO` line) are marker counts from `~/.zoe-logs/`. No turn
  text was read.
