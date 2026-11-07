---
type: Reference
title: The nightly self-review (self-evolution) - Zoe reads her own real conversations, flags what went wrong, and drives fixes
description: Flag-dark ZOE_SELF_REVIEW=off|shadow|on. A 12B night-window job that assembles each real household turn (what was asked, what Zoe did, what she said, latency, speaker, struggle receipts), flags likely failures by deterministic rules, has the 12B write a short cause + fix-class review per flagged turn inside a time and token budget, and writes a PRIVATE per-night file, a counts-only summary fed to capability_gap and the struggle store, neutral synthetic candidate cells for the self-improver, and one counts-only morning-brief line.
tags: [self-evolution, samantha, night-job, 12b, struggle, capability-gap, self-improver, privacy, forget]
timestamp: 2026-11-07T00:00:00Z
---

# The nightly self-review

**Status 2026-11-07: built, flag-dark (`ZOE_SELF_REVIEW` unset = `off`), the night window's job list carries it but the installed
unit does not until the owner reinstalls it (steps below).** Owner request 2026-11-07: Zoe's self-evolution should do what the
human-supervised owner-session reviews do - look at real interactions, find what went wrong, drive fixes.

## What it is, as built

| piece | where |
|---|---|
| the review (gather, exclude, flag, review, write, forget, morning line) | `services/zoe-data/self_review.py` |
| the night job / owner CLI | `scripts/maintenance/zoe-self-review.py` |
| the night-window job `self_review` (after `struggle_proposals`, before `omi_day_review`; best effort, 600 s cap, `--budget-s`) | `scripts/night/night_window.py` |
| capability backlog source (`self_review`, weight 0.6, mode `on` nights only) | `services/zoe-data/capability_gap.py`, `scripts/maintenance/capability_gap_backlog.py` |
| the forget cascade step `self_review`; the member erase via `struggle_detector.delete_user` | `services/zoe-data/memory_forget_cascade.py`, `struggle_detector.py` |
| inventory + probe rows | `forget_reach.py` (`self_review_records`, `self_improver_candidates`), `tests/test_forget_completeness_probe.py` (`self_review`) |
| the morning-brief line | `proactive/triggers/morning_checkin.py` (`self_review_line`) |
| tests (ci_safe, fakes only) | `services/zoe-data/tests/test_self_review.py` |

**One night.** The window hands the job the 12B (port 11500) and the seconds it has left. The CLI refuses the live 4B's port (11434)
and any non-loopback model: the review never runs on the daytime brain.

1. **Gather** the turns since the last run (`state.json` `reviewed_until`, never more than 36 h back): `chat_messages` through the
   app's own helpers (`user_filters.message_owner_expr`, `memory_provenance.off_record_sql`) - Zoe's own process reading its own
   store, read-only. Each user turn is paired with the reply that followed it; the voice lane's double save is one turn. A reply that
   was never saved is read back from the Flue session store (`labs/flue-zoe-brain-2x/data/zoe-brain.db`, opened `mode=ro`,
   `assistant_text_delta` joined to the `user_message` with the same words). Receipts from the service log: `VOICE TIMING` (path,
   first-token latency = `pre_brain_ms + brain_ttft_ms`), `WRITE_CLAIM_BLOCKED` (the claim guard); the STT log (audio length);
   `struggle_events` (signals by turn id).
2. **Exclude** (counted per reason, never read further): the row's server-side `traffic_class` harness / probe / ci, a synthetic or
   demo id (even one re-admitted as a person), a harness panel (`replay*`, `test*`, `probe*`, ...), off-the-record rows, distress
   turns, and turns naming an entity their member forgot (`memory_forgotten.keep_unforgotten`, strict: a ledger outage withholds).
