# Voice reflex projection lab

`sim.py` answers one question before the reflex is switched on: of the panel's turns, how many would get the reflex, why
not the rest, and what would the first-sound distribution look like against today's. It runs the REAL
`scripts/setup/zoe_voice_reflex.py` `decide()`, so the rules are the shipped rules.

Inputs are numbers only: per-turn (timestamp, endpoint close ms, panel TTFA) from the felt scorecard's allowlisted extract of
the panel log (`--panel`, one read-only ssh, nothing but timestamps and integers leaves the device), a saved extract
(`--lines`), invented turns (`--synthetic N`), or, after the shadow week, the panel's own `REFLEX_DECISION` lines
(`--decisions FILE`). No audio, no model, no device, no write.

Exact from the log: the close and its time, the hour (night), the gap since the previous turn (the 30 s budget, the 4 h idle
greeting), and the timing. NOT in the log, so assumptions (default = every one passes = the ceiling; the report prints a
sensitivity grid): the speaker verdict, media, the room floor, the still-talking window, wake versus window turn, speech
length, the decision latency (a 70 / 215 ms p50 / p95 speaker score and a 240 ms floor for the post-close mic window, both
unmeasured on the panel) and the player start (100 ms, partly measured). The `REFLEX_DECISION` shadow week replaces every
assumption with a count.

Not modelled: the corpus audio (`~/.zoe-voice-samples`; replaying it needs the endpointer's model on the box, which this lane
does not load), the reply being cut by the reflex (that is a delay of zero by design: the reply is never waited for), and
anything about how the sound is heard.

Safety count (`--decisions`): the SHADOW'S OWN count is the daemon's `REFLEX_SAFETY would_play= class=` lines (the class
from the server's done-frame `safety` key). Today's server sends none, so the lab prints `safety count: UNAVAILABLE`, never a
zero. `--markers` adds the LAB measure: would-play decisions with a server `DISTRESS_HANDOFF` / `MEDICAL_SAFETY` /
`CHILD_SAFETY_FLOOR` log marker within 45 s, a time correlation that cannot see refused commands.

Self-check: `tests/unit/test_voice_reflex_sim.py` (CI).
