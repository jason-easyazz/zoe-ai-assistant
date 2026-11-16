# listen-pace lab: the shadow replayer

What level would Zoe's end-of-turn wait have used per turn, and what would it have bought? `replay.py` reads the panel
daemon's `FIRST_SOUND` lines (the PR 2495 instrument), runs the REAL controller (`services/zoe-data/listen_pace.py`, loaded by
path) as if `ZOE_LISTEN_PACE=on`, and prints cut-offs per 100 eligible turns (observed vs predicted), cut-offs avoided, mean
wait (observed vs predicted) and ms of waiting saved per 100 turns. SSOT: `docs/knowledge/voice-listen-pace.md`.

- Read-only. No network, no live reads, no ssh: hand it a file. Prints numbers and enums only.
- Prediction rules (and why they are sound) are in the module docstring. Short version: a shorter level never changes whether a
  turn was cut, and its saving is an upper bound; a longer level's avoided cut-offs are exact only when the line carries
  `talk_first_ms` (not on PR 2495's line yet), otherwise reported as "possibly avoided".
- `--observed-level N` (a log recorded at a level other than L0): each wait is first undone through level N's table, then
  the replayed level's table applies, so a close replays to that level's own tail. No number in this
  README was produced with a non-zero N.
- The log has no speaker, so the replay is per panel (household), which is v1's learning unit anyway.

```
python3 labs/listen-pace/replay.py panel-voice.log [--turns-per-day 10] [--observed-level 0] [--child] [--per-turn] [--json]
```

Tests (synthetic lines generated in the test, hand-run, not CI-wired):

```
flock -x -w 14000 /tmp/zoe-test-lane.lock timeout 180 python3 -m pytest -p no:cacheprovider -q labs/listen-pace/test_replay.py
```

## Record

No real panel data yet: PR 2495 is not on the panel, so no `FIRST_SOUND` line exists live. The numbers below are SYNTHETIC
corpora from `test_replay.py`'s generator (`corpus(n, cut_rate, seed)`, 40 % hesitating falls, 10 turns a day), shown only to
make the output shape concrete. They are not a measurement of anyone.
