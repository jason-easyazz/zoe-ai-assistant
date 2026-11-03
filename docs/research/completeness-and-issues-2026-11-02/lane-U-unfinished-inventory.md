# Lane 4-U: the unfinished inventory (2026-11-02)

Read-only completeness audit. Source of truth: `origin/main` at `27d1b9d43` (2026-11-02 12:20 UTC). Live reads: the running zoe-data process
environment (pid started 2026-11-02 20:20 AWST, ZOE_* names and values, secrets excluded), the brain sidecar's environment (ZOE_* only),
`systemctl --user list-timers --all`, `docker ps`. Nothing was run, restarted or changed. Labels: **[verified]** = checked in code, git, `gh` or the
live process; **[inferred]** = reasoned from docs or partial evidence. The four earlier rounds (10-18, 10-19, 10-27, 10-29) are not repeated; where an
item overlaps, only the state change since is given.

Method notes and limits:
- Panel-Pi daemon flags (`ZOE_SPECULATIVE_TURN` on the daemon side, `BARGE_DUCK_ENABLED`, `ZOE_STT_STREAM_UPLOAD`, `SPEAKER_ID_ENABLED`, `PANEL_BT_*`) could not be read
  (no ssh in scope); they are marked "panel: unknown".
- "First commit" = `git log --reverse -S<FLAG> origin/main` (the first commit that adds the string anywhere).
- Three referenced records are NOT on main: `architecture-deep-dive-2-2026-10-19.md` and `stack-currency-and-field-2026-10-27.md` live only on PR #2123's branch
  (`claude/zoe-deep-dive-2-2026-10-19`, still OPEN), and `docs/architecture/self-evolution-nightly-loop.md` (the Zoe score / Zoe page / nightly loop design that the
  10-29 synthesis, PLANS "Desktop UI overhaul" and ROADMAP cite) also exists only on that branch [verified: `git ls-tree origin/main` has none of the three;
  `git branch -a --contains 24e8e6aee` = that branch + `pr/2123`]. Main therefore links to a design it does not contain.

---

## 1. PLANS.md: claimed status vs reality

`docs/PLANS.md` on main: 32 "Active" sections (lines 20-203) + 8 Backlog checkboxes (lines 208-215). Every PR number cited in the Active/Backlog block
(133 distinct) was checked with `gh`: all MERGED except **#2083 CLOSED** (acknowledged in the text) and "#6101" (a Multica ticket id, not a PR) [verified].
So the cited-PR column is uniformly "merged"; the gap is elsewhere: **what PLANS says is next, or dark, is often no longer true.**

