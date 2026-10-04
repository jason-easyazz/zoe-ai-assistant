---
type: Reference
title: Conversation-quality classes (2026-10-04) — own-fact recall, challenge verification, narrated recall
description: Three "doesn't work like a human assistant" classes seen live on 2026-10-04, the flag-dark fix for each (ZOE_OWN_FACT_PRECEDENCE, ZOE_VERIFY_ON_CHALLENGE + ZOE_TRIVIA_HEDGE, ZOE_STRIP_NARRATION), the live code path each rides, what is and is not wired, the router-corpus retrain step, and how to flip and roll back.
tags: [conversation, router, recall, web-search, zoe-data, flag-dark, samantha]
timestamp: 2026-10-04T14:00:00Z
---

# Conversation-quality classes (2026-10-04)

Everything here is **flag-dark (default OFF)**. With every flag unset the outbound brain
message and every routing decision are byte-identical to before (pinned by the negative
controls named below). Nothing here touches the Gemma brain, the Flue sidecar (`labs/`), the
router GGUF or the served head files.

## 1. Own-fact questions never reach a clock/calendar/weather tool — `ZOE_OWN_FACT_PRECEDENCE`

**Observed.** "When is my birthday" → head `time` (the word "when") → "It's 7:50 AM." The
same family: "what's my address", "how old am I", "where do I live", "when is mum's birthday".
The S1 floor (`ZOE_ROUTER_HEAD_MIN_CONF`) only helps when the head is *unsure*; this one is a
*confident* misroute, which no threshold catches (see `two-stage-router-rollout.md`).

**Rule** (`memory_gate.own_fact_question_kind`): a possessive / first-person anchor AND a fact
noun (birthday, address, age, phone number, email, name…) that ENDS the question, or a first-person
stored-fact verb ("where do I live", "when was I born"), plus a bare "who am I" (anchored to the whole
utterance: "who am I meeting tomorrow" is a calendar question). Only my/our and the user's own relations
("mum's") count as possessives: "Obama's age", "what's my phone bill", "what's my job today" are not
own-fact. "what time is it", "when is Easter", "what's my
schedule", "how old is the universe", "when is my birthday party" never match.

**Live path** (one rule on every head surface, like the evidence and event-time rules):

| surface | where | effect when the flag is on |
|---|---|---|
| Tier-1 router (`route()`; voice, chat, Skybridge) | `semantic_router.question_precedence` → `own_fact_target` (called from `_apply_question_precedence`) | a clock/calendar/weather/list/timer claim is re-pointed to `memory` / `recall_memory`, `reason="own_fact_question"`; `chat`, `memory`, `people` claims are kept; an event-time question stays the event-time rule's |
| Keyword lanes (INTENT_GATE) | `semantic_router.head_verdict` → `fast_tiers.intent_gate_decision` | only a `memory_*` / `people_*` intent may answer; `time_query`, `date_query`, `calendar_show`, `weather`, … are vetoed |
| Flue recall floor | `zoe_flue_client._recall_question_shape` → shape `own_fact` | the for-prompt packet is injected (when `ZOE_SEAM_RECALL_INJECT` is on) for shapes the personal regex misses ("how old is my mum") |

Precedence order is evidence → event-time → own-fact.