3. **Flag** by code only (`flag_turn`): `misroute` (a router-training / FALLTHROUGH struggle), `garbled_stt` (a MISHEAR struggle, a
   word three times in a row, under 2 characters per second of audio, mostly fragments; voice only), `claim_no_tool` (the claim guard
   blocked it), `empty_reply` (none, or truncated), `ack_contradicted` (an ack the member's next turn contradicts within 120 s),
   `rephrase_retry` (a RE_ASK / REPHRASE struggle, or the next turn within 60 s shares half its content words), `emotion_misread`
   (comfort for a remark of four words or fewer that carries no feeling), `cant_do` (a CANT_DO struggle).
4. **Review** each flagged turn, most severe first, on the 12B: one call (160 tokens out) asking for `{"cause", "fix", "note"}`, `fix`
   from a fixed list (`router_training, stt, capability_gap, claim_guard, answer_quality, manner, latency, none, unclear`). The budget
   charges each call's full output allowance BEFORE it is made and refuses a call the remaining time cannot hold
   (`ZOE_SELF_REVIEW_MAX_TOKENS`, default 4000; `ZOE_SELF_REVIEW_MAX_CALLS`, default 20; the window's `--budget-s`). A turn it does
   not reach keeps its flags (`no_time`).
5. **Write** (under a file lock, atomic, 0600, directory 0700): `~/.zoe/self-review/<night>.md` (the readable review),
   `<night>.self-review.jsonl` (its records), `<night>.self-review-summary.json` (counts only). Mode `on` adds: one wordless
   `struggle_events` row per (turn, class) (signal `SELF_REVIEW`, fix type `self_review` - not a proposal kind, so the proposals pass
   never clusters them by themselves; a re-run adds nothing twice); for an OWNER turn with a manner failure, a synthetic ask the 12B
   invents in the same shape, appended to `~/.zoe/self-improver/candidate-cells.jsonl`; the morning-brief line.

## Privacy (code, tested)

| whose turn | the model sees it | the private file | summaries / brief |
|---|---|---|---|
| the owner (`household_authority.lookup_role` = owner) | yes | words + reply + review | counts, ids, hashes |
| another adult | no | ids + classes, "words not shown" | counts, ids, hashes |
| a child (or a role lookup that failed) | yes, under the kid-safe rubric | ids + classes + review; a review echoing four of the child's words is withheld | counts, ids, hashes |
| a guest / an unverified voice | no | ids + classes | counts |

Household words never leave the box; the model is the local 12B. The summary carries a 12-hex utterance hash (the same key the logs
already carry) and a fixed area label from capability_gap's lexicon, never words. A candidate cell carries no turn id and no member,
and the guard (`paraphrase_ok`) refuses one that reuses three of the original's words, a deny term
(`~/.zoe/self-improver/deny-terms.txt`), a name-like capitalised word, a number of four digits or more, an address or a URL.
Candidates are NOT the self-improver's frozen held-out split: promoting one is a human step (the split is pinned by
`manner_block_ab.py` `SPLIT_DIGEST`).

**Forget.** "Forget Dana": the cascade step `self_review` (`self_review.erase_entity`, strict, retried from the ledger hashes like
every step) drops each of the member's records naming the entity and re-renders the night's report and summary without it. A member
erase (`MemoryService.delete_user` -> `struggle_detector.delete_user` -> `self_review.delete_user`) drops all their records and
summary events. The owner's reader (`--show`, `load_reviews`) re-checks the ledger on every read.

## Owner steps

1. Shadow first (writes the private review only; nothing downstream): add `ZOE_SELF_REVIEW=shadow` to `~/assistant/services/zoe-data/.env`
   (the night window's `EnvironmentFile`).
2. Reinstall the night-window unit so its `--jobs` carries `self_review` (the tracked unit changed):
   ```
   ~/assistant/scripts/night/install_night_window.sh
   systemctl --user cat zoe-night-window.service | grep -- --jobs
   ```
3. After a night: `ls -l ~/.zoe/self-review/`; read `~/.zoe/self-review/<night>.md`; the night report's `self_review` job line.
   A rehearsal at any hour, nothing written, no model: `python3 ~/assistant/scripts/maintenance/zoe-self-review.py --dry-run --no-model`
   (needs the zoe-data environment: `set -a; . ~/assistant/services/zoe-data/.env; set +a` first).
4. When a week of shadow reviews reads right: `ZOE_SELF_REVIEW=on` (struggle rows, candidate cells, the morning line). The capability
   backlog picks the summaries up on its next run (`python3 ~/assistant/scripts/maintenance/capability_gap_backlog.py`).
5. Off at any time: remove the line (or `ZOE_SELF_REVIEW=off`). Erase everything: `rm -r ~/.zoe/self-review`.

## Honest limits

- Unmeasured on the box: the 12B's time per review (~25-30 s at 6.5 tok/s decode) means about 15-20 reviews fit the 600 s cap.
- The tool/handler that ran is known only through the receipts above (claim guard, struggle signals, path); a "claimed a write with
  no tool" the claim guard did not see is not flagged.
- The Flue fallback matches by words and time; a reply to a turn whose words the sidecar wrapped differently is not found.
- Not built (later phases): reviews driving fixes automatically (the struggle proposals and the capability backlog are where a fix is
  decided), a judged quality score per turn, promotion of candidate cells into the self-improver's split.
