# Samantha Evolution — Execution Packets (cheap-model executable)

> Companion to [`samantha-evolution-plan.md`](samantha-evolution-plan.md) (the spec).
> **One packet = one increment = one session = one PR.** Every file/function/flag/line
> below was verified against the tree at `56b34c8c` (2026-07-07). Line numbers drift —
> treat them as "where to look first," confirm with a search for the quoted symbol
> before editing. Written so a small model can execute without judgment calls; when a
> packet's STOP condition fires, stop and report — do not improvise.

## P0 — Protocol every packet inherits (read once, obey always)

1. **Worktree:** `git worktree add --detach /home/zoe/.worktrees/<packet-id> origin/main`,
   branch `fix/<packet-id>` or `feature/<packet-id>`. Never work in `/home/zoe/assistant`.
   **Immediately symlink the live env in:**
   `ln -sf /home/zoe/assistant/services/zoe-data/.env <worktree>/services/zoe-data/.env`.
   Without it the voice replay gate (rule 3) **silently skips** — `measure_voice.py`
   requires the live-service `.env` and exits 0 with no JSON, which reads as a pass but
   validated nothing. (Learned 2026-07-08: the Agent-tool auto-worktrees omit this, so
   every builder-agent voice gate skipped until the `.env` was symlinked by hand. If you
   run in an auto-created worktree, symlink `.env` before the gate.)
2. **Flag default OFF.** The flag named in the packet gates ALL new behaviour. Flag-off
   must be byte-identical to today (write a test that proves it where the packet says so).
3. **Voice replay gate (any packet touching `voice_*`, `tts_*`, brain, or Kokoro config):**
   `flock /tmp/zoe-voice-harness.lock python scripts/maintenance/voice_regression_probe.py`
   — zero new hard failures, no per-stage speed regression. Full doc:
   [`docs/knowledge/voice-pipeline.md`](../knowledge/voice-pipeline.md).
4. **New test files must be added to `validate.yml`'s enumerated list AND carry the
   `ci_safe` marker** (else they silently never run —
   [`docs/knowledge/merge-and-deploy.md`](../knowledge/merge-and-deploy.md)).
5. **Merge:** push → `gh pr create` → `gh pr merge <branch> --squash --auto` → fix +
   reply + GraphQL-`resolveReviewThread` every Greptile thread → verify merged via REST
   (`gh api repos/.../pulls/N --jq .merged`), never GraphQL pr-view. If `behind`:
   `gh api -X PUT .../pulls/N/update-branch` and re-wait.
