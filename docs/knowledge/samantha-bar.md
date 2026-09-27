---
type: Reference
title: Samantha bar harness (samantha_bar.py v0)
description: The Samantha-quality regression gate. Eight scripted multi-day memory and companion scenarios run against throwaway demo users through the live API. Covers how to run it, what each scenario proves, the scoring and judge, the baseline and teardown contracts, and known limits.
tags: [memory, samantha, eval, regression-gate, harness, zoe-data]
timestamp: 2026-09-28T06:00:00Z
---

# Samantha bar harness (`scripts/perf/samantha_bar.py`, v0)

Tracker row: B3 "Samantha bar harness v0" in [the program](../architecture/beat-the-bar-2026-program.md).
Tests: `tests/unit/test_samantha_bar.py` (ci_safe, pure).

It is the memory and companion counterpart of the voice replay gate. Each run creates two
fresh users, `demo_bar_<8 hex>` A and B, and talks to the live zoe-data API as them
(`POST /api/chat/?stream=false` with `X-Internal-Token` + `X-Zoe-User-Id`). It scores
eight scenarios and compares them against a baseline. A scenario is red only when it
passed before and does not pass now.

## Run

```bash
python3 scripts/perf/samantha_bar.py --dry-run                 # the plan; no network, no writes
ZOE_PERF=1 \
  flock /tmp/zoe-voice-harness.lock nice -n 5 \
  python3 scripts/perf/samantha_bar.py --compare-baseline      # exit 1 on a regression
... --record-baseline      # this run becomes the bar (refused if it errored or teardown is unproven)
... --samples 3            # judged scenarios ask 3x in fresh sessions, majority vote (odd only)
... --teardown-only        # clean up a run that was killed before its own teardown
```

Artifacts in `~/.cache/zoe/`:
- `samantha_bar_last.json`: the full evidence.
- `samantha_bar_trend.jsonl`: one line per run.
- `samantha_bar_baseline.json`: `revision` has the voice probe's shape (commit, tree,
  `dirty`, `clean_verified`) for the checkout the live service runs from; also the judge
  rubric sha and the per-scenario verdicts.
- `samantha_bar_pending_teardown.json`: exists only while a teardown is unproven.

Exit codes: 0 = ran with no regression; 1 = regression; 2 = refused, error, or teardown not
proven; 3 = harness lock held. Without `ZOE_PERF=1` it prints a skip notice, exits 0 and
writes nothing. This follows the `scripts/perf/` convention.

**Gates.** A refusal is written to `samantha_bar_last.json` with `status: refused`. The run
refuses when:
- it is inside 01:45–03:15 local, or within 30 minutes of that window (the nightly jobs);
- a `deploy.yml` run is queued or in progress, or `gh` cannot say whether one is;
- the service `.env` does not give `ZOE_INTERNAL_TOKEN` and `POSTGRES_URL`.

It waits (up to 20 minutes) for `/readyz` to report `ready` with `memory_capture` ok. This
covers a store cutover. It also waits (up to 15 minutes) for MemAvailable ≥ 1.2 GB. It
takes the shared `/tmp/zoe-voice-harness.lock`, because it uses the brain slot, and it
raises its niceness to at least 5.

## Scenarios

The facts are synthetic and deliberately distinctive: Marisol from Lisbon, Dunedin then
Hobart, an aquarium interview, dad Teodor the lighthouse keeper. A real household fact
cannot satisfy a check by accident. Ambient system knowledge, such as the home location,
cannot answer "where do I live".

Day 1 runs first. The day-1 sessions are then backdated 26 hours in Postgres (the
`set_session_age` recipe from `samantha_live`, applied to the harness's own `bar-*` session
ids only). Day 2 follows.

| id | proves | scoring |
|---|---|---|
| S1 | A fact said in one session is recalled in a new session the same day. | deterministic: both `marisol` and `lisbon` |
| S2 | After a move, the current city wins and the old one is not asserted as current. | `hobart` is required. If `dunedin` is also mentioned, the judge decides "previous home" vs "still lives there". |
| S3 | Asked about something never said (the dentist), Zoe declines instead of inventing. | A clear decline with no `Dr X` or `dentist is X` is a deterministic pass. Anything else goes to the judge. |
| S4 | A day-1 worry is acknowledged on day 2, gently and not verbatim. | `interview` must appear, the reply must share fewer than 7 consecutive words with the day-1 sentence, and the judge must say "warm, in its own words" |
| S5 | A proactive hook, if one fires, carries the day-1 open loop. | Reads `proactive_pending` for demo A. No hook gives SKIP. An `emotional_followup` without the loop gives FAIL. |
| S6 | Demo B never sees demo A's facts. | Deterministic: no A needle may appear in B's reply or in B's `/for-prompt` packet. If A's own packet holds none of them, the result is SKIP, because the test would be vacuous. |
| S7 | A short duplicate ("my dad is Teodor") does not erase the richer fact. | The reply must name Teodor and lighthouse, and A's packet must still hold `lighthouse`. |
| S8 | S1 and S7 facts survive 32 filler turns spread over 3 sessions. | deterministic: `marisol` and `lighthouse` |

The judge is the brain itself: llama-server `:11434` `/v1/chat/completions` with temperature 0,
`top_k` 1 and seed 0. It gets a fixed system prompt and one rubric per judged scenario, and
must answer `VERDICT: PASS|FAIL` / `REASON: …`. Its verdict and reason are recorded, and an
unparseable answer is ERROR. The rubric text is pinned by sha256 in the tests. Editing it
changes what a pass means, so update the pin and re-record the baseline. `compare` notes a
rubric mismatch.

