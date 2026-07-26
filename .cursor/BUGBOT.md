# Bugbot review guide — Zoe

Read `AGENTS.md` first; it is the binding contract. This file is the short list of
things that have actually broken this repo, so a local `/review` catches them before
any cloud reviewer is spent.

## The rocks — never propose swapping these

`Gemma 4 E4B-QAT + MTP` (brain), `Moonshine v2 Medium` (STT), `Kokoro` (TTS). Optimise
*around* them. A PR that swaps one fails `test_canonical_invariants.py` by design.
Flag any change that reaches for a different model instead of working within these.

## Voice path — the highest-consequence area

Files: `services/zoe-data/routers/voice_tts.py`, `zoe_core_client.py`, `fast_tiers.py`,
`tts_waterfall.py`, `scripts/setup/zoe_voice_daemon.py`, Kokoro/Moonshine config.

- **Said-vs-did is the bar.** A command that used to work and now doesn't is a bug, not
  a regression to argue about. "Can't do it" = bug.
- **The replay gate starts from a saved WAV**, so it cannot see endpointing, VAD,
  barge-in or TTS. A change in those is invisible to CI — say so in review.
- **The Kokoro phrase cache only serves at `speed == 1.0`** (~2ms vs a 1–2.5s cold
  synth). Anything that varies speed on short, frequent utterances silently destroys
  the hot path.
- New always-on work must not land inside `zoe-data` — sidecars or subprocesses only.

## Flags and defaults

- Every new behaviour ships behind a flag, **default OFF**, and the flag-off path must
  be byte-identical to current behaviour. `None` beats a "neutral" literal value.
- Read flags with the shared `typed_env` helpers (`env_bool`/`env_int`/`env_str`).
  A hand-rolled wrapper works at runtime but is **invisible** to
  `tools/audit/flag_inventory.py` — a flag nobody can find is a flag nobody can turn off.
- Never read a live flag's value from its code default. The default and the deployed
  value disagree often, and every wrong conclusion in this repo has lived in that gap.

## Tests and gates

- Tests join CI by a co-located `pytestmark = pytest.mark.ci_safe`, never by
  enumeration in `validate.yml`. Hand-listing files silently drops new tests.
- **A gate that cannot go red is not a gate.** New checks need a negative control:
  break the thing on purpose and show the test fails. Skip / timeout / absent artifact
  is never a pass.
- Watch for fabricated denominators and clamped divisors (`max(1, …)`) — they invent a
  verdict out of missing data. "No evidence" and "everything failed" are different.

## Repo hygiene

- Retire by deleting; git keeps history. No `_old` / `_v2` / `_fixed` / backup copies.
- Don't duplicate mechanics that already exist — check for a shared helper first.
- Timezone-dependent logic uses the household timezone (`ZOE_TIMEZONE`, default
  `Australia/Perth`), never the host clock. This box runs UTC; the house does not.
- New root-level files must be registered in `.zoe/manifest.json`.

## What to prioritise

Rank findings by whether they can **fail silently**. A swallowed error, an unchecked
return code, a cache that quietly stops hitting, or a worker that loses auth without a
message costs far more here than a style issue — those are the bugs this codebase
actually ships.