6. **After merge:** update `samantha-evolution-plan.md` §6 (tick the box, add the PR#)
   and §7 if the NEXT ACTION changed; update `docs/PLANS.md` if a workstream completed.
7. **Global STOP conditions (report, don't improvise):** a rock would need changing;
   free RAM < 1 GB during any measurement; the replay gate regresses; a migration would
   be needed that the packet didn't name; any prod `.env` change (operator-only).

---

## P-W0 — Capture positive-control (diagnostic; NO code changes)

**Goal:** prove a real authenticated voice turn lands in `chat_messages` with
`metadata.user_id`, and a consolidation sweep picks it up. This is the memory
buildplan's open question (its §6/§7) — everything else trusts this.

**Steps:**
1. Ask the operator (Jason) to speak one normal command to the panel while
   authenticated (e.g. "add milk to the shopping list"). Note the wall-clock time.
2. Read DB connection details from `services/zoe-data/.env` (`DATABASE_URL` /
   `POSTGRES_*`); connect with `docker exec -i zoe-database psql -U <user> -d <db>`.
3. `SELECT id, session_id, created_at, metadata FROM chat_messages ORDER BY created_at
   DESC LIMIT 5;` — PASS needs a row newer than step 1 **with** `metadata->>'user_id'`
   set to the real user (not NULL, not 'guest').
4. Wait > `ZOE_IDLE_CONSOLIDATION_IDLE_S` (default 180 s idle), then check
   `journalctl --user -u zoe-data.service | grep MEMORY_IDLE_CONSOLIDATE` for a line
   naming that session + user (log emitted at `memory_idle_consolidation.py`
   `MEMORY_IDLE_CONSOLIDATE session=… user=… stored=…`; sweep entrypoint
   `run_idle_consolidation_sweep()`).
5. Also run `python scripts/maintenance/check_emotional_thread.py` and record output.
6. Record the result in `zoe-memory-samantha-buildplan.md` §6 **and**
   `samantha-evolution-plan.md` §6 (W0 box), with the row id as evidence.

**FAIL branches (root-cause in this order, read-only):**
- No new row at all → capture regression: check `routers/chat.py` `_save_chat_message`
  is reached from the voice path (`routers/voice_tts.py` callers), check zoe-data logs
  for exceptions at the turn timestamp.
- Row exists, `metadata` NULL → 1b stamping regression: read `_save_chat_message` and
  the `effective_user` passed by the voice caller.
- Row + metadata OK, no sweep line → engine: confirm `ZOE_IDLE_CONSOLIDATION_ENABLED=1`
  in the *live process* env (`cat /proc/$(pgrep -f 'uvicorn main:app')/environ | tr '\0' '\n' | grep IDLE`).
**STOP:** any fix that needs a code change gets its own packet — this one only diagnoses.

---

## P-W1.3 — Sentence-streamed TTS in the LiveKit conversation lane

**Goal:** conversation mode gets first-audio at first-sentence, like `/ws/voice/` does.
**Flag:** `ZOE_LIVEKIT_STREAM_TTS` (new), default OFF. **Replay gate: mandatory.**

**Facts (verified):**
- The LiveKit lane synthesizes the WHOLE reply in one call at TWO sites in
  `services/zoe-data/routers/voice_livekit.py` (search `from routers.voice_tts import
  synthesize as _synth`, ~:533 and ~:592), then sends ONE data message containing
  `audio_base64` + `content_type` via `_send_data(local_participant, payload)` (:465).
- The per-sentence machinery already exists and is importable:
  `tts_waterfall._stream_kokoro_sentence_wavs` (re-exported by `routers/voice_tts.py`,
  import block ~:32) — working usage example at `routers/voice_tts.py:3457` (the WS
  lane); sentence splitting: `_split_sentences` / `_extract_complete_sentences`
  (`voice_tts.py` ~:523/:537).

**Change:**
1. In both `_synth` sites: when the flag is ON, split the reply with `_split_sentences`
   and send one data message **per sentence** — same payload shape as today plus
   `"seq": <n>` and `"final": <bool>` keys; flag OFF keeps the single-message path
   untouched (do not refactor it).
2. Clients: `services/zoe-ui/dist/touch/js/skybridge-voice.js`,
   `services/zoe-ui/dist/voice.html`, `services/zoe-ui/dist/touch/voice.html` — these
   `dist/` files ARE the canonical, hand-maintained sources (no build step regenerates
   them; `services/zoe-ui/AGENTS.md`: "hand-maintained HTML/CSS/JS, NOT build output")
   and #1051 edited `skybridge-voice.js` in place the same way. Read
   `services/zoe-ui/AGENTS.md` before editing; if a changed file is in the service
   worker's Workbox precache list, **bump `SW_VERSION` in `dist/sw.js`** (its #1
   "changes aren't showing up" gotcha). Find the handler for the existing audio message
   (search for `audio_base64`); make it enqueue and play sequentially; treat a message
   WITHOUT `seq` exactly as today (backward compatible — old server, new client and
   vice versa must both work).
3. Barge-in interplay: the #1051 pipeline-cancel must also stop the queued sentences —
   on `stop_playback`, clear the client queue; server-side, check the turn's cancel
   token between sentences and stop synthesizing.
**Tests:** new `services/zoe-data/tests/test_livekit_stream_tts.py` (mock `_send_data`,
assert: flag OFF → 1 message; flag ON → N messages with ordered `seq`, final flagged;
cancel between sentences stops the stream). Add to `validate.yml` + `ci_safe`.
**DoD:** lab session shows first-audio ≤ the `/ws/voice/` lane's on the same utterance;
replay gate green. **STOP:** if the client queue work exceeds ~150 changed lines,
report — the panel shell is fragile (foundation-before-features rule); don't redesign it.

---

## P-W1.4 — Live measurement session (M1/M3/M4 close-out)

**Goal:** record the numbers the ADR deferred. **Operator present** (human at mic).
**No code changes** — a results-recording packet.

1. Baseline snapshot: `free -m`, `smem -rs swap | head -20` (or `/proc/<pid>/status`
   VmRSS/VmSwap for zoe-data + llama-server), before starting.
2. Under `flock /tmp/zoe-voice-harness.lock`, run `python scripts/perf/measure_voice.py`
   and `python scripts/perf/measure_tts.py`; save outputs.
3. Live mic script (operator): 10× interrupt Zoe mid-sentence (M1 — bar: clean stop,
   time-to-stop < ~300 ms, measured from the barge log line to `stop_playback` send);
   15 utterances incl. mid-thought pauses (M2 — Smart Turn beats energy VAD on
   false-cutoffs + false-waits); 10× end-of-speech→first-audio (M3 — median ≤ pre-#1051
   baseline; compare against `docs/architecture/zoe-core-turn-latency.md` numbers).
4. RAM after: same snapshot; delta = M4.
5. Write results into `ADR-ambient-voice-framework.md` (a "## Live measurements" section),
   tick W1.4 in the plan §6. **STOP:** free RAM < 1 GB at any point → abort the session.

---

## P-W2.1 — Presence primitive (read-only helper)

**Goal:** `presence.py` answers "is someone plausibly at a panel right now?"
**Flag:** none needed (pure read helper + tests; nothing calls it yet).

**Facts (verified):** `ui_panel_sessions` (Postgres) has `panel_id, user_id, page,
ui_context, is_foreground, last_seen_at` — upsert on panel activity
(`routers/ui_actions.py`, e.g. the SELECT at ~:347); `panels.last_seen_at` tracks
registered kiosks (`routers/chat.py` ~:942).

**Change:** new `services/zoe-data/proactive/presence.py`:
```python
async def panel_presence(user_id: str, within_s: int = 900) -> str | None:
    """Return the panel_id of a panel this user is plausibly near, else None."""
```
Logic (single bounded query, no LLM): a `ui_panel_sessions` row for `user_id` with
`is_foreground = 1` and `last_seen_at` within `within_s` → that panel; else None.
`within_s` from env `ZOE_PRESENCE_WINDOW_S` default 900.
**Tests:** `tests/test_proactive_presence.py` (seeded fake db: fresh-foreground hit,
stale miss, other-user miss, background miss). Add to `validate.yml` + `ci_safe`.
**DoD:** tests green; helper unused in prod paths (this packet wires nothing).

---

## P-W2.2 — Spoken delivery adapter (morning brief only)

**Goal:** Zoe speaks the morning brief on the panel when someone's there.
**Flag:** `ZOE_PROACTIVE_SPOKEN` default OFF; allowlist env
`ZOE_PROACTIVE_SPOKEN_TRIGGERS` default `morning_checkin`. **Depends: P-W2.1 merged.**

**Facts (verified):** delivery funnel is `proactive/engine.py::fire_notification` —
quiet hours checked at ~:97 (`_is_in_quiet_hours`, env `ZOE_QUIET_START_HOUR`/`END`),
push sent at ~:153 via `_send_push` → `routers.push.send_push_to_user`. The panel
announce path is `mcp_server.py` `panel_announce` (~:2734): it calls
`_enqueue_panel_tool(db, user_id_fallback, panel_id, action_type="panel_announce",
payload={"message": …})`; the underlying queue is `ui_orchestrator.enqueue_ui_action`
(:71) and `panel_announce` is in `ALLOWED_ACTION_TYPES`.

**Change:** in `fire_notification`, after compose and the quiet-hours check, BEFORE
push: if flag ON **and** `trigger_type` in the allowlist **and**
`await panel_presence(user_id)` returns a panel → enqueue a `panel_announce` action
with the composed message (reuse `_enqueue_panel_tool`'s enqueue shape — import the
orchestrator, do NOT duplicate its validation), then **still send the push** (receipt +
fallback). Any exception in the spoken path must not block the push (wrap; log
`PROACTIVE_SPOKEN` line with trigger/panel/outcome for the watch week).
**Tests:** `tests/test_proactive_spoken.py`: flag OFF → no enqueue; ON+presence+allowed
trigger → exactly one enqueue AND push still sent; ON+no-presence → push only;
non-allowlisted trigger → push only; enqueue raising → push still sent.
**DoD:** lab: forced morning brief with a fresh panel session speaks on the kiosk.
**STOP:** do not touch `_send_push`, quiet-hours logic, or any trigger's compose.

---

## P-W3.2 — Stop embedding audit rows — ✅ DONE, do not execute

Shipped as **#1084** (`memory_service.py` `_AUDIT_NULL_EMBEDDING` — audit upserts pass a
constant unit-basis embedding; reads unchanged). Kept only so a session holding a stale
copy of this doc doesn't redo it.

