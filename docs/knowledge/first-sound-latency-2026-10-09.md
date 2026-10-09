---
type: Reference
title: First-sound latency on brain turns (2026-10-09)
description: Live paired re-measurement of end of speech to first sound on memory, tool and chat brain turns (10-13 per shape), the three flag-dark levers it names, what was found and not fixed, and how to re-measure.
tags: [voice, latency, ttfa, first-sound, narration, kokoro, flue, measurement, d10]
timestamp: 2026-10-09T23:10:00+08:00
---

# First-sound latency on brain turns (2026-10-09)

Blueprint D10 asks for first sound about 2 s after the last word. After #1760/#1761 the measured path is **3.3 / 2.8 / 3.4 s** (memory / tool / chat). With the levers on it is **2.0 / 2.3 / 2.5 s**. Nothing is default-on: the three levers change what is heard, so they wait for an ear check and the replay gate (this IS a voice-path change). Earlier breakdown: [panel-ttfa-breakdown-2026-09-28.md](panel-ttfa-breakdown-2026-09-28.md).

## Method
`scripts/perf/measure_first_sound.py` (`ZOE_PERF=1`, harness lock, refuses during a landing / night window) speaks each prompt with Kokoro (0.32 s lead, 0.85 s tail), transcribes it with Moonshine, then runs the real router, tiers, `brain_streaming` (replay envelope, nothing written), the stream loop's own first-unit and filler rules, and Kokoro. Every prompt runs under every flag set (paired, order rotated, fresh session per turn). Times are seconds from the clip POST. The Pi's tail 0.85, deliver 0.08 and sink 0.08 are carried constants (`est.` rows). Run 2026-10-09 22:22-22:49: 10 memory, 13 tool, 10 chat brain turns per condition. Caveats: synthetic voice, a second in-process Moonshine, temperature 0.7 (whether a tool is called varies per run), and identical prompts across conditions make 2 of 3 runs prefix-cache hits (rotation spreads that; absolute times are about 0.1 s optimistic). A first run with one shared session per condition thrashed the llama cache and was discarded. The run's build also carried diagnostics (packet-step timers, an idle-Kokoro re-synthesis) that were dropped from the committed script to keep this PR under the size limit; the stage logic is unchanged.

## Before (all flags off) and after
| median s | memory | tool | chat |
|---|---|---|---|
| STT of the padded clip (3.97 / 5.82 / 4.69 s) | 0.56 | 0.88 | 0.67 |
| router + tiers (two-stage FunctionGemma decode) | 0.35 | 0.30 | 0.03 |
| packet build in zoe-data | 0.01 | 0.01 | 0.01 |
| POST to first delta seen by the stream loop | 0.78 | 1.50 | 1.19 |
| first speakable unit ready, from dispatch | 0.98 | 1.50 | 1.20 |
| Kokoro first chunk (brain still decoding) | 0.33 | 0.30 | 0.45 |
| **before: first audible, POST to audio ready** | **2.25** | **1.77** | **2.38** |
| before: est. end of speech to sound | 3.26 | 2.78 | 3.39 |
| after `CLAUSE`: first audible (paired change) | 2.91 (+0.22, 5/10 faster) | 1.78 (0.00, 5/13) | **1.45 (-0.77, 10/10)** |
| after `CLAUSE`+`TOOL_ACK`: first audible (paired change) | **1.03 (-1.11, 10/10)** | **1.33 (-0.36, 7/13)** | 1.80 (-0.52, 10/10; the ack never fires on chat, 0/10, so this is noise vs 1.45) |
| after, best case: est. end of speech to sound | 2.04 | 2.34 | 2.46 |

"First audible" counts the tool filler too: today 3/10 memory and 11/13 tool turns hear "Let me check..." at 1.7-2.7 s. The ack fired on 7/10 memory and 8/13 tool turns (the router said `chat` for the rest, so the filler still applies).

## Levers (all `ZOE_FIRST_SOUND_*`, default OFF)
- **`NARRATION_EARLY`** (follows `CLAUSE` when unset). With `ZOE_STRIP_NARRATION=1` (live) the stripper holds a reply's first sentence until it closes: raw sidecar stream 0.37 s to first delta, through the seam 1.28 s; chat 1.19 s to 0.14 s with it on. Output is byte-identical (equivalence test over 800+ reply x chunking cases), so it is safe alone, but it unblocks the existing 60-character clause and 90-character soft-cap rules, so audio changes on long openings.
- **`CLAUSE`** cuts the first unit at the first `,;:` or dash (24 characters, 4 words; never after a digit, abbreviation, initial or inside a quote or bracket). Chat -0.77 s. The cut adds a gap of about 0.15 s (the Pi trims each chunk to a 130 ms tail, `_trim_chunk_silence`); the pitch reset needs an ear check: `measure_first_sound.py --ear-check DIR` writes A (today) / B (cut) WAV pairs.
- **`TOOL_ACK`** speaks one cached line ("Let me check your calendar.") at dispatch for tool-class router domains, with the brain request already in flight (`prefetch`). Memory -1.1 s. Never on chat.

## Found, not fixed
- The tool-start sentinel (today's filler trigger) reaches the loop 0.64 s after dispatch because `observe()` is gated on the runtime's batched write; tapping `toolcall_start` in `early-text.ts` would cut it. Sidecar change, not made here.
- The router decode (0.30-0.35 s) runs synchronously inside the async handler on tool-ish turns; recall block up to 0.57 s and the continuity block 0.28-0.8 s (with 1.3k extra prefill tokens) sit before the POST: recall-packet territory, untouched.
- `push.broadcaster.broadcast` awaits every websocket inline (3-4 times per turn, no timeout): unmeasured here, unbounded if a client stalls.
- Prefix cache is healthy: first-round re-prefill median 1 token, p90 59, max 1293 (flags-off turns, n=33); Kokoro 0.38 s while the brain decodes vs 0.32 s quiet.

## Operating
Enable with `ZOE_FIRST_SOUND_CLAUSE=1` (and `ZOE_FIRST_SOUND_TOOL_ACK=1`) after the ear check and `voice_regression_probe.py`; roll back by unsetting. Re-measure: `ZOE_PERF=1 $(bash scripts/deploy/zoe_data_python.sh) scripts/perf/measure_first_sound.py --wait --json out.json`. Tests: `tests/test_first_sound_policy.py`, `tests/test_first_sound_narration_early.py` (each broken once to go red).
