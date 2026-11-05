---
type: Reference
title: Voice-session bridge - "where we left off" across a panel session rollover
description: Why a panel voice conversation forgot everything after 5 minutes of silence (the session id rolls and the Flue sidecar starts the new session with history=0), the evidence for a speaker-scoped bridge over a longer TTL, and the flag-dark fix - the same confirmed speaker's last exchanges, deterministic, forget-ledger checked (strict), token-capped, after the cached prefix, expiring after 2 h. Flags ZOE_VOICE_SESSION_BRIDGE (default off), ZOE_VOICE_SESSION_BRIDGE_TTL_S.
tags: [voice-path, memory, speaker-id, flue, zoe-data, flag-dark, privacy]
timestamp: 2026-11-05T00:00:00+08:00
---

# Voice-session bridge (`voice_session_bridge.py`)

## The gap

`routers/voice_tts._get_or_create_voice_session` keeps one session id per panel,
`voice-panel-<panel>-<hex>`, and mints a new one after `_VOICE_SESSION_TTL_S` (5 min) of
silence. The live brain is the Flue sidecar, which keys its own history on that id
(`labs/flue-zoe-brain-2x/src/context-window.ts` windows it). A new id therefore starts at
history=0: after any pause Zoe has forgotten what was just being talked about (found in
PR #2292's investigation).

## Decision: a speaker-scoped bridge, not a longer TTL

Measured over the replay corpus `~/.zoe-voice-samples` (1,707 captures, file mtimes =
capture times, 2026-06-20 onward): 1,700 inter-utterance gaps, **350 of them >= 5 min**
(i.e. a session roll). Of those rolls, the panel was spoken to again within:

| within | 10 min | 30 min | 1 h | 2 h | 3 h | 4 h | 6 h | 12 h |
|---|---|---|---|---|---|---|---|---|
| share of rolls | 11% | 26% | 40% | 49% | 53% | 56% | 63% | 80% |

The curve flattens after ~2 h (+4 points to 3 h, +3 more to 4 h), so the bridge expires
**2 h** after the exchange (`ZOE_VOICE_SESSION_BRIDGE_TTL_S`, clamped 300..14400). Beyond
that a resumption is more often a new topic, and the day brief / continuity / recall blocks
own it.

Why not simply raise `_VOICE_SESSION_TTL_S`:

- **Privacy between household members.** The session is per PANEL, not per speaker: every
  voice at the panel inside the TTL shares the same Flue session history. A 2 h TTL would hand
  the previous speaker's whole conversation to anyone who walks up within 2 h. The bridge is
  scoped to the same confirmed speaker.
- **Context / KV cost.** A longer TTL keeps the whole session history (windowed to the
  ~6.6k-token prompt budget) and, after a pause, the single llama slot has usually served
  other traffic, so resuming re-prefills that history. The bridge adds at most ~200 tokens,
  inside the latest user message, on the first 3 brain turns of the new session only.
- **Bounded exposure.** The bridge carries 3 exchanges, not a session, and expires.

## What the bridge does

- **Capture** (`note_exchange`, called from `_schedule_voice_chat_save`): in process memory,
  only for a turn the speaker gate CONFIRMED (`speaker_verified is True`). Panel binding alone
  (`None`), a rejected voice (`False`), a guest, an off-the-record turn (`memory_provenance`)
  are never held. A spoken forget / retraction / off-the-record cue ("forget that", "never
  mind what I said", "scratch that", ...) drops everything held for that member and is not
  held itself. `forget_redact.clear_turn_caches` also drops it.
- **Rollover** (`note_rollover`, from `_get_or_create_voice_session`): the new session id
  remembers the one it replaced, so the chain (up to 4 hops) stays on one panel.
- **Carry** (`block_for`, from `zoe_flue_client._run_flue_brain_streaming_turn`): only when
  THIS turn's verdict is confirmed (`restraint.current_verdict() is True` - voice is a
  password) and only that member's own exchanges from the replaced session(s), unexpired, the
  newest 3, 200 chars a side, 800 chars in all (~200 tokens at 4 chars/token).
- **Forget, fail closed**: every exchange (user words AND reply) goes through
  `memory_forgotten.keep_unforgotten(..., strict=True)` within 0.5 s, then the live
  tombstones. An unconfigured ledger, an outage, a slow lookup or any error withholds the
  whole bridge.
- **Placement**: a `[MEMORY CONTEXT — where you and this user left off ...]` block in the
  preamble of the latest user message (identity -> bridge -> fast-path -> recall -> offer ->
  the user's words). The sidecar's system prompt and stored history are untouched, so the KV
  prefix is stable; the delimiters are the registered pair, so the sidecar's stale-block
  elision (`ZOE_BRAIN_ELIDE_STALE_BLOCKS`) removes it from older messages, which is why it is
  re-sent on each of the first 3 brain turns. No sidecar change.
- **No model call.** The extract is deterministic. No summary is computed (so nothing runs
  in the night window or as a daytime 4B call).

## Limits, stated honestly

- In-memory only: a zoe-data restart drops what is held (no bridge, never a wrong one).
- Fast-path exchanges count as exchanges (they are saved through the same function).
- The block is stored verbatim in the new Flue session's first user message; a name
  forgotten AFTER that turn stays in the sidecar store (the wire elision hides it from later
  prompts when `ZOE_BRAIN_ELIDE_STALE_BLOCKS` is on). Same property as every other block.
- Inside the 5 min TTL a different speaker still shares the panel session - pre-existing,
  tracked in [open-problems.md](open-problems.md).

## Owner steps

Enable after the voice landing (replay gate) and a live check:

```
# services/zoe-data/.env
ZOE_VOICE_SESSION_BRIDGE=1
# optional, default 7200
ZOE_VOICE_SESSION_BRIDGE_TTL_S=7200
```

then restart zoe-data and verify: talk, wait > 5 min, ask a follow-up; the log shows one
`VOICE_SESSION_BRIDGE session=... exchanges=N chars=M` line and the reply follows on. A
different member's voice at the same panel must show no such line.

Tests: `services/zoe-data/tests/test_voice_session_bridge.py` (ci_safe; every guard has a
negative control).