---

## P-W4.1 — SER bake-off (lab only; NO prod code)

**Goal:** pick the W4 model with measured numbers. Isolated venv `~/.spikes/ser-bakeoff`
(`--system-site-packages`, same pattern as the Pipecat spike). **STOP before installing**
if `free -m` shows < 1.5 GB available.
1. Candidates (find current HF ids; do not guess): Wav2Small (arXiv 2408.13920 — check
   the paper/HF for released weights), emotion2vec (+ its FunASR ONNX runtime),
   `audeering` wav2vec2 MSP-Podcast dimensional (judge only). Record each model's
   **weight license** — a non-commercial license is a kill criterion for prod (judge-use ok).
2. For each: run over the 25 WAVs in `~/.zoe-voice-samples` (read-only), record
   per-utterance latency (p50/p95), steady RSS of the scoring process, and
   valence/arousal outputs; correlate candidates against the audeering judge.
3. Kill criteria: > 300 MB resident, > 150 ms p95 per utterance, or a prod-blocking
   license.
4. Output: OKF record `docs/knowledge/ser-bakeoff.md` (frontmatter `type: Reference`,
   registered in `docs/knowledge/index.md`) with the numbers table + a one-line pick;
   tick W4.1 in the plan §6. No zoe-data changes in this packet.

