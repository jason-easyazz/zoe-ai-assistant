---
type: decision-record
title: Voice reflex noise input - open design decision
description: The reflex's noise_dbfs input is the RMS of the wake turn's pre-roll, which contains the wake word itself, so the shadow decision will mostly report "noisy". Open, not redesigned.
tags: [voice, reflex, panel, open-decision]
---

# Voice reflex noise input (OPEN, 2026-11-14)

**Status: open design decision. Nothing acts on it; the reflex is unwired and `REFLEX_ENABLED` is off.**

## The finding

- `zoe_voice_reflex.Ctx.noise_dbfs` is described as "pre-roll RMS" with `REFLEX_NOISE_FLOOR_DBFS = -40.0`
  (provisional) as the `noisy` bar (`scripts/setup/zoe_voice_reflex.py`).
- The daemon's shadow decision (`_shadow_reflex_skip` in `scripts/setup/zoe_voice_daemon.py`) computes it from the
  recording's leading frames: for a wake turn that is `frames = list(_PREROLL)` in `record_command`, the ~1.6 s buffer
  captured up to the wake detection. It therefore holds the wake word itself. Speech at conversational level is far
  above -40 dBFS, so the shadow `skip=` will report `noisy` on most wake turns and mask every later guard (night,
  guest, low_confidence, late, ...). [code; the share is unmeasured until the panel logs exist]
- Follow-up turns use the endpointer lookback (`n_lookback` frames) instead, which can also contain speech onset.

## What the shadow data will show

Expect `skip=noisy` to dominate on wake turns. That share is a property of the input's definition, not evidence that
the panel's rooms are loud. Read the other guards' distribution only on turns that clear `noisy`, or after the input is
redefined.

## Options (not chosen)

1. Measure the slice of the pre-roll BEFORE the wake word (needs the wake detector's onset, or a fixed lead slice).
2. A rolling ambient floor from the ambient-capture thread (`_ambient_capture_thread`) between turns.
3. Drop `noisy` and rely on `media` (the PulseAudio watch) plus `spk` (which already filters a television voice).

Owner / design call. Do not loosen the guard until one is chosen and measured on the panel.
