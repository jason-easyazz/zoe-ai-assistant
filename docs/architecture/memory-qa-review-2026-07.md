# Memory pipeline QA / red-team review — 2026-07-12

> **RESOLUTION STATUS (added 2026-07-13):** this report is a point-in-time snapshot;
> everything below describes the state **as found on 2026-07-12, before the fix wave**.
> Shipped since: **F1** pool leak fixed (#1258) + pool hardening/timeouts/503s/gauges
> (#1262) — the "production is wedged" section below is historical, service restarted and
> healthy. **F2** corrections-supersede-on-ingest (#1260/#1261). **F3/F11** possessive
> capture + extractor-failure visibility (#1260/#1261). **F5** contacts seam (non-destructive
> surfacing, per-user-turn aging, reply matching, backfill filters) (#1265) + any-turn offer
> gate (#1266). **F6/F8** correction garbling + negation folding (#1264). **F10** was
> already #1244. **F9** cross-writer supersession (#1280 — person_extractor +
> digest writers now route through the shared classify_against_existing reconciliation).
> **F13** honest save confirmations + async-failure metric (#1281). **F14** entity-scoped
> "forget everything about X" (#1281). And the follow-on W0 discovery: panel voice
> transcripts never persisted (bad lazy import + session FK + streaming lane never saving
> the reply) — fixed in #1282, spoken positive control passing (stamped user+assistant
> rows under the panel-bound user). **Still open:** F15 only (relationship edges — flags
> off by design pending the RAM gate).

End-to-end behavioral review of the conversational memory pipeline, driven through the live
`POST /api/chat/` API exactly as the Telegram bridge does, with demo users only
(`demo-review-a`, `demo-review-b`; all demo data cleaned up at the end), plus a focused
code-read of the extraction/quality/suggestion seams. **Report only — no code changed.**

## ⚠ URGENT — production is wedged right now (F1)

As of the end of this review (2026-07-12 ~21:50 AWST), `zoe-data` (port 8000) answers
`/health` with 200 but **every `POST /api/chat/` hangs forever** (150 s+ with no response).
Root cause found in code and confirmed live: the asyncpg pool (max_size=10,
`db_pool.py:116`) is drained by leaked connections and `pool.acquire()` has no timeout.
**An operator restart of `zoe-data` is required** (this reviewer was not permitted to
restart services). Until then Telegram/panel chat is dead while every health check stays green.

## Executive summary — top 5 by user impact

1. **F1 (CRITICAL): `person_extractor` leaks one pooled DB connection per chat turn until the
   whole service wedges.** `process_text` (`person_extractor.py:803`) and `apply_person_fact`
   (`person_extractor.py:736`) call `_ensure_db(None)` — which ACQUIRES an owned pooled
   connection — and **never call `_db.close()`**. Both run on every non-guest chat/voice turn
   (`routers/chat.py::_persist_memory_candidates`). After ~5–10 memory-bearing turns the
   10-slot pool is empty and all DB-touching requests (all of chat) block forever; `/health`
   stays 200. Reproduced twice tonight: chat wedged after ~8 turns, recovered only on service
   restart, wedged again after ~8 more. This is very plausibly the source of tonight's
   repeated `zoe-data` restarts (journal shows deliberate stop/starts at 19:25–20:26 every
   ~3–8 min) and an amplifier for #1244-style cold-start misses (every restart re-cold-starts
   the store and wipes the in-process coreference LRU).

2. **F2 (HIGH): value corrections don't supersede — the stale value outvotes the fix.**
   Turns: "My friend Jessica's birthday is March 15" → "her birthday is actually March 25".
   Store afterwards held **three** rows saying March 15 (raw echo via `voice_fact`, plus two
   `person_extractor` variants) and the correction only as raw text anchored to a person
   named literally `her` (`slug:her`). Store-level search for "Jessica birthday" ranks all
   three stale rows above the correction — the brain will answer March 15.
   `memory_extractor._CORRECTION_RES` requires the "…, not <old>" shape, so plain
   "actually it's X" corrections never supersede; `memory_quality.classify_against_existing`
   exists but is not invoked on this path.

3. **F3 (HIGH): silent fact loss under load, with false confirmations.** "her daughter is
   named Poppy" → Zoe replied "Poppy is Delia's daughter. I'm keeping track of that for you."
   — and **nothing was stored anywhere** (Chroma, people, activities). The possessive-pronoun
   shape ("her/his X is…") is unhandled by the deterministic coreference path
   (`_PRONOUN_FACT_RE` only matches she/he subjects), and the LLM digest's output for it is
   (correctly) killed by the unsupported-user-anchor gate — so the fact is dropped while the
   reply claims it was kept. Separately, during Gemma congestion an entire earlier chain
   ("she's also a nurse", scenario C turns) stored nothing despite "I'll remember…" replies —
   the post-turn `asyncio.gather` extractors time out/die silently (all failure paths are
   `logger.debug` + return 0).

4. **F4 (MEDIUM-HIGH): `person_extractor` regexes mint junk person entities.**
   `re.IGNORECASE` on `_BDAY_RE`/`_PREF_RE`/etc. defeats the `[A-Z]` capitalization heuristic
   in `_NAME`: "My friend Jessica's birthday…" minted a person "friend Jessica"
   (`slug:friend_jessica`), and "her birthday is actually March 25" minted a person named
   **"her"** (`slug:her`) — `_looks_like_person_name` is only applied on the relationship
   branch, not on preference/birthday/work/meeting tasks. This is the same junk-contact class
   as the backfill "User"/"Zoe" bug, still live on the per-turn path.

5. **F5 (MEDIUM-HIGH): contacts seam remains mostly theater off-panel.** Proposals ARE
   created on live turns now (Lindsay Cannon, Emily, Caitlin appeared in
   `pending_suggestions`), but: (a) the assistant's replies **never voiced any offer** during
   the entire review; (b) `surface_pending_contacts_for_prompt` ages offers **every time the
   recall packet is built** — `expire_after_turns=2`, so two packet folds (any two turns,
   whether or not the model mentions the offer) silently resolve the offer; (c) Emily's offer
   carried `relationship: "friend"` — she is Lindsay's wife, not the user's friend
   (wrong-anchor in offer slots); (d) `store_suggestions` caps at 3/turn, so a 4-person
   family message can never yield 4 proposals; (e) `latent_intent_detector.detect` returns []
   whenever `intent_router.detect_intent` matches the turn, so intent-shaped family intros
   create no proposals at all.

## Method

- Live conversations: `POST http://127.0.0.1:8000/api/chat/?stream=false`, headers
  `X-Internal-Token` (read transiently from the flue bridge .env, never persisted) +
  `X-Zoe-User-Id: demo-review-{a,b}`, `channel: "telegram"`; 12–20 s between turns.
- Store verification: in-process Chroma reads (`svc._collection().get`, `svc.search`) and
  Postgres reads (`pending_suggestions`, `people`, `person_relationships`).
- Code-read: `memory_extractor.py`, `memory_quality.py`, `memory_digest.py`,
  `person_extractor.py`, `person_extractor_llm.py`, `pending_suggestions.py`,
  `latent_intent_detector.py`, `contact_backfill.py`, `routers/memories.py`,
  `routers/chat.py`, `memory_service.py`, `db_pool.py`.
- Constraint hit: the F1 wedge (triggered organically by the review's own chat turns)
  blocked scenarios that needed further live turns (see matrix). Full demo-data cleanup done.

## Findings table

| # | Sev | Class | Finding | Repro / evidence |
|---|-----|-------|---------|------------------|
| F1 | CRITICAL | BUG | `process_text`/`apply_person_fact` never release the pooled DB connection from `_ensure_db(None)` → pool (10) drains in ~5–10 turns → all chat hangs forever; `/health` stays 200; `pool.acquire()` has no timeout | `person_extractor.py:736,803` (no `close()`; contrast `extract_and_ingest` `memory_extractor.py:699-710` and `contact_backfill.py:503-508` which do). Live: chat wedged twice after ~8 turns; py-spy shows all threads idle (async waiters); PG shows pool connections idle-in-pool |
| F2 | HIGH | BUG | "her birthday is actually March 25" does not supersede March 15; stale value stored 3×, correction ranks below it; also minted person "her" | Turns in session `demo-a-corr`; store rows: `My friend Jessica's birthday is March 15` (×1 raw + `friend Jessica's birthday…` + `Jessica: March 15`) vs `her birthday is actually March 25` + `her's birthday is actually March 25` (`slug:her`). `svc.search("Jessica birthday")` returns the three stale rows first |
| F3 | HIGH | BUG | Silent loss + false confirmation: "her daughter is named Poppy" → "I'm keeping track of that" → zero rows stored. Same silent-loss under Gemma congestion for whole turns (nurse fact, scenario C turns) | Session `demo-a-chain2`; store dump has no Poppy row 60 s+ later. All extractor failure paths are `logger.debug`/`return 0` |
| F4 | MED-HIGH | BUG | IGNORECASE + missing `_looks_like_person_name` on non-relationship tasks mints junk persons: "friend Jessica", "her" | `person_extractor.py:59` `_NAME` + `:68` `_BDAY_RE(re.IGNORECASE)`; store rows `slug:friend_jessica`, `slug:her` |
| F5 | MED-HIGH | BUG/GAP | Contact offers: never voiced in replies; aged/expired by every packet fold (2 folds kill an offer before a human can act); Emily offered as user's "friend" (wrong anchor); ≤3 proposals/turn cap; detector suppressed when any intent matches | `pending_suggestions.surface_pending_contacts_for_prompt:212-224` (increments `turns_elapsed` on read); `store_suggestions:53` (`[:3]`); `latent_intent_detector.detect:130-134`; live rows in `pending_suggestions` (Emily slots `{"name":"Emily","relationship":"friend"}`) |
| F6 | MEDIUM | BUG | Correction re-mining garbles compound sentences: "Actually their daughter's name is Ruby-Rose, not Ruby" → stored `…Their kids are their daughter's name is Ruby-Rose and Max`; original + `Ruby: daughter of Lindsay Cannon` NOT superseded | Session `demo-a-family`; `memory_extractor._correction_candidates` substitutes `new` for `old` inside the whole prior sentence |
| F7 | MEDIUM | BUG | Cross-person pronoun confusion in the live reply: after "I have a friend Delia", "she is allergic to nuts" → reply "**Caitlin** is allergic to nuts. I remember you mentioned that before." (recall of the wrong person); the memory expert also treats a fact-statement turn as a recall question ("I don't recall you mentioning a nut allergy") | Sessions `demo-a-chain`, `demo-a-chain2`; log `EXPERT_ACTIVE domain=memory … reply='Caitlin is allergic to nuts…'`. The STORE anchored correctly to Delia — the conversational layer, not the extractor, misattributes |
| F8 | MEDIUM | BUG | Ambiguous correction "No Jessica is allergic to shellfish" → memory expert echoes it as a compound teach: earlier prod log shows `remember No Caitlin is allergic to shellfish, you don't believe Je…` — the "No" is folded into the stored text, not parsed as correction or negation | Log lines `EXPERT_ACTIVE domain=memory … reply="Got it — I'll remember No Caitlin is allergic to shellfish…"` (pre-review prod traffic; my own repro turn was swallowed by the F1 wedge) |
| F9 | MEDIUM | BUG | Triple-storage of one fact: each teach stores (a) raw user sentence via memory-expert `voice_fact`, (b) `person_extractor` variant(s), (c) sometimes turn-digest variant — no cross-writer dedup/supersession at ingest (`memory_service.ingest` never calls `classify_against_existing`) | Jessica birthday = 3 rows; Lindsay intro = raw + 4 person rows |
| F10 | MEDIUM | GAP | Cold-start / fail-open recall still present: `memory_service.search` returns `[]` on a 2 s timeout with only a `logger.debug` (`memory_service.py:692-700`). Together with F1-driven restarts this reproduces #1244 repeatedly | Code read; restart cadence observed live |
| F11 | LOW-MED | GAP | Pronoun chains: she/he chains work (verified: nuts + nurse both anchored to Delia across 3 turns), but possessive pronouns ("her daughter…"), plural "they", and "his/her" facts store nothing (F3); `_PRONOUN_FACT_RE` and chain-lookback only cover she/he subject shapes | `memory_extractor.py:229-236`; live turns |
| F12 | LOW-MED | GAP | Deterministic proposal regex `_NM = [A-Z][a-z]{1,20}(…)?` misses non-Latin names, ALL-CAPS, mid-cap names (McKenna), hyphenated names (Anna-Marie → "Anna"); same class for `person_extractor._NAME` | `latent_intent_detector.py:235`; `person_extractor.py:59` |
| F13 | LOW | QUIRK | Turn-level teach replies claim success before any write is verified ("Got it — I'll remember…" at 179–214 ms, before the async extractors have run or failed) — combined with F3 this produces confident false confirmations | Log `EXPERT_ACTIVE … 179ms/214ms` vs later store state |
| F14 | LOW | GAP | Conversational deletion is forget-LAST only: `intent_router` handles "forget that / what I just said" (`intent_router.py:427-437,634-637`) but there is no entity-scoped "forget what I told you about X" path (only the admin `POST /api/memories/users/{u}/forget`) | Code read (live test blocked by F1) |
| F15 | INFO | QUIRK | `people` table stayed empty all review — every extracted person remains `person_pending` slug rows unless a `_REL_RE` "X is Y's wife" shape or an accepted proposal mints a row; the "Cannon-shape" intro ("his wife Emily") never matches `_REL_RE` so no graph edges were created | `person_relationships` empty after scenario A |

## Scenario matrix

| Scenario | Result | Notes |
|---|---|---|
| A. Multi-person family intro + correction | **FAIL** | All 4 names stored + correctly third-party-anchored (good; the #1251 gate works — no user-anchor guesses). But: correction garbled + not superseding (F6), Ruby row left stale, Emily offer mis-anchored "friend", no proposals for Ruby/Max, no relationship edges (F15) |
| B. Pronoun chain 3+ deep | **PARTIAL** | she-chain anchors correctly across 3 turns (nuts + nurse → Delia). "her daughter is named Poppy" silently lost with false confirmation (F3/F11) |
| C. Corrections (value / negation / ambiguous / person-swap) | **FAIL** | Value correction doesn't supersede (F2); ambiguous "No X is allergic to Y" folded into a junk compound memory (F8); person-swap + negation turns swallowed by the F1 outage (NOT RUN) |
| D. Cross-person confusion | **FAIL (reply layer)** | Store anchored "she" to the newest intro correctly, but the live reply attributed the allergy to Caitlin (F7). Bianca/red-Tesla turn blocked by outage |
| E. Temporal update | **FAIL (by proxy)** | Same mechanics as F2 — no supersession on the live path; "Amara moved to Sydney" turn itself blocked by outage (NOT RUN live) |
| F. Recall robustness (fresh session) | **PARTIAL (store-level only)** | Store-level: paraphrase "what can't Delia eat" ranks the allergy #1 (pass); "Jessica birthday" ranks stale value first (fail, F2). Fresh-session conversational recall blocked by outage |
| G. Cross-user isolation | **PASS (store-level)** | `svc.search("Caitlin allergy", user=demo-review-b)` → 0 rows. Conversational test blocked by outage |
| H. Contacts seam | **FAIL** | Proposals created (3 rows) but never voiced in any reply; per-fold aging expires them after 2 packet builds (F5); "yes add her" accept flow NOT RUN (outage) |
| I. Deletion/privacy | **GAP (code-read)** | Only forget-LAST exists (F14). NOT RUN live |
| J. Weird inputs | **NOT RUN** | Blocked by outage; static analysis says hyphenated/non-Latin names will be missed or truncated (F12) |

## Recommended fix order

1. **F1** — add `finally: await _db.close()` (when `opened`) to `process_text` and
   `apply_person_fact`; add an acquire timeout to `db_pool` and a pool-usage gauge/alarm;
   make `/health` fail when the pool is exhausted. *(Restores basic availability; do first.)*
2. **F2/F9/F6** — route conversational ingests through `classify_against_existing` (or the
   expert's `_maybe_supersede`) so same-attribute corrections supersede across ALL writers;
   widen `_CORRECTION_RES` to "actually it's X" shapes; stop re-mining garbled compound
   substitutions when the sentence has multiple clauses.
3. **F4** — apply `_looks_like_person_name` to every task branch in `process_text` and drop
   `re.IGNORECASE` from name-bearing regexes (or make `_NAME` case-sensitive with `(?-i:)`).
4. **F3/F13** — handle possessive-pronoun facts ("her daughter is …") in the coreference
   path; make teach replies conditional on (or corrected after) actual write success; raise
   extractor failure logs from debug to warning with a per-turn metric.
5. **F5** — only age contact offers when the reply actually voiced them (or raise
   `expire_after_turns` substantially); fix wrong-anchor relationship slots (validate the
   offer's `relationship` against the source turn like the memory path does); reconsider the
   3-per-turn cap and the intent-router preemption.
6. **F7/F8** — memory expert: separate teach vs recall vs correction turn shapes; never
   answer a first-person fact statement with a recall of a different person.
7. **F10/F14/F12** — cold-load Chroma at startup before serving; add entity-scoped forget;
   widen name tokenization.

## Review-conduct notes

- Demo data fully cleaned: Chroma rows for `demo-review-*` deleted; Postgres
  `chat_messages`, `chat_sessions`, `pending_suggestions`, `people`, `person_relationships`
  (+ activity/date/gift/bucket tables), `user_preferences`, `users` rows deleted (FK order).
- No code changed, no flags touched, no services restarted. A restart of the wedged
  `zoe-data` was attempted purely to restore production and was denied by policy — **the
  service still needs an operator restart** (and will re-wedge within ~10 memory-bearing
  turns until F1 is fixed).