**Hook preview for the follow-on packet (do not build yet):** the LiveKit lane already
holds `wav_bytes = _pcm_frames_to_wav(frames)` right before STT
(`voice_livekit.py` ~:532), and the panel lane saves utterance WAVs under
`ZOE_VOICE_SAVE_AUDIO` (`voice_tts.py` ~:1963) — scoring runs as a **subprocess/sidecar**
(P0 rule: nothing new in-process in zoe-data), writes go through
`_run_voice_memory_passes` (`voice_tts.py` ~:2200) → the existing admission gate with
`memory_type="emotional_moment"` (existing writers: `memory_digest.py` ~:473,
`intent_router.py` ~:2524-2560).

---

## P-W5.1 — Speaker-ID shadow mode

**Goal:** identify-and-log on every panel voice turn; zero behaviour change.
**Flag:** `ZOE_SPEAKER_ID_SHADOW` (new), default OFF.

**Facts (verified, all in `services/zoe-data/routers/voice_tts.py`, router prefix
`/api/voice`):** enroll `POST /api/voice/enroll` (~:4483, computes
`_compute_resemblyzer_embedding` 256-dim, stores in `speaker_profiles` — schema in
alembic `0001` :599: `id, user_id, display_name, embedding_blob BYTEA, enrolled_at,
sample_count, panel_id`); identify endpoint ~:4562 (cosine vs enrolled profiles,
threshold env `ZOE_SPEAKER_ID_THRESHOLD` default 0.82 at ~:4633); profile list ~:4653
(feeds the settings page "Voice Identity" section); delete ~:4680.