| # | Section (PLANS.md line) | Last commit on its code | Flag: code default -> live | Claimed | Reality | Verdict |
|---|---|---|---|---|---|---|
| 1 | Live flag state (L20) | n/a | see section 4 | snapshot "2026-10-18" | `ZOE_CHANGE_NOTICES=on`, `ZOE_MUSIC_DISCOVERY=on`, `ZOE_BRIEF_ON_FIRST_TURN=1`, `ZOE_RELATIONSHIP_GRAPH_ENABLED=1`, `ZOE_TEMPORAL_RELATIONSHIPS_ENABLED=1`, `ZOE_PERSON_MERGE_ENABLED=1`, `ZOE_GRAPH_RECALL_BOOST=1`, `ZOE_OMI_SPEAKER_TAG=shadow`, `ZOE_OMI_LEARN_VOICES=1` are live and absent from the block; the block is 15 days old [verified live env] | stale-claim |
| 2 | Contacts data model (L28) | 2026-10-17 `7ba9dc65d` | `ZOE_CONTACTS_API` '' (off) -> unset | built flag-dark; next = connectors sync | unchanged; no sync engine, no UI on the card API; router 404 while off (`services/zoe-data/main.py:2393`) [verified] | built-but-dark (16 d) |
| 3 | Struggle detector (L31) | 2026-10-28 `2337ce5ed` | DETECT shadow->unset; PROPOSALS shadow->unset; `ZOE_ROUTER_STRUGGLE_FEED` off->unset | P1-P3 built | all three still shadow/off; `zoe-router-retrain.timer` NOT installed [verified timers]; the feed flag is invisible to the flag inventory (read via a module constant, `scripts/maintenance/router_struggle_retrain.py:67`) [verified] | built-but-dark |
| 4 | Panel remote access (L34) | doc only, 2026-10-18 | none | design, partly superseded | Wi-Fi onboarding / away mode / panel firewall parts: nothing built [verified no code] | design-only |
| 5 | Communication preferences (L37) | 2026-10-18 `4dc5a3003` | `ZOE_CHANGE_NOTICES` off -> **on** | "flag-dark; next: land #2108, a shadow week, then on" | **on live**; #2108 merged 10-18; the `deploy.yml` failure-marker step does not exist (`git grep` of `.github/workflows/deploy.yml` for notice/marker = 0 hits); P2 not started [verified] | live (stale-claim) |
| 6 | Calendar data model (L40) | 2026-10-15 `c54ec6226` | none | built; no sync engine | unchanged: schema + .ics, no producer of the new tables beyond the API [verified] | done (storage); consumer missing |
| 7 | Panel anywhere (L43) | 2026-10-19 `4111e4df7` | `ZOE_RELAY_INGRESS` '' -> unset | F0 lab + R1 box side built dark (#2095) | nothing since 10-19: C3, panel side, voice edges, owner runbook steps all open [verified git log] | started-and-stalled (14 d) |
| 8 | Household comms + modernization (L47) | various | `ZOE_INTERCOM` off -> unset | "research done, nothing built" | A3/A4 merged; D1 intercom built dark (#2143, 10-18); B6 "promote 30-day-on flags into code" not done; B8 weekly timer NOT installed [verified] | mixed: started; D = built-but-dark |
| 9 | Test traffic off the brain (L54) | 2026-10-26 `810f7e514` | `ZOE_BRAIN_SLOT_QUIET_S=180` live | built | live; owner purge SQL unknown [inferred] | live |
| 10 | Honest instruments (L57) | 2026-10-29 `f8de25f41` | `ZOE_ROUTER_SHADOW_TEXT` -> **1 live** | next: baselines + re-pin + remove SHADOW_TEXT | `ZOE_ROUTER_SHADOW_TEXT=1` still in the running process 14 days later [verified]; baseline/re-pin status not on main [inferred] | built; follow-through stalled |
| 11 | Credential vault (L61) | 2026-10-19 `de7d5183f` | `ZOE_CREDENTIAL_BROKER` (not in registry) | V0/V1 lab only; waits on P0 | P0 (`docs/architecture/p0-deroot.md`, "nothing applied") unchanged since 10-25 [verified doc status] | started-and-stalled (blocked on P0) |
| 12 | Memories space (L64) | 2026-10-26 `16c69a717` | `ZOE_MEMORIES_VOICE` false -> unset | P1 built; P3 dark, owner step | P3 still dark (7 d) [verified] | built-but-dark |
| 13 | Bluetooth settings (L67) | 2026-10-19 (`d4c4c2a51`) | `ZOE_BLUETOOTH_SETTINGS` false -> unset | built, not deployed | still not deployed; the firmware path is UNVERIFIED on hardware (`docs/knowledge/bluetooth-settings.md:78`) [verified] | built-but-dark (20 d) |
| 14 | Return-from-absence review (L70) | n/a | n/a | "Still open and gating (2026-09-28): #1723, #1745, #1727 ..." | #1723, #1745, #1727 all MERGED (09-27/09-28) [verified gh]; the line still says "gating" | stale-claim |
| 15 | Memory fidelity (L76) | 2026-10-17 (`memory_authority.py`) | n/a | P1.1/P1.2 built; P1.3-P3 📝 | audit doc status (10-29): "P1.3-P1.5, P2, P3 not started" [verified]; 28 days since #1868 | started-and-stalled |
| 16 | Panel identity (L82) | 2026-10-30 `aa57cf5fb` | FACE_ID true live; ENROLL_OFFER on; SPEAKER_MODEL resnet34 | W5 in flight | live; "~50 real turns" identity measurement not recorded [inferred]; identity score card design-only | live (with open measurement) |
| 17 | Ask-card conversation (L92) | 2026-07-17 `07adf3b61` | `ZOE_COMPOSE_UI=1` | "Next: **PR-1a**" | **PR-1a merged 2026-07-17 (#1364)** and the `?conv=1` entry (PR-1b) is in `touch/home.html:4974` [verified]; Phase 2 (estate loads `zoe-compose.js`) NOT done: no `touch/*.html` references it [verified] | stale-claim; Phase 2 design-only (108 d) |
| 18 | Two-stage router (L103) | 2026-10-19 | HEAD active; `ZOE_ROUTER_SELFTRAIN` false -> unset | selftrain blocked by REJECT 07-26 | last selftrain journal 2026-07-26 (`data/router_selftrain/runs`) [verified]; superseded in practice by the struggle feed (row 3) | live; selftrain stalled 99 d |
| 19 | Music discovery (L111) | 2026-07-13 `6adf0e0a5` | `ZOE_MUSIC_DISCOVERY` off -> **on** | "default OFF, operator flips" | on live [verified]; the "I found N albums" hook not built [verified no code] | live (stale-claim) |
| 20 | Samantha evolution (L117) | various | `ZOE_LIVEKIT_STREAM_TTS` false -> unset | W1.3 remaining | W1.3 built dark since 2026-07-07 (118 d) [verified flag first commit `c755c09a6`]; M3/M4 not recorded | started-and-stalled |
| 21 | Action agent (L123) | none | none | research only | nothing built; #2004 (dependency) merged 10-13 [verified] | design-only (21 d) |
| 22 | Zoe abilities (L128) | none | none | design only | nothing built [verified] | design-only (19 d) |
| 23 | Samantha-grade memory (L131) | live | IDLE_CONSOLIDATION etc. on | live | live [verified env] | live |
| 24 | Ambient voice (L136) | 2026-11-02 | `ZOE_SPECULATIVE_TURN` false -> unset (panel: unknown) | W1.3 + M3/M4; B1.1 flag-dark | B1.1 dark 36 d, "live Pi proof" never recorded; Omi phase 1 live | started-and-stalled |
| 25 | Agent + tooling readiness (L143) | 2026-06-26 | n/a | remaining: enforcement verification | no verification record since 06-26 [inferred] | started-and-stalled (129 d) |
| 26 | Flue convergence (L147) | 2026-08-10 | `ZOE_MULTICA=true`, `ZOE_MULTICA_AUTO_ADMIT=true` live | brain cutover done; Multica/Hermes retire by recreation | brain done; `zoe-multica-backend` + `zoe-multica-web` containers up 2 weeks, `zoe-multica-heartbeat.timer` live [verified docker/timers]; recreation of the board not done | live (brain) / stalled (retirement) |
| 27 | TRIAGE-JUDGE TT1 (L164) | 2026-08-03 `d139402e8` | `ZOE_MULTICA_TRIAGE_JUDGE` false -> unset | next: TT2 + wiring | nothing since 08-03 (91 d) [verified] | built-but-dark; kill candidate |
| 28 | Touch/Chat UI (L170) | 2026-07-15 (#1345) | n/a | consolidation remaining (4 card producers) | no consolidation commit since [inferred from git log --grep] | started-and-stalled |
| 29 | Household lists (L175) | 2026-10-15 `d41e1d89e` | `ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE` registered -> unset | "PR in review" | merged 2026-10-15 [verified]; operator merge-script run unknown [inferred] | done (stale-claim) |
| 30 | Desktop UI overhaul (L180) | 2026-10-26 `d344c3096` | n/a | in progress | Wave 4b partial; the "Zoe page" design is not on main (see method notes) | started |
| 31 | Tech-debt remediation (L192) | 2026-09-25 (plan) | relationship flags | "Wave 3 merged but DARK (all behind flags, default OFF)" | the three Wave-3 flags are **on live** [verified]; Wave 4 splits not executed: `routers/voice_tts.py` is **7,532 lines**, `routers/chat.py` 3,368, `intent_router.py` 6,108 [verified wc] | stale-claim (W3) / design-only (W4) |
| 32 | Brain tool-selection reliability (L203) | doc 2026-08-10 | n/a | blocked on a reproducer | unchanged 97 d; the doc itself says dropping it is legitimate | kill candidate |

Backlog (L208-215): items 1, 2, 3, 5, 7 checked and true [verified PRs merged]. **Item 4** (enforce tool use across agents) open since 2026-06-23, no
verification record; **item 6** (Omnigent DOX reference "pending the container fix") is stale: the fix was applied 2026-06-26 per item 1 of the same list;
**item 8** (richer ideas-board surface) "not started" for 132 days [verified no code].

**Counts (32 Active sections):** live 6 (rows 9, 16, 19, 23, 5, 18-router) · done 3 (6 storage, 29, 31-W1..3) · built-but-dark 6 (2, 3, 12, 13, 27, 8-D) ·
started-and-stalled 9 (7, 10, 11, 15, 20, 24, 25, 26-retirement, 28) · design-only 5 (4, 17-Phase 2, 21, 22, 31-W4) · stale claim in the text 9
(1, 5, 14, 17, 19, 29, 31, backlog 6, and the ROADMAP below). Some rows carry two verdicts; the primary one is counted.
Backlog: 5 done, 3 open (2 stalled, 1 stale).

**`docs/ROADMAP.md` itself** says "updated: 2026-10-19" and prescribes a Monday cadence; it has not changed in 14 days (two Mondays) [verified git log], and its
Now/Next still list A3/A4 as next although they merged on 10-19.

---

## 2. IDEAS.md: every pinned or exploring idea

`docs/IDEAS.md` on main (two more ideas, the TV launcher and HA-Family-Hub pieces, are only on this docs branch, pinned 10-29/11-01). Age = first commit adding the
heading (`git log -S` on the file). Movement = code, flag, PR or record since.

| Idea (IDEAS.md line) | Status | Pinned | Age (d) | Movement | Still fits VISION? |
|---|---|---|---|---|---|
| Flue as harness substrate (L21) | 🔨 | 06-23 | 132 | brain on Flue 2.x live; harness half not | yes; status should split (brain done) |
| Self-evolution harness (L25) | 🔬 | 06-23 | 132 | struggle loop P1-P3 dark; nightly-loop design unmerged (#2123 branch) | yes |
| "(Jason: annotate)" video (L29) | 💡 | 06-23 | 132 | **none**; the one line was never added | no content to judge: kill |
| Household comms (L36) | 🔬 | 10-18 | 15 | intercom D1 built dark (#2143) | yes |
| Action agent / Fo (L40) | 🔬 | 10-12 | 21 | research only | yes, after reliability |
| Abilities / connect my email (L44) | 🔬 | 10-16 | 17 | design only | yes |
| Password vault (L47) | 🔬 | 10-18 | 15 | V0/V1 lab, blocked on P0 | yes |
| Identity score card (L50) | 🔬 | 10-13 | 20 | design only (`identity-presence-engine.md`, design 10-30) | yes |
| Omi press-to-record firmware (L53) | 🔬 | 10-13 | 20 | research + phase-2 lab firmware (`d4c4c2a51`) | yes |
| Voice for Bluetooth devices (L56) | 💡 | 10-13 | 20 | none: `intent_router.py` has 0 "bluetooth" hits [verified] | yes, after BT deploy |
| Pinterest-style ideas surface (L59) | 💡 | 06-23 | 132 | **none** (also PLANS backlog 8) [verified] | weak: dev-tooling, not household; kill |
| Relationship graph into recall (L63) | ⚠️ "live-state UNVERIFIED" | 07-09 | 116 | **now verified live**: `ZOE_RELATIONSHIP_GRAPH_ENABLED=1` and `ZOE_GRAPH_RECALL_BOOST=1` in the running process [verified] | status stale: mark live; the "acceptance gap" (no functional probe recorded) remains |
| Orb reacts to music (L73) | 💡 | 07-16 | 109 | **none** [verified no code] | marginal: kill or fold into Ask-card Phase 2 |
| Thin Tauri shell (L76) | 🗄️ | 07-19 | 106 | none (parked by design) | parked correctly |
| Face enrolment & deletion UX (L83) | 💡 | 07-28 | 97 | operator script only (`scripts/setup/zoe_enroll_flow.py`); no Settings card in `dist/` [verified: no `face/enroll` in dist] | yes: face ID is live, so this is a gap in a live feature |
| oh-my-pi mined pieces (L93) | 💡 | 08-05 | 89 | **none**: no TTSR seam [verified]; the "voice replay invariant" not written into voice-pipeline.md [inferred] | piece (2) yes; rest: kill |
| Probe-only env for replays (L96) | 💡 | 09-28 | 35 | none [verified] | yes (small) |
| build_kokoro_venv follow-ups (L99) | 💡 | 09-28 | 35 | partly: `UNIT_LD_LIBRARY_PATH` now in `scripts/setup/build_kokoro_venv.sh` [verified]; whitespace refusal unknown | close or finish (XS) |
| YouTube Music cookie refresh (L107) | 💡 | 09-28 | 35 | job built 07-09 behind `ZOE_YTMUSIC_REFRESH_ENABLED` (`services/zoe-data/main.py:1387`), still unset live [verified] | yes: it is the "music silently down" class the synthesis ranks #4 |
| Device-code OAuth for YT Music (L110) | 💡 | 09-28 | 35 | blocked upstream | parked |
| Streaming STT during recording (L116) | 💡 | 09-28 | 35 | **built**: `ZOE_STT_STREAM_UNDER_SPEECH=1` live on zoe-data; daemon `ZOE_STT_STREAM_UPLOAD` built 10-11 (panel: unknown) [verified] | status stale: built |
| Field-scan pieces P1-P10 (L119) | 💡 | 10-03 | 30 | P1 pull-not-push built and default ON (`services/zoe-data/proactive/pull.py:1`); P2 duck-decide-resume built dark on the daemon (#1830, `BARGE_DUCK_ENABLED` default false); P5 persona drift built dark (`persona_drift.py`, #1846); P9 failure-fed proposals = struggle P2 built; P3 speaker gate = ResNet34 live; P4, P7, P8, P10, location-UI: none [verified] | yes; five of ten have moved and the board does not say so |
| Ambient-memory field scan (L132) | 💡 | 10-17 | 16 | pendant telemetry built (gap audit P0-2, `routers/omi_recordings.py`); "not a person" voice tag partly (migration 0055); the rest none | yes |

**No movement in 60+ days (5):** "(Jason: annotate)" video (132 d), Pinterest ideas surface (132 d), orb reacts to music (109 d), oh-my-pi residue (89 d),
and the Tauri shell (106 d, parked on purpose). Face-enrolment UX (97 d) has operator-side movement only.
**Status labels now wrong (4):** relationship graph (live), streaming STT (built), field-scan P1/P2/P5/P9 (built), Flue substrate (brain half done).

---

## 3. Design-only / draft / parked documents, and the phased plans

**Partial section.** The per-phase sweep of the 22 phased plans and the half-built-surfaces sweep (section 5) were delegated to two sub-lanes that had not
returned when this report was due. What follows is what this lane verified itself.

Documents whose status line says design-only / nothing built / draft / not started, by last change on main [verified `git log -1`, status line read]:

| Doc | Date | Status line says | Changed since? |
|---|---|---|---|
| architecture/stage-a-channel-agnostic-core.md | 06-21 | DRAFT, no code | superseded by fast_tiers (#746) [inferred]; retire |
| architecture/zoe-core-turn-latency.md | 06-25 | draft | core lane dormant; retire |
| architecture/chat-split-and-typed-config-plan.md | 07-07 | planned | not executed: `routers/chat.py` 3,368 lines |
| architecture/voice-tts-split-plan.md | 07-26 | planned | not executed: `routers/voice_tts.py` 7,532 lines |
| architecture/brain-tool-selection-reliability.md | 08-10 | fix NOT attempted, blocked on a reproducer | no change, 84 d |
| architecture/tech-debt-remediation-plan.md | 09-25 | planned | W1-W3 merged (W3 flags now live); W4 not started |
| architecture/b3-1-bitemporal-supersession.md | 09-26 | lab spike, flag-dark, nothing on main path | `ZOE_BITEMPORAL_SUPERSEDE` lab-only, 37 d |
| architecture/b1-speculative-turn-start.md | 09-27 | flag-dark | no live proof, 36 d |
| architecture/brain-speed-tuning.md | 10-04 | research-only | none |
| architecture/samantha-brain-blueprint-2026-10-09.md | 10-08 | DRAFT for the owner, design only | its items partly built as shadow guards (section 4a) |
| architecture/tts-first-chunk-latency.md | 10-11 | draft | none recorded |
| architecture/zoe-abilities.md | 10-16 | design only; nothing built | unchanged |
| architecture/panel-remote-access.md | 10-18 | design only; partly superseded | unchanged |
| architecture/ask-card-conversation-plan.md | 10-19 | planned | **wrong**: PR-1a/1b merged 07-17 (#1364); Phase 2 not started |
| architecture/gbrain-memory-graph-recall.md | 10-19 | design, not started | the boost it designs is live (`ZOE_GRAPH_RECALL_BOOST=1`) |
| architecture/zoe-iphone-app.md | 10-19 | box side M0 dark; no Swift code | unchanged |
| architecture/p0-deroot.md | 10-25 | plan + lab proof, nothing applied | unchanged; gates the vault |
| architecture/memories-space.md | 10-26 | design; P4/P4b backend dark | P3 voice dark |
| architecture/identity-presence-engine.md | 10-30 | design only | unchanged |
| research/memory-fidelity-audit-2026-10-05.md | 10-29 | P1.3-P1.5, P2, P3 not started | unchanged |
| research/*-2026-10-04 (11 records) and the 10-05..10-09 DRAFT-for-owner records (about 15) | 10-04..10-09 | research-only / DRAFT | several fed built items; none marked absorbed |
| research/EXECUTIVE_SUMMARY.md, README.md | 10-19 | "Ready for Implementation" | stale 2025 template status |
| self-evolution-nightly-loop.md (Zoe score, Zoe page, nightly loop) | n/a | **not on main** (only on #2123's branch) | cited by main's PLANS/ROADMAP |

Phased plans, lane-verified highlights: panel-anywhere F0 lab + R1 box dark, stalled since 10-19; credential-vault V0/V1 lab, V2+ blocked on P0 (nothing applied);
communication-preferences P1 **live**, failure-marker + P2 not started; contacts/calendar storage built, sync engine (abilities A2) not started; zoe-abilities C0-C2/A0-A6
none started; ask-card Phase 1 done, Phase 2 not started; voice-tts split 0 of 6 PRs; chat split not started; tech-debt W4 not started; struggle P1-P3 built dark,
retrain timer not installed; omi phase 1 live (`ZOE_OMI_SYNC_ENABLED=1`), day review shadow, raise dark; alarms P1 dark (today), P2/P3 in PRs #2241/#2243;
samantha-evolution W1.3 dark 118 d; beat-the-bar B1.1 dark 36 d. Uninstalled timer templates on main: `zoe-router-retrain` (10-19), `zoe-samantha-weekly` (10-29),
`zoe-offbox-backup` (10-18, off-box backups still not running 15 days after merge), `zoe-agents-sampler` (10-04), `zoe-omnigent-runner-reaper` (08-09),
`zoe-self-review` (04-12); `~/.config/systemd/user/zoe-dreaming.timer` is installed but not on main and not listed active (retired unit left behind) [verified].
Also: container `zoe-harness-scratch-samantha-bar-3898330` has been up 8 days (a scratch harness that did not tear down) [verified docker ps].

## 5. Half-built surfaces

Not completed by this lane (sub-lane outstanding). Verified in passing: ROADMAP Now-1 still lists "delete the two fake-success endpoints" (`routers/stubs.py`);
contacts router mounts only when on (`main.py:2393`); the estate does not load `zoe-compose.js`; no voice intent for Bluetooth; no Settings face-enrolment card in `dist/`.
Recommend a follow-up lane for routes-without-UI, zero-writer tables, brain-tool-to-handler mapping and unregistered jobs.

---

## 4. Flag-dark features

Source: `docs/knowledge/flag-inventory.json` on main (840 registered flags: 802 prod, 38 lab) filtered to feature gates whose code default is
off/false/shadow/empty-as-off (216 candidates), then hand-checked against their readers (several `''`-default flags are really default-ON kill switches:
`ZOE_FORGET_REDACT`, `ZOE_FORGET_NEAR`, `ZOE_FORGET_ALIAS_SWEEP`, `ZOE_ROLE_GUESS_GUARD`, `ZOE_PULL_NOT_PUSH`, `ZOE_LAYOUT_MEMORY`, `ZOE_FLUE_EARLY_TEXT`,
`ZOE_FLUE_ABORT_GUARD`, `ZOE_BRAIN_STREAM`; those are excluded) [verified readers]. Live = zoe-data process env; brain-side flags read from the
`flue-zoe-brain-2x` process env.

Two inventory blind spots found on the way [verified]:
- `ZOE_ROUTER_STRUGGLE_FEED` (phase 3 of the struggle loop) is not in the registry or the inventory: `scripts/maintenance/router_struggle_retrain.py:67` reads it
  through a `FLAG = "..."` constant, which the scanner does not see.
- `ZOE_CREDENTIAL_BROKER` (vault V3) and `ZOE_INTERCOM_PTT` are named in docs but read by no code on main; `ZOE_IDENTITY_ENGINE` appears in one doc commit only.

### 4a. Built, gated, and dark today (code default off/shadow, unset in the live process)

| Flag | Default | First commit (dark since) | Days | Flip criterion written? | Measured? |
|---|---|---|---|---|---|
| `ZOE_OTEL_ENABLED` | off | 2026-05-04 `95db32ac0` | 182 | no | no |
| `ZOE_MEMORY_LINT_IN_DREAMING` | off | 2026-06-18 `5fc845c6f` | 137 | no | no |
| `ZOE_LIVEKIT_FAST_TIERS` | false | 2026-06-21 `8e7984492` | 134 | no | no |
| `ZOE_ROUTER_MODE` (tier-1 semantic router) | shadow | 2026-06-21 `9af73cb13` | 134 | no (superseded by `ZOE_ROUTER_HEAD=active`) | n/a: superseded |
| `ZOE_CHAT_INJECT_DB_MEMORY` | off | 2026-06-22 `5e436261d` | 133 | no | no (likely superseded by the seam recall) [inferred] |
| `ZOE_PERSON_LLM_PREFILTER` | off | 2026-07-06 `8783b3bbb` | 119 | no | no |
| `ZOE_LIVEKIT_STREAM_TTS` (W1.3) | false | 2026-07-07 `c755c09a6` | 118 | in plan text only ("W1.3 + M3/M4") | no |
| `ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED` | off | 2026-07-08 `3e24bcccc` | 117 | runbook mention only | no |
| `ZOE_YTMUSIC_REFRESH_ENABLED` | false | 2026-07-09 `a0580de42` | 116 | yes (IDEAS L107: enable + verify it fires) | no |
| `ZOE_ROUTER_SELFTRAIN` | false | 2026-07-14 `2ce5f7293` | 111 | yes (ratchet PASS) | **yes once: REJECT 2026-07-26**; never re-run |
| `ZOE_ANNOUNCE_STRICT_PANEL` | false | 2026-07-19 `db69e7f99` | 106 | no | no |
| `ZOE_MULTICA_CROSS_REVIEW` | false | 2026-08-02 `0f7d310f2` | 92 | no | no |
| `ZOE_MULTICA_TRIAGE_JUDGE` | false | 2026-08-03 `d139402e8` | 91 | "lab-proven" (no bar) | no |
| `ZOE_BRAIN_FAILOVER` | false | 2026-08-09 `db47cb2d3` | 85 | partial (state-of-zoe review) | no |
| `ZOE_SPECULATIVE_TURN` (B1.1) | false (panel: unknown) | 2026-09-27 `ebe3d4082` | 36 | **yes** (`b1-speculative-turn-start.md:279` "Flip criteria") | offline sweep only; live Pi proof never recorded |
| `ZOE_WEB_SEARCH_TOOL` (brain) | false; brain env unset | 2026-09-27 `bd8e49508` | 36 | no | no |
| `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL` | off | 2026-09-28 `a0102950d` | 35 | no | no |
| `ZOE_PERSONA_LAYER` / `ZOE_PERSONA_DRIFT` | false | 2026-10-04 `d531aeaf7` | 29 | "provisional until a baseline week" (`persona-layer.md:109`) | 10-06 A/B measured the layer INERT (`persona-layer.md:32`) |
| `ZOE_TRIVIA_HEDGE` | off | 2026-10-04 `13a3a9b64` | 29 | "needs filler path + replay before on" (`conversation-quality-classes.md:112`) | no |
| `ZOE_CONTACT_OFFER_BATCH` | off | 2026-10-04 `9b4f89e71` | 29 | no | no |
| `ZOE_AFFECT_CONSENT_GATE` | false | 2026-10-05 `54166d589` | 28 | n/a: owner decided affect ON with no consent row (10-05) | n/a: dead by decision |
| `ZOE_NIGHT_MIND` (readers, `serve`) | off | 2026-10-08 `d9ef95688` | 25 | yes (replay gate, `night-mind.md:131`) | writer measured; readers never |
| `ZOE_RESTRAINT`, `ZOE_STRUCTURAL_CLAIMS/_READER/_VERIFIER`, `ZOE_RECALL_GATE`, `ZOE_DELIVERY_LEDGER`, `ZOE_COMMITMENTS`, `ZOE_DISTRESS_GENTLE` | shadow | 2026-10-09..11 | 22-24 | restraint and structural: bench families named (P4/P3.d; S-cells); recall gate, delivery ledger, commitments, distress: no written flip bar | shadow log lines exist by design; no shadow-week read-out recorded in docs [inferred from grep] |
| `ZOE_RISK_CUES` | off | 2026-10-12 `88e7837c5` | 21 | **yes** ("flip only on a printed KEEP", `risk-cue-tier.md:149`) | A/B 21/60 vs 21-22/60 measured, no KEEP |
| `ZOE_ENROLL_OFFER_KIDS` | false | 2026-10-12 `e9ead90f3` | 21 | owner decision | n/a |
| `ZOE_BLUETOOTH_SETTINGS` | false | 2026-10-13 `0bb27aa2e` | 20 | yes (deploy agent + one supervised firmware run) | no (path UNVERIFIED on hardware) |
| `ZOE_MISHEAR_CLARIFY`, `ZOE_FOLLOWUP_ADDRESSED` | off | 2026-10-13/14 | 19-20 | no | no |
| `ZOE_STRUGGLE_DETECT` / `_PROPOSALS` / `ROUTER_STRUGGLE_FEED` | shadow / shadow / off | 2026-10-14..18 | 15-19 | weak ("re-run after a week of shadow data", `struggle-detector.md:117`) | one shadow run: 0 labelled failures |
| `ZOE_STT_SNAP` | shadow | 2026-10-15 `584e93d2e` | 18 | no | no |
| `ZOE_CONTACTS_API` | off | 2026-10-15 `c128edc1c` | 18 | no (no consumer to flip for) | no |
| `ZOE_RELAY_INGRESS` | off | 2026-10-17 `f4303b268` | 16 | yes (runbook) | T5/T10/T11 lab cells measured; C3 not |
| `ZOE_INTERCOM` | off | 2026-10-18 `37d4fc95b` | 15 | yes (`lan-intercom.md:64`: shadow then on) | no |
| `ZOE_PANEL_REVIEW_CARDS`, `ZOE_DAY_REVIEW_RAISE` | false | 2026-10-18/19 | 14-15 | prerequisites listed (`day-review-raise.md:85-88`) | cost measured; behaviour not |
| `ZOE_LOCAL_BACKUP_ENCRYPT`, `ZOE_PHONE_APP` | off | 2026-10-19 | 14 | phone app: M0 only, no client exists | no |
| `ZOE_MEMORIES_VOICE` | false | 2026-10-26 `16c69a717` | 7 | "measure on the panel before turning it on" (`memories-voice.md:105`) | no |
| `ZOE_STRUGGLE_OUTCOMES` | false | 2026-10-27 | 6 | no | no |
| `ZOE_ALARMS` | off | 2026-11-02 | 0 | P2/P3 open (#2241/#2243) | n/a |

Daemon-side, panel unknown: `BARGE_DUCK_ENABLED` (duck-decide-resume, #1830, 2026-10-04), `ZOE_STT_STREAM_UPLOAD` (2026-10-11), `SPEAKER_ID_ENABLED`.

### 4b. "Built, dark for more than 30 days, no measurement" (the list asked for)

14 flags [verified dates and live env; "no measurement" = no measured result in any doc on main, inferred from grep]:

1. `ZOE_OTEL_ENABLED` (182 d) 2. `ZOE_MEMORY_LINT_IN_DREAMING` (137 d) 3. `ZOE_LIVEKIT_FAST_TIERS` (134 d) 4. `ZOE_CHAT_INJECT_DB_MEMORY` (133 d)
5. `ZOE_PERSON_LLM_PREFILTER` (119 d) 6. `ZOE_LIVEKIT_STREAM_TTS` (118 d) 7. `ZOE_PERSON_BIRTHDAY_CAPTURE_ENABLED` (117 d) 8. `ZOE_YTMUSIC_REFRESH_ENABLED` (116 d)
9. `ZOE_ANNOUNCE_STRICT_PANEL` (106 d) 10. `ZOE_MULTICA_CROSS_REVIEW` (92 d) 11. `ZOE_MULTICA_TRIAGE_JUDGE` (91 d) 12. `ZOE_BRAIN_FAILOVER` (85 d)
13. `ZOE_WEB_SEARCH_TOOL` (36 d) 14. `ZOE_PROACTIVE_BRIEF_ON_ARRIVAL` (35 d).
Near the line: `ZOE_SPECULATIVE_TURN` (36 d, criterion written, offline-only measurement), `ZOE_ROUTER_SELFTRAIN` (111 d, measured once, REJECT, never re-run),
`ZOE_ROUTER_MODE` (superseded), and the 8 shadow-default memory/proactive guards that cross 30 days between 2026-11-08 and 11-10.

### 4c. The opposite drift: on live, still documented as dark or pending
`ZOE_CHANGE_NOTICES=on`, `ZOE_MUSIC_DISCOVERY=on`, the three Wave-3 relationship flags plus `ZOE_GRAPH_RECALL_BOOST=1`, `ZOE_ROUTER_SHADOW_TEXT=1` (documented as
"to be removed"), `ZOE_MULTICA=true` + `ZOE_MULTICA_AUTO_ADMIT=true` (Multica "deliberately paused" per PLANS L147-155) [verified live env]. The 10-29 synthesis
counted this class; these are the instances still standing on 11-02.

---

## 6. Open PRs older than 7 days, and drafts

14 open PRs (8 drafts) [verified `gh pr list` + GraphQL thread counts].

| # | Title (short) | Age d | Draft | Merge state | Red checks | Unresolved threads | Waiting on | Action |
|---|---|---|---|---|---|---|---|---|
| 2116 | flag registry + CI rules (B6), Samantha evidence (B8) | 15 | no | DIRTY | size-and-overlap (+18k/-3.5k, 35 files) | 0 | nothing: superseded by merged #2221/#2222 (owner comment 10-29) | **close** |
| 2123 | architecture deep dive round 2 (+ stack currency, nightly-loop design on the same branch) | 15 | yes | CONFLICTING | none | 0 | conflicts in hot docs; holds `self-evolution-nightly-loop.md` that main already cites | rebase and land the design doc, or split it out |
| 2134 | Multica board behind Zoe sign-in | 15 | yes | CONFLICTING | GitGuardian | 0 | secret scan on branch history + owner decision (no OIDC on any Multica release) | clean re-branch or close |
| 2158 | caregiver curation (memories) | 15 | no | DIRTY | replay-evidence, voice-gate | 3 of 7 | rebase, threads, voice replay on head | finish (land before #2159) |
| 2159 | caregiver curation by voice | 15 | no | CLEAN (stacked) | none | 0 | #2158 | hold, retarget |
| 2191 | stack currency and the field (research) | 6 | yes | CONFLICTING | validate | 0 | conflicts + validate | rebase/fix or close |
| 2217 | synthesis deep dive 2026-10-29 | 5 | yes | MERGEABLE | validate | 0 | validate red; same content as this worktree's branch commit `2976a422a` | fix validate and land, or close [inferred duplicate] |
| 2225 | why recall is not reaching the brain | 4 | yes | MERGEABLE | none | 0 | nothing | mark ready, land |
| 2229 | llama.cpp b11982 A/B record | 4 | yes | MERGEABLE | none | 0 | nothing; duplicated in #2231 | fold into #2231 |
| 2231 | llama.cpp b11194 -> b11982 | 3 | no | BEHIND | replay-evidence, verdict, voice-gate | 1 | owner live switch, then replay on head | update, owner step, land |
| 2241/2242/2243/2248 | alarms P2, follow-up speaker verdict, alarms P3, person-centred memory design | 0 | mixed | in CI | validate (2248) | 0 | checks running; #2243 stacked on #2241 | normal flow |

Three docs PRs (#2191, #2217, #2248) are all red on `validate` with no known cause; one shared fix (likely the research index/registry check) may clear all three [inferred].

---

## 7. The ledger `docs/knowledge/open-problems.md`

718 lines, **500 dated open entries** (the ledger deletes fixed lines, so every dated line is open by its own rule) [verified].

- 181 entries are older than 21 days (dated before 2026-10-12).
- 56 distinct PRs are cited: 45 merged, 9 closed unmerged (#1930, #1932, #1935-#1938, #2066, #2074, #2083), 2 open (#2116, #2134).
- **147 of the 181 old entries cite no PR (133) or only closed-unmerged PRs (14, L157-170)**. About 3 of them say inline that they were fixed
  (L187 "fixed here (migration 0043)", L213 "CLOSED by migration 0044", L120 a 10-19 "MEASURED" note) and should have been deleted, so the real stale set is about 144.

| Theme | Old entries with no merged PR |
|---|---|
| memory (night mind, structural floors, restraint, person half) | 62 |
| infra / ops (Docker, night window, Chroma, compose, cloudflared) | 25 |
| CI / instruments | 23 |
| security / privacy | 18 |
| voice | 15 |
| panel / UI | 2 |
| music | 1 |
| identity | 1 |
| **total** | **147** |

Oldest concrete ones (10-04/10-05, no PR): Docker log/memory caps (L46); panel Pi at 76-82 C with no fan (L49); YouTube Music login expired (L50);
barge-in phase 1 never run in the lab (L51); zoe-auth prints a setup token on every start (L53); tunnel hostname answers 530 instead of an Access 302 (L56);
a bot token reached the journal once and was never rotated (L57); two admin 10-year HA tokens behind one client (L59); Music Assistant 2.8.7 with three
advisories (L61); three services answer on the LAN address (L62); the self-hosted runner is not ephemeral and has no cap (L63); `.env` mode 664 (L64);
the zoe-data unit still loading the retired Hermes env file, and compose image pins in comments only (L112-115).

**Owner/operator-gated entries still open: 163** (by the entry's owner field), 47 of them older than 21 days. Of the 163: 31 need a decision before an agent
can act; about 32 are box work (Docker, firewall, tokens, HA UI, hardware); about 34 are flag flips, replay or training windows and shadow-week reviews; about 12 are
pendant/Bluetooth hardware; about 54 are policy, backups, retention, history rewrite, rotation. The recent security block L595-L612 (credential rotation,
public-history rewrite, LAN-exposure firewall 3a-3f) is all "owner applies the runbook".

---

## 8. Skipped / xfail tests that wait on something

- **Dead (unconditional) skips, 2 modules:** `tests/unit/test_auth_security.py:21` ("framework uses JWT, app uses X-Session-ID ... needs updating"; untouched since
  #1075, 2026-07-06) and `tests/unit/test_experts.py:24` ("Expert classes ... need to be exported from mem-agent", a service that no longer exists). X will never
  happen for the second; the first has no owner. Delete both (the synthesis also lists them; still present on 11-02).
- **Stale "until" commentary:** `tests/unit/test_digest_pack.py:75` skips "night_mind not on this tree ... once PR #1930 lands"; #1930 closed unmerged but
  `services/zoe-data/night_mind.py` is on main, so the skip no longer fires. Comment-only fix.
- **Real "until X":** `services/zoe-data/tests/samantha_live/test_live_dedup.py:97,145`, `xfail(strict=True)` until `_attribute_key` supersedes "works at X"
  corrections and paraphrased same-attribute facts under the real brain. X has not happened [inferred: `memory_quality.py` last changed 10-13 with no such rule];
  live-brain-only, so it never runs in CI.
- **Owner-gated:** `tests/unit/test_privacy_canary.py:93` skips until the owner provisions `ZOE_PRIVACY_CANARY_KEY` (ledger L596-597, open).
- About 195 conditional skips are environment routing (Node, Postgres, voice corpus, tools, opt-in live flags) plus 154 `importorskip`; every import target spot-checked
  still exists. No JS `it.skip`/`test.skip`/`.todo` outside vendored `dist/` code.

---

## 9. Finish or kill

### 9a. Finish: the 25 smallest-cost-per-promise items

| # | Item | Size | Exact next step |
|---|---|---|---|
| 1 | PLANS/IDEAS/ROADMAP truth pass | S | one docs PR: fix the 9 stale claims in section 1 and 4 IDEAS labels; regenerate the PLANS live-flag block from the process env |
| 2 | Land #2225 (recall not reaching the brain) | S | mark ready |
| 3 | Close #2116, fold #2229 into #2231 | S | close with a pointer |
| 4 | Land the nightly-loop design on main | S | split `self-evolution-nightly-loop.md` out of #2123 into its own docs PR |
| 5 | Shared `validate` failure on #2191/#2217/#2248 | S | read one log, fix the index check once |
| 6 | Delete 2 dead test modules + the stale #1930 skip comment | S | one PR |
| 7 | Prune the ledger | S | delete the ~3 self-declared-fixed lines and re-date or close the 14 lines citing closed PRs |
| 8 | YouTube Music cookie refresh | S | owner sets `ZOE_YTMUSIC_REFRESH_ENABLED=true`; check the `ytmusic_cookie_refresh` job fires once |
| 9 | Remove `ZOE_ROUTER_SHADOW_TEXT=1` from the live env | S | owner env edit + restart in a quiet window |
| 10 | Install `zoe-offbox-backup.timer` | S | owner picks target/key, installs, runs the restore drill |
| 11 | Install `zoe-samantha-weekly.timer` | S | owner installs the template |
| 12 | Remove the stray `zoe-dreaming.timer` and the 8-day scratch harness container | S | owner `rm` + `docker rm` |
| 13 | Night mind readers | S | replay gate with `ZOE_NIGHT_MIND=serve`, then set it |
| 14 | `ZOE_MEMORIES_VOICE` | S | measure the spoken summary on the panel, set it |
| 15 | Register `ZOE_ROUTER_STRUGGLE_FEED` in the flag registry; teach the scanner `FLAG = "..."` constants | S | one PR |
| 16 | Shadow read-out for restraint / recall gate / delivery ledger / structural | M | one script that counts the shadow log lines for 14 days and prints would-act rates; owner decides per flag |
| 17 | Caregiver curation #2158 (+#2159) | M | rebase, resolve 3 threads, replay on head |
| 18 | llama.cpp #2231 | M | owner live switch, replay, land |
| 19 | Bluetooth settings deploy | M | `deploy-pi-bluetooth.sh` DRY_RUN, flip, one supervised firmware run |
| 20 | Face enrolment card in Settings | M | wrap `zoe_enroll_flow.py`'s POST in the existing Settings identity card |
| 21 | Change notices: deploy failure marker | M | add the marker step to `deploy.yml` (load-bearing, own PR) |
| 22 | W1.3 streamed TTS in LiveKit | M | measure `ZOE_LIVEKIT_STREAM_TTS` on the Ask card; flip or delete |
| 23 | B1.1 speculative turn | M | the live Pi proof the design's flip criteria name; else retire |
| 24 | Ask-card Phase 2 | M | load `zoe-compose.js` in `touch/home.html`, render `zoe.component` trees (compose is already on) |
| 25 | Struggle phase 3 timer | M | install `zoe-router-retrain.timer` with the flag at `shadow` |

### 9b. Kill (design-only or dark for 60+ days with no owner pull)

- IDEAS "(Jason: annotate)" video (132 d, empty), Pinterest ideas surface + PLANS backlog 8 (132 d), orb reacts to music (109 d), oh-my-pi residue except the voice
  replay invariant (89 d).
- TRIAGE-JUDGE TT1 (`ZOE_MULTICA_TRIAGE_JUDGE`, 91 d) and `ZOE_MULTICA_CROSS_REVIEW` (92 d): the Multica executor path they serve is being retired.
- `ZOE_ROUTER_MODE` tier-1 semantic router shadow (134 d, superseded by the two-stage head), `ZOE_ROUTER_SELFTRAIN` loop (superseded by the struggle feed).
- `ZOE_CHAT_INJECT_DB_MEMORY` (133 d), `ZOE_LIVEKIT_FAST_TIERS` (134 d), `ZOE_OTEL_ENABLED` (182 d), `ZOE_MEMORY_LINT_IN_DREAMING` (137 d),
  `ZOE_PERSON_LLM_PREFILTER` (119 d), `ZOE_ANNOUNCE_STRICT_PANEL` (106 d): no criterion, no measurement; delete the code path or write a flip bar this month.
- `ZOE_AFFECT_CONSENT_GATE`: dead by owner decision 10-05.
- Brain tool-selection reliability investigation (84 d, the doc says dropping it is legitimate).
- Docs: `stage-a-channel-agnostic-core.md`, `zoe-core-turn-latency.md`, the 2025 `EXECUTIVE_SUMMARY.md`/"Ready for Implementation" research READMEs.
- Unused timer templates `zoe-self-review` (204 d) and `zoe-agents-sampler`, `zoe-omnigent-runner-reaper`, unless installed this month.

---

## Addendum to section 3: per-phase results from the phased-plan sub-lane (arrived after the draft above)

Labels: [V] means checked against a squash commit, `gh` or `git grep` on main. [I] means inferred.

- **panel-anywhere**
  - F0 lab done (#2094).
  - R1 box side is built but dark (#2095, #2115; `ZOE_RELAY_INGRESS` is off).
  - R1 outstanding: C3, the panel certificate and CA, and the voice-lane edges. `services/zoe-data/AGENTS.md:190` says R1 does not go live until these are closed.
  - R2-R6 design-only.
- **panel-remote-access**
  - P1 prerequisites built (#2082: kiosk gate, firewall, device-token session). Whether they are applied on the panel is unknown.
  - The Headscale link is superseded (#2083 closed).
  - P2 Wi-Fi onboarding is design-only: only `wifi-portal/portal.html` exists.
  - P3 away mode is partial, through the relay.
- **communication-preferences**
  - P1 is live, but built in a different shape from the design: an in-process loop, flag `ZOE_CHANGE_NOTICES`.
  - The `deploy.yml` failure marker is absent.
  - P2 started and stalled: `comm_prefs.py` serves the owner only, and there is no `notice_deliveries` table and no callbacks.
  - P3 and P4 design-only.
- **zoe-abilities**: C0-C2 and A0-A6 all design-only, with zero code [V].
- **credential-vault and p0-deroot**
  - V0/V1 lab done (#2152).
  - V2, V3, V5 and V6 design-only.
  - P0 artifacts and lab merged (#2101), but steps P0-0 to P0-3 are not applied. `docs/knowledge/offbox-backup.md:359` still relies on the pre-P0-3 grant.
- **contacts and calendar**
  - Storage landed for both.
  - Calendar §7 prerequisites are not done: only the router honours `read_only`, at `routers/calendar.py:347,454`.
  - `event_alarms` is stored, but nothing fires it.
  - The sync engines are design-only and blocked on abilities A0 plus the vault.
- **identity-presence-engine**: phases 0-5 and 7 design-only, with no `identity_engine` code. Phase 6 (face) is live through the older panel-identity plan.
- **omi-integration**
  - Sync phase 1 is live. Tagging is live (shadow), and learned voices are live.
  - Secure phase-2 firmware is built (#2135) but not flashed. That is an owner hardware step.
  - Live-stream P1-P5 and B9.2-B9.7 are superseded.
- **memories-space**
  - P1, P2, P4b and P5 are live or in shadow.
  - P3 (`ZOE_MEMORIES_VOICE`) and P6 (`ZOE_DAY_REVIEW_RAISE`) are built but dark.
  - P7 caregiver curation is in open PRs #2158/#2159.
  - The P6 voice dialogue tier is design-only.
- **ask-card**
  - Correction to my own section 3 table: PR-1a (#1364), PR-1b and PR-1c (touch/voice.html retired in #2127) are all done.
  - `dist/voice.html` is the Telegram `/talk` page, not the retired surface.
  - PR-2a is design-only: the estate does not load `zoe-compose.js` (only `chat.html:2412` does). Size S.
- **desktop-ui**
  - Waves F, 1 and 3 done.
  - Waves 0, 4 and 4b started and stalled.
  - Waves 5 and 6, and the Zoe page, design-only.
- **voice-tts-split**
  - Only PR 1 is done (#1540).
  - PRs 2-6 are not started, and `voice_tts.py` grew from about 4.8k to 7,532 lines.
  - Trap: `voice_session.py` on main is the #2025 voice-session module, not PR 6 of the split.
- **chat-split / typed config**: effectively complete. C1-C3 and T1-T4 merged (#1377, #1435, #1541, #1127/#1130/#1132/#1136/#1152). The only residue is the optional C4.
- **tech-debt**
  - Waves 1-2 and most of 3 are done.
  - Wave 3 tier 2 has no `people_service.py`.
  - The harness fence-out is design-only: `main.py:349-382` still runs the Multica poll loop in-process.
  - The doc header still says "planned".
- **samantha-evolution W-items**
  - Done or live: W0, W1.1, W1.2, W2.1, W3.1, W3.2, W3.4, W5 (W5 shadow-week sign-off not verified), W6 (in a different shape), W8, W11.1.
  - Built but dark: W1.3 (#1469), W5.4 persona, W16 scoreboard (timer not installed).
  - Started and stalled: W1.4, W3.3, W3 definition of done (unmet), W7, W12, W13, W14 (offbox timer not installed), W15, W18.
  - Design-only: W3.5, W4 emotion sensing, W5.3, W9, W10, W11.2, W17.
- **beat-the-bar**
  - The tracker is stale (B7.2 still shows ⬜ although it is done).
  - Done: B0.1, B0.2, B0.4-B0.9, B0.11, B0.14, B10.0.
  - Built but dark: B1.1, B3.11, B10.1 web_search.
  - Design-only:
    - B1.2/B1.3, B1.6-B1.9
    - B2.1, B2.3-B2.5
    - B3.3, B3.5-B3.8
    - B4.1, B4.2, B4.4, B4.5
    - B5.2-B5.6
    - B7.3/B7.4, B8.1-B8.3
    - B0.13 JetPack reflash, B0.15 DGX Spark evaluation
  - Started and stalled: B0.12, B1.4, B6.2, B7.1.
  - Superseded: B0.10, B5.1, B9.2-B9.7.
- **zoe-iphone-app**
  - M0 box side built but dark (#2146).
  - No Swift code, no APNs sender, no erase path, no QR card.
  - M1-M3 design-only. Blocked on the Apple account and APNs key.
- **alarms**: P1 built but dark (#2236). P2 is in review (#2241); P3 is a draft (#2243).
- **struggle-detector**: P1/P2 in shadow, P3 and outcomes dark, retrain timer not installed.
- **self-evolution-nightly-loop**
  - The doc is not on main.
  - Phase 0 code is on main: overlay seam #2153, queue #2165 / migration 0062, honest instruments #2137/#2145/#2222.
  - Phases 1-4 design-only.
