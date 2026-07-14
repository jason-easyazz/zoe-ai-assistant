# router-selftrain — miner + labeler (lane A)

Turns **real family traffic** into labelled router training examples, so the
router learns its **own measured mistakes** instead of more synthetic templates.

```
shadow log (what the router DID)  ─┐
                                   ├─► hard cases ─► local Gemma oracle ─► candidate_<stamp>.jsonl
chat_messages (what was SAID)     ─┘                (JSON-schema constrained)
```

Lane A (this dir) produces candidates. The **train → eval → promote orchestrator
(lane B)** consumes them. Nothing here is wired into the runtime; it is hand-run
(or driven by lane B) and it never sends an utterance off-box.

## Run

```bash
python3 labs/router-selftrain/mine_candidates.py --dry-run   # mine + guard, no brain, no write
python3 labs/router-selftrain/mine_candidates.py             # mine + label + write
python3 labs/router-selftrain/mine_candidates.py --since $(date -d '7 days ago' +%s)
```

Outputs (git-ignored — they contain raw family text):

- `data/router_selftrain/candidate_<UTCSTAMP>.jsonl` — one row per example, in the
  exact shape `labs/functiongemma-finetune/train_lora.py` already consumes:
  `{"text", "tool", "args", "source"}`, `tool: null` = the gold answer is **no tool
  call**, `source` = `selftrain-<reason>`.
- `data/router_selftrain/candidate_<UTCSTAMP>.meta.json` — counts per reason, the
  shadow window mined, the hash-join yield, and the held-out-guard result.

## Getting the raw text (the join)

The shadow log stores only a 12-hex **hash** of the utterance (`utt`) — the
router deliberately never writes the family's words. Training needs the words, so
there are two paths, both implemented:

| path | how | when |
| --- | --- | --- |
| **FORWARD** | `ZOE_ROUTER_SHADOW_TEXT=1` makes the router also write `utt_text` into the shadow-log **file** | rounds mined from traffic captured after the opt-in |
| **BOOTSTRAP** | re-hash every `chat_messages.content` with the router's own `sha256(text)[:12]` and join on the hash | history written before the flag existed |

The bootstrap join is **verified working**: the first real run recovered 37/65
distinct shadow utterances. The unresolved remainder are turns that never reached
`chat_messages` (eval-harness and warmup traffic).

### `ZOE_ROUTER_SHADOW_TEXT` — the opt-in flag

**Default OFF, and it should stay off unless the household has agreed to a
self-training round.** When on:

- raw text is added to the shadow-log **file only**; the INFO log line stays
  hash-only, so journald / log shipping never sees the words;
- the hash is still written either way;
- the text is mined by a local script and labelled by the **local** Gemma brain.
  **It never leaves the box.**

## Mining reasons

Only **two-stage** records (`mode` ∈ `shadow2`/`active`) can be mined — a
head-shadow-only record has no router decision to disagree with.

| reason | condition | gold |
| --- | --- | --- |
| `disagreement` | two-stage picked a different domain than the live route, and the live route was a real tool domain | the correct call for that domain |
| `abstention` | two-stage gated / abstained / failed, but the live route **was** a real tool domain | the correct call for that domain |
| `chat-negative` | the live route was `chat` | no tool (`null`) |

Agreement on a tool is **not** mined — there is nothing to learn from a case the
router already gets right.

`chat-negative` covers two shapes: plain **reinforcement** (both said chat) and a
measured **false positive** (live chatted, two-stage fired a tool). The false
positives are the real mistakes, so they are kept **first** when the negative cap
bites.

## Labeling — the oracle is an independent second opinion

The live local Gemma brain (`:11434`) is asked for the gold call with a
`response_format: json_schema` grammar, so it can only emit a legal tool name (or
the explicit `none` sentinel) plus an args object.

The oracle **always sees the full 20-tool menu and is told nothing about what the
live router did**, and a row survives only on **two-source agreement**:

- **tool reasons** — kept only if the oracle names a tool whose domain matches the
  live route's domain. Oracle says `none`, or names an off-domain tool → **dropped**.
- **chat-negative** — kept only if the oracle *also* declines to call a tool.

This is load-bearing, not paranoia. `actual_routed` is the *similarity router's*
choice and it can itself be wrong: the first dry-run turned up **a smart-home
utterance that the live router had sent to the `timers` domain**. Hinting the live
domain to the oracle would have had it rubber-stamp that misroute, and we would
have trained the router to set a timer for a light. Args are filtered to the tool's
legal keys; anything the oracle can't answer cleanly is dropped. **A wrong label is
far worse than a missing one — this corpus trains an autonomous loop.**

Calls are serial, `/slots`-gated and paced, so a mining run never fights a live
voice turn. (Using `:11434` as a *labeling oracle* follows the precedent set by
`functiongemma-finetune`'s paraphrase generation — operator-sanctioned, gentle,
serial. No lab **engineering** model runs here.)

## Safety rails

- **HELD-OUT GUARD (the loop's integrity property).** Every candidate is
  normalised and compared against the frozen 81-case eval corpus
  (`labs/needle-benchmark/corpus.jsonl`). **One match aborts the run and writes
  nothing.** It is an abort, not a filter: a collision means the window is
  contaminated, and silently dropping the rows would hide that. Training on the
  promotion gate's own corpus would destroy the whole safety property of the loop.
- **Caps** — ≤ 400 examples/round, ≤ 100 chat-negatives, so one bad week cannot
  swamp the corpus.
- **Dedup** — normalised text, within the round *and* against every existing set in
  `labs/functiongemma-finetune/data/*.jsonl`.
- **Local only** — no cloud API ever sees an utterance.
- Candidate files are **git-ignored**; raw family text is never committed.

## First real run (2026-07-14)

173 shadow records → hash join 37/65 → 16 mined → 11 after dedup → **6 labelled**
(6 chat-negative, 0 disagreement, 0 abstention).

Both tool candidates were **dropped by the two-source guard**: one meta-question
about Zoe's own capabilities (live-routed to `lists`) and one smart-home command
(live-routed to `timers`) were *the live router's own misroutes*, and the oracle
correctly refused to complete them. The guard did exactly its job.

> Deliberately no verbatim utterances in this file: mined text is real family
> traffic and stays on the box. Findings are described, never quoted.

The honest read: **tool-example yield is currently limited by shadow-log volume**,
not by the miner. The window is small and mostly harness traffic, and the hash join
only recovers turns that reached `chat_messages`. Volume comes once
`ZOE_ROUTER_SHADOW_TEXT=1` is flipped for a round and real family traffic
accumulates.

## Tests

`tests/unit/test_router_selftrain_miner.py` (marked `ci_safe`) — held-out guard
aborts, dedup, each mining reason's shape, the hash-join contract, the oracle
contract (misroutes dropped), and the output contract lane B depends on. The
opt-in flag's privacy behaviour is covered in
`services/zoe-data/tests/test_router_head_shadow.py`.
