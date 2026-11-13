# Felt scorecard (lab)

One read-only command that prints the numbers saying whether Zoe's memory circuit is closed on real
household turns. It turns the 2026-11-11 hand measurement (`docs/research/samantha-frontier-2026-11-11.md`
sections 1 and 3; methods from `samantha-frontier-2026-11-11/lane-1-circuit-remeasure.md`,
`lane-4-turn-runtime.md`, `lane-7-supply-and-matching.md`) into one script. This is experiment P1 of that record.

```bash
python3 labs/felt-scorecard/scorecard.py                 # short window 7 d, long window 30 d, last 8 nights
python3 labs/felt-scorecard/scorecard.py --json
python3 labs/felt-scorecard/scorecard.py --panel         # also read the panel log on the Pi over ssh
# the 2026-11-11 windows:
python3 labs/felt-scorecard/scorecard.py --since 2026-11-05T00:00 --until 2026-11-11T22:30 \
    --long-since 2026-10-12T08:00 --panel
```

Runs on the Zoe host with the system `python3` (stdlib + `asyncpg`, which the sibling maintenance scripts
already use). Measured runtime: 1.2-3.0 s (target < 60 s); every line of every rotation is timestamped to check coverage.

## Rails

- **Read-only everywhere.** Postgres: the connection sets `default_transaction_read_only=on` and every query
  runs inside `transaction(readonly=True)`. Every query goes through `_agg()`, which refuses anything that is
  not a single `SELECT` and raises if a query returns any non-numeric value (pinned by
  `test_db_collect_read_only_and_all_via_agg`, which fails if either read-only setting is removed or a query
  reaches the connection without `_agg()`). To tell member log lines apart, the candidate `user=` and
  `session=` values found in the logs are sent INTO one query (`unnest ... WITH ORDINALITY`), which returns
  only the ordinals of those that belong to a real member account; no id comes out of the database.
- **MemPalace** `chroma.sqlite3` is opened with `mode=ro` and read with ONE `GROUP BY` (`durable_groups()`).
  The store cannot do the member join itself (members live in Postgres), so that query returns grouped
  rows of exactly these columns: `user_id` (an account id, used only to keep member rows, matched against
  the Postgres ordinals, never printed), `source` and `status` (writer and lifecycle labels; `source` is
  mapped through `SOURCE_ALLOWLIST` and anything else prints as `other`; `status` is only tested for
  `approved`), and a count. `added_ts` / `added_at` are used only in the SQL window filter. The document text
  (`chroma:document`) is never read. The only log-derived strings that reach the output are fixed vocabularies:
  `RECALL_EVERY_TURN skipped=` reasons go through `SKIP_REASONS` (the `Selection.skipped` values in
  `services/zoe-data/recall_every_turn.py`), anything else prints as `other`; night dates are digit-only.
- **Nothing is printed but counts and timings.** No log line, row, id or env value. The DSN comes from
  `POSTGRES_URL` or the zoe-data service `.env`, through `_resolve_dsn()` in
  `scripts/maintenance/purge_orphaned_test_data.py` (the same resolver `merge_duplicate_lists.py` reuses).
- **Panel is opt-in, and an allowlist.** `--panel` runs `LC_ALL=C sed -nE` on the Pi with `PANEL_SED`: each
  rule rewrites a matching line to `<YYYY-MM-DD HH:MM:SS> <tag> <value>`, built only from captured
  timestamp digits, a fixed tag letter (`T` TTFA seconds, `C` close ms, `Q` tail enum, `R` other Recorded)
  and a number or fixed enum; every other line prints nothing. No field, whatever its name or position,
  can carry text off the device (`test_panel_allowlist_emits_only_timestamp_tag_number` feeds it lines with
  text in unexpected fields). The local parser accepts only that exact shape.