Several results count as not-pass: a brain-fallback reply ("trouble reaching my brain"), an
HTTP error, and an empty reply are all ERROR. For the baseline, a previously passing
scenario that now reports SKIP or ERROR is a regression. **A skip is not a pass.**

Evidence never stores raw replies by default. It keeps the length, a 12-character sha,
latency, which synthetic needles were found, whether the seed fact "landed" in the recall
packet (and how long that took), and the judge's reason. `--keep-replies` adds 240-character
excerpts to the local results file for debugging.

## Teardown (the demo-users-only guardrail)

Every identity is asserted against `^demo_bar_[0-9a-f]{8}$` before any write. The pending
file is written before the first turn. Teardown runs in a `finally` block, and SIGTERM is
routed through it. Teardown then has to be proven:

1. **Quiesce.** Wait until each user's packet count is stable. The per-turn digest writes in
   the background.
2. **Memory store, through the API only.** Call
   `POST /api/memories/users/{demo}/forget-synthetic` with the internal token (see the route
   contract below). If `ZOE_BAR_ADMIN_SESSION` holds an admin `X-Session-ID`, the admin
   `/forget` is used instead. Both call `MemoryService.delete_user`, which removes every row
   the id owns, in any status, plus its audit rows.
3. **Postgres, by exact id.** Delete every session owned by a demo user, together with its
   `chat_messages` and `memory_consolidation_state`. Delete every public base table's rows
   where `user_id::text` is a demo id; foreign-key refusals are retried. Delete the
   `users` row that `/api/chat` created.
4. **Prove it.** The residual must be 0. In synthetic mode the residual is a second
   `forget-synthetic` call, whose `removed` count covers every status; a late row gets
   deleted rather than leaked. In admin mode it is the admin export. The `/for-prompt` count
   must also be 0,
   and a count-back of every one of those Postgres tables must be 0. A count that cannot be
   read is not treated as zero. If any check fails, the whole sequence runs once more, which
   catches a late digest write. If it is still unproven, the status is `error`, the exit
   code is 2, the pending file is kept, and the next run refuses to start until
   `--teardown-only` proves it.

The harness never opens Chroma or MemPalace itself. Before any write it checks that the
forget path works, by calling it on a fresh, unused demo id; that call deletes nothing. If
the check fails (the route is not deployed, the token is refused, or the admin session is
bad), the run refuses. A run that cannot clean up must not write.
`DELETE /api/chat/sessions/{id}` ignores `X-Zoe-User-Id`, which is why chat rows are
removed in Postgres.

### Route contract: `POST /api/memories/users/{id}/forget-synthetic`

- **Auth:** the internal token only (`X-Internal-Token` == `ZOE_INTERNAL_TOKEN`). Loopback
  alone is not enough, and neither is an admin session.
  - A missing header returns **401**.
  - A wrong token, or no token provisioned on the host, returns **403**.
- **Id rule:** `user_filters.synthetic_forget_refusal`.
  - The id must match `^(demo|test)[-_]`. The match is case-sensitive and narrower than the
    batch filter: `probe`/`ci`/`e2e`/`bench` ids are refused.
  - The id must have no surrounding whitespace.
  - The id must not be a guest sentinel.
  - The id must not be in `ZOE_SYNTHETIC_USER_ALLOWLIST`. An allowlisted id is treated as a
    real user, so only the admin `/forget` can erase it.
  - A refused id gets **403** with the reason, and nothing is deleted.
- **Effect:** the same `MemoryService.delete_user` as the admin forget, with
  `actor="internal:forget-synthetic"`. It is idempotent. The response is
  `{"user_id", "removed", "mode": "synthetic"}`.
- **Audit:** every call that reaches the id check logs one WARNING line,
  `MEMORY_FORGET_SYNTHETIC user=<id> removed=<n>` or `MEMORY_FORGET_SYNTHETIC refused
  user=<id> reason=…`, to the zoe-data app log.
- **Tests:** `services/zoe-data/tests/test_memory_forget_synthetic.py`, including a
  negative control that loosens the pattern.

## Known limits (v0)

- **Needs the route deployed.** The live zoe-data must serve `forget-synthetic`. Until it
  does, the preflight refuses unless an admin session is supplied.
- **Multi-day is approximated.** Backdating moves only the Postgres chat rows. Memory-store
  `added_at` stays "now", so recency ranking sees everything as same-day.
- **S5 is structurally SKIP for demo users today.** `emotional_followup` and the other
  proactive triggers filter `is_synthetic_user`, so no hook fires for `demo_*` ids
  ([synthetic users](synthetic-users-and-proactive-recipients.md)). S5 starts measuring
  when a lab sets `ZOE_SYNTHETIC_USER_ALLOWLIST`.
- **Self-judging.** The judge is the same Gemma that answers. Temperature 0 makes it stable,
  not unbiased. Deterministic checks gate first, and the judge only decides the ambiguous
  remainder.
- **Nondeterministic brain.** Use `--samples 3` for a baseline you intend to gate on.
- **Load.** One run is about 45 brain turns plus per-turn digests plus judge calls. That is
  roughly 15–30 minutes of brain slot, which is why it takes the harness lock.
