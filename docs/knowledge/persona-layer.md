---
type: Reference
title: Persona layer — phase 0 (household persona + per-member mode, flag-dark)
description: What landed for Zoe's personality layer in phase 0 — the persona record, where it lives, how it renders and is swapped into a prompt, which lane it reaches today (the dormant legacy lane, NOT the live Flue brain), the routes, the drift stub, and what remains.
tags: [persona, personality, governance, flag-dark, W5.4, P5]
timestamp: 2026-10-04T00:00:00Z
---

# Persona layer — phase 0

Implements the first slice of the design in
[personality-identity-layer-2026-10-04.md](../research/personality-identity-layer-2026-10-04.md)
under the owner's Q16 decision (2026-10-04): *household persona with per-member modes, kid mode
later, safety note first.* Packet numbering: **W5.4** (per-user personas + kid mode) in the
[Samantha evolution plan](../architecture/samantha-evolution-plan.md), measured by **P5** (the
persona-drift band). The normative rules are in
[the emotional-safety note](../governance/emotional-safety-note.md); read it before extending
anything here.

Everything is behind `ZOE_PERSONA_LAYER` (default OFF, per-call read). Flag off ⇒ the routes are
absent (404) and every prompt is byte-identical to today.

## Which persona is live (read from the live path)

| Copy | Lane | Status |
|---|---|---|
| `ZOE_SOUL` + nine doctrines (`ZOE_INSTRUCTIONS`) in `labs/flue-zoe-brain-2x/src/agents/zoe.ts` | Flue 2.x sidecar, `:3579` | **LIVE** — the brain that answers voice and chat |
| `_ZOE_SOUL_BASE` / `_ZOE_SOUL_VOICE` in `services/zoe-data/zoe_agent.py` | legacy lane | dormant (selected only when `ZOE_BRAIN_BACKEND` is not `flue` and `ZOE_USE_CORE_BRAIN` is off) |
| `services/zoe-core/SOUL.md` | core (Pi agent) lane | dormant |

**In phase 0 the persona layer reaches the dormant legacy lane only.** The live Flue prompt is
untouched on purpose: `labs/flue-zoe-brain-2x/src/*` is voice path (replay-gated) and
`deploy.yml` restarts the sidecar on any change there. The sidecar consumer is phase 1 (below).

## What exists

- **Record** (`services/zoe-data/persona_layer.py`): `traits` (1–5 from a fixed 16-word vocabulary,
  each `low|mid|high`, no conflicting pairs), `voice_style` (four enum keys: brevity, directness,
  humour, warmth), `boundaries` (≤5 one-line sentences ≤120 chars, filtered for markup and
  instruction-shaped text), optional `backstory` (≤300 chars). The default is today's persona
  expressed as data (warm / curious / thoughtful; honest, direct when it helps, gentle when it's
  needed; no boundaries, no backstory).
- **Member mode**: `companion | mentor | helper | kid` + a `minor` flag. Stored default `companion`,
  non-minor — but the layer is **opt-in per member**: with no row a member keeps the fixed persona
  (`UNSET`), and a PUT without a mode opts them in at `companion`. A minor holds only `kid`/`helper` (validator, route and renderer all enforce it);
  `kid` implies minor; leaving minor status needs an explicit `"minor": false` by an admin.
  Guests, sentinel ids and synthetic test users get the household tone and no mode. **A minor is HELD
  on the fixed persona** (`persona_layer.MINORS_GET_PERSONA = False`, governance note §7: no crisis
  path yet), and so is a real member whose mode could not be loaded — a failed lookup never defaults
  a child to `companion`.
- **Storage**: alembic `0035_persona_layer` — `household_persona` (one row, validated JSON +
  content `version`) and `member_modes`. No household row ⇒ the default. Nothing affective is stored. The downgrade drops `household_persona` only and
  KEEPS `member_modes` (the sole record that a member is a minor).