**Router corpus.** The phrasings are committed with the right label — first-person facts →
`memory` / `recall_memory`, relation lookups → `people` (the label set's existing convention):

- stage 1: `labs/setfit-router/data/misses.jsonl` (`source: own_fact_miss_2026-10-04`)
- stage 2: `labs/functiongemma-finetune/data/train_misses.jsonl`

**Retrain status: NOT retrained, and not retrainable here.** A stage-1 head retrain needs the
bge-small embedding model (a model load) plus the training pins (`labs/setfit-router/requirements.txt`),
and publishing a head changes `services/zoe-data/models/*`, which is a **voice-path** directory
(replay-gated, parity fixture regeneration, voice merges are serial). The stage-2 GGUF retrain is
the self-train job's. So the guard is the live fix; the corpus rows make the next sanctioned retrain
learn the class. To retrain (a brain-stopped window; never delete `runs/<gen>/merged`):

```
cd labs/setfit-router && python3 train.py        # reads data/train.jsonl + data/misses.jsonl
cp artifacts/head_mlp.joblib    ../../services/zoe-data/models/router_head_mlp.joblib
cp artifacts/head_logreg.joblib ../../services/zoe-data/models/router_head_logreg.joblib
python3 scripts/maintenance/export_router_heads.py --corpus \
    --fixture services/zoe-data/tests/fixtures/router_heads_parity.npz
# regenerate tests/fixtures/router_samantha_probe_vectors.json; replay-gate the PR
```

Rollout order: replay gate on `~/.zoe-voice-samples` first, then set `ZOE_OWN_FACT_PRECEDENCE=1`.
Rollback: unset + restart. Tests: `services/zoe-data/tests/test_own_fact_precedence.py`.

## 2. Hallucinated trivia and doubling down on "are you sure"

**Observed.** A sports-history question got a wrong winner; "are you sure" got "I'm pretty
sure". The Flue brain has a `web_search` tool but a 4B model rarely elects to check itself.

### `ZOE_VERIFY_ON_CHALLENGE` — check the claim in code

`verify_on_challenge.prepare`, called first in `zoe_flue_client._run_flue_brain_streaming_turn`
(non-streaming rides the same path):

1. the message is a short standalone challenge ("are you sure", "that's wrong", "really?", "I
   don't think so", optionally with a short counter-claim);
2. the previous exchange (read from `chat_messages` for the session) is a **checkable world
   claim**: the previous user question is world trivia (`trivia_gate.is_world_trivia`: a who-won /
   what-year / how-many / capital-of shape, no personal anchor, no live-data cue, not a command), is
   not an own-fact / evidence question, is not owned by a keyword intent, and the previous assistant
   turn asserted something (not a tool confirmation such as "Reminder set…", not a decline);
3. ONE search, built from the previous user question, hard-walled at **8 s**, through
   `browser_broker.search_web`;
4. results → a `[MEMORY CONTEXT — live web check …]` block appended after the user's words telling
   the brain to say whether it was right, give the corrected fact and name the source domain;
   untrusted web text is framed as quoted DATA (instruction-shaped sentences are dropped, each snippet
   truncated, brackets/quotes neutralised); household questions (named people, family words, home/here,
   any capitalised word outside a small public-entity allowlist) are never "world trivia", so no household
   name is ever sent to a search provider; a spoken (`voice_mode`) challenge gets a 3 s wall instead of 8 s
   because no filler plays during the lookup;
   timeout / no results / blocked / error → the seam answers itself with an honest "I can't check that
   right now, so please treat my last answer as unconfirmed rather than certain." (no brain call;
   outcome label `seam_reply`, never `ok`).

**About "through the broker".** `browser_broker.py` has no search action: its CloakBrowser surface
reads a given URL, and launching Chromium for one check is the wrong cost on this box. The search
tier the repo already runs for chat and for the brain's `web_search` tool is
`research_evidence.fetch_web_fallback` (Tavily when `TAVILY_API_KEY` is set, else DuckDuckGo HTML,
with an honest status). `browser_broker.search_web` is the broker's single bounded entry point onto
it (never raises, http(s)-only results with domains). Reusing the registered `[MEMORY CONTEXT` block
pair means no sidecar change and stale copies are elided automatically.

### `ZOE_TRIVIA_HEDGE` — hedge before the challenge ever comes

A world-trivia question (same `is_world_trivia`) on a turn with no memory block gets one appended
`[MEMORY CONTEXT — accuracy note …]` block: if you answer from memory rather than a tool, say it as
what you recall and offer to check it online. Never beside a verify/recall/continuity block.

Not covered, by design: the voice lane that bypasses the Flue seam (zoe-core / legacy lane — the legacy
`zoe_agent._is_verification_challenge` path stays as it was), and any *spoken* UX for the lookup delay
(an 8 s wall on a voice turn needs the voice filler path and a replay gate before it is switched on
for voice). Tests: `test_verify_on_challenge.py`, `test_broker_search_web.py`.

## 3. Narrated recall — `ZOE_STRIP_NARRATION`

**Observed.** "Who am I" → "I'll check what I've got on file about you." then the answer. The 4B
announces the `recall_memory` call in text before making it; both reach the user.

- **At the source:** the recall block's open line gains "use them to answer directly, never say you
  are checking or looking anything up" (`zoe_flue_client._recall_block_open`; only on turns where the
  recall floor injects a block). The brain's soul prompt (`labs/flue-zoe-brain-2x/src/agents/zoe.ts`) is
  deliberately **not** edited: a `labs/flue-zoe-brain*` change auto-restarts the Flue sidecar on deploy.
  The follow-up there is one line in `PERSONAL_RECALL_DOCTRINE`: "call the tool silently; never announce it".
- **Post-filter** (`narration_filter`, wrapping the stream in `run_flue_brain_streaming`): drop up to
  two LEADING lookup-announcing sentences ("I'll check…", "Let me look…", "Checking…", "One moment while
  I look that up", "Okay, I'll see what I remember…") when a real answer follows. When the answer is in the SAME sentence ("Let me look: you have 3 events today." — colon, dash, or a comma before an answer word) only the announcement clause goes. The sentence splitter respects abbreviations (Dr., Mr., St., e.g., i.e., "No. 5"), initials and decimals. Kept: a sentence
  carrying a promise ("…and get back to you", "let you know", "tomorrow", "with you"), an announcement with
  nothing after it, and anything not at the very start. Tool/thinking sentinels pass straight through.
- "who am I" / "tell me about myself" / "what do you remember about me" are `own_fact` shapes (kind
  `self`), so with `ZOE_OWN_FACT_PRECEDENCE` the recall floor also claims them.

The voice processing fillers ("Let me check.", `ZOE_VOICE_FILLER_PHRASES`) are separate spoken events,
not part of the reply, and are untouched. Tests: `test_narration_filter.py`.

## Bar fixtures

`scripts/perf/samantha_bar_conv.py` holds S17 (own-fact vs clock), S18 (challenge → source or honest
can't-check, never "pretty sure") and S19 (answered, not narrated) as synthetic asks + pure scorers,
pinned by `tests/unit/test_samantha_bar_conv.py`. They are **not** in `samantha_bar.SCENARIO_IDS`:
wiring them changes the plan and the baseline contract, and each needs its flag on in the live service
first. To wire: add the ids to `SCENARIO_IDS` / `SCENARIOS` with `expected: "FAIL"` (targets), add a
`put(...)` per scenario in `run_scenarios` using the scorers, flip the flag, re-record. The bar was not
run for this change.

## Unverified (no live run was possible)

- Live behaviour of every flag: the head's real scores on the new phrasings, the real Tavily/DDG
  response for a trivia query, and the 4B's reaction to the new blocks. The tests fake the head, the
  sidecar, the search and the history.
- Whether `chat_messages` already holds the current user turn when the seam runs (the reader handles
  both orders).
- The replay gate (`voice_regression_probe.py`) for the voice-path files touched
  (`semantic_router.py`, `fast_tiers.py`, `zoe_flue_client.py`, `memory_gate.py`): required before the
  router flag is set; the flags being dark makes this PR a no-op at runtime.