- **Unmeasured is never zero.** A number that cannot be taken read-only, or not for its full window, prints
  `unmeasured` with the reason. App-log rotations are read plain and gzip (streamed). Every log-derived number
  for a window is `unmeasured`, never a partial count, when the retained rotations do not provably cover it:
  the earliest retained line is after the window start; a rotation whose span can touch the window failed to
  read in full (unreadable or truncated, e.g. a cut gzip); or two consecutive rotations are further apart
  than the largest gap between lines inside any rotation (floor 10 min), which means a rotation is missing;
  or the latest retained line is earlier than the window end minus that same tolerance (a stale or stopped
  log). The panel row applies the same two ends to the Pi log (its own largest in-window gap, floor 10 min).
  A marker whose numeric field is not a number makes its metric `unmeasured` ("malformed marker"), never a
  crash. An empty or negative window, or `--nights` below 1, is refused at argument parsing (exit 2).
  The pendant funnel's `offered` follows the same rule for the long window. Within brain timing, `brain_ttft`
  and `pre_brain+brain_ttft` each stand on their own samples (the second only from lines carrying both
  fields) and each is `unmeasured` when it has none. A measured count of 0
  with n=0 carries a note that there was no data in the window.

## The numbers, what they mean and where they come from

Traffic split (lane 1): a log line is harness when it carries `traffic=harness`, when its `user=` matches
`user_filters.SYNTHETIC_USER_RE`, or when its `session=` starts `qa`/`demo`/`bench`/`probe`/`test`. This
script also treats `bar-` and `replay-` sessions as harness (both are harness-only prefixes; on the
2026-11-11 window this changes no count, because every `bar-` line already carries the tag and no
`replay-` session reaches the app log). A member line is one whose `user=` is a `users.role='member'`
account that is neither a guest sentinel, a `guest-<id>` principal nor synthetic; for lines that carry
only `session=` (`FLUE_TOOLS`, `VOICE TIMING`) it is a `chat_sessions` row owned by such an account. Guest
and unknown sessions are reported separately as `other`. Consecutive rotations can repeat lines at the boundary, so only that overlap is de-duplicated (a line in
the later file within the earlier file's span that the earlier file also holds); a line repeated inside one
file is a real repeated event and is counted every time. Lane 1 de-duplicated globally; on the 11-11
windows the two rules give identical counts.

| # | number | source | definition |
|---|---|---|---|
| 1 | Memory served / used per member brain reply | `PROVENANCE_REPLY` (app log) | member, `kind=brain`, harness excluded; `served>0`, `used>0`. `used` is a text-overlap proxy [Experimental] |
| 2 | Every-turn recall: relevant row found | `RECALL_EVERY_TURN` | member lines; `skipped=-` are eligible; `rows>0` = found; `claimed!=-` = another block claimed the turn; `injected>0` |
| 3 | Continuity carried | `SEAM_CONTINUITY` | member lines, `matched=True` |
| 4 | Tool calls / recall calls per member brain turn | `FLUE_TOOLS` | member sessions; `tools=[...]` non-empty; `recall_memory=called:yes`; other (guest/unknown) and harness shown separately |
| 5 | Night-mind threads raised | Postgres `night_threads` | `count(*)` vs `last_raised_at` set; all time (the table has no window column that joins) |
| 6a | Durable rows per day, panel/chat | MemPalace `mempalace_drawers` | one GROUP BY; member `user_id`, `added_ts` (else `added_at`) in window; long and short window; by allowlisted `source` |
| 6b | Durable rows per day, pendant | `omi_day_candidates` | `written_ref` set |
| 7 | Pendant funnel | `omi_recordings`, `omi_segments`, `omi_day_reviews`, `omi_day_candidates`, `DAY_REVIEW_RAISE` | recordings (by `started_at`) and hours; transcripts = `status='done'` with a non-empty transcript; segments; wearer = `speaker='you'`; reviews ready/failed; candidates; offered = `DAY_REVIEW_RAISE raised=1` log lines; decided = `decided_via` set; written = `written_ref` set |
| 8 | Commitments ledger | Postgres `commitments` | rows by `created_at`; replay artifacts = `session_id LIKE 'replay-%'` (lane 7 C); household = not replay, real user, not a harness session |
| 9 | Speech end to first sound, panel | Pi `voice.log` (`--panel`) | per turn: endpoint close (`close=deep|quiet=Nms`, else the 640/800 ms tail floor) + `turn_stream TTFA=`, each TTFA paired with the closest preceding `Recorded` line within 60 s [Experimental] |
| 10 | Brain dispatch to first text | `VOICE TIMING` | member sessions; `brain_ttft_ms` p50/p90 (lane 1 10b) and `pre_brain_ms + brain_ttft_ms` p50 (lane 4 E+F); other lines counted separately |
| 11 | Night windows completed, last N | `night-window.log` | a night counts when any `NIGHT_WINDOW_SUMMARY` for that run date has `outcome='ok...'`; nights with no summary are not completed |
| 12 | Members active, days of use, real member turns | `chat_messages` x `chat_sessions` x `users` | `role='user'` rows of `role='member'` accounts, guest and synthetic excluded; days are local (Australia/Perth) calendar days |
| 13 | Corrections applied to stored memory | `VALUE_CORRECTION_APPLIED` / `_STALE` / `_KIND_GUESS` | member lines, harness excluded; applied lines with `rows=` summed; stale and kind-guess refusals counted. A count, not a rate (a correction that matched no row writes no line) |
| 9b | Share of turns with first sound under 1 s / 0.6 s | as row 9 | per-turn total strictly under 1000 ms / 600 ms (`FIRST_SOUND_BANDS_MS`) |

### ALERTS and PERFORMANCE (printed after the rows above, one row per source)

"Things that told, or should have told, the owner something is wrong", counted per source over the long window
(default 30 d) unless the source supports less; each row states its window. A source that cannot be read, or does not
cover the window, prints `unmeasured` with the reason. Unit and check names pass through fixed allowlists taken from
the writer scripts; any other name prints as `other`.

| key | source (writer) | counts |
|---|---|---|
| `alerts_crash_loops` | `~/.zoe-logs/crash-loop-watch.log` (`scripts/maintenance/zoe_crash_loop_watch.py`, appended by its unit) | lines starting with the red circle (alert) / green circle (recovery); per unit from `CRASH_UNITS` (zoe-data + `OTHER_UNITS`). The writer prints messages as one line WITHOUT a timestamp; only its status lines (at least hourly) carry one, so an alert counts when the stamps around it lie in the window, one straddling an edge is reported apart. A silence over 2 h between stamps makes the row `unmeasured`. Delivery failures (stderr) counted. Alerts are lines: an undelivered unit alert is re-printed each tick |
| `alerts_night` | `~/.zoe/night-reports/<date>.json` (`scripts/night/night_window.py` `write_report`, the twin of `<date>.md`) | per night (D 04:00 local in window), real `mode=night` runs only: best outcome ok / degraded / not ok, or no report (counted). A real-night report in the window that exists but cannot be read (corrupt JSON, no outcome or start) makes the row `unmeasured` with the count, never "no report"; alarm = `restore_status` NOT HEALTHY, `night_lost` or `decode_alert` (what the single `ALARM` file carries; that file is deleted by the next clean window, so it only says "now"). Narrowed, and said so, when the reports start inside the window. Not the same as row 11 (night-window.log, last N) |
| `alerts_backups` | `~/.zoe-logs/backup-verify.log` (`zoe-backup-verify.sh`; `ALERT:` lines from `zoe_offbox.alert`) | the log has NO per-line timestamps, so the count is over the WHOLE log (window "whole log"); runs end at the script's last line; kinds allowlisted; file mtime as last write, and the drill state file's (`~/.local/state/zoe-offbox/last_restore_drill.json`) last run time and pass flag; a missing, unreadable or malformed state file makes the row `unmeasured` (the readable ALERT count is carried in the reason) |
| `alerts_health` | `~/.zoe-logs/health.log` (`scripts/maintenance/zoe_health.py`, the repo `zoe-health.service`) | `ALERT DOWN:` / `ALERT recovered:` lines, incl. the night-window timer alarm (#2419); check names allowlisted |
| `alerts_distress_no_contact` | zoe-data app log, `DISTRESS_NO_CONTACT` (`services/zoe-data/distress_handoff.py`) | lines only, harness excluded. Distress events themselves are household content and are not counted |
| `perf_panel_kv_miss` | zoe-data app log, `PANEL_KV_MISS kind=first` or `kind=warm` (`zoe_flue_client._panel_kv_alarm`) | a PERFORMANCE row: misses by kind, days with a cold first reply (the days `kv_miss_alert.py` sends its one owner alert), `kv_miss_alert: alert not delivered` lines; both harness-filtered like every app-log count |

The two app-log rows use the same coverage rules as the rows above; when the only gap is that retention starts after
the long window start, they report the narrower window from the earliest retained line and say so.
Paths: `--crash-log`, `--night-reports`, `--backup-log`, `--backup-state`, `--health-log`.

Percentiles interpolate linearly between closest ranks.

## Sample output (real run, 2026-11-12 09:21 local, default windows)

```text
Felt scorecard  generated 2026-11-12T09:21:48+08:00  runtime 2.93 s
short window 2026-11-05T09:21..2026-11-12T09:21   long window 2026-10-13T09:21..2026-11-12T09:21

* Memory served / used per member brain reply
    value : served 4 of 34 (11.8%); used 2 of 34 (5.9%)   n=34
    window: 2026-11-05 09:21..2026-11-12 09:21
    how   : PROVENANCE_REPLY kind=brain, member user=, harness excluded; served>0 / used>0 [Experimental] (used= is a text-overlap proxy)
* Every-turn recall (shadow): relevant row found on eligible member turns
    value : found 1 of 19 (5.3%); claimed by another block 2; injected 0   n=19
    window: 2026-11-05 09:21..2026-11-12 09:21 (first marker 11-08 19:41)
    how   : RECALL_EVERY_TURN member lines (21), skipped=- are eligible; rows>0 found; skipped: warming 2 [Proven]
* Continuity carried (matched) on member brain turns
    value : 0 of 32 (0.0%)   n=32
    window: 2026-11-05 09:21..2026-11-12 09:21
    how   : SEAM_CONTINUITY matched=True, member user=, harness excluded [Proven]
* Tool calls / recall_memory calls per member brain turn
    value : tool calls 0 of 34 (0.0%); recall_memory 0 of 34 (0.0%)   n=34
    window: 2026-11-05 09:21..2026-11-12 09:21
    how   : FLUE_TOOLS tools=[...] non-empty / recall_memory=called:yes on member sessions (chat_sessions of a real member account); other (guest/unknown) non-harness: tools 0, recall 0 of 19; harness control: tools 51, recall 26 of 1025 [Proven]
* Night-mind threads ever raised on a turn
    value : 0 of 44 (0.0%)   n=44
    window: all time
    how   : night_threads count(*) vs last_raised_at set (no serve marker exists yet) [Proven]
* Durable memory rows per day, panel/chat (member)
    value : 0.93/day over 30 d (28 rows, 22 approved); 0.14/day over last 7.0 d (1 rows)   n=28
    window: 2026-10-13 09:21..2026-11-12 09:21
    how   : MemPalace mempalace_drawers, one GROUP BY (user_id, source, status), member user_id, added_ts/added_at in window; by source: idle_consolidation 8, digest 5, synthesis 4, voice_turn_digest 4, conversation 2, voice 2, consolidation 1, turn_digest 1, voice_regex 1 [Proven]
* Durable memory rows per day, pendant
    value : 0.00/day (0 written)   n=106
    window: 2026-10-13 09:21..2026-11-12 09:21
    how   : omi_day_candidates written_ref set [Proven]
* Pendant funnel
    value : recordings 2277 (126.5 h) -> transcripts 946 (111.2 h) -> segments 9928 -> wearer 12 -> reviews 34 (31 ready, 3 failed) -> candidates 106 -> offered 0 -> decided 0 -> written 0   n=2277
    window: 2026-10-13 09:21..2026-11-12 09:21
    how   : omi_recordings by started_at (transcript = status done and non-empty); omi_segments speaker='you'; omi_day_reviews / omi_day_candidates by created_at; offered = DAY_REVIEW_RAISE raised=1 log lines [Proven]
* Commitments ledger rows
    value : 32 rows: replay artifacts 32, household 0 (kept 0); all rows kept 0 / missed 30 / open 2   n=32
    window: 2026-10-13 09:21..2026-11-12 09:21
    how   : commitments by created_at; replay = session_id 'replay-%'; household = not replay, real user, not a qa/demo/bench/probe/test/bar session [Proven]
* Speech end to first sound, panel
    value : unmeasured
    reason: panel log lives on the Pi; skipped by default (pass --panel to read it over ssh)
* Brain dispatch to first text (member voice turns)
    value : brain_ttft p50 1.89 s / p90 4.52 s; pre_brain+brain_ttft p50 2.15 s (n=34)   n=34
    window: 2026-11-05 09:21..2026-11-12 09:21
    how   : VOICE TIMING brain_ttft_ms on member sessions (lane 1 10b); pre_brain_ms+brain_ttft_ms is lane 4 E+F; other (guest/unknown) lines excluded: 12 [Proven]
* Night windows completed, last 8
    value : 5 of 8   n=8
    window: nights 2026-11-05..2026-11-12
    how   : NIGHT_WINDOW_SUMMARY outcome='ok...' per run date; 2 nights with no summary line [Proven]
* Members active / days of use / real member turns
    value : 1 member(s) active, 25 days of use, 283 real member user turns (of 693 user rows)   n=283
    window: 2026-10-13 09:21..2026-11-12 09:21
    how   : chat_messages role=user joined to chat_sessions and users role=member; guest/synthetic excluded; local days [Proven]
```

With `--panel` on the default window (run at 08:39): speech end to first sound p50 2.69 s / p90 5.05 s, n=66.

## Against the 2026-11-11 hand measurement

Run with the 11-11 windows (`--since 2026-11-05T00:00 --until 2026-11-11T22:30 --long-since 2026-10-12T08:00
--panel`; 2026-10-12T08:00 local is lane 7's 2026-10-12 00:00 UTC start). Lane 4's latency rows were re-run
on lane 4's own window (`--since 2026-11-04T00:00 --until 2026-11-11T22:22`).

| number | 11-11 by hand | scorecard, same window | within 5%? | why it differs |
|---|---|---|---|---|
| Memory served / used, member replies | served 4 of 32, used 2 of 32 (lane 1 3e) | served 4 of 32, used 2 of 32 | yes, exact | |
| Every-turn recall | found 1 of 17, claimed 2, injected 0; 19 lines, 2 warming (lane 1 3c) | found 1 of 17, claimed 2, injected 0; 19 lines, 2 warming | yes, exact | |
| Continuity | 0 of 30 (lane 1 2) | 0 of 30 | yes, exact | |
| Tool / recall calls | 0 of 51 / 0 of 51 "household"; harness recall 31 of 1,102 (lane 1 1a-1c) | member 0 of 32 / 0 of 32; other (guest/unknown) 0 of 19; harness tools 69, recall 31 of 1,102 | rate: yes (0 either way); n: no (32 + 19 = 51) | definition changed on review: only member sessions count; lane 1's 51 were all non-harness lines (12 of its 20 sessions are owned by the member, 7 by non-member accounts, 1 has no chat session row) |
| Night-mind threads raised | 0 of 42 (lane 1 6a) | 0 of 44 | raised: exact; threads: no (+2) | the table has no windowing column; 2 threads were written on the 2026-11-12 night, after the hand count |
| Durable rows, panel, 30 d | 48 rows, 38 approved; by source idle_consolidation 17, voice_turn_digest 10, digest 8 ... (lane 7 A1) | 48 rows, 38 approved, same source split | yes, exact | |
| Durable rows, panel, since 11-03 | 1 row in 9 days (lane 7 A1) | 1 row in the last 6.9 days | yes (same row) | |
| Durable rows, pendant | 0 (lane 7 A2) | 0 | yes | |
| Pendant recordings | 2,263 (126.4 h) (lane 7 A2) | 2,260 (126.4 h) | yes (-0.1%) | lane 7 states its start (10-12 UTC) but no end; it most likely also counted recordings started after 22:30, which this window excludes [Speculative] |
| Transcripts | 945 (111.1 h) | 945 (111.1 h) | yes, exact | |
| Segments / wearer | 9,926 / 12 | 9,926 / 12 | yes, exact | |
| Reviews / candidates | 33 (30 ready, 3 failed) / 106 | 33 (30 ready, 3 failed) / 106 | yes, exact | |
| Offered / approved / written | 0 / 0 / 0 | 0 offered (raise lines) / 0 decided / 0 written | yes | the rotated app logs reach back to 2026-10-10, so the raise-line count covers the whole window |
| Commitments | 33 rows, 31 missed, 2 open; 33 of 33 replay; 0 household (lane 7 C) | 32 rows, 30 missed, 2 open; 32 of 32 replay; 0 household | yes (-3%) | the live table itself now holds 32 rows in total (a first run at 06:21 on the same window still read 33); one replay row was removed outside this script, which writes nothing |
| Speech end to first sound | lane 1: ~2.7 s / ~5.4 s (inferred, sum of separate p50s, n=64/12); lane 4: 2.78 / 4.88 s to first PCM, n=72 | lane 1 window: 2.73 / 5.28 s, n=63; lane 4 window: 2.69 / 4.66 s, n=72 | lane 1: yes / yes (+1%, -2%); lane 4: yes / yes (-3.3%, -4.6%) | per-turn sum of close + TTFA. It leaves out lane 4 stage B (Recorded to server receipt, median 156 ms), which needs the server-side join, so it reads slightly low against lane 4. Lane 4's "~3.0 / 5.1 s" adds a further +130 ms acoustic lag and +80 ms device start from records; the scorecard does not |
| Brain dispatch to first text | lane 1 10b: brain_ttft p50 1.87 / p90 3.34 s, n=44; lane 4: E+F median 2.04 s, n=49 | member only: 1.89 / 4.85 s, n=32 (12 other lines excluded); lane 4 window: E+F p50 2.15 s (n=38) | p50: yes (+1%); p90: no (+45%); E+F: no (+5.4%) | definition changed on review: member sessions only. Before the change (all non-harness lines) the same windows gave 1.85 / 3.31 s, n=44 and E+F 2.04 s, n=50, i.e. within 2% of both lanes; the excluded lines sat below the member tail, so dropping them moves the p90 most |
| Night windows | 5 of 8 (11-04..11-11) | 5 of 8 | yes, exact | |
| Members / days / real turns, 30 d | lane 7: 1 / 22 / 394; lane 1: 1 / 25 / 308 | 1 / 25 / 394 | turns: lane 7 yes (exact), lane 1 no; days: lane 1 yes, lane 7 no | the scorecard uses lane 7's join (member accounts only). Lane 1's 308 used a different filter that its record does not fully spell out, and the frontier record itself prints the range "308-394 / 22-25 by filter". 25 days is local-calendar days; the same rows span 23 UTC days |

Summary: of the 18 rows, 14 reproduce within 5% on the 11-11 windows. Not within 5%: the night-thread
denominator (the table cannot be windowed; it grew by 2 overnight); the usage row (the two hand counts
already disagreed; the scorecard matches lane 7's turns and lane 1's days); and the tool-call n and the
brain-timing p90 and E+F, which moved because the review restricted both to member sessions (the hand
counts included guest panel turns). No row became `unmeasured` on these windows: the retained app logs
start 2026-10-10, before both window starts, every rotation read in full, and the largest gap between
rotations is under 3 minutes, so no coverage rule fired. The panel row stays
`unmeasured` without `--panel`.

## What it cannot measure (and prints as unmeasured or labels)

- **Speech end to first sound without `--panel`**: the panel log lives on the Pi. With `--panel` it is an
  inferred number [Experimental] that leaves out the upload stage; the turn-id join of lane 4 (Pi log to the
  zoe-data stderr JSON log) is not implemented.
- **Night-mind serve per turn**: no zoe-data marker records a served block (lane 1, could-not-verify); the
  scorecard prints the thread table's `last_raised_at` count, which is the only read-only proxy.
- **True memory coverage**: `used=` is a text-overlap proxy and `RECALL_EVERY_TURN` measures lexical
  relevance against the member's own rows; neither is a judged relevance score.
- **Household promise follow-through**: the ledger holds 0 household rows, so the rate has no denominator;
  the scorecard prints the counts, never a rate.
- **Backup alerts by date**: `backup-verify.log` has no per-line timestamps; dating them needs the unit's journal or a
  timestamp in the script's output.
- **Health alerts on this host**: the installed `zoe-health.service` may run another script and log elsewhere than the
  repo unit's `health.log`; the row is then `unmeasured` (file missing), which is the honest answer.
- **Offered before the log retention start**: `DAY_REVIEW_RAISE` lines older than the oldest rotated app log
  (2026-10-10 on 2026-11-12) are gone, so a window that starts earlier makes `offered`, and every short-window
  log number, `unmeasured`.

## Tests

`test_scorecard.py` feeds every parser synthetic log lines, a synthetic gzip rotation, a synthetic chroma-shaped
sqlite file and a fake asyncpg connection (no live access).
Hand-run, not CI-wired (no `ci_safe` marker):

```bash
flock -x -w 3000 /tmp/zoe-test-lane.lock python3 -m pytest -q -p no:cacheprovider labs/felt-scorecard/test_scorecard.py
```
