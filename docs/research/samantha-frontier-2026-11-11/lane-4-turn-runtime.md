---
type: research-lane
title: "Lane 4 - the turn runtime: measured waterfall, turn-taking truth, never-mute, and a falsifiable native voice runtime"
date: 2026-11-11
description: Speech-end to first PCM at the wall panel for 72 real household turns (2026-11-04 to 2026-11-11), joined across the Pi daemon log and the zoe-data JSON log; what endpointing actually runs; every way the house goes silent today and what the person hears; a hard-budget runtime design with experiments.
---

# Lane 4 - the turn runtime (2026-11-11)

Read-only. Nothing on the Orin or the Pi was changed, restarted, played or flipped; no tests or harnesses ran.
Repo read at the clean worktree (origin/main at 1ddb70951). Live reads: `~/.zoe-logs/zoe-data.stderr*.log*` (ms JSON),
`~/.zoe-logs/zoe-data.app.log*`, `journalctl --user` on the Orin, the running zoe-data process environment (names only),
`scripts/maintenance/zoe_ground_truth.sh`, and over ssh to `zoe-pi`: `voice.log`, `BUILD.json`, `.env.voice` (switch
names; on/off stated only for non-secret feature switches), `free -m`, `uptime`, `vcgencmd`, `systemctl --user status`.

Labels: [Proven] measured or read this session; [Experimental] reported by a record or vendor, not re-measured here;
[Speculative] my design or extrapolation. Source: [code] file:line, [live] log marker + count, [record] committed doc,
[contradicted] a doc or prior claim the live data disagrees with. No utterance, transcript, name, device id or token appears.
Path shorthand: `D` = `scripts/setup/zoe_voice_daemon.py`, `VT` = `services/zoe-data/routers/voice_tts.py`.

## 0. Headline

1. **Today: 2.8 s median / 4.9 s p90 speech-end to first PCM write (n=72), about 3.0 / 5.1 s to first sound** once the
   acoustic endpoint lag and the device start are added. Brain conversation turns: **3.2 / 5.7 s (n=49)**, about 3.45 / 5.9 s
   corrected. Fast-tier and other non-brain turns: **1.9 / 4.3 s (n=23)**, about 2.1 / 4.5 s corrected. Against the 11-03
   baseline (3.2 / 6.5 s, which was inferred) the median has not moved and the p90 is a little better. The 90-day target
   (2.0 / 4.0) is met by 9 of 49 brain turns and 9 of 23 non-brain turns. [Proven, live]
2. **The largest single chunk is brain dispatch to first speakable text: median 2.04 s, p90 3.8 s (n=49).** On 38 of 49
   brain turns the first text reached the voice loop within 50 ms of the Flue call **finishing**: the reply is
   effectively not streamed. The flag-dark first-clause levers (`ZOE_FIRST_SOUND_CLAUSE`, `..._NARRATION_EARLY`) were
   measured on 10-09 at -0.77 s for chat (10 of 10 faster). This is the biggest removable piece. Next is the
   prompt-cache miss: 23 of 49 brain turns re-prefilled 200 or more tokens, with a median "TTFT" of 2.48 s against 1.66 s
   on cache hits. [Proven live for the gap; the saving is Experimental, from the 10-09 record]