**Change:** in the panel voice turn path (where the utterance WAV exists — near the
`ZOE_VOICE_SAVE_AUDIO` block ~:1963): when the shadow flag is ON and profiles exist,
compute the embedding **in a background task** (`_spawn_bg`, same pattern as
`_run_voice_memory_passes` at ~:2617) and log one line
`SPEAKER_ID_SHADOW panel=… best_user=… score=… resolved_user=…` (the resolved user
from today's fallback chain, for agreement measurement). No return-value change, no
identity decision. If resemblyzer is not installed → log once and disable for the
process lifetime.
**Tests:** `tests/test_speaker_id_shadow.py` (flag off → no call; on+no profiles →
no-op; on+profiles → one log with score; import-error path). `validate.yml` + `ci_safe`.
**Operator steps (record in the PR):** enroll Jason (+ consenting family) via the
settings page; watch a week of shadow lines; compute agreement/false-accept before any
packet lets identification act.
**STOP:** this packet must not change user resolution — if the wiring point would, report.

---

## Software & model manifest (verified on the host, 2026-07-07)

| Need | Status | Used by |
|---|---|---|
| Smart Turn v3.2 ONNX (`/home/zoe/models/smart-turn-v3.2-cpu.onnx`) | ✅ present | W1 (live) |
| Silero VAD (`/home/zoe/models/silero_vad.onnx`) | ✅ present | W1 (live) |
| resemblyzer (`requirements.txt:42`) | ✅ installed | W5 speaker-ID |
| ffmpeg (`~/.local/bin/ffmpeg`) | ✅ present | W8 OGG/Opus transcode |
| uv (`~/.local/bin/uv`) | ✅ present | H4 lock file; venv spikes |
| onnxruntime | ✅ in-process already (Moonshine/Kokoro) | W4 SER runtime |
| SER model weights (Wav2Small / emotion2vec) | ❌ obtain at P-W4.1 (license check is the gate) | W4 |
| Presence hardware (FP2 mmWave / ESPresense) | ❌ optional shelf, not required for W2 rung 1 | W2 |
| SIP trunk account + LiveKit SIP config | ❌ operator decision at W12.3 (own ADR) | W12 |
| Email account credentials (IMAP/Gmail) | ❌ operator provides at W9.1 (consent step) | W9 |
| Nothing else — every other workstream runs on the installed stack. | | |

## Packet template (for authoring gate-opened packets)

Measurement-gated workstreams (W4.2, W5.2, W6.x, W11, W12.3) get their packet written
AFTER their gate produces numbers — by design, since the measurements change the design.
Whoever opens the gate authors the packet using this exact shape (the standard every
packet above follows): **Goal** (one sentence) · **Flag** (name, default OFF) ·
**Facts (verified)** — file:line anchors re-checked against fresh main, never copied
from an older doc · **Change** — numbered steps naming symbols to reuse (search for
existing machinery FIRST; this plan's biggest recurring discovery is that the mechanism
already exists) · **Tests** — named file, cases, must-fail-on-old-code, `ci_safe` +
validate.yml, replay gate if voice · **Software** — from the manifest above, or the
obtain-step · **DoD** · **STOP conditions**. Then link it from the plan §6 line.

## Pre-packets (design-stable; verify anchors, then execute like any packet)

- **P-W2.5 follow-through:** table `zoe_commitments` (new migration: id, user_id, text,
  source_interaction, due_hint, status) written when the brain's reply matches promise
  patterns ("I'll remind you / I'll keep an eye on") — detector is deterministic regex +
  intent metadata, NOT model judgment; a proactive trigger (reuse `triggers/base.py`
  shape) checks status and composes a follow-up. Tests: promise detected + row written;
  trigger fires once; no promise → no row. Software: none.
- **P-W5.3 onboarding interview:** extend the enroll flow (voice_tts `/api/voice/enroll`
  UI) with a 5-question guided voice interview whose answers write through the EXISTING
  people/person_relationships writers (`person_extractor` fulfillment path) + a consent
  row. Deterministic script, Gemma phrases. Tests: interview answers land in people
  graph attributed to the new user. Software: none.
- **P-W8.1 Telegram voice notes:** in `labs/flue-zoe-telegram/src/app.ts` add
  `bot.on('message:voice')` → download OGG/Opus (Bot API `getFile`) → ffmpeg → WAV 16k →
  POST to zoe-data STT (`_transcribe_audio` route) → brain via the existing bot path →
  reply text AND `sendVoice` (Kokoro WAV → `ffmpeg -c:a libopus` OGG). Flag
  `ZOE_TG_VOICE_NOTES`, allow-list enforced (existing). Tests: mocked update → transcode
  called, sendVoice OGG. Software: ffmpeg ✅.
- **P-W9.1 email triage rung 1:** REQUIRES W15 fencing merged + operator mailbox
  consent. Poller sidecar (NOT in zoe-data — P0 rule) reads via IMAP (stdlib `imaplib`,
  read-only, UID watermark), deterministic triage (sender ∈ people graph, thread
  recency, flags), writes 2-3 fenced items to a new table `email_brief_items` (migration: id,
  user_id, message_uid, sender_person_id NULLABLE FK→people, subject, reason,
  fenced_summary TEXT, created_at, consumed BOOLEAN default false) that the
  morning-brief composer reads (and marks consumed).
  No LLM in the poller; Gemma phrases at brief time. Tests: seeded mailbox fixtures →
  correct picks; injection fixture stays fenced. Software: none new (imaplib stdlib).
- **P-W13.1 proactive show:** after P-W2.2, in the spoken-delivery adapter also enqueue
  `show_card` (already in `ui_orchestrator.ALLOWED_ACTION_TYPES`) with a compose tree
  from `ui_compose.compose_card()` when `ZOE_COMPOSE_UI` is ON; screenshot-verify via
  `panel_browser_screenshot`. Tests: presence+flags ON → announce + show_card both
  enqueued; compose failure → announce still delivered. Software: none.
- **P-W14.1 backup verify+extend:** shell + doc work: confirm
  `postgres-nightly-backup.sh` is scheduled (systemd timer/cron) and dumps are fresh;
  add Chroma `data/` tar + `.env`-flag snapshot (no secret VALUES — names only) to the
  script; rsync target for off-box copy (operator names the destination). Restore drill
  = restore into a throwaway postgres container + point a lab zoe-data at it + run the
  memory acceptance suite. Tests: the drill IS the test; record in `docs/knowledge/`.
  Software: none new.
- **P-W16.1 scoreboard:** new `scripts/maintenance/samantha_scoreboard.py` run by the
  existing weekly digest cycle (`routers/system.py` loops): runs the acceptance suite +
  `voice_regression_probe --summary` **taken with `flock -n` (non-blocking)** — if the
  harness lock is held, skip the probe column for that week and record "probe skipped
  (lock held)"; the scoreboard must never block or contend with a live harness/Kokoro
  run — + greps the week's log-line counters
  (`PROACTIVE_SPOKEN`, barge log, `SPEAKER_ID_SHADOW`), appends one row to
  `docs/knowledge/samantha-scoreboard.md`. Tests: composer unit-tested on fixture logs.
  Software: none.
- **P-W17.1 cross-surface thread:** maintain `user_active_thread` (new small table:
  user_id, updated_at, summary TEXT) — updated post-turn off the hot path (same
  `_spawn_bg` pattern as `_run_voice_memory_passes`), read into the for-prompt packet
  via `zoe_memory_compose.compose_packet` as a `[thread]` block behind
  `ZOE_CROSS_SURFACE_THREAD`. Summary = last 6 turns truncated, deterministic (no LLM).
  Tests: turn on panel → block visible to a chat-lane packet for same user; flag OFF
  byte-identical. Software: none.
- **P-W18.1 voice feedback intent:** add feedback patterns to the intent layer
  ("that's wrong", "that's not what I meant", "good girl/thanks that's right") →
  INSERT into the EXISTING `chat_feedback` table (`routers/chat.py` ~:4022 shape) keyed
  to the session's last interaction id; capture a following correction utterance
  verbatim into `corrected_response`. Tests: each phrase → row; unrelated phrase → none.
  Software: none.

## Deferred packets (author at gate-open, using the template above)

| Packet | Gate | Seed facts already verified |
|---|---|---|
| P-W3.3 reap generalization | operator picks the usage signals | pattern `voice_livekit.py` :90-93; targets homeassistant (370 MB swap) / music-assistant (325 MB) |
| P-W4.2 SER scoring hook | P-W4.1 pick + W3 headroom | hook points in P-W4.1 preview; subprocess/sidecar only |
| P-W5.2 identification acts | P-W5.1 shadow numbers | threshold env exists; PIN challenge stays for sensitive scopes |
| P-W6.x ambient attribution | W5 live + consent design + P-F4 scoping merged | insert `voice_tts.py` ~:4434; schema ready |
| P-W9.2/9.3 email memory + drafts | P-W9.1 watch week | admission gate + `[email]` citations; auto-send forbidden |
| P-W10.x Zoe's own thread | W7 loop closed once | rides the proposal contract; `zoe_self` scope |
| P-W11.x expressive delivery | P-W4.1 pick (or rung 1 without) | sidecar `voice`+`speed` params (`kokoro_sidecar.py` :280/:305) |
| P-W12.x remote link | W8.1 merged (rung 2 = W2×W8) | tunnel live; SIP = own ADR |
| P-W13.2/13.3 compose gates + authoring | ZOE_COMPOSE_UI watch week; W7 closed | catalog validator is the boundary |
| P-W15.1 trust boundary | before P-W9.1 | fencing + per-source tool tiers + injection fixtures |
