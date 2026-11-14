# Calendar subscription engine (lab)

End-to-end proof of `services/zoe-data/calendar_feed.py` (Stage 1 core; record
[connected-accounts.md](../../docs/knowledge/connected-accounts.md)). `run_lab.py` runs the REAL engine against a loopback
`http.server` on 127.0.0.1. The server serves a synthetic `.ics` with a strong ETag and answers 304 to a matching
`If-None-Match`, and the script changes the file between runs. The database is a scratch SQLite file in a temp directory,
with migrations 0052 / 0078 / 0079 applied. The forget ledger uses its in-memory backend, with a synthetic salt that is set only
in the lab process.

```bash
ICAL=$(dirname "$(find ~/.cache/uv -maxdepth 4 -type d -name 'icalendar-*.dist-info' | head -1)")
PYTHONPATH=$ICAL flock -x -w 14000 /tmp/zoe-test-lane.lock python3 labs/calendar-feed/run_lab.py --big 5
```

`icalendar` is not installed on the box (CI pins it). The command above puts a cached uv copy (7.3.0) on `PYTHONPATH` for this
one run and installs nothing.

## Rails

- Nothing stores a link. The engine gets bytes from the lab's own fetcher (`loopback_fetcher`), and the loopback address lives
  only in that closure. No URL column, no secret, and `account_secrets` is not touched.
- The only network traffic is 127.0.0.1. There is no live database, no flag, no `.env`, and nothing is written outside the
  temp directory, which is removed when the run ends.
- Every title, place and person in the feed is invented.

## Result (2026-11-14, Jetson Orin, system python3.10)

| step | outcome | code | seen | created | updated | unchanged | deleted | suppressed | events after |
|---|---|---|---|---|---|---|---|---|---|
| 1 create (3 events + a weekly series + its override) | ok | - | 5 | 5 | 0 | 0 | 0 | 0 | 5 |
| 2 unchanged (304) | ok | - | 0 | 0 | 0 | 5 | 0 | 0 | 5 |
| 3 unchanged (server ignores validators; same sha256) | ok | - | 0 | 0 | 0 | 5 | 0 | 0 | 5 |
| 4 change (title + SEQUENCE) | ok | - | 5 | 0 | 1 | 4 | 0 | 0 | 5 |
| 5 remote delete | ok | - | 4 | 0 | 0 | 4 | 1 | 0 | 4 |
| 6 empty feed: guard holds | partial | suspect_empty | 0 | 0 | 0 | 0 | 0 | 0 | 4 |
| 7 the same empty feed again: confirmed | ok | - | 0 | 0 | 0 | 0 | 4 | 0 | 0 |
| 8 back | ok | - | 5 | 5 | 0 | 0 | 0 | 0 | 5 |
| 9 a forgotten event resent (+1 new) | ok | - | 6 | 1 | 0 | 4 | 0 | 1 | 5 |
| 10 disconnect | 6 rows removed | | | | | | | | 0 (events, attendees, alarms, calendars, offers) |

No step was pruned or skipped, and no rule was unsupported. In steps 2 and 3 there was no parse, so `seen` is 0.

Synthetic 5.00 MB feed (12,599 VEVENTs, every tenth one weekly), one run each, in child processes:

| what | wall time | RSS before | peak RSS |
|---|---|---|---|
| `parse_ics` alone | 8.6 s | 86.0 MB | 124.4 MB |
| full engine (parse in `run_blocking` + 12,599 creates into SQLite) | 14.35 s | 86.0 MB | 173.9 MB |

The parse runs in a worker thread, so the event loop stays free. Even so, 8.6 s of CPU on the box that runs voice argues for a
scheduler that syncs one account at a time and keeps clear of the night window. The SQLite write timings say nothing about
PostgreSQL.
