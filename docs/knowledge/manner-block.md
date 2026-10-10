# The manner block (`ZOE_MANNER_BLOCK=off|on`, default off)

**Status 2026-10-10: built, flag-dark, NOT yet shown to help.** The live verdict is queued behind the night-brain testing window (owner
priority change 10:20). Do not flip the flag until `scripts/perf/manner_block_ab.py --phase report` prints `VERDICT ... KEEP` on the
held-out half with the regression cells run.

## What it is

A <=160-token paragraph of counted behaviours (MITI / EPITOME style: reflect before advising, name one specific true thing, one
question at a time, no hooks, disagree once kindly about a stated risk, no flattery), appended to the system prompt of the live Flue
brain (the 4B day brain) for adults only. Record: `docs/research/samantha-gap-register-2026-10-09.md` felt gap 5 (G10),
`docs/architecture/samantha-brain-blueprint-2026-10-09.md` ("Manner"), `docs/research/person-likeness-2026-10-09.md` section 5.3.

| Piece | File |
|---|---|
| flag, eligibility (adults only), language, envelope text | `services/zoe-data/manner_block.py` |
| the words, per language (data) | `services/zoe-data/lexicons_data/{en,es}.json` key `"manner"`; a language with no entry gets English |
| the seam to the sidecar | `zoe_flue_client._manner_context_block` / `_wrap_message_with_manner` (one construction site) |
| the sidecar half | `labs/flue-zoe-brain-2x/src/manner.ts`, wired in `providers/capped-completions.ts` `applyPolicies` |
| tests | `services/zoe-data/tests/test_manner_block.py`, `labs/flue-zoe-brain-2x/test/manner_block.test.ts` |
| the A/B | `scripts/perf/manner_block_ab.py` |

**Wire.** One envelope line `" zoe-manner:<JSON string>"` between the persona line and the identity line (like `zoe-persona`). The sidecar
strips it (the model never sees it) and appends the block to THAT turn's system prompt, after the doctrines and BEFORE the user-model
card, so the bytes up to the card are the same for every member (one warm prefix). It is never a mid-conversation message. Flag off, a
minor, a guest, a failed lookup or a 1.x sidecar sends no line: the wire bytes are exactly today's (pinned by golden tests on both sides).

**Voice-path files touched** (`scripts/maintenance/voice_gate_check.py` globs): `labs/flue-zoe-brain-2x/src/*` and
`services/zoe-data/zoe_flue_client.py`. The voice gate (replay) therefore runs, and `deploy.yml` restarts the sidecar on merge. No
`routers/voice_tts.py`, `fast_tiers.py`, `ask_when_ambiguous.py`, `clean_goodbye.py` or night-mind file is touched.

**Adults only.** Withheld from guest / anonymous / voice ids, a `member_modes` row with `minor` or mode `kid`, an `auth_users.role` of
`child` / `kid` / `minor` / `teenager` / `teen`, and (fail closed) any lookup that errors or takes > 0.5 s. A real member with NO
`member_modes` row and an adult role is treated as an adult, so **set the minor rows before flipping the flag**. Synthetic bench ids
(`demo_*`, `test_*`) get the block so the bench can measure it.

## The A/B (protocol fixed before any live number)

In-process like `person_half_enforce_ab.py`: the real zoe-data composition and the real seam, the live llama-server 4B, scored by the
person bench's own scorers and Wilson bars. The sidecar differs: the live :3579 build predates the envelope, so the harness starts its
own sidecar from this worktree's `dist` on :3589 (scratch session store, throw-away token, a stub for the data URL: no live DB read),
and a **canary** variant ("end every reply with banana") proves the seam end to end (it did: 2026-10-10 09:44).

* Arms: `none` (flag unset) vs `on:<variant>`; `V0` is the shipped lexicon text.
* Cells: PRIMARY P6.a (reflective listening), P5b.b (planted flaw named), P5b.u (good plan praised), P9.a (due loop voiced); regression
  P2, P5a, P5c, P6.b, P7, P8 and a bar sample (S1, S3, S4 deterministic half, S28-S31). Judged halves are not run.
* Split: DEV and a frozen HELD-OUT half (`SPLIT_DIGEST`; the held-out flaw / plan messages were written before the first run).
  Variants (at most four: V0-V3) are chosen on DEV only; the verdict is the held-out half, once.
* KEEP: >= 2 of the cells that can separate (P6.a, P5b.b, P5b.u) separate upward (Wilson lower bound of `on` above the upper bound
  of `none`) on held-out; none separates down; no regression cell separates down (violation counts: up). A point drop of >= 2 asks is
  printed as WATCH.
* Budget: 1500 s of live brain time in total (ledger `~/.cache/zoe/manner_ab/brain_time.json`), harness lock held, never during a brain
  window, a landing, or < 180 s of zoe-data uptime.

### Numbers so far (DEV half, V0 = the shipped text, 2026-10-09/10 live 4B, n shown)

| half | none | on:V0 | verdict |
|---|---|---|---|
| P6.a reflective listening | 8/10 | 8/10 | no separation |
| P5b.b planted flaw touched | 21/60 | 25/60 | no separation (+0.07, CI -0.11..+0.23) |
| P5b.u good plan praised | 12/20 | 14/20 | no separation |
| P9.a due loop voiced | 10/10 | 10/10 | ceiling in this rig |

Findings that change the plan: (1) **P9.a is a selector gate, not a manner**: handed a `[RAISE]` block the 4B voices it 10/10 with or
without the block; the live 4/10 is consistent with the raise not being injected inside the 2 h gap (`samantha-bar.md` line ~712; not verified here). It stays in the
table as a regression cell. (2) **The 4B answers a risky plan with sympathy, not the problem** ("That sounds like a really frustrating
moment to send"); V0's "say so once, kindly" did not change that, so V1 / V2 make it a procedure (look for a real problem first; the
FIRST sentence names it) and use examples outside every bench topic.

Block size by the llama-server tokenizer: English 114 tokens, Spanish 135 (budget 160).

## Resume (after the night-testing window)

```
cd <this worktree>; Z=/home/zoe/.zoe/venvs/zoe-data-py312/bin/python
ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock $Z scripts/perf/manner_block_ab.py --phase dev --arms V1,V2 --cells P6,flaw,good --run-id mab-1
$Z scripts/perf/manner_block_ab.py --phase report --run-id mab-1 --variant V1      # pick on DEV only; edit the lexicon text if not V0
ZOE_PERF=1 flock ... --phase heldout --arms none,<chosen> --run-id mab-1           # the verdict, once
ZOE_PERF=1 flock ... --phase regress --arms none,<chosen> --run-id mab-1
$Z scripts/perf/manner_block_ab.py --phase report --run-id mab-1 --variant <chosen>
```
Needs `npm run build` in `labs/flue-zoe-brain-2x` first (the harness's sidecar runs `dist/`). If the chosen variant is not V0, move its text
into `lexicons_data/en.json` (and translate `es.json`), update the sha256 goldens in `test_manner_block.py`, and re-run the A/B on the final
bytes. Flip `ZOE_MANNER_BLOCK=on` only on a printed KEEP.
