# Lane 2-I: the panel and voice experience, end to end (second-round deep dive, 2026-10-19)

Scope: what the household feels at the wall panel: latency, reliability, wake word, identity,
barge-in, the kiosk UI, enrolment, deploy, panel health, the replay corpus. Read-only. Code is
origin/main `4b4ceac76` (includes #2117). Live reads 2026-10-19 00:05-00:25 AWST from the Jetson
(`~/.zoe-logs`, `~/.cache/zoe`, `/proc` of zoe-data PID 609135) and the Pi over ssh (`zoe-pi`).
No transcripts, names or utterances are quoted; log figures are counts and timings only.
Labels: **[verified]** = read directly in code or live state this session; **[inferred]** =
derived, with the reasoning given.

Path shorthand: `D` = `scripts/setup/zoe_voice_daemon.py`, `VT` = `services/zoe-data/routers/voice_tts.py`,
`H` = `services/zoe-ui/dist/touch/home.html`.

---

## 0. Headline

1. **Household usage is light and bursty.** Last week the panel did 46 wakes and 150 streamed
   turns. 83 of those turns were on one day (10-12) and none after 10-16 [verified, Pi
   `voice.log`]. Every finding below rests on small numbers. Most of them are owner test bursts,
   not family use.
2. **The latency median is better than 09-28. The tail is worse, and the tail is the brain
   prompt cache.** The daemon's TTFA (POST to first PCM): median 2.33 s, p90 5.64 s (n=150).
   The week of 09-28 was 2.48 / 4.79 (n=43). On the server, `brain_ttft_ms` is median 1.38 s and
   p90 5.87 s (n=102), and **29 of 100 panel brain turns re-prefilled more than 500 tokens**,
   18 of them on warm back-to-back turns [verified]. End of speech to first sound is about
   3.2 s median and about 6.5 s p90 [inferred: TTFA plus the measured 0.72-0.88 s tail plus
   about 0.08 s sink].
3. **Voice ID has not identified anyone in practice yet.** In 158 of 159 panel `voice/command`
   turns last week the server logged `identified=None`. Member scope came from PIN or panel
   binding, and once #2026 (strict identity) landed, 49 of those turns ran as guest
   [verified]. The owner flipped `ZOE_SPEAKER_MODEL=resnet34`, `SPEAKER_ID_SHADOW=0` and
   **`ZOE_VOICE_SESSION=1`** at 2026-10-18 23:13. No panel turn has happened since. Voice
   sessions are therefore **ON**, not off as the brief assumed [verified, `/proc/609135/environ`
   and the `.env` mtime]. Only **one** voice profile is enrolled. Every other member is a guest
   by voice.
4. **The wake threshold sits 0.01 above a dense band of background audio.** Near-misses top out
   at 0.44 against a threshold of 0.45, at about 14 a day and peaking 20:00-23:00. 9% of wakes
   end in an empty transcript [verified counts]. There is still no false-accept or false-reject
   rate. The model (`hey_zoe.onnx`) is not in git. Two copies exist (the Pi and
   `~/smb-drop` on the Jetson) and its provenance is undocumented.
5. **The Pi copy matches main** (sha256 `94c26ffd...` = `3e55673af`). Drift is only checked at
   deploy time, though. **36 of the panel's 46 env keys exist only on the Pi**, so a second
   panel built from the repo would wake to "hey jarvis" with the amplitude endpointer and no
   speaker ID [verified].
6. **The Pi wears its SD card and runs hot with no fan.** Chromium wrote about 3.5 GB in 34 h
   (≈2.4 GB a day). No cooling device or fan hwmon is present. `get_throttled=0xe0000`: the
   soft temperature limit and throttling have both occurred since boot. The journal is
   volatile, so crash history is lost at each reboot [verified].

---

## 1. The daemon

### Size and structure [verified]
- `D` is **4,862 lines**, with 178 functions/methods and 15 classes (`_SttStreamUploader`,
  `_Endpointer`, `_BargeDetector`, `_SinkInputDucker`, `_BargeDecider`, `_PlayoutLedger`,
  `_BargeEpisode`, `_BargeMonitor`, `_SpeculativeTurn`, `_HealthHandler`, ...). Companions on the
  Pi: `zoe_voice_announce.py`, `zoe_voice_enroll_mode.py`, `zoe_enroll_flow.py`,
  `zoe_speaker_resnet34.py`, `zoe_face_id.py`.
- **Threads** (`threading.Thread(` sites, `D:276-4683`). Long-lived: main wake loop,
  barge-in VAD (`D:4652`), ambient (`D:4677`, disabled live), announce poll (`D:4683`, every
  5 s, `D:3990`), health HTTP server (`D:4549`, :7777). Per turn: stt-stream-upload (`D:276`),
  barge-monitor (`D:1668`), wake-beep / wake-screen / wake-notify (`D:2035-2037`),
  speaker-shadow / secondary / adapt (`D:2984, 2861, 2714`), the `_run_turn` worker (`D:3635`),
  spec-turn (`D:3248`, flag off). One-shot: resnet34 warmup, resemblyzer warmup, profile sync
  (`D:4666-4675`). Live: `Tasks: 12`, RSS 716 MB, about 12% CPU while idle-listening [verified].
- **One global Silero model is shared by every VAD consumer.** `_vad_prob` still feeds each
  1,280-sample chunk as two 512 windows and **drops 256 samples per chunk**. `reset_states()` is
  never called (`D:952-975`). The open-problems entry of 2026-10-05 is still open.

### State machine (main loop `D:4740-4840`)
`IDLE` (OWW `predict` on every 80 ms chunk, `_PREROLL` deque of 20 chunks = 1.6 s, `D:919`)
→ wake needs **3 hits ≥ 0.45 within 1.0 s** (live `WAKE_CONFIRM_COUNT=3`,
`WAKE_CONFIRM_WINDOW_S=1.0`) and at least 5 s since the last wake (`MIN_WAKE_INTERVAL_S=5`)
→ `on_wake` (beep, screen wake via `127.0.0.1:8765/wake`, `POST /api/voice/wake`, which
prewarms the brain on the server) → `record_command` from the still-open wake stream
(`_Endpointer`, VAD tail, STT under-speech upload) → `_do_single_turn_stream` (NDJSON stream,
PCM chunks into `aplay`, `_BargeMonitor` on its own mic stream) → `_follow_up_listen`
(5 s window, VAD 0.50, **`FOLLOW_UP_MAX_TURNS=999`**) → back to IDLE. Side states: orb-tap
`/activate` (`D:4745-4761`), conversation mode ("let's talk", 60 s hard cap), enrolment mode,
announcements (speak, then listen).

### Network calls per turn [verified]
Every call is a module-level `requests.post/get` (`D:1164, 1223, 2016, 2389, 3377, 4019`).
**There is no `requests.Session`, so no keep-alive.** Each call opens a new TCP and TLS
connection to nginx on `https://192.168.1.218` with `VERIFY_SSL=false`.
A typical wake turn makes:

| call | when | timeout |
|---|---|---|
| `POST /api/voice/wake` | at wake (thread) | 3 s, no retry (`D:2006`) |
| `POST 127.0.0.1:8765/wake` (panel agent, screen) | at wake | local (`D:2017`) |
| `POST /api/voice/stt_stream/chunk` × (speech s / 0.32) | while speaking: ~10-15 per 4 s turn | 5 s, no retry (`D:299`) |
| `POST /api/voice/turn_stream` (streamed NDJSON) | endpoint | 60 s (`D:3377`) |
| `POST /api/voice/turn_stream/speculation` | B1.1 only (off) | 15 s (`D:3270`) |
| `POST /api/voice/adapt` | high-certainty claim (thread) | 5 s (`D:2686`) |
| `GET /api/voice/announcements` | every 5 s, always | (`D:4019`) ≈17k handshakes a day |
| `GET /api/voice/profiles/sync` | hourly and on enrol | (`D:2389`) |

A follow-up turn repeats the stream and turn POSTs. [inferred] One fresh TLS handshake per
0.32 s batch is probably 10-30 ms of CPU and two LAN round trips each. That is small next to
the brain, but it is pure waste, and a pooled `requests.Session` is a one-line class fix.

### Flags and where they are set [verified]
- Pi: `/home/pi/.config/systemd/user/zoe-voice.service` has `EnvironmentFile=-/etc/zoe-voice.env`
  (absent) and `-/home/pi/.zoe-voice/.env.voice` (46 keys, modified 10-18 23:13). The `/proc`
  environ of PID 88518 matches the file. Live voice-relevant values: `WAKEWORD_THRESHOLD=0.45`,
  `WAKE_CONFIRM_COUNT=3`, `VAD_ENDPOINT_ENABLED=1`, `VAD_ENDPOINT_SILENCE_S=0.8`,
  `ZOE_VAD_TAIL_MS=640`, `BARGE_IN_THRESHOLD=0.75`, `SPEAKER_ID_ENABLED=true`,
  `SPEAKER_ID_THRESHOLD=0.75` (Resemblyzer; unused with resnet34 acting), `ZOE_SPEAKER_MODEL=resnet34`,
  `SPEAKER_ID_SHADOW=0`, `ZOE_STT_STREAM_UPLOAD=1`, `ZOE_VOICE_STREAM=1`. **Not set (so off):**
  `BARGE_DUCK_ENABLED`, `ZOE_SPECULATIVE_TURN`, `ZOE_VAD_CLEAN_TAIL_MS`, `ZOE_VAD_HESITATION_TAIL_MS`.
- **The unit's `Nice=-5` has no effect.** The process runs at NI 0, because a `--user` unit
  cannot raise its own priority.
- Server: zoe-data user unit → `EnvironmentFile=/home/zoe/assistant/services/zoe-data/.env`
  (modified 10-18 23:13) plus `~/.hermes/.env` (retired, still loaded). Live:
  `ZOE_VOICE_SESSION=1`, `ZOE_SPEAKER_MODEL=resnet34`, `ZOE_SPEAKER_ID_THRESHOLD=0.70`
  (Resemblyzer), `ZOE_STT_STREAM_UNDER_SPEECH=1`, `ZOE_FIRST_SOUND_TOOL_ACK=1` (CLAUSE and
  NARRATION_EARLY not set), `ZOE_FLUE_ABORT_ON_CANCEL=1`, `ZOE_ENROLL_OFFER=on`,
  `ZOE_FACE_ID_ENABLED=true`, `ZOE_VOICE_BARGE_IN=1`, `ZOE_LIVEKIT_ONDEMAND=true`;
  `ZOE_SPECULATIVE_TURN` is not set.

### Deploy and drift [verified]
- The deploy is hand-run from a worktree: `scripts/setup/deploy-pi-voice.sh` (292 lines). It
  rsyncs 8 files with a `.bak-<ts>` suffix, runs `pip install -r pi-requirements.txt`, renders
  and enables the unit, restarts, waits for `is-active` and `/health`, waits for
  `speaker_resnet34==ready` in resnet34 mode, and compares md5 (`:209-284`). `deploy.yml`
  never touches the Pi.
- **Today the Pi copy matches main.** Pi `zoe_voice_daemon.py` sha256 `94c26ffd...` equals
  `git show origin/main:` and equals the last commit touching the file, `3e55673af` (10-18,
  "voice-path identity is server-decided"). The announce, enroll_mode, enroll_flow, resnet34 and
  face_id files also match main.
- **Drift is not detected between deploys.** `/health` returns
  `{status, service, panel_id, uptime_s, wake_phrase, speaker_model, speaker_resnet34}`
  (`D:4469-4487`) with no version or sha, and `zoe_ground_truth.sh` checks the panel's push
  channel but not the daemon's code. The daemon dir holds 9 `zoe_voice_daemon.py.bak-*` copies
  plus a July tarball.
- **The config record lives on the Pi alone.** 36 of the 46 live keys are absent from
  `scripts/setup/zoe-voice.env.example` (14 keys). The example says `WAKEWORD_THRESHOLD=0.5`,
  the code default is 0.28 (`D:408`) and live is 0.45. `VAD_ENDPOINT_ENABLED` defaults to false
  (`D:180`), so a fresh panel falls back to the 1.5 s amplitude tail.
  `hey_zoe.onnx` is not in git, and the installer prints "without hey_zoe.onnx the bundled model
  is hey_jarvis" (`pi_voice_daemon_install.sh:207`).

---

## 2. The latency budget today

### Live numbers (panel turns, Pi clock = POST to first PCM; server stages from `zoe-data.stderr.log`) [verified]

| stage | 09-28 breakdown (n=10) | live now | source |
|---|---|---|---|
| wake → recording start | not on the TTFA path | beep + stream reuse; no gap | `D:4822-4834` |
| endpoint tail (speech end → closed) | ~850 ms median (720-880 at 640/800) | same config; 139/167 W3 recordings closed on `tail=deep`, 19 `quiet`, 6 hit max | Pi log `Recorded ...` |
| recording length | — | median 3.60 s, p90 6.96 s, max 27.7 s (n=167, W3) | Pi log |
| speaker-ID score | 540 ms (then on the path) | off the path (#1760); ResNet34 97-273 ms in a thread | Pi log `Speaker ID (dual...)` |
| upload | 99 ms | not re-measured | — |
| STT (Moonshine, whole clip) | 710 ms median | **730 ms median, p90 2.14 s** (n=159); under-speech hits 12, `finish_ms` median 7.5 | `voice/turn_stream ... STT=`, `STT_STREAM` |
| pre-brain (router, scope) | 87 ms | **79 ms median, p90 517 ms** (n=102) | `VOICE TIMING pre_brain_ms` |
| brain TTFT | 386 ms (warm, cache hit) | **1,381 ms median, p90 5,868 ms** (n=102) | `VOICE TIMING brain_ttft_ms` |
| first token → first sentence | 1,077 ms (Flue flush) | fixed by #1761 (first sentence ~749 ms warm, probe) | `first-sound-latency-2026-10-09.md` |
| Kokoro first unit | 305-396 ms | not re-measured live | — |
| **daemon TTFA** | **2.74 s median** | **2.33 s median, p90 5.64 s** (n=150, 10-12..10-16); 2.48 / 4.79 the week of 09-28 | Pi `turn_stream TTFA=` |
| sink start | 70-90 ms | — | — |
| **end of speech → first sound** | ≈4.0 s | **≈3.2 s median, ≈6.5 s p90** [inferred] | tail + TTFA + sink |

Caveats. TTFA counts the first audible chunk, which since 10-09 can be the `TOOL_ACK` cached
line or a filler ("first audible", not first answer). The p90 is dominated by brain TTFT.

### The prompt cache is the p90 [verified counts, inferred cause]
- `FLUE_PROMPT_CACHE` for panel sessions: `first_prompt_n` median 100, p90 3,481, max 4,392.
  **29/100 turns re-prefilled more than 500 tokens.** 11 were the first turn of a new session
  (19 distinct sessions in 100 turns). **18 were warm turns less than 5 min after the previous
  panel turn.**
- llama-server is single-slot (`--parallel 1`, voice-pipeline.md B0.4). The replay probe ran
  288 times in October, many on 10-12 to 10-17, often in the same hours as panel turns. The bar,
  the Telegram brain and probe sessions share that slot [inferred: a warm miss means another
  consumer evicted the panel's KV].
- At about 2.1 ms per prompt token (voice-pipeline.md), a 3.5k re-prefill is **about 7 s**. That
  is the household's slowest turn.

### Are the numbers bound to a commit?
- **The probe numbers are bound.** `voice_regression_last.json` carries
  `revision.commit=730ef4a6f`, `tree`, `clean_verified=True` and `dirty=True` (worktree), with
  medians stt 510 / brain 1,378 / e2e 1,761 ms, 20 clips, `status: pass`, 2026-10-18 15:48Z.
- **The live panel numbers are not bound.** Neither the Pi log nor the zoe-data log lines carry
  a build sha or a turn id shared between the two hosts, and zoe-data restarted 8-46 times a day
  this fortnight (`LiveKit voice agent task created` count: 103 in the last 7 days).
- **The probe is noisy.** October: 288 runs, 207 pass, 78 fail (**43 SPEED, 35
  said-vs-did**), 2 error, 1 skip. Most SPEED fails clear on a rerun (e.g. 10-17 22:32-23:13:
  five fails at 1.7-2.1× STT, then a pass at 23:20 on the same commit).

### Where the next 500 ms is (ranked by evidence)
1. **Keep the panel's KV resident.** This is the p90, worth seconds rather than 500 ms. Options:
   a second llama slot for the panel session, `--slot-save-path` restore, or quiet hours for the
   harness and bar during waking hours. Measure with `FLUE_PROMPT_CACHE first_prompt_n` per panel
   turn.
2. **The endpoint tail.** Live is 640 ms deep and 800 ms any-quiet. `ZOE_VAD_CLEAN_TAIL_MS=560`
   (built, `D:222`) ended 124/246 corpus turns a median 160 ms earlier, with 2% cut
   mid-sentence. **B1.1 speculative turn-start** (`ZOE_SPECULATIVE_TURN`, off on both hosts)
   fires at 320 ms of deep quiet: 0.3-0.5 s, corpus cancel rate 14.3%
   (`docs/architecture/b1-speculative-turn-start.md:226`).
3. **STT under speech is only partly delivering.** It went live 10-14. It produced 12 `hit` lines
   on the server, against about 61 panel turns 10-14..10-16. On 10-16 all 4 hit; on 10-15 7/21
   did [verified counts; the miss reason is not logged, since `outcome=fallback` lines are absent,
   which suggests the stream id was never attached: inferred]. A hit removes about 0.25 s per
   second of clip (median clip 3.6 s → ≈0.6-0.9 s saved).
4. **`ZOE_FIRST_SOUND_CLAUSE`.** Chat turns −0.77 s (10/10 paired). It waits on the ear check
   (first-sound-latency doc).
5. **Kokoro streams whole units** (`kokoro_sidecar.py:739-745`, open-problems 10-05). This would
   take ≥100 ms off 60+ character units.
6. **The 1.6 s pre-roll goes into STT on every wake turn**, about 0.4 s of STT at 0.25 s/s. Its
   accuracy risk is known.

---

## 3. Wake word and endpointing

- **Model** [verified]: `/home/pi/.zoe-voice/hey_zoe.onnx`, 415,224 B, dated 2026-04-19, sha256
  `6475cc16...`. An identical copy sits at `/home/zoe/smb-drop/hey_zoe.onnx`, next to an older
  `hey_zoe.onnx.invalid` (661 KB). It is **not in git**, and **the nightly backup does not cover
  it** (the backup timer's own description says "MemPalace tarball + zoe.db"). Training recipe,
  data and date are undocumented. The Pi runs the ONNX backend, while openWakeWord's classifiers
  are trained on TFLite features; there is no `hey_zoe.tflite` (`speech-stack-docs-vs-zoe-2026-10-05.md:234-248`).
  Speex NS is not installed (32 log lines say so).
- **Threshold**: 0.45 live, with 3 confirmations in 1.0 s and a 5 s minimum interval. The model's
  design point is 0.5. Near-miss logging covers scores 0.18 up to the threshold, rate-limited to
  one per 12 s (`D:4799-4806`).
- **What the log shows** (Pi `voice.log`, 07-15 to 10-18) [verified counts, inferred meaning]:
  - **1,370 near-misses vs 303 wakes.** By hour, near-misses peak 21:00-22:00 (112, 111) and
    07:00-08:00, which is TV and radio time.
  - **The near-miss maximum is 0.44 every week** (buckets Sep-Oct: 95 at 0.2, 66 at 0.3,
    40 at 0.4). Background audio regularly gets within 0.01-0.05 of firing.
  - **27/303 wakes (9%) were followed by the empty-transcript retry chime, and 6 by a
    no-speech recording.** That is a false-accept proxy (wake with nothing said).
  - **13 wakes between 01:00 and 05:00** (6+1+2+4) over three months. That is an upper-bound
    proxy for overnight false accepts.
  - **12 near-misses were followed by a wake 1.5-10 s later** (a re-try proxy for false
    rejects). 52 more fell within 1.5 s; those are the score ramp of the same utterance, not a
    retry. It is a lower bound: a give-up, or a first attempt scoring below 0.18, is invisible.
- **There is still no measured FAR/FRR.** Open since 10-05 (`open-problems.md:133`, SP gap 5,
  samantha-gap G04). Five TV false-wake clips are quarantined (`quarantine-tv-falsewakes-20260719`).
- **What the field offers**, all to test against an instrument first: openWakeWord's own
  `custom_verifier_models` (a per-speaker verifier on top of `hey_zoe`, documented upstream and
  unused here); speaker-verified wake as OVOS does it (gate the wake on the enrolled speaker's
  embedding; ResNet34 is already resident on the Pi at about 100-270 ms per clip); a pre-wake VAD
  gate; newer trainers (WakeForge-class) with large synthetic negative sets; microWakeWord-class
  models that publish ≤0.16 false accepts per hour on DiPCo (`inference-speech-stack-2026-10-03.md:339`).
  Tooling names WakeForge and Pre-Wake-VAD come from the brief and are **[unverified]** here.
- **The cheapest instrument** [inferred design]: (a) **positive set**: the 1,485-clip corpus.
  Each clip starts with 1.6 s of pre-roll that contains the wake phrase, so replaying OWW over
  the first 2 s of each clip gives an owner FRR at any threshold, offline, on the Jetson. (b)
  **negative set**: have the daemon save a 3 s ring buffer around each near-miss (`D:4799`)
  into a local, never-uploaded, 7-day-retained folder. TV and radio hours give about 14 clips a
  day, labelled for free as "not a wake". Sweep the threshold to get FA per hour. (c) the 5 TV
  clips must score 0/5. Gate per `speech-stack-docs-vs-zoe` §5: owner FRR <10%, TV 0/5.
- **Endpointing** [verified]: Silero endpointer with a 640 ms deep tail and 800 ms any-quiet;
  Smart Turn is not in this daemon (no `smart_turn` symbol in `D`; endpointing is Silero plus the
  server's trailing-conjunction hold `ZOE_CONTINUE_WINDOW_S=2.0`). Long speech is one turn:
  soft cap 12 s, hard 30 s, conversation 60 s, snap 3 s (`D:136-157`, commit `1d21d7d25`). Live W3:
  161/167 recordings stopped on silence and 6 on max duration.

---

## 4. Identity at the panel in practice

**Models.** Resemblyzer (threshold 0.70 on the server, 0.75 on the Pi) is now **secondary**.
WeSpeaker ResNet34 INT8 (`wespeaker_resnet34_i8qdq_u8s8.onnx`) is **acting** since 10-18 23:13,
with thresholds 0.233 (short) / 0.265 (long) at FAR 1% (`services/zoe-data/speaker_models.py:50`).
The 0.70 in the brief applies to Resemblyzer only.

**Live behaviour, last 7 days (server `voice/command panel=zoe-touch-pi`)** [verified counts]:

| day | member | guest |
|---|---|---|
| 10-12 | 84 | 3 |
| 10-13 | 7 | 0 |
| 10-14 (#2026 strict lands) | 14 | 25 |
| 10-15 | 4 | 17 |
| 10-16 | 1 | 4 |

- `identified=` (voice) was **None on 158/159 turns**. Member scope came from
  `bound` / `panel_recent` / `panel_default` (64 + 29 + 16), which #2026 then retired. That is why
  guest jumps from 10-14. **What the household has felt since 10-14: Zoe answers as a guest,
  and any private ask (calendar, lists, reminders) raises the PIN challenge.** `voice_cards`
  sends the "locked" card and the spoken answer gives no titles and no counts (`services/zoe-data/AGENTS.md:70`).
- **The Resemblyzer shadow** (Pi `speaker_shadow_metrics.jsonl`, 487 rows 07-28..10-16, one
  profile): owner scores p10 0.58, p25 0.67, median 0.75, p90 0.84. **33% (162/487) fall below
  0.70.** Had Resemblyzer been acting, about 1 owner turn in 3 would have been guest [inferred:
  assumes every row is the owner; `truth` is null on all rows].
- **ResNet34 live** (7 dual lines on 10-15 and 10-16, before it acted): scores 0.39-0.68 against
  thresholds of about 0.25, all accepted, 97-273 ms on the Pi. Too few to calibrate.
- **The two-cluster trap** (memory note, `speaker-shadow/report.json` on the Pi) [verified]: the
  corpus splits into k=2 (silhouette 0.42; A=860, B=452, centroid cosine 0.40). The two models
  **disagree on who the owner is** (A-membership agreement 0.56, Jaccard 0.53). Corpus "owner"
  labels are therefore not ground truth, which blocks a household FRR/FAR from the corpus as-is.
  Owner score p50 0.86 against the TV clips' max of 0.87: the TV beats the owner's median
  (`owner_median_minus_tv_max = -0.011`).
- **Enrolment state** [verified]: Pi `speaker_profiles.json` has **1 profile**
  (`Speaker profiles synced: 1` at 10-18 23:13, 2 earlier that day) and `face_profiles.json` has
  3 profiles (hand-run 07-19). The face flag is on, but the face module has no runtime producer;
  only enrolment calls it (`identity-presence-engine.md` §1). A schema bug surfaced on 10-15:
  `/api/voice/profiles/sync` returned **500 nineteen times** with "column does not exist"; none
  since.
- **Voice sessions** (`ZOE_VOICE_SESSION=1`, TTL 120 s, `voice_session.py`): with only ResNet34
  claims counting, the enrolled member should now get private reads without a PIN for 2 minutes
  after a verified turn. Every other member stays a guest until enrolled. **Unverified live: no
  panel turn has run since the flip.**
- **Gap list** (`docs/architecture/identity-presence-engine.md` §1 and §6): no evidence ledger or
  fusion (everything binary); touch activity invisible (`is_foreground: true` always,
  `touch-ui-executor.js:674,690`); no presence sensor; no unknown-voice handling; no per-user
  security settings; `child` and `family_member` collapse to the guest matrix (`guest_policy.py:127-132`);
  the crowded-room rule is not enforced for voice-card reads.

---

## 5. Barge-in and turn-taking

- **Live** [verified]: `BARGE_IN_ENABLED=true`, `BARGE_IN_THRESHOLD=0.75`, 3 of 6 chunks (≈240 ms
  of speech in 480 ms) or the fast path (2 chunks ≥0.95, ≈160 ms), 800 ms grace from the first
  write, once per playback. On fire: **hard stop** (`Barge-in: TTS playback terminated
  immediately`). The server side aborts the Flue run (`ZOE_FLUE_ABORT_ON_CANCEL=1`).
- **Duck → decide → resume** (`BARGE_DUCK_ENABLED`, `D:86-97`, `voice-pipeline.md:1274-1340`)
  is built, unit-tested and **has never run on the panel**.
- **What a cough does today** [inferred from the rules plus the log]: a loud cough is about two
  80 ms chunks of high Silero probability. If it scores ≥0.95 on two consecutive chunks after the
  800 ms grace, the fast path fires and the reply is **cut and lost**. Nothing resumes it, and
  the barged text is not marked unheard (A3 is open). A softer cough fails the 3-of-6 rule and
  is ignored. Log evidence since 09-01: **8 barge-ins, 4 of them followed by no follow-up
  speech** (the person did not then say anything), which is consistent with false barges (small n).
- **Turn-taking feel**: the follow-up window is 5 s after every reply, with
  `FOLLOW_UP_MAX_TURNS=999`. W3: 153 follow-up windows, 121 heard speech (79%). Addressedness
  (`ZOE_FOLLOWUP_ADDRESSED`) is off, so a TV in the room can keep a loop alive. The cooldown is
  0.4 s.
- **Not covered by any gate**: the replay harness starts from saved WAVs and stops before TTS,
  so barge, duck, endpoint and wake are untested by the voice gate (`voice-pipeline.md:1270-1272`).

---

## 6. The kiosk UI

- **`H`** [verified]: 4,972 lines and 384,499 B. Two inline `<script>` blocks (3,872 lines), one
  `<style>` (1,062 lines), 277 `function` declarations, 18 `setInterval`s, 5 external scripts
  (`touch-memories-space.js`, `touch-event-detail.js`, `touch-voice-session.js`,
  `touch-voice-enroll.js`, `touch-ui-executor.js?v=estate-11-panel-target`). The estate picker
  has **14 cards** (`H:4340`): home, rooms, sleep, ask, memories, weather, music, timer | day, list,
  reminder, person, confirm, settings. `dist/js/touch-ui-executor.js` is 2,785 lines. 23 other
  pages live in `dist/touch/`.
- **CSP and the orb → daemon call: fixed.** `nginx.d/security-headers.inc:12-19` now allows
  `http://localhost:7777` in `connect-src`, and the orb uses `mode:'no-cors'` (`H:3451, 4767`;
  #1822, #1829, proven on the panel 10-04).
- **PIN card: fixed in layers.** It is dismissable with an expiry-aware session (#1822). A stale
  session raises the card in place (#1864). An expired PIN session no longer deafens the kiosk
  (`4a1d12dc0`, 10-13). The push socket self-heals, with a deaf-panel ERROR (#2027,
  10-16). Ground truth now checks the push channel (`zoe_ground_truth.sh:143-159`). Residual: the
  server log has **4 "push channel has ZERO subscribers"** voice turns in the last week [verified].
- **The Ask-card conversation mode (LiveKit)** (`H:4461-4600`) loads lazily, is on-demand and
  is now a room per session (#2117 A10). **Zero `/api/voice/livekit-token` requests were logged
  10-05..10-19** [verified]: the mode exists but nobody used it. livekit-client was 2.5.0 at the
  10-04 review.
- **Since 10-04** (20 commits touching `dist/touch`): wave-1 unblockers, viewer mode, wave-6
  resilience, 48 px targets, Bluetooth settings (flag-dark), the voice session UI, enrolment
  phrases on the talk screen (#2070), the Memories space in place of the Ask Zoe launcher
  (#2054), the calendar event sheet (#2056), push self-heal, house state and music needing a
  member or the panel (`cece37cd6` #2099, `a4c1cb726`), and per-turn UI only to the originating panel (#2117 A11).
- **Still awkward** [verified unless noted]: `H` is one 385 KB file with inline JS. Every change
  is a monolith edit, and the kiosk only gets #2117's executor (`?v=` bump, SW 4.87.4) after a
  page reload; whether the kiosk has reloaded since the 23:29 deploy is **unverified**. Kiosk
  (CrOS-UA) **403s on `/api/ui/actions/pending` ran about 210 a day on 10-14..10-16**, alongside
  200s, and stopped after the 10-17 reboot. A second registered page or a stale token is
  [inferred]. The 10-04 review's P2/P3 tail (desktop) is out of this lane.

---

## 7. Enrolment and setup

- **Voice enrolment, Zoe-driven** (`zoe-driven-voice-enrollment.md`; `ZOE_ENROLL_OFFER=on`):
  offer on a PIN step-up or a conversation end → spoken yes → the daemon records 3 phrases (5
  for ResNet34 re-asks) and shows each on the talk screen (#2070) → `POST /api/voice/enroll`.
  **Server log: 4 `ENROLL_OFFER` decisions in two weeks** (guest → no offer; already_enrolled ×2;
  one spoken "learn" request) [verified]. **A structural dead end** [inferred from
  `decide_offer`: never guests]: an unenrolled member is a guest by voice, so the only moment
  that can offer is a PIN step-up. A member who never types a PIN is never offered.
- **Face**: hand-run only (`zoe_enroll_flow.py face`), 3 profiles. No producer at runtime, a USB
  power-budget caveat, and no face UI anywhere.
- **Bluetooth settings**: built, flag-dark, **not deployed** (`ZOE_BLUETOOTH_SETTINGS`,
  `PANEL_BT_ENABLED`); OTA unverified on hardware (`bluetooth-settings.md`).
- **Wi-Fi onboarding**: design only (balena wifi-connect, `docs/architecture/panel-anywhere.md:313-320`,
  #2080).
- **Provisioning**: a TOFU 6-character code (`/api/panels/provision/*`, `/confirm` needs a member
  session). The QR / ECDSA pairing redesign is design only (`panel-anywhere.md` §6).
- **A new household member today**: they talk → they are a guest (no profile) → private asks get
  a PIN challenge → the PIN needs zoe-auth to have created them a profile with a passcode (an
  admin step on desktop) → after a correct PIN, about 20 s later Zoe offers voice enrolment →
  yes → 3-5 phrases → a verified voice opens a 120 s session. Breaks: no in-panel "add a person";
  a child is never offered (`ZOE_ENROLL_OFFER_KIDS` off, an owner decision); `family_member` and
  `child` roles collapse to the guest matrix, so even a PIN'd child gets guest capabilities.
- **A second panel today** [verified pieces, inferred sequence]: flash the Pi OS →
  `scripts/setup/touchscreen/install_touchscreen.sh` (TouchKio, kiosk service, gate, watchdog,
  firewall) → `pi_voice_daemon_install.sh --zoe-url ... --panel-id ...` → **hand-copy
  `hey_zoe.onnx`** (not in git; otherwise "hey jarvis") → `fetch_speaker_models.sh` and the
  face models → mint a device token via an admin API call → **hand-write `.env.voice`. The repo
  example has 14 of the 46 keys, and the code defaults differ from the tuned panel** (wake 0.28,
  amplitude endpointing, speaker ID off, STT upload off) → bind the panel → `deploy-pi-voice.sh`
  with a different `SSH_TARGET`. Announce and enrol commands use claim-any routing unless
  `ZOE_ANNOUNCE_STRICT_PANEL` is set (`zoe-driven-voice-enrollment.md`, "Not built"), so **a
  second speaker could claim the first panel's enrolment command**.

---

## 8. Panel health [verified]

- Raspberry Pi 5 Model B Rev 1.1, kernel 6.12.34, uptime 1 d 9 h (rebooted 10-17 14:23 and
  10-15 07:17; before that 43 days). Load is a flat ~1.0. Free memory 6.7 GB of 8 GB, swap 0 used.
- **Thermal**: 67 °C at 00:09 (the ledger says 76-82 °C under load). `get_throttled=0xe0000`: ARM
  frequency capping, throttling and the soft temperature limit **have all occurred since boot**.
  No undervoltage (EXT5V 5.13 V). **No cooling device and no fan hwmon: no active cooler is
  detected.** Clock 2.4 GHz at read time. The turn log has no temperature or throttle field (open
  problem 10-05).
- **Storage**: root is the SD card (`mmcblk0`, "USD00", 29 G, 54% used). There is no NVMe, and
  `/tmp` is on the SD. The block device wrote **7.58 M sectors ≈ 3.9 GB in 34 h**. Per-process
  `write_bytes`: Chromium network service 2.0 GB, Chromium browser 1.5 GB, panel agent 189 MB.
  At about 2.7 GB a day this is SD wear driven by Chromium's HTTP cache [inferred: the network
  service writes the disk cache]. eMMC-style `life_time` is unavailable for an SD card.
- **Logging**: journald `Storage=volatile`. `journalctl --user` has no journal files, so the
  daemon's crash history across reboots exists only in `voice.log` (1.67 MB, since 06-19, with a
  NUL-byte hole). It holds 55 daemon starts against 51 clean stops since June, so about 4
  unclean exits. W3 had 10 starts, 9 of them clean (deploys). `voice.log` **stores transcript
  text in plaintext** (the `turn_stream TTFA=... transcript=` line, `D:3556`): a privacy item on
  a device in the hallway.
- **Units**: system `zoe-kiosk` (TouchKio `start-kiosk.sh`, Restart=always), `zoe-kiosk-watchdog.timer`
  (active), `zoe-panel-firewall` (active), `nqptp`; `zoe-kiosk-gate` inactive, `tailscaled`
  inactive, `zoe-native-wake` disabled (a legacy `/opt/zoe-wake`). User units: `zoe-voice`,
  `zoe-panel-agent`, `zoe-airplay` (shairport-sync 5.x → Pulse, active since boot), `omi-sync`,
  `pulseaudio`.
- **Server-side reliability as the panel feels it**: zoe-data had **8-46 starts a day** (103 in
  the last week); Moonshine warmup is median 2.4 s and max 5.9 s per start. The panel's announce
  poll logged about 100-130 `502 Bad Gateway` a week (W1 102, W2 133, W3 113). Server: 168
  "db pool exhausted" lines, including 14 "panel binding lookup FAILED"; 75 zoe-auth session
  timeouts; 18 "No speech provider available" (Kokoro down → a spoken error or no audio). The Pi
  logged 8 "transcript but no audio" turns in W3, and 4 server "voice command stream failure".

---

## 9. Replay corpus coverage

- **Structure** [verified]: `~/.zoe-voice-samples` holds **1,485 top-level WAVs** named
  `HHMMSS_ms.wav`, with no subfolders by category and **no label or transcript manifest**.
  Quarantine: format 6, non-speech 51, replay-dups 63, TV false wakes 5. Capture months:
  Jun 298, Jul 771, Aug 193, Sep 49, Oct 174 (52% from July).
- **What the gate replays** [verified]: 20 **pinned** clips (`voice_regression_baseline.json`,
  `clip_profile`). Decoding `route_hash = sha1(outcome)[:8]` (`scripts/perf/measure_voice.py:209`,
  outcome format `replay_samples.py:509-555`) gives **14 → brain, 2 → `tier0:weather:weather`,
  1 → empty → brain, and 2 → two other deterministic tiers** (hashes `87c2ccfd`, `b5bc77d1`, not
  decodable from candidate labels). So the gate's deterministic coverage is about 4 of 20 clips.
- **Families vs the router** [inferred; keyword classification of the 60 unique corpus clips
  that appear with transcripts in `voice_stt.jsonl` replay rows, and of 173 live October panel
  turns, counts only]:

| family | corpus (60) | live Oct (173) |
|---|---|---|
| conversation / chat | 5 | 23 |
| smart home | 7 | 14 |
| calendar | 3 | 14 |
| weather | 2 | 11 |
| people / memory | 3 | 9 |
| greeting / ack | 2 | 6 |
| calculate | 1 | 5 |
| time / date | 2 | 3 |
| timer / alarm | 1 | 1 |
| **lists / shopping** | **0** | 2 |
| **music** | **0** | 1 |
| reminders, notes / journal, money, recipe | 0 | 0 |
| empty | 6 | 9 |
| unclassified | 27 | 74 |

  Zero corpus coverage among the transcribed clips: **lists** (the largest Skybridge domain by
  code, 33 `domain="lists"` sites), music, reminders, notes and journal, transactions, recipe,
  timers (1). Of the intent_router families (`intent_router.py`, about 60 intents:
  `list_add`/`list_show`/`list_remove`, `reminder_create`/`list`, `timer_create`, `music_*`,
  `journal_*`, `transaction_*`, `people_*`, `panel_setup`, `lets_talk`, ...), only weather and
  a fast-tier pair are pinned in the gate.
- **Is it the right instrument?** It is right for STT drift and gross brain breakage. It is
  **not** a measure of correctness for household asks, for three reasons. (1)
  "said-vs-did" compares a clip's route and heard-hash with **its own baseline**, not with an
  expected intent. A clip that was wrong in the baseline stays wrong and green. (2) The 20-clip
  pinned set is 70% brain, so wording noise (the 10-15 flaky-tolerance incident) and SPEED
  noise dominate: 27% of October runs failed. (3) It stops before TTS, so wake, endpoint, barge,
  identity and playback are invisible. **Needed** [inferred]: a content-free label manifest
  (clip id → intent family → expected outcome class, written once by the owner or by a
  Jetson-local pass), a stratified pinned set of about 60 clips covering every Skybridge domain,
  correctness reported separately from speed, and the wake FAR/FRR set from §3.

---

## 10. Ranked: the ten things that would most improve the panel's day-to-day feel

| # | change | evidence | size | owner at the panel? |
|---|---|---|---|---|
| 1 | **Keep the panel's brain KV resident.** A dedicated llama slot or slot-save for the panel session, or a harness/bar quiet window during waking hours, plus a panel-only `first_prompt_n` alarm. | 29/100 panel turns re-prefilled >500 tokens (18 warm); brain TTFT p90 5.9 s → TTFA p90 5.6 s | M | no |
| 2 | **Stop dev churn reaching the household.** Batch landings into windows; daemon-side "I'm updating, one moment" on a 502/connect error instead of silence; readiness-gated restarts. | 103 zoe-data starts in 7 days; about 15 announce 502s a day; 8 no-audio and 4 stream-failure turns in W3 | S-M | no |
| 3 | **Verify that voice ID now works for the owner, then enrol the household.** First 50 post-flip turns: the `identified=` rate, ResNet34 score margin, session open/close reasons. Then guided enrolment for each adult. | 158/159 turns `identified=None`; guest share 25/39 → 17/21 after #2026; 1 profile enrolled; flip at 10-18 23:13 with no turns since | S (instrument) + owner | **yes** (each member enrols at the panel) |
| 4 | **Break the enrolment-offer dead end.** Let an unknown voice's private ask lead to "who's this? → PIN → offer", or add a "teach Zoe my voice" tile per member. Fix the `child`/`family_member` → guest collapse decision. | 4 offer decisions in 2 weeks, 0 to an unenrolled member; `guest_policy.py:127-132` | S-M | owner decision (kids), members at the panel |
| 5 | **Shorten the endpoint tail.** Stage `ZOE_VAD_CLEAN_TAIL_MS=560`, then B1.1 speculative turn-start, and fix the Silero feed (256 dropped samples per chunk, no `reset_states`) first, because both depend on it. | Tail 720-880 ms on every turn; clean tail −160 ms median on half the turns (2% cut); B1.1 0.3-0.5 s, 14% cancel | M (replay-gated) | **yes** (ear check) |
| 6 | **Build the wake FAR/FRR instrument, then a speaker-verified wake.** Offline FRR on the corpus pre-roll; a near-miss ring buffer as the free negative set; then the OWW custom verifier or a ResNet34 wake gate. Commit and back up `hey_zoe.onnx` with provenance. | Threshold 0.45 vs near-miss max 0.44; 9% of wakes → empty chime; 13 wakes 01:00-05:00; model in 2 untracked copies | S (instrument), M (verifier) | no (optional labelling) |
| 7 | **Turn on duck → decide → resume.** A cough ducks instead of killing the reply; mark the barged text as unheard (A3). | 4 of 8 barges since 09-01 followed by no speech; the duck is built and never run | S + flag | **yes** (audible duck test) |
| 8 | **Panel hardware hygiene.** Move the Chromium disk cache to tmpfs or cap it; fit an Active Cooler; a persistent but rate-capped journal; put temperature and `get_throttled` in the turn log; drop transcripts from `voice.log`. | ≈2.7 GB/day of SD writes (Chromium); throttled 0xe0000, no fan; volatile journal; plaintext transcripts | S (+ about A$8 cooler) | **yes** (fit the cooler) |
| 9 | **Make the corpus a correctness instrument.** A content-free label manifest, a stratified ~60-clip pinned set covering lists, timers, reminders, music and smart home, and speed separated from said-vs-did. | 14/20 pinned clips → brain; 0 lists or music clips among those transcribed; 27% of October runs failed (43 speed, 35 said-vs-did) | M | optional (owner labels) |
| 10 | **Panel as code.** A version sha in `/health` checked by ground truth; the full `.env.voice` as a tracked template with the tuned values; a `requests.Session` for keep-alive; a per-turn id shared between Pi and server logs so live TTFA can be bound to a commit; `ZOE_ANNOUNCE_STRICT_PANEL` before a second panel. | 36/46 keys Pi-only; drift checked only at deploy; no turn id joins the two logs; claim-any command routing | S-M | no |

---

## Instruments used (for reproduction)

- Pi: `ssh -o BatchMode=yes zoe-pi` with read-only `cat`, `grep -a`, `python3 -c` over
  `~/.zoe-voice/voice.log`, `speaker_shadow_metrics.jsonl`, `speaker-shadow/{report,compare}.json`,
  `/proc/<pid>/{environ,io}`, `vcgencmd`, `systemctl [--user] status|cat`, `last -x reboot`,
  `/sys/block/mmcblk0/stat`.
- Jetson: `~/.zoe-logs/zoe-data.stderr.log` (JSON, 10-05..10-19), `~/.zoe-voice/voice_stt.jsonl`,
  `~/.cache/zoe/voice_regression_{last,baseline,trend}*`, `/proc/609135/environ`,
  `systemctl --user cat zoe-data`.
- Code: origin/main exported read-only. Route hashes decoded with `sha1(outcome)[:8]`.
- Not done (per the brief): no harness, no tests, no restart, no mic use, no DB reads.