- **Rendering**: deterministic, no LLM. A fixed identity lead, the traits ("You are very warm,
  curious and a little patient."), the voice line (the style plus today's non-configurable
  contractions / banned-openers / acknowledge-first floor), **one** mode sentence, boundaries,
  backstory. The budget is **175 tokens** (`estimate_tokens` = max of chars/4 and words×1.35, the
  repo's convention plus a proxy); a record that would exceed it in *any* mode is **rejected at
  write time**, never truncated. The default renders to ~125 tokens.
- **Prompt assembly**: `persona_layer.apply_to_prompt(system_prompt, fixed_block, user_id)`
  replaces the fixed persona paragraphs with the rendered block. `zoe_agent.py` slices those
  paragraphs out of the souls (`_ZOE_PERSONA_FIXED`, `_ZOE_PERSONA_FIXED_VOICE` — sliced, never
  retyped, hash-pinned) and calls it at the voice builder and the two chat sites, after an
  `await persona_layer.refresh(user_id)` that fills a 30 s in-process snapshot. Flag off, nothing
  loaded, or the fixed block not found ⇒ the SAME prompt object comes back.
- **Routes** (`routers/persona.py`, mounted only when the flag is on): `GET /api/persona`
  (signed-in member: record, defaults, vocabulary, the exact block), `PUT /api/persona` and
  `POST /api/persona/reset` (admin), `GET|PUT|DELETE /api/persona/modes/{user_id}` (the member or
  an admin; minors admin-only), `GET /api/persona/block?user_id=` (internal token only — the
  sidecar's future consumer).
- **Drift stub** (`services/zoe-data/persona_drift.py`): anchor-similarity (weighted top-k mean
  against persona sentences minus a negative seed set) + style adherence + trait-cue rates. CLI:
  `python3 services/zoe-data/persona_drift.py transcript.jsonl [--persona p.json] [--embedder
  lexical|bge] [--member companion|mentor|helper|guest|kid] [--json]` — plain text (no labels) is
  refused without `--member`; unlabeled rows are never scored; exit 0 ok / 1 exceeded / 2 error / 3 insufficient sample. Skips kid and
  minor rows, keeps no text or ids, writes nothing. The bar (≥30 replies, deviation ≤10 %, style
  pass ≥90 %) and band thresholds are **provisional** until a baseline week is measured. Nothing
  live calls it (`ZOE_PERSONA_DRIFT` gates the unused in-process hook).

## Not in this PR

No voice, panel-chip or phone editor (the PUT is the only editor); no sidecar consumer
(`src/persona.ts`) and so no effect on the live brain; no kid *assignment* (waits on W5 enrolment)
and no tool narrowing; no nickname/notes fields; no deterministic crisis path (the note requires
it; it is not built); no P5 log line in the chat path; no S13 persona-adherence bar scenario.

## Phase 1 (next)

1. Sidecar `src/persona.ts` cloned from `user-model.ts` (fetch `/api/persona/block`, per-user cache,
   fail-open, appended after the user-model suffix), flag-gated, replay-gated; delivery proof like
   the user-model A/B. Until then the flag changes nothing live.
2. Editing: voice `persona_adjust` intent with confirm, touch chips, phone QR for boundaries.
3. The crisis path, before the persona layer is enabled for any minor (note §7).
4. Baseline week → calibrate the drift bar → log-only drift line beside `FLUE_CONTEXT_BUDGET`.

## Verification

`tests/test_persona_layer.py`, `tests/test_persona_routes.py`, `tests/test_persona_drift.py` (all
`ci_safe`). Negative controls are described in each file's docstring; the load-bearing ones are the
flag-off identity test (with a flag-on positive control beside it), the `_ZOE_SOUL_BASE` /
`_ZOE_SOUL_VOICE` sha256 pins, the minor/kid rule at validator, renderer and route, and the static
scan that only `routers/persona.py` writes the persona.