3. **Silence is common and has two causes nobody planned for.** In 7.9 days the Pi saw the server unavailable in **190
   episodes** (160 of them outside the 01:00-04:59 night window). **Kokoro was stopped and restarted 128 times** (about 156 s
   each, 7.3 h in total, around the clock). A real turn at 06:42 today landed in a Kokoro gap: the person heard a beep and
   no words. When zoe-data is down, the daemon falls back to **espeak-ng** ("One moment.", then "Sorry, I can't reach my
   brain..."). That is a second, robot voice, while 12 af_sky clips already sit on the Pi (744 KB) and go unused on that path.
   [Proven: live and code]
4. **Endpointing at the wall is the fixed tail: 640 ms of deep quiet, or 800 ms of any quiet.** Smart Turn does not run
   on the panel. `ZOE_SMART_TURN_ENABLED` is set in zoe-data, but it is read only by the LiveKit lane, which served zero
   household turns this week. The deployed panel build is exactly `5bfd9c13` (md5 match). "DIFFERS from checkout" means
   main has since gained 36 named-guest lines (#2343/#2346); it is not drift. Duck-decide-resume is off at the wall and
   has never run in a room. [Proven: code and live]

## 1. Measured latency waterfall (speech end to first sound at the panel)

### Method (exact markers)

- **Window:** Pi `voice.log` lines dated 2026-11-04 00:00 to 2026-11-11 22:22. zoe-data `zoe-data.stderr.4..1.log.gz` +
  `zoe-data.stderr.log` (JSON, ms timestamps) over the same days. That is 82 recorded turns; 73 played audio; all 73 joined. One
  joined turn had no recorded tail rule, so the stage tables use 72.
- **Join:** the Pi line `turn_stream TTFA=<s> len=<n> utt=<id>` is matched to the server `STT_CAPTURE ... utt=<id>`. The
  closest capture within 30 s after the Pi `Recorded` line is taken, because repeated phrasings share an utt hash. All server
  lines for that turn come from the same `request_id`.
- **Clock:** Pi vs Orin offset measured over one ssh session, 15 ping-pongs: Pi **4.1-4.3 ms behind**, RTT 2.3 ms. The script corrects it.
- **Stages (per turn, so the B..G parts sum to the measured total):**
  - A endpoint tail: the `Recorded ... tail=deep|quiet` rule. Deep = `ZOE_VAD_TAIL_MS=640`, quiet = `VAD_ENDPOINT_SILENCE_S=0.8`
    (live `.env.voice`). This is the config floor. The acoustic lag from true end of speech to close is larger:
    p50 768 / p90 848 ms on 979 corpus clips, from the Smart Turn record (`docs/knowledge/smart-turn-offline-eval-2026-11-04.md`).
    The "corrected" totals add +130 ms for that and +80 ms for the aplay/Pulse start (record: panel TTFA breakdown, 70-90 ms).
  - B Recorded to server receipt: the `middleware.logging "Request completed"` for `/api/voice/turn_stream` minus its
    `duration_ms`. It covers WAV encode, speaker-ID scoring (active mode: `SPEAKER_ID_ENABLED` on, shadow off on the Pi) and the upload.
  - C receipt to `voice/turn_stream panel=... STT=` (the `STT_STREAM outcome=hit` finish is inside it).
  - D STT line to `voice/command panel=...`.
  - E `VOICE TIMING ... pre_brain_ms`; F `brain_ttft_ms` (brain turns only: a turn with no `VOICE TIMING` line is "non-brain").
  - G residual: first text to first PCM write = (receipt + Pi TTFA) - (`voice/command` + E + F). It holds Kokoro synth and delivery.
  - Pi TTFA: the daemon's own POST to first PCM write (`D:3914`, `D:4133`). It is logged after playback drains, so it is used as a duration only.
- **Caveats:** n is small and the speakers are household members. Four brain turns and one other turn opened with the cached
  first-turn-of-day greeting (TTFA under 0.15 s). They count as "first sound"; their exclusion is shown. Receipt time is taken
  as POST start, an error of tens of ms. Script and intermediate rows are in the lane scratchpad, not the repo (they hold utt hashes).

### Waterfall: brain conversation turns (n=49: 37 follow-up, 12 wake; tails 40 deep, 9 quiet)

| stage | median ms | p90 ms | n | label |
|---|---|---|---|---|
| A endpoint tail (config floor) | 640 | 800 | 49 | [Proven, live] |
| A' acoustic lag beyond the floor (corpus) | +128 | +48..+208 | 979 | [Experimental, record] |
| B Recorded to server receipt (encode + speaker ID + upload) | 156 | 257 | 49 | [Proven, live] |
| C receipt to STT done (streamed STT; reported STT 10 / 460) | 54 | 504 | 49 | [Proven, live] |
| D STT to `voice/command` | 81 | 515 | 49 | [Proven, live] |
| E pre_brain (router, tiers, scope) | 214 | 687 | 49 | [Proven, live] |
| **F brain to first text at the voice loop (`brain_ttft_ms`)** | **1825** | **3248** | 49 | [Proven, live] |
| ...F on prompt-cache hit (first_prompt_n < 200) | 1658 | 2312 | 26 | [Proven, live] |
| ...F on re-prefill (first_prompt_n >= 200) | 2478 | 5134 | 23 | [Proven, live] |
| G first text to first PCM write (Kokoro + deliver) | 326 | 528 | 49 | [Proven, live] |
| Pi TTFA (POST to first PCM) | 2470 | 4630 | 49 | [Proven, live] |
| **Total speech end (config tail) to first PCM** | **3240** | **5725** | 49 | [Proven, live] |
| total, greeting turns excluded | 3261 | 5850 | 45 | [Proven, live] |
| total corrected (+130 lag, +80 device) | ~3450 | ~5935 | 49 | [Experimental] |

### Waterfall: non-brain turns (fast tier, Skybridge, router tool path, greeting) (n=23)

| stage | median ms | p90 ms | n |
|---|---|---|---|
| A endpoint tail (config floor) | 640 | 640 | 23 |
| B Recorded to receipt | 158 | 227 | 23 |
| C receipt to STT done | 54 | 566 | 23 |
| D STT to `voice/command` (holds the two-stage router decode) | 409 | 788 | 23 |
| H `voice/command` to first PCM (tier answer + Kokoro + deliver) | 643 | 1268 | 23 |
| Pi TTFA | 1120 | 3470 | 23 |
| **Total speech end (config tail) to first PCM** | **1905** | **4288** | 23 |
| total corrected | ~2115 | ~4498 | 23 |

All turns (n=72): **2776 / 4878 ms**, about 2986 / 5088 corrected. By window: wake turns median 2.0 s (greeting clips
help), follow-up turns 3.0 s. [Proven, live]

### Against baseline and target

| | median | p90 | source |
|---|---|---|---|
| 11-03 baseline | ~3.2 s | ~6.5 s | [record, itself inferred: lane E section 0] |
| today, all turns (corrected) | ~3.0 s | ~5.1 s | [Proven, live] |
| today, brain (corrected) | ~3.45 s | ~5.9 s | [Proven, live] |
| today, non-brain (corrected) | ~2.1 s | ~4.5 s | [Proven, live] |
| 90-day target | 2.0 s | 4.0 s | - |

Turns within budget (corrected): brain, 3/49 at 1.2 s or less, 9/49 at 2.0 s or less, 34/49 at 4.0 s or less. Non-brain, 1/23 at 1.2 s or less,
9/23 at 2.0 s or less. **No turn produced any sound within 600 ms of speech end** (0/72). [Proven, live]

### What the numbers say

- **The removable chunk is F, not the brain's speed.** On 10-09, with the prompt cache healthy, the raw sidecar's first delta
  came 0.37 s after dispatch, but 1.28 s through the seam, because `ZOE_STRIP_NARRATION` holds the first sentence until it
  closes [record: first-sound-latency-2026-10-09.md]. Today the first text appears within 50 ms of the Flue call's own end
  (`FLUE_TOOLS ... ms=`) on **38 of 49** brain turns, and at least 300 ms before the end on only 10 [Proven, live]. Short
  voice replies are therefore heard only once they are complete. The live stream wrappers are in
  `services/zoe-data/zoe_flue_client.py:2765-2800`: narration strip (present in env), mirror guard (present), emotional
  guard (present), reply polish (default on), clean goodbye (present). Each may hold text up to a sentence boundary. Which
  one holds on which turn is **not attributed** here (could not verify without a run).
- **Prompt-cache misses are back.** On 10-09 the first-round re-prefill was median 1 token, p90 59 [record]. This week it
  is median 193, p90 1105, and 11 turns logged `PANEL_KV_MISS` (6 `kind=first`, 5 `kind=warm`) [Proven, live].
  `voice/wake brain prewarm` ran (69 `kv=skipped:hot`, 16 `kv=warmed`) and still did not prevent `kind=first` misses of
  about 1,100 tokens. The prewarm warms a prefix the turn does not reuse. [Proven, live; cause Speculative]
- **The pre-brain chain is serial: A + B + C + D + E is about 1.15 s median before the brain starts.** B now includes speaker-ID
  scoring on the critical path, because the gate is active (Recorded to `Speaker claim` median 175 ms). B1.1 speculation,
  which would overlap the tail with the brain, is built and flag-dark on both ends (`D:248-250`; `ZOE_SPECULATIVE_TURN`
  absent from `.env.voice`). [Proven, code and live]
- **STT is no longer a cost on the median turn.** `ZOE_STT_STREAM_UPLOAD` is on at the Pi and `STT_STREAM outcome=hit`
  covers 67 of 73 turns (6 fallbacks were `reason=empty`). Its p90 (about 0.5 s) is the long-utterance tail. [Proven, live]
  The nightly gate's "STT 795 ms vs 488 baseline" is a warm batch-harness number and does not describe the live path.

**Single largest removable chunk:** brain dispatch to first speakable text, the 2.04 s median (E+F). About 0.8-1.1 s of it
is first-sentence holding, recoverable by streaming the first clause (10-09 measured -0.77 s on chat). About 0.8 s more, on the
47% of turns that miss the cache, is re-prefill. [Experimental until re-measured live]

## 2. Turn-taking truth

- **What closes a turn at the wall today:** the daemon `_Endpointer` in VAD mode. It closes on 640 ms of deep quiet (Silero
  < 0.10, `ZOE_VAD_TAIL_MS=640`) or 800 ms of any quiet (`VAD_ENDPOINT_SILENCE_S=0.8`) (`D:192`, `D:213`, live `.env.voice`).
  This week: 81 `tail=deep|quiet` closes (68 deep, 13 quiet), 1 `tail=no_speech`, 0 hard-cap stops (all 83 `stop=silence`).
  The adaptive clean and hesitation tails default to 0, which is off (`D:233-236`); neither is set. [Proven, code and live]
- **`ZOE_SMART_TURN_ENABLED=1` in zoe-data:** present in the running process env [live]. Code: read only by
  `routers/voice_livekit.py:159-180` ("only consulted on the barge-in (Silero) path" of the LiveKit lane). `voice_turn.py`
  is that lane's scorer. The panel daemon has no Smart Turn code (grep: no `smart_turn`/`voice_turn` in `D`), and the Pi
  has no Smart Turn model file (`~/.zoe-voice/models` holds face and speaker models only). The LiveKit lane logged only
  its "on-demand mode: agent will start on first /livekit" startup line this week, and no sessions. **The switch affects no
  household turn.** `ZOE_VOICE_BARGE_IN=1` is the same: it is read only in `voice_livekit.py:122-125`. [Proven, code and live]
- **Deployed build:** `BUILD.json` sha `5bfd9c13...`, deployed 2026-11-09 05:29Z. The md5 of the installed daemon equals
  `git show 5bfd9c134:scripts/setup/zoe_voice_daemon.py`. The checkout differs by +36 lines from #2343/#2346 (named
  guests, flag-dark). Ground truth's "DIFFERS" is main moving ahead, not an unknown build. [Proven]
- **#2359 is live:** `WAKE_CHIME_ON_WAKE` is absent, which means default off (`D:591`). Since 11-09 the `Recorded` lines
  carry `muted=`: 4 turns so far, all `muted=0`. [Proven, live; n=4]
- **Smart Turn offline (#2280, 979 corpus turns):** **NOT YET.** The criteria were at least 90% correct end-of-turn and a median tail at least
  300 ms shorter. The kill rule was more cut-offs than today. Live tail: 4.7% cut, 95.3% correct, lag p50 768. Every Smart Turn
  policy that saves 300 ms or more cuts 1.7-3.2x as often (8.1-15.0%). The best saves 240 ms p50 at 6.2% cuts. Inference is about 118 ms per check
  on the Jetson; the Pi is unmeasured. The recommended next arm, Smart Turn as an extender only (it cannot cut more than
  today), is built but **not yet run** because the box never had a quiet window. [record]
- **Cut-off / over-wait counters (#2295, `scripts/perf/turn_taking_metrics.py`):** the proxy events, counted by hand on the
  same lines: `Continuation merged` 0, `hard cap mid-speech` 0, `No continuation` 0, `Turn held` 0, over 82 turns.
  **That is 0 per 100 for both, but the counter is structurally blind.** It sees only trailer-word holds
  (`ZOE_VOICE_CONTINUATION_HOLD` defaults on, `services/zoe-data/voice_utterance_shape.py:385`) and hard caps. An acoustic
  cut-off after a complete-sounding prefix, which is where the corpus cuts concentrate, never logs. Treat it as unmeasured
  live. [Proven count; the meaning is Speculative]
- **Wake:** 23 wakes against **375 near-miss lines** (`max_score` under the 0.50 threshold) in the same window. Missed wakes
  cannot be told from noise without labels. [Proven count]
- **Barge-in:** the hard stop is live (`BARGE_IN_ENABLED`, threshold 0.75). There were **0** `Barge-in detected` lines this week,
  and 2 in November. `BARGE_DUCK_ENABLED` is absent, which means duck-decide-resume is off. **Lab-pending**: the offline simulation
  predicts 9 of 10 (gap: a lone "stop" resumes) and 0 stuck states in 5,000 fuzz runs. The real-Silero corpus run and the
  owner's panel hour (E2) have not happened. [Proven: code and live; record]

## 3. Never mute

What the Pi is: Raspberry Pi 5 Model B, 4 cores, 8,063 MB. `free -m`: 1,501 used, 6,561 available, swap unused. Load
0.22-0.35. The daemon uses RSS 780 MB and about 13% of one core averaged over 2.4 days (7 h 37 m CPU). Temperature 61.5 C;
`get_throttled=0xe0000` (frequency-capped, throttled and soft-temp-limit have occurred since boot; none now). 12 GB disk free.
Uptime 4 days 3 h. The daemon started 5 times this week (4 clean stops, which are deploys), with no crash. [Proven, live]

| # | Failure | What the person hears NOW | Evidence | Smallest audible fix |
|---|---|---|---|---|
| 1 | zoe-data restarting (111 systemd starts in 7 days, at most 12 in one hour; the Pi saw 160 daytime unavailability episodes, median 10 s) | Wake works. The stream POST gets a 502 from nginx, falls back to the blocking turn, then hears **espeak-ng "One moment."** (robot voice). Retry after 4 s. If it still fails, **espeak "Sorry, I can't reach my brain right now..."** | `D:4160-4188`, `D:4321-4325`, `_espeak_local D:2807`; Pi log, 7 days: 190 `poll recovered` episodes, 24,269 s blind in total (30 episodes / 12,439 s at night); journal `Started Zoe Data` 111 | Play a Pi-local **af_sky** clip instead of espeak (the `voice_phrases.json` ids `hold_on`, `brain_unreachable`, `service_unreachable`). The clips exist in Zoe's voice on the Orin; ship them with the daemon. [Speculative, about 0.5 day] |
| 2 | Orin down or network loss | Same as 1, except that a connect timeout is a "restartish" failure only if the connection provably never opened (`D:1543-1556`). A hung zoe-data that accepts and never answers waits for the `timeout=60` read (`D:3925`), then the blocking turn, then espeak: **up to about 60 s of silence** | code | The same cached clips, plus a Pi-side deadline: no first frame within 6 s, play the cached "give me a moment" clip. [Speculative] |
| 3 | **Kokoro stopped or reloading** (128 clean stop/start cycles in 7.9 days, median 156 s, p90 220 s, 7.3 h in total, every hour of the day; attributed: 16 of 128 within 120 s of a zoe-data stop) | zoe-data is up and the brain answers, but `/stream` returns an error per chunk. The turn is **silent** by owner rule ("Kokoro is the ONLY voice", voice-pipeline.md section 3). The daemon logs `transcript but no audio` and plays the follow-up **beep**. **Live 2026-11-11 06:42**: Kokoro stopped 06:40:08 and was ready 06:42:46; a turn at 06:42:09 got a beep. The reply was generated and never heard | `D:4146-4150`; Pi `transcript but no audio` 22 lifetime; journal `Stopping Kokoro` 128 | (a) Find and gate whatever stops the live Kokoro by day (**could not attribute**: not deploy alone; no repo script found). (b) On `transcript but no audio`, play the cached af_sky "my voice is restarting, one moment" clip instead of a bare beep, and keep the reply for re-delivery. [Speculative] |
| 4 | llama slot busy (night jobs, extractor; `BRAIN_SLOT_GUARD ... waited_ms=237503` seen this week) or a cold prefix | **Silence until the reply**: brain p90 first PCM 4.6 s, max 6.2 s this week. On a router `chat` turn the timer filler is **suppressed by design** (`SPACER skip ... reason=chat_domain` 9 times; `voice_spacer.py` docstring). The tool ack fired on 8 of 49 brain turns. 0 timer fillers on brain turns | live, code | A wait cue that is not a canned line: see experiment X7 (the latency-masking study says non-verbal sounds did NOT help, so it is an A/B, not a default). [Speculative] |
| 5 | Night window (zoe-data, Kokoro and 4B stopped for the 12B) | Same as 1. Wake works, then espeak. 30 episodes, 12,439 s this week | live | Same cached clip, with night wording ("I'm resting, I'll be back at about four"). [Speculative] |
| 6 | Panel daemon crash | `Restart=always`, `RestartSec=5`, burst 5 in 120 s: deaf for about 5-15 s, then normal. 0 crashes this week | unit file on the Pi | Already adequate. [Proven] |
| 7 | Daemon alive but deaf (mic or Jabra lost, the Pulse source swapped) | **Nothing, possibly indefinitely.** `/health` reports build, uptime and speaker model; it has no mic liveness. `WatchdogSec=0`, no sd_notify. Mic open retries 5 times, then that one recording returns None (`D:2709-2720`); the wake loop's own stream read is `exception_on_overflow=False` | code | Add `last_frame_age_s` and the input RMS to `/health`, plus `sd_notify` WATCHDOG fed by the wake loop only while frames arrive (systemd restarts it). The panel UI shows "can't hear" from `/health`. [Speculative, about 1 day] |
| 8 | Wake heard, then wrong STT or not addressed | "Empty transcript": retry chime (2 this week). Not addressed: silence (2) | live | Fine as designed. |

**What already exists for never-mute** [Proven, code and live]:
- 12 pre-rendered af_sky "thinking" clips on the Pi (`~/.zoe-voice/buffers/buf_01..12.wav`, 744 KB, Jun 20). They play only on
  the blocking `/turn` path after 0.8 s (`D:3610-3636`), never on the streaming path, which is the live one, and never on the
  restart path (`D:4255-4260`: "restart handling owns the speaker"). They were used 32 times in the log's lifetime.
- `voice_phrases.json`: one phrase set, about 14 ids and about 20 variants, including `brain_unreachable`, `service_unreachable`, `hold_on`
  (#2271). The Kokoro sidecar keeps a phrase cache (`~/.zoe/kokoro_cache`, 1,001 WAVs, 119 MB, about 119 KB per clip).
- espeak-ng is installed on the Pi and is the only voice the Pi can produce on its own today.

**Cost of a Pi phrase cache** [Proven inputs, Speculative design]: about 20 variants x about 60-120 KB = **1-2.5 MB on disk, 0 resident RAM**
(played by `aplay` from file, as the buffer clips are). Close to 0 CPU, against 6.5 GB available and a load of 0.3. Render on the Orin
by the existing Kokoro, ship with `deploy-pi-voice.sh`, and pin with the build sha so a phrase edit re-renders. An earcon
ladder (chime, then a soft tone at 3 s, then the cached line at 6 s) costs the same. A watchdog costs one timer.

## 4. The dream, made falsifiable: a Pi+Orin voice runtime with a hard budget

### Budget (proposed)
- **B0:** an audible acknowledgement within **600 ms** of speech end, always (including Orin down).
- **B1:** a command's first content sound within **1.2 s**.
- **B2:** a conversation's first content sound within **2.0 s**.

### Can it be met? Arithmetic against the measured waterfall [Speculative]

| | today median | budget-runtime median | how |
|---|---|---|---|
| speech end to commit | ~770 | ~450 (speculate at 320 ms deep quiet; commit at 640) | B1.1 is built and flag-dark; Smart Turn only as an extender |
| commit to server has text | 156 + 54 = 210 | ~60 | speaker-ID scored under speech or after the POST (shadow-style async); STT is already under speech |
| routing (non-brain D) | 409 | ~150 | start the router on the stable streaming partial before commit; it is cancelled with the speculation |
| first content synth | Kokoro ~300 | ~300 (or ~0 for a Pi-cached "Okay.") | Kokoro rock; cached acks for fixed confirmations |
| **command total** | ~2100 corrected | **~1.0-1.2 s** | B1 is marginal; meeting it needs both speculation AND an early router |
| brain to first clause | 2038 | ~700 (prefix hit ~350 + about 6 tokens at 20-27 tok/s ~250, no first-sentence hold) | first-clause streaming; prefix kept warm |
| **conversation total** | ~3450 corrected | **~1.9-2.1 s** | B2 is reachable only with speculation, first-clause streaming AND a warm prefix |
| B0 ack | none (0/72 under 600 ms) | Pi-local, at the predicted end of turn | the Pi owns it: no network, works with the Orin down |

The arithmetic says B2 is reachable on today's rocks only if all three levers land, and that B0 is reachable only from
the Pi. Every component above was measured on this box; none of the combinations has been.

### What moves to the Pi [Speculative]
1. The **B0 ack and every degraded-state line** (phrase cache, earcon ladder, deadline timers): the Pi is the only part
   that is up when the Orin is not.
2. **End-of-turn prediction** (it already lives there) plus, later, an extender model. Pi CPU for Smart Turn is unmeasured, so measure it first.
3. **Speaker-ID scoring off the critical path**: send the verdict as a follow-up frame (`voice-pipeline.md` notes that no such path
   exists in active mode).
4. Not STT, brain or TTS: the rocks stay on the Orin.

### Adopt or beat: maintained projects

| Project | What it would give | Verified this session | Bake-off role |
|---|---|---|---|
| Smart Turn v3.2 (Pipecat) | an audio end-of-turn model | Only via the repo's own eval (#2280) and `voice_turn.py`. The source is not in the opensrc cache. [record: unverified upstream] | Adopt as an **extender** only if X6 passes |
| Pipecat (Daily) | cascade orchestration, interruption, Smart Turn wiring | Not cached. [record: unverified] | Reference for the frame and interruption model; adopting it would replace the daemon and the turn_stream protocol |
| LiveKit Agents | turn detector (text model), adaptive interruption (a cloud service) | `livekit/livekit` and `python-sdks` are cached; `agents` is not. Zoe already has a dormant LiveKit lane (`routers/voice_livekit.py`) with Silero barge-in and Smart Turn wired. [Proven code for Zoe's lane; upstream unverified] | The existing lane is the cheapest "adopt" arm: it can run in a lab without any new dependency |
| Wyoming satellite / linux-voice-assistant (OHF, Home Assistant Assist) | the standard satellite protocol, wake on the device | **Both are installed on the Pi and disabled** (`wyoming-satellite.service`, `linux-voice-assistant.service`: the HA Assist satellite at commit 341e3dc 2026-03-31, `pymicro_wakeword`, `aioesphomeapi` 42.7.0). [Proven, live] | Protocol arm: it gives up Zoe's endpointing, speculation and barge-in control unless extended. Likely to lose on B0/B2; useful as a never-mute reference |

**How a bake-off decides (house rule: ties go to the maintained system):** the same 40 corpus utterances are played from
a speaker at the panel position (a lab night, never household hours), through each arm, on this hardware. Each arm is
measured with this lane's join (speech end to first PCM, B0/B1/B2 pass rates), its Orin RAM delta (it must be 0 or less: the
Orin has about 1.7 GB available today), its Pi CPU, the cut-offs per 100 (scored by ear against the clip's true end) and the
behaviour with zoe-data stopped. An adopted arm must match the native one within 10% on the pass rates and win on maintenance.
Effort: 5-8 days. [Speculative]

## Experiments

All are [Speculative] until measured. Every voice-path build is replay-gated, landed outside the night window and live-verified after the deploy.

| id | Hypothesis | Build | Measure | Success | Failure | Days |
|---|---|---|---|---|---|---|
| X1 | The house never goes silent or robotic in an outage if the Pi can speak Zoe's own voice | Render the `voice_phrases.json` outage ids with Kokoro, ship them to the Pi with the build sha, and replace `_espeak_local` and the bare beep on `transcript but no audio` with them | Lab: stop zoe-data, then Kokoro, then the network, one at a time (lab window only); live: Pi log counts per week | 0 espeak lines and 0 "beep only" replies across at least 5 live outages; a cached line within 1 s of the failure | Any espeak, or a cached line overlapping a real reply | 1.5 |
| X2 | Most daytime mute minutes are self-inflicted Kokoro restarts | Attribute the 128 stops (journal `_PID`/caller; correlate with runner jobs and landings), then gate the stopper to quiet hours, or use the harness without stopping the live sidecar | `Stopping Kokoro` per day; Kokoro-down minutes between 07:00 and 22:00 | Under 10 daytime minutes a week | No attribution after 1 day | 1-2 |
| X3 | First-sentence holding is the largest removable chunk | Ear-check, then enable `ZOE_FIRST_SOUND_NARRATION_EARLY` + `..._CLAUSE` | This lane's join over 7 days: E+F and the total, brain turns | E+F median 1.2 s or less; brain total median (corrected) 2.7 s or less; no rise in re-asks (struggle detector) | Under -0.3 s, or the ear check fails on the pitch reset | 1 + a week of data |
| X4 | Cache misses cost about 0.8 s on half the brain turns and are fixable | Log the diverging token offset on `PANEL_KV_MISS`; make the wake prewarm warm the exact prefix the turn uses (manner, continuity and tool blocks) | `first_prompt_n` distribution | prompt_n of 200 or more on under 10% of brain turns | The divergence is per-turn content that cannot be pre-warmed | 2 |
| X5 | Speculative start hides about 0.4 s of tail plus pre-brain | Enable B1.1 (`ZOE_SPECULATIVE_TURN`) in a lab, then live | Total median, cancel rate, cancelled-but-audible count | Brain total -0.35 s or more; cancel rate under 15%; 0 audible cancelled turns; 0 double writes | Any audible cancelled turn or side effect | 2 + lab |
| X6 | Smart Turn as an extender holds mid-thought pauses without new cuts | Run the built `--extend-only` arm offline in a quiet window (and time Smart Turn on the Pi CPU) | correct %, cuts, holds of 1 s pauses, added wait | 90% or more correct, cuts no higher than live, holds 30% or more of 1 s pauses, over-waits 5 per 100 or fewer; Pi check 150 ms or less | Over-waits above 5 per 100, or Pi check above 250 ms | 0.5 run + 2 build |
| X7 | A Pi-local B0 cue at the predicted end of turn makes waits feel shorter, without the canned-line feel | Earcon ladder (soft tone at about 450 ms on chat turns; a cached af_sky line only at 4 s) behind a Pi flag | Blind A/B "robot feel" panel (lane E method) plus re-ask rate | Preferred in 60% or more of pairs; no rise in re-asks | Not preferred (the field result says non-verbal cues may not help) | 2 |
| X8 | Speaker ID can leave the critical path | Score after the POST and send the verdict as a follow-up frame before any write | B stage | B median 60 ms or less; 0 writes under the wrong identity | Any write before the verdict | 2 |
| X9 | A deaf daemon is detectable and self-heals | `/health` frame age plus RMS; sd_notify watchdog; panel "can't hear" state | Lab: unplug the Jabra | Restart or an on-panel state within 30 s | Restart loop with the device absent | 1 |
| X10 | Duck-decide-resume is ready for a room | Run the owner's E2 hour (scripted) | `BARGE_DECIDE` lines | 8 or more of 10 correct, 0 stuck mutes | Any stuck mute | owner hour |
| X11 | A native hard-budget runtime beats adopting | Bake-off: native (X3+X4+X5+X7) vs the dormant LiveKit lane vs the installed HA satellite | B0/B1/B2 pass rates, Orin RAM, cuts, outage behaviour | Native B2 pass 70% or more with B0 100%, and an adopted arm not within 10% | An adopted arm within 10%: adopt it | 5-8 |

## Could not verify

- **Who stops Kokoro 128 times a week.** Not attributed: no repo script found; only 16 of 128 coincide with a zoe-data stop.
- **Which stream wrapper holds the first text on a given turn**, and the live values of `ZOE_EMOTIONAL_TURN_GUARD`,
  `ZOE_MIRROR_GUARD` and `ZOE_CLEAN_GOODBYE`. Their names are present; their values were not printed (rail).
- **The Recorded-to-POST split inside stage B.** The daemon logs no POST start. The speaker-claim line is logged after the
  POST started on some turns, so the scoring order in active mode needs a code read with a run.
- **The true acoustic end of speech per live turn.** The config floor plus the corpus lag are used; the live per-turn lag is unmeasured.
- **Smart Turn on the Pi CPU**, the Pi's behaviour on mic-device loss, and Pipecat, LiveKit Agents and Smart Turn upstream
  source (not in the opensrc cache).
- **Missed wakes.** The 375 near-misses are unlabelled.
- **Whether the 190 Pi-side unavailability episodes are all zoe-data restarts.** 150 were nginx 502s, 38 read timeouts
  (hung or slow), 2 HTTP 500s; the "poll blind" figures include up to 60 s of the Pi's own backoff, so the totals are upper bounds.
