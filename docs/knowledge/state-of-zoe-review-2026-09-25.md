---
type: review-record
date: 2026-09-25
audience: Jason first, then every agent
status: complete — live fixes applied where the harness allowed; the rest is sequenced below
---
# State of Zoe — full review, 2026-09-25

> Jason has been away since 2026-08-10 (last merge #1679). This record answers four
> questions: **what is broken right now, what changed in the world that affects Zoe, what
> could make her better, and in what order.** Every "live" claim below was read from the
> running box on 2026-09-25 (not from a doc); every external claim carries a source.
> Six research agents ran in parallel; their raw reports are condensed here. Items marked
> UNVERIFIED could not be confirmed from a primary source.

## 0. TL;DR

- **Zoe has been effectively unreachable since 2026-09-03.** The last chat message is
  2026-09-03. Three independent failures stacked: the **Telegram bot could not connect**
  (fixed today, root cause below), the **touch panel is off the network** (needs a physical
  check), and **memory recall is silently broken** by Chroma HNSW corruption since
  2026-09-03 21:05.
- **The box is memory-starved and it is structural, not a leak.** ~100 MiB free, 5.2 GB in
  zram swap that itself costs ~1.9 GB of real RAM. That single fact is the root cause of the
  voice replay gate skipping 40 runs in a row, the self-hosted test lane flatlining for 21
  runs, the weekly music-discovery job never producing anything, and two of the last three
  deploys refusing to run. A concrete plan to free 3–4 GB is in §3.
- **Nothing in the rocks needs swapping, but the ground under them moved.** The llama.cpp
  crash that forced FlashAttention OFF with the MTP drafter was **fixed upstream on
  2026-06-30**, ten days after the build Zoe runs. Re-enabling FA + q8 V-cache is the
  single biggest free win (RAM and prefill). Moonshine's package gained keyword biasing and
  speculative decoding; Kokoro can run on ONNX Runtime CUDA at roughly a third of its
  current 2.3 GB. Details §5.
- **Security must-dos found by the dependency audit:** the pinned YouTube PO-token image
  (`bgutil-ytdlp-pot-provider:1.3.1`) carries a **CVSS 8.8 RCE** (fixed in 2.0.0; the LAN
  vector is already closed by the loopback bind, the browser-origin vector is not); the
  **zoe-auth container still runs FastAPI 0.104.1 / pydantic 2.5.0 / bcrypt 4.1.2** — the
  "DONE" audit in PR 1645 never reached the box; host `anyio` has a **critical** TLS
  host-spoofing CVE; Node 22.22.0 is four security releases behind; the Samba image was
  last pushed in 2021. Details §4 and §6.
- **Platform clocks are ticking:** Python 3.10 reaches end-of-life on **2026-10-31**
  (onnxruntime 1.23.2 is already the last cp310 wheel; websockets 17, av 18, numpy 2.3,
  scikit-learn 1.8 all need 3.11+); GitHub will **stop running `pull_request_target`
  workflows on public repos on 2026-11-02** unless allow-listed (this hits `voice-gate.yml`
  and the `break-glass` escape hatch); the Omnigent container's Claude refresh token now
  runs to **2026-10-11**; the self-signed TLS cert expires **2026-11-24**.
- **Costs:** Claude Code weekly limits dropped ~17% on 2026-09-14 versus the summer
  promotion; Copilot moved to per-review credits; CodeRabbit is **free on this public repo**
  and could replace the ~$380/month Greptile line. Details §6.
- **Compared with the field, Zoe is ahead** on per-stage latency accounting, deterministic
  "when to speak" scaffolding, consented local identity, and the self-evolution harness.
  She is **behind** on false-interruption recovery, speculative turn-start, a delivery
  policy for proactive speech, and structured bi-temporal memory supersession. Sixteen
  concrete pieces to borrow, ranked, in §7.

## 1. What was done today (and what was blocked)

| Action | Result |
|---|---|
| Telegram front door: added a systemd drop-in raising Node's Happy-Eyeballs attempt timeout to 1500 ms, restarted the bot | **Fixed.** `/health` → 200, `polling: true`, "took the bot over" in the journal. Mirrored into `scripts/setup/systemd/flue-zoe-telegram.service` in this branch. |
| Zombie `zoe-purge-presence.timer` (its script was deleted in #1583) | **Disabled** and reset-failed. Delete the two unit files at leisure. |
| `tests/conftest.py` stale comment ("zoe-core lives in docs/archive") | Corrected in this branch. |
| Stop the retired `openclaw-gateway.service` (~270 MB RSS + ~265 MB swap) and the dead Hermes keep-warm timer | **Done** (`disable --now` both). The watchdog script no longer probes OpenClaw or Hermes; the health-check script warns instead of failing on Hermes, skips OpenClaw, and probes the zoe-data user unit correctly. Both scripts test-ran clean (backups kept as `*.bak-20260925`). |
| Nightly MemPalace backup racing a live SQLite write | **Fixed** in `~/scripts/maintenance/mempalace-nightly-backup.sh`: snapshots `chroma.sqlite3` with the online backup API, tars the snapshot in place of the live file. Test run wrote a 32 MB tarball whose `chroma.sqlite3` is a valid SQLite header. |
| journald volatile (no history survives a reboot) | **Fixed**: `/var/log/journal` created, `Storage=persistent`, `SystemMaxUse=400M`, journald restarted and flushed. |
| Docker cruft | **Pruned** 12.3 GB (dangling images + build cache). |
| Dependabot alerts disabled on the repo | **Enabled** (vulnerability alerts only; no auto-PRs). |
| Host packages: 9 security patches (anyio, urllib3, pillow, pyasn1, idna, h2, hpack, msgpack, pydantic-settings) + the 6 drifted pins (uvicorn 0.49.0, websockets 16.1.1, aiortc 1.15.0, av 17.1.0, ag-ui-protocol 0.1.19, python-json-logger) | **Installed** into `~/.local` (dry-run first: numpy 1.26.4 / torch 2.8.0 untouched; uvicorn `auto` still resolves to the legacy `websockets_impl`; drift check now reports zero drifts). **They take effect only when zoe-data is restarted, which the classifier blocked** — restart it (`systemctl --user restart zoe-data`), poll `/health` and `/readyz`, and run the replay gate once RAM allows. Rollback: `pip install --user uvicorn==0.34.0 websockets==14.1 aiortc==1.14.0 av==16.1.0 ag-ui-protocol==0.1.14`. |
| Node 22.23.3 (security release) | **Downloaded** via nvm; `~/.nvm/current` still points at 22.22.0 on purpose. Switch the symlink and restart the two Flue units when a replay gate can run (the brain sidecar is on the voice path). |
| Chroma HNSW rebuild (`mempalace_drawers`: 146 live / 3,963 tombstones; 191 docs in SQLite) | **Done** (second attempt, after the "fix everything" go-ahead): zoe-data stopped, `check_memory_tombstones.py --execute mempalace_drawers --yes` rebuilt 191 rows (pre-compaction export + salvage JSON taken by the tool), `hnsw:resize_factor=2.0` + `hnsw:space=l2` re-applied to the new vector segment, a fresh-process query returned sane hits ("User's name is Jason") and a metadata update succeeded (the crash path), zoe-data restarted in 6 s and `/health` now reports `memory_capture: ok — self-recall ok`. |
| zoe-data restart (loads the installed package set) | **Done** as part of the rebuild. `/readyz` ready, brain/STT/TTS ok, TTS on CUDA, zero errors in the app log since; the four idle `pi --mode rpc` children died with it; resident size fell from 1.66 GB to 1.1 GB. |
| Apply the `functiongemma-router` swap-guard template | **Done**: template copied, daemon reloaded, router restarted; now 606 MB resident with `VmSwap: 0`, `MemorySwapMax=0`, `MemoryLow=768M`. |
| Node 22.23.3 | **Live** for both Flue units (`~/.nvm/current` switched, `flue-zoe-telegram` and `flue-zoe-brain-2x` restarted, both healthy on the new binary). Validated by the replay gate below. |
| **Voice replay gate** (the end-to-end test of everything above) | **PASS** at 22:39, via the nightly unit itself (remote-STT mode): 20 samples, said-vs-did **13/13 OK, fail=0** (7 empty = non-speech captures), medians STT 374 ms / brain 2,983 ms / e2e 2,077 ms (warm harness, relative). First real pass after 41 consecutive skips. Artifact bound to commit `01e2e365`, clean tree. |
| zoe-auth image stale (FastAPI 0.104.1 / pydantic 2.5.0 / bcrypt 4.1.2 running) | **Rebuilt and recreated** (`docker compose build zoe-auth && up -d --no-deps zoe-auth`): container healthy, now FastAPI 0.141.1 / pydantic 2.13.4 / bcrypt 4.3.0 / starlette 1.7.0, `/health` 200. |
| Local `ci_safe` unit lane (what `validate` runs) | **6,277 passed, 0 failed** on the host after the package bumps (97 s). |
| CI `validate` red on the PR: `test_multi_hop_graph_recall` | **Fixed** (second commit on the PR). Not a ranker regression: the benchmark's fixture pinned `added_at` to fixed dates while its autouse fixture turns hybrid retrieval on; the semantic term decays with age but the "person" preference boost is constant, so past ~2 months the answer row entered the top five even with the graph OFF. Dates are now relative to `now`. Negative control reproduces the failure with the old date; whole file 7/7. |
| Multica API 401 | **Explained**: the API returns 200 with the configured token; the 401s came from the six-week-old zoe-data process calling without one. Cleared by the restart; confirm at the next 06:00 autopilot run. |
| zram shrink | **Blocked** (root-level `swapoff`/`/sys` writes and the `/etc/systemd/nvzramconfig.sh` edit were both refused by the classifier). One-time recipe for you, with the brain and Kokoro stopped: `for n in 7 6 5 4 3 2 1 0; do sudo swapoff /dev/zram$n; done`, then set each device's `disksize` to 244 MiB (1/8 of RAM across 8 devices), `mkswap` + `swapon -p 5`, and change the `/ 2 /` to `/ 8 /` in `/etc/systemd/nvzramconfig.sh` so it sticks across reboots. Expected gain 2–3 GB. |
| Issue #863 (README says "E2B") | Already fixed on main (README line 27 reads E4B). Close the issue. |

Measured effect of today's reclaim (before → after): available memory **25 MiB → ~830 MiB** (gateway + timer stopped, zoe-data restarted lean, router resident, journald capped). The big lever (zram, §3 step 2) is still ahead and is the one step that needs root.

**Correction from Jason (22:30):** the touch panel is off because he turned it off. It is not a fault; V2 in the register is withdrawn.

## 2. Live health — what is broken, why, and the exact fix

Everything here was verified on the box on 2026-09-25 between 21:30 and 22:10 AWST.

### 2.1 BROKEN

**Telegram bot (fixed today).** `flue-zoe-telegram` logged `ETIMEDOUT` to `api.telegram.org`
on every poll; the 1-minute watchdog restarted it ~4,000 times. Root cause, proven with a
negative control: Node 22's connection logic gives each resolved address 250 ms before
moving on (`--network-family-autoselection-attempt-timeout`); the TCP round trip from this
box to Telegram now measures ~410 ms, so every hostname connect aborted. `curl` (no such
timeout) always worked, which is why the earlier IPv6 theory looked plausible but the
IPv4-first flag did **not** fix it, while raising the timeout did. Two follow-ups: (a) the
grammy error path printed the **bot token in the URL into journald ~4,000 times** — rotate
the token via BotFather and `journalctl --vacuum-time=1d`; (b) make the watchdog back off
instead of restarting every 60 s.

**Touch panel (`zoe-pi`, 192.168.1.61).** No route to host, ARP incomplete on both NICs.
Last server-side panel activity 2026-09-03; the last kiosk request before it went dark was a
403 on `/api/ui/actions/pending`, which suggests the kiosk-guest session had already expired.
Physical check needed (power, Wi-Fi). Nothing on the Pi (VAD tail, speaker-ID shadow, kiosk
build) can be verified until it is back.

**Memory recall (Chroma HNSW corruption).** `/health` reports
`memory_capture: degraded — "Cannot return the results in a contigious 2D array"` since
2026-09-03 21:05 (`search failed user=jason`). This is the documented pre-crash signature
from the 2026-07-31 incident: queries return nonsense before the segfault surfaces. Related
symptoms: `mempalace_drawers` is 96% dead rows ("COMPACT RECOMMENDED"), and the nightly
`memory_digest` has logged **44 consecutive zero-effect runs**. The SQLite metadata is
intact (it is the source of truth); the HNSW binary is not. Recovery recipe (worked on
2026-07-31): stop zoe-data → tar `~/.mempalace` → in a fresh process `delete_collection`
first, recreate with `hnsw:space=l2, hnsw:resize_factor=2.0`, re-add all documents from
`chroma.sqlite3` in batches of 100 (~6 min for ~3.5k docs) → verify a semantic query and a
metadata update → start zoe-data. Do this in the same maintenance window as the RAM work
(§3), never with ~100 MiB free.

**Voice replay gate + self-hosted tests + deploy gate.** All three are the same failure:
the probe refuses below 700 MB free, the test lane below 800 MB, the deploy's memory gate
was recalibrated to 250 MB on 2026-08-10 but the voice-path check still needs a fresh
passing replay artifact. Result: no real PASS in 40 nightly runs, 21 test runs that never
reached pytest, and any voice-path change on main is undeployable. Fix = §3.

**YouTube Music provider.** Music Assistant's `ytmusic` provider has logged
`User does not have Youtube Music Premium` / `cookies are no longer valid` every ~2 minutes
since at least 2026-08-10. Known failure mode; fix is the panel Sources → Reconnect flow,
then a zoe-data restart. Upstream confirms the cause is Google's cookie sessions dying
within hours; MA 2.10.x only made the error clearer. The `bgutil-ytdlp-pot-provider` image
is pinned at 1.3.1; 2.0.0 shipped 2026-09-08.

**Multica autopilot "Platform Health Check".** Fails daily; `multica_client` gets **401**
from `:8080/api/issues` — token drift between env and container. Cheap to fix, and until
then the board runner is blind.

### 2.2 DEGRADED

- **zoe-health.service** fails every 5 minutes (since 2026-06-08!) because
  `~/bin/zoe-health-check.sh` hard-requires the retired Hermes agent on `:8642`; the
  companion `zoe-watchdog.sh` cron pushes a "hermes-agent is not responding" system
  notification every 5 minutes (21,000+ lines), and `zoe-keepwarm.timer` pings the dead
  Hermes every 8 minutes. Fix: demote the Hermes block to a warning (or delete it) in both
  scripts and `systemctl --user disable --now zoe-keepwarm.timer`.
- **Nightly backup** works most nights (last full success 2026-09-24 02:37; Postgres dump
  missing on 09-21 and 09-25). The 09-25 failure was `tar: chroma.sqlite3: file changed as
  we read it`, and because the unit uses `set -e` the Postgres step was skipped. Fix:
  snapshot the SQLite with `sqlite3.Connection.backup()` before tarring, and make the
  Postgres step independent (`ExecStart=-`).
- **functiongemma-router** has no swap guard: the #1622 template (2026-08-04) was
  committed but never copied to the box; 255 MB of the router sits in swap. Fix: `cp` the
  template, `daemon-reload`, restart at a quiet moment.
- **Shared Serena** leaks to >1 GB roughly hourly (65 recycles in 3 days) and the health
  probe hits `/mcp`, which upstream issue #2088 (2026-09-20) shows retains ~50 kB per
  request. The hourly reaper and nightly pre-gate restart are masking this. Two Codex stdio
  Serenas also spawn from inside the Omnigent container (`/root/.codex/config.toml` still
  uses `command=` instead of the shared URL) — exactly the class AGENTS.md warns about.
- **Home Assistant** is on 2026.5.2; stable is 2026.9.3. One repair pending
  (`config_entry_only_mcp_server`). Note 2026.9's breaking tool-name change (§5.7) before
  upgrading.
- **cloudflared** re-registers ~20×/day and logged 194 "context canceled" errors during
  the 21:23 load spike (load average hit 55 while an Omnigent polly session ran claude +
  codex + 2 Serenas + 3 codebase-memory processes).
- **zoe-data stdout log** is 445 MB and unrotated. **journald is volatile** (no
  `/var/log/journal`), so nothing older than ~3 days survives; the 6-week OOM history is
  gone. Create the directory and set `SystemMaxUse=`.
- **Disk clutter** (not urgent, 23% used): 127 dangling Docker images (21 GB), 9.8 GB
  build cache, 280 stale worktree dirs (~23 GB, only 26 registered), a 1.5 GB
  `engineering_pipeline_runs.jsonl.backup` under `~/.zoe`.

### 2.3 OK

Postgres (alembic at 0028 = newest migration; 39 MB; no bloat, no long transactions),
GitHub runner (2.337.0, current), `gh` auth, branch protection (`validate` + `secret-scan`,
strict, admins enforced), Omnigent OAuth (refresh valid to 2026-10-11 — the 08-18 cliff in
the docs did not bite), llama-server / Kokoro / router / Flue 2.x brain all healthy with
their swap guards holding (cgroup swap = 0 for the voice units).

### 2.4 Flags that are ON but documented as dark

| Flag | Live | Doc says | Decision needed |
|---|---|---|---|
| `ZOE_FACE_ID_ENABLED` | true | must stay off until users can delete their own face data (no delete UI exists) | turn off, or build the delete UI (ZOE-6129) and update the policy doc |
| `ZOE_MUSIC_DISCOVERY` | on | flip only after a verified manual run | every weekly run exits 2 on the memory gate; turn off until RAM is fixed |
| `ZOE_COMPOSE_UI` | 1 | "built but flag-dark, PR-2c lab-prove then operator flips" | confirm what compose does live; update PLANS |
| contacts flags, `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN` | 1 | several docs still say "dark/blocked" | doc sweep only |

## 3. Memory: the structural problem and the plan

Measured composition (RSS, no-swap pinned): llama-server 7.3 GB, Kokoro sidecar 2.55 GB,
zoe-data 1.66 GB = **11.5 GB pinned**. zram holds 5.2 GB of compressed swap using
**~1.9 GB of physical RAM**. What is left (~2 GB) hosts Docker (Music Assistant alone is
~1.8 GB RSS + 0.8 GB swap), Home Assistant, Multica, Omnigent, and every agent CLI session.
The 2026-07-06 profile already measured this; it is now worse (perf-hardening-plan calls the
3.6 GB swap figure "stale" — today it is 5.2 GB).

Plan, in order of RAM-per-risk (each step measured with `free -m` before/after and
`tegrastats`, in a window with no voice traffic):

1. **Stop retired/idle consumers (~1 GB, zero risk):** `openclaw-gateway` (retirement
   decided 2026-07-22; 270 MB + 265 MB swap), the four dormant `pi` RPC children of zoe-data
   (~256 MB), the Hermes keep-warm timer. Also fix the Omnigent container's Codex config so
   it stops spawning private Serenas.
2. **Right-size zram (2–3 GB):** eight ~978 MB devices are compressing 5.2 GB and costing
   ~1.9 GB of RAM. Jetson AI Lab's own guidance for 16 GB Orins is to disable
   `nvzramconfig` and use the NVMe swapfile (already present, 50 GB, 87 MB used). Shrink to
   ≤2 GB total first, watch swap-in rate for 24 h, then decide on off.
3. **Headless target (~800 MB if the GUI is still enabled):** `systemctl get-default`; set
   `multi-user.target`; disable `nvargus-daemon` if not needed by the PanaCast path.
4. **Rebuild llama.cpp ≥ 2026-06-30 and re-enable `--flash-attn on` + `--cache-type-v
   q8_0` (100–300 MB, plus prefill speed):** the crash Zoe's build works around (PR #25148,
   "CUDA: fix Gemma E4B MTP FlashAttention", merged 2026-06-30) is fixed. Lab-prove with a
   multi-turn replay under `flock`; keep `--fit off` (MTP + `--fit` still crashes, #24117);
   watch #25522 (a 31B-only FA crash in the same kernel family). Same rebuild picks up the
   MTP CUDA-graph change (#28549, 2026-09-16) and the grammar token-id fix (b11169) for the
   router sidecar.
5. **Kokoro on ONNX Runtime CUDA instead of PyTorch (1.2–1.8 GB):** same model, same
   voices; NVIDIA's own Orin demos and `jetson-containers` ship a `kokoro-tts-onnx` recipe.
   Must hold RTF < 0.3 and pass the replay gate; a CUDA context still costs 300–500 MB.
   UNVERIFIED on Tegra until measured.
6. **Music Assistant memory cap:** MA 2.10 respects container cgroup limits; set one in
   `docker-compose.modules.yml` once the YT provider is reconnected.

Steps 1–3 need no code change and should clear the 700/800 MB gates on their own. Step 4
restores deployability of voice-path changes and is the prerequisite for everything in §5
that touches the brain. The samantha-plan W3 definition of done (≥2 GB freed, recorded in
an OKF profile) applies.

## 4. Security items

1. **Telegram bot token leaked into journald** (~4,000 lines). Rotate + vacuum.
2. **`GET /api/voice/livekit-token` hands an unauthenticated LAN caller a valid join
   token** (documented in `livekit-key-rotation.md` §"open"). Add the auth dependency
   (replay-gated).
3. **Face-ID enabled without a delete UI** — a retention-policy breach by the repo's own
   policy doc (§2.4).
4. **Three secret-scanning alerts from 2025 are still open** (two GitHub OAuth tokens, one
   Slack webhook). Confirm they were rotated and close them.
5. **Dependabot is disabled on the repo.** Enable security updates (alerts only; the
   deliberate pins in `requirements.txt` need `ignore:` rules so it does not fight them).
6. **`pull_request_target` restriction (GitHub, 2026-11-02):** `voice-gate.yml` and
   `break-glass.yml` both trigger on it. Allow-list them in repo Actions settings before
   November or the break-glass escape stops working during the next outage.
7. **Retention change (GitHub, 2026-10-01):** check runs/statuses will follow the 90-day
   log retention; the voice-gate evidence bound to old heads becomes unreadable. Nothing
   depends on it long-term, but greploop/break-glass audit scripts must not assume
   persistence.
8. **Omnigent's `claude-sdk` harness on subscription OAuth** now sits on the wrong side of
   Anthropic's stated policy ("developers using the Agent SDK should use API key
   authentication"); the native `claude` harness is explicitly fine. Route the polly lane
   through the native harness or an API key.
9. **`bgutil-ytdlp-pot-provider` 1.3.1 → 2.0.0** (GHSA-qpv9-8xfj-xx9m, CVSS 8.8, RCE via
   crafted proxy settings). The compose file already binds `127.0.0.1:4416` (verified with
   `ss`), which closes the LAN vector; the browser-origin vector (a malicious page making
   localhost POSTs) is live because the Jetson runs CloakBrowser. Bumped in this branch;
   run `music_jsruntime_probe.sh` after deploy. Music Assistant installs the matching
   client plugin unpinned, so it already floats to 2.x.
10. **zoe-auth image is stale:** running container has FastAPI 0.104.1 / pydantic 2.5.0 /
    bcrypt 4.1.2 (image built 2026-07-17) while the file says 0.141.1 / 2.13.4 / 4.3.0
    (PR 1645, 2026-08-09). Rebuild the image and find out why the deploy rebuild gate
    skipped it.
11. **Host site-packages with fixed advisories in the zoe-data import graph:** anyio 4.13.0
    (**critical**, TLS host spoofing, fix 4.14.2), urllib3 2.6.3 (2× high, fix 2.7.0),
    pillow 12.2.0 (12 advisories, fix 12.3.0), transformers 5.5.0 (high, path traversal on
    `save_pretrained`, fix 5.10.0 — replay-gated), pyasn1 0.6.2 (fix 0.6.4), idna, h2/hpack,
    msgpack, pydantic-settings, mcp 1.27.0 (server-transport only). All install on 3.10.
    Box first, then record in `requirements.txt`, then restart zoe-data and poll `/health`.
12. **Node 22.22.0 on the host** lacks 22.22.3 → 22.23.2 (three High CVEs in http2 and
    permissions); latest 22.23.3. Bump via nvm; the brain sidecar restart is replay-gated
    even with no code diff. Also `npm update hono nanoid` in both Flue trees (hono ≤4.13.4
    has three moderate advisories, nanoid <3.3.18 a High).
13. **`dperson/samba`** (zoe-smb-drop) was last pushed 2021-03-31 — five years of Samba
    CVEs. Replace (e.g. `ghcr.io/servercontainers/samba`) or drop the share.
14. **Dependabot alerts** were disabled on the repo; enabled today (alerts only, no
    version-update PRs — a `requirements.txt` bump changes no executed byte here, so the
    proposed grouped/monthly `dependabot.yml` with the deliberate-pin `ignore:` rules is in
    Appendix D for when you want it).

## 5. What changed in the world that matters to Zoe

### 5.1 Brain: llama.cpp and Gemma 4 (optimise around the rock)
- **FA + MTP crash fixed 2026-06-30** (PR #25148) — see §3 step 4. Zoe's build
  (`f449e0553`, 2026-06-20) predates it by ten days. Latest tag b11178.
- **Gemma 4 "stealth" July refresh** (tool-calling reliability, fewer truncations) —
  whether new E4B weights were pushed to Hugging Face is contradicted across sources
  (UNVERIFIED). Action: `sha256sum` the local `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` and
  the MTP drafter against `unsloth/gemma-4-E4B-it-qat-GGUF`; if different, stage on a spare
  path and replay-gate.
- No new E4B checkpoint, no Gemma-4-based 270M/1B, no "Gemma 4n". Gemma 4 12B Unified
  (audio in) does not fit beside Kokoro. E4B already accepts audio and `llama-server` does
  route `input_audio` (needs a BF16 mmproj, ~300M encoder) — not for the hot path on this
  RAM budget, but it exists.
- Sampling: Google/Unsloth say T=1.0, top-p 0.95, top-k 64 for Gemma 4; a Sept-2026 CPU
  benchmark (arXiv 2609.07370) shows sub-2B tool calling collapses under sampling —
  Zoe's greedy GBNF stage-2 router is the right design. The brain's `--temp 0.7` with no
  per-request override is the known 14% `test_tool_action_dispatches` flake (not
  user-visible; router decides first).

### 5.2 STT: Moonshine (rock unchanged, package moved)
`moonshine-voice` 0.0.62 (installed, unpinned) → **0.1.5 (2026-08-24)**: speculative
decoding on by default (−28% decode latency in vendor numbers), **`keyterms`/`context`
runtime biasing** (a direct fix for household names and device names being misheard),
MIT-licensed models, breaking: ORT-only models and a `DialogFlow → AgentFlow` rename.
Replay-gate mandatory. No Moonshine v3; Medium Streaming remains 6.66% WER on Open ASR.

### 5.3 TTS: Kokoro (rock unchanged, runtime can shrink)
Kokoro is still v1.0; the win is the runtime (§3 step 5). New small expressive models
worth a bake-off for W11 only: Kyutai Pocket TTS (100M, MIT, CPU real-time, could give the
**Pi panel a local announcement voice** so the Orin need not synthesise short openers),
Chatterbox Nano/Turbo (MIT, `[laugh]` tags), Supertonic 3 (expression tags, OpenRAIL),
NeuTTS Air (GGUF). None replaces Kokoro.

### 5.4 Turn detection, VAD, speaker-ID
- Smart Turn **v3.2** (2026-01, same I/O, 8 MB int8) — drop-in if Zoe is on v3.0/3.1.
  No v4.
- Silero VAD **v6.2.x** (16% fewer errors on noisy data) — confirm the model file is v6.
- LiveKit Turn Detector **v1.0 (2026-06-17)** is audio-based; `v1-mini` runs on CPU;
  the text model Zoe's lane may reference is deprecated and removed in 2.0. LiveKit's
  adaptive-interruption model is Cloud-only.
- Research direction (arXiv 2609.11066, 2606.18094): prosody-only endpointing wins; text
  features raise false alarms; training on time-to-next-onset beats binary EOT.
- Speaker-ID: Resemblyzer (2019) is measurably weaker than sherpa-onnx's ERes2Net/CAM++
  (~25 MB ONNX, Pi-capable). Run one in shadow during the W5 week on the same `(boot, seq)`
  rows. Omi's verification rule (distance < 0.65 **and** a 0.10 margin over the second-best
  profile) is the cheap fix for the two-cluster enrolment trap in Zoe's own corpus.

### 5.5 Memory stack: MemPalace and Chroma
- MemPalace 3.3.1 → **3.10.0 (2026-09-16)**. 3.8.0 removed a 440 MB per-collection-open
  growth on long-running servers; 3.6.0 added atomic fact supersession and distinguishes
  index damage from data loss (Zoe's exact incident); 3.10.0 adds read-time decay and a
  3-tool MCP. **But 3.10.0 requires `chromadb>=1.5.4,<2`** (verified on PyPI), so the
  upgrade forces the Chroma 0.6.3 → 1.5.x migration the requirements comment warns about
  (silent write drops if done wrong). Sequence: after the HNSW rebuild, copy the palace,
  `mempalace migrate` in a throwaway venv, reconcile row counts against
  `export_memory_store.py`, confirm `memory_recall_probe` self-recalls. Not in place.
- mem0 v2.2.0 (2026-09-23) ships an official **Pi plugin** and "User Profiles"; Letta's
  sleep-time compute paper (5× less hot-path compute when queries are predictable); Honcho
  v3 has fully deterministic dream gating. Borrowables in §7.

### 5.6 Brain chassis: Flue and Pi
- **Flue was silent, then shipped eight releases in two weeks:** 2.0.4–2.0.8, **2.1.0
  (2026-09-18), 2.1.1 (2026-09-23)**. Skip 2.0.4–2.0.6 (broken packaging). Gains for a
  Node sidecar over a small local model: "Connection error" now retryable, truncated
  tool-call batches resume instead of failing, compaction-continue bug fixed, **first
  streamed delta flushed immediately** (a direct first-audio latency win), per-tool
  `timeoutMs`. No breaking API 2.0.1 → 2.1.1; session store format unchanged (one-time
  re-fold). Workflows are gone for good; the migration guide prescribes `init()` handles +
  `durable: true` tools — that is the design answer for `labs/flue-executor`.
- Flue still has **no tool-iteration cap** (only 10 attempts / 1 h per submission). Keep
  Zoe's own cap.
- **Flue 2.2.0 (on main, unreleased) bumps Pi to 0.87.1**, which changes the provider
  stream contract (`TranscriptContext`) and removes `shouldStopAfterTurn`. It also brings
  two llama.cpp tool-call fixes (#9816 strict schemas, #9528 `enable_thinking`). Check
  whether the sidecar registers a custom provider before taking it.
- Pi's **RPC mode changed in 0.84.0** (`message_update` is deltas-only) — the dormant
  `zoe-core` lane's client must be adapted before that lane is ever revived.

### 5.7 Home Assistant and Music Assistant
- **HA 2026.9 (2026-09-02) domain-prefixes every LLM tool name**
  (`HassTurnOn → intent__HassTurnOn`, `GetLiveContext → homeassistant__GetLiveContext`;
  unprefixed names hard-fail in 2027.3). Any Zoe prompt, router corpus or
  `homeassistant-mcp-bridge` mapping that names an HA tool must be updated **before** HA is
  upgraded from 2026.5.2. 2026.8 added a native `llama_cpp` conversation integration and
  per-API MCP endpoints; 2026.10 adds an MCP `require_admin` default and a `device_id`
  meta field (lets Zoe pass the panel's device so "turn on the lights" resolves to that
  room — worth adopting).
- **Music Assistant 2.10.4 (2026-09-18)**: native AirPlay 2 (`cliairplay`), up to 3
  concurrent YT Music streams, YT treated as a realtime source (crossfade success
  11% → 93%), "keep retrying YT when the PO-token server is not up". Local audio provider
  retired in favour of Sendspin. The `music-assistant-client` 1.5.x REST login changed for
  newer servers — check Zoe's client against 2.10 before pulling `:stable`.

### 5.8 Agent tooling, protocols, review pipeline
- **MCP spec 2026-07-28** is the largest revision yet: stateless core (no `initialize`),
  `Mcp-Session-Id` removed from Streamable HTTP, tasks moved to an extension, roots /
  sampling / logging deprecated. Nothing breaks today (SDKs keep 1.x), but the shared-Serena
  session design is on a deprecation path. Serena 1.7.0 is still a single FIFO executor;
  two September bugs (#2088 leak, #2063 post-timeout concurrent execution) hit Zoe's
  topology directly.
- **codebase-memory 0.8.1 → 0.11.0**: 0.10 introduced a per-user shared daemon (thin stdio
  sessions), 0.11 cut peak memory 40–80%. This directly addresses the "14 spawns, 1.27 GB"
  problem; upgrade requires a one-time reindex and re-checking any Cypher that relies on
  `CALLS` (split into `CALL_REFERENCE` in 0.10).
- **AG-UI 1.0.0 (2026-09-17)** (Zoe pins 0.1.19): optional nulls omitted from payloads
  (check the kiosk JS), `StateDelta` typed, `THINKING_*` → `REASONING_*`, new
  `ACTIVITY_SNAPSHOT/DELTA` events — the right carrier for "brain builds a card, then
  updates it in place". Google's A2UI (v1.0 RC) is the declarative component-catalog spec
  CopilotKit layers on top; a fixed catalog is the safe way to let a 4B brain author UI.
- **Claude Code 2.1.220 → 2.1.282**: headless runs now prompt (then deny after 2 min) on
  substitution-`rm`; `"type":"sdk"` MCP entries are skipped; `anthropic-skills:` namespace
  no longer loads; AGENTS.md is read natively when no CLAUDE.md exists (Zoe's import shim
  still wins). Codex 0.146 → 0.157: `--full-auto` and `codex mcp-server` removed. Omnigent
  0.7.0 → 0.15.0: 21 migrations, `omni` → `omni run`, tmux ≥3.3, pi minimum 0.84.2, and
  agent CLIs no longer inherit host credentials (needs `env_passthrough`). None of these are
  urgent while the pins hold; all bite the moment a container is rebuilt unpinned.
- **Review cost.** Greptile is $30 + $1/credit (the July ~$380 reproduces exactly).
  CodeRabbit is free on public repos; Codex review is inside the ChatGPT plan; Copilot code
  review is $0.05–1 per Lite review in credits and its default flips to the pricier Balanced
  tier on **2026-09-28** (set Lite explicitly). Claude's hosted code review is Team/Enterprise
  only at $15–25 per review — not for this repo. Suggested trio: CodeRabbit + Codex +
  Copilot Lite, with Greptile on its free Starter tier for label-gated high-risk PRs.
- Multica images are ~60 releases behind (v0.3.1 digest vs v0.5.3); v0.5.3 natively
  auto-completes issues when linked PRs merge, which retires part of
  `multica_board_runner.py`. Upgrade is a project (two minor lines, migrations, possible
  Redis requirement UNVERIFIED), not a bump.
- NVIDIA's SkillSpector 2.9.5+ can point its LLM stage at a local OpenAI-compatible
  endpoint (i.e. Zoe's llama-server), satisfying the "no egress of internal skills" rule.

### 5.9 Platform: JetPack, Python
- Box: Seeed reComputer J401 (Orin NX 16 GB), JetPack 6.2.1 (L4T 36.4.3 per
  `nv_tegra_release`; the l4t-core package reports 36.4.7), CUDA 12.6, Python 3.10.12,
  Node 22.22.0, llama.cpp b9733 (upstream b11179, 1,455 commits ahead).
- **Python 3.10 EOL: 2026-10-31.** Already 3.10-capped: `websockets` 17, `av` 18,
  `numpy` ≥2.3, `scikit-learn` ≥1.8, `SQLAlchemy` 2.1, `onnxruntime` ≥1.25 — and
  **onnxruntime 1.24.x ships no cp310 wheel at all, so 1.23.2 is the ceiling** (the
  requirements comment claiming 1.24.3 headroom is stale). Pipecat is ≥3.11; Omnigent
  ≥3.12 (containerised, unaffected).
- **Recommended path: split the interpreters rather than reflash.** zoe-data's only CUDA
  consumer is Kokoro, which is already a separate sidecar process; zoe-data itself needs
  torch only for Resemblyzer (CPU). So keep `kokoro-tts.service` and `llama-server` on the
  system 3.10 / CUDA 12.6, and move `zoe-data.service` to a uv-managed **3.12 venv** with
  PyPI CPU torch, onnxruntime 1.30, websockets 17, av 18, numpy 2.x, scikit-learn 1.9
  (retrain/re-export the router head), starlette 1.7. Gate: full `ci_safe` lanes in the
  venv + voice replay + memory recall probe. This removes the EOL pressure without a
  reflash and unblocks the Chroma 1.x migration (which drops chroma-hnswlib, one of the two
  concrete `numpy<2` blockers; the other is the Jetson torch 2.8.0 wheel).
- **JetPack 7.2 / 7.2.1 (2026-08-11)** now lists the Orin family (7.0/7.1 were
  Thor-only): Ubuntu 24.04, kernel 6.8, CUDA 13.2.2, Python 3.12 — a full reflash, and the
  Seeed J401 carrier needs a Seeed JP7 BSP that is UNVERIFIED as available. jetson-ai-lab
  publishes no `jp7` wheel index yet (its `sbsa/cu130` index has cp312 torch 2.9–2.11 but
  does not state Orin support). Treat as a later rebuild window, after the RAM work and
  the Chroma migration. JetPack 6.2.3 is the last 6.x.
- Model artifacts behind upstream: the Gemma 4 E4B QAT GGUF + MTP drafter were
  re-uploaded by Unsloth on **2026-07-17** ("Added Gemma official chat template update") —
  local files (2026-06-20) differ by ~2 KB and `llama-server.service` passes no
  `--chat-template`, so the embedded template is live; a re-download is replay-gated. The
  Silero VAD file at `~/models/silero_vad.onnx` is the **v6.0** model (md5 matches); the
  v6.2.1 file is already in `~/.cache/torch/hub` and was never copied over; `voice_vad.py`
  still says "v5".
- Check `nvpmodel -q`: Orin NX 16 GB gains +11–14% tokens/s in MAXN SUPER if it is not
  already there. The one controlled small-model study found the 25 W-class mode beats MAXN
  on joules per token.
- Check `nvpmodel -q`: Orin NX 16 GB gains +11–14% tokens/s in MAXN SUPER if it is not
  already there. The one controlled small-model study found the 25 W-class mode beats MAXN
  on joules per token.

## 6. Dependency currency (host truth vs declared)

`requirements.txt` was reconciled to the box on 2026-08-06, then #1638 bumped pins on
2026-08-09 without installing them — violating the file's own "box first, file second"
rule. Drift today: uvicorn 0.34.0 (file 0.49.0), websockets 14.1 (16.1.1), aiortc 1.14.0
(1.15.0), av 16.1.0 (17.1.0), ag-ui-protocol 0.1.14 (0.1.19), python-json-logger **not
installed** (optional import, falls back). `validate.yml` pins av/aiortc to the box's
versions, `requirements.txt` to the newer ones. `deploy.yml` installs only 9 packages; 30
in `requirements.txt` are never installed by any automation. Decide the contract: either
deploy runs `pip install -r` (voice-gated) or the header says plainly that the box is
hand-managed and the file is a snapshot.

Deliberate holds that still hold (reason re-checked 2026-09-25):
- `uvicorn<0.50` — 0.50.0 (2026-07-04) did flip `--ws auto` to `websockets-sansio` and
  deprecated the legacy implementation; 0.51–0.54 changed nothing ws-related. Sansio has
  been the default for ~3 months, so the eventual move (with an explicit `--ws` in the
  unit) is reasonable, but it stays replay-gated.
- `APScheduler 3.10.4` — 3.11 *deprecates* (does not remove) pytz zones, so persisted
  triggers still unpickle while pytz is importable (APT `python3-tz` is), and 3.11.0 added
  `export_jobs()/import_jobs()`, the drain tool the comment says does not exist. 4.x is
  still 4.0.0a6. The move needs `tzlocal>=3` in both workflows and `tzlocal` declared in
  `requirements.txt` (it is not).
- `numpy<2` — concrete blockers are chroma-hnswlib 0.7.6 and the Jetson torch 2.8.0 wheel;
  the `ctranslate2` justification in the comment is stale (not installed).
- `onnxruntime==1.23.2` — last cp310 aarch64 wheel (rewrite the comment).
- `scikit-learn 1.7.2` / `joblib 1.5.3` — router artifact coupling and the 3.10 ceiling.
- `bcrypt<5`, `mempalace 3.3.1`/`chromadb 0.6.3` (one-way migration, §5.5), `redis 5.x`
  (better: delete the dead redis client path; `zoe-redis` was retired in March).
- `passlib` is imported nowhere — remove it from both files. `python-json-logger` is
  imported nowhere — delete the line or install it. `httpx<0.28` in zoe-auth is not
  re-derivable (the pinning commit changed only the pin line; zoe-data already runs 0.28.1)
  — try dropping it.

Safe/valuable bumps once the gates are green: close the six drifts (uvicorn 0.49.0,
websockets 16.1.1, av 17.1.0 + aiortc 1.15.0 together, ag-ui-protocol 0.1.19), pydantic
2.13.5, alembic 1.20.0, PyJWT 2.15.0, pywebpush 2.5.0, livekit 1.1.20, SQLAlchemy 2.0.54;
pin `moonshine-voice` 0.0.62 and `fastembed` 0.8.0 to record reality, then
`moonshine-voice` 0.1.5 (§5.2); `ag-ui-protocol` 1.0.0 with the kiosk null check (§5.8);
Smart Turn v3.2, Silero v6.2 (file copy); `livekit-agents` 1.8.3; Flue 2.1.1 (§5.6) with
`hono`/`nanoid` security updates; codebase-memory 0.11.0; SkillSpector 2.12; Docker:
cloudflared (running 2025.10.1 vs 2026.9.3 — `:latest` never re-pulls on `up`; pin by
digest), `pgvector:pg17` for 17.11 (stay on 17), HA one monthly release at a time after the
tool-name sweep, Music Assistant 2.10.4 via the two-stage probe, Multica after reading its
changelog against the executor. The Pi daemon's `pi-requirements.txt` is all floating
minimums (`silero-vad>=5.1` now resolves to v6): freeze a `pip freeze` from the live Pi.

Big-ticket, sequenced: (1) RAM plan → (2) llama.cpp rebuild + FA → (3) Chroma HNSW rebuild
→ (4) zoe-data on a Python 3.12 venv (§5.9) → (5) MemPalace 3.10 + Chroma 1.5.x migration
on a copy, on that venv → (6) APScheduler 3.11.3 via export/import with pytz present →
(7) HA 2026.9 after the tool-name sweep → (8) Omnigent 0.15 + pi 0.87.1 (pi ≥0.84.2 is
Omnigent's new floor; the dormant `zoe-core` lane must be ported to the 0.84 RPC contract
or retired by removal first) → (9) JetPack 7.2.x reflash only once the J401 BSP and Orin
wheel index exist.

## 7. Comparable projects: where Zoe stands, and what to borrow

**Ahead of the field:** nobody in the survey publishes a per-stage latency budget as
complete as Zoe's; the deterministic-scaffold approach to *when* to speak is more advanced
than anything open; consented, local, per-panel voice/face identity has no open peer now
that Limitless was acquired and region-killed; the self-evolution PR harness with replay
gates is unique. NVIDIA's own Orin voice demos use the same Parakeet/Moonshine → Gemma 4 →
Kokoro shape Zoe chose a year ago.

**Behind:** false-interruption recovery (LiveKit pauses and resumes; Zoe cancels),
speculative turn-start (HF speech-to-speech and Pipecat gate the brain call on the first
"complete" verdict and reopen if speech resumes — attacks Zoe's 800 ms endpoint wait),
a candidate-ranking + delivery-policy stage for proactive speech (N.E.K.O, Nomi, Kindroid
all back off when ignored), bi-temporal supersession (Graphiti's overlap rule; mem0's
"keep the richer fact") which is the exact fix for Zoe's distilled-vs-richer dedupe bug,
and a domain-namespaced tool vocabulary (HA just paid for not having one).

Ranked pieces to borrow (effort S/M, all lab-first, all respecting the rocks and the RAM
ceiling):

| # | Piece | From | Effort | Why now |
|---|---|---|---|---|
| 1 | Speculative turn-start with a speculation gate + Smart Turn v3.2 | HF `speech-to-speech` `--speculative_reopen_ms`; Pipecat `speculation_gate.py` | M | hides most of the 800 ms endpoint wait; first sentence ready before confirm |
| 2 | False-interruption pause → 2 s timer → resume if no final transcript | LiveKit `agent_activity.py` | S–M | coughs and "mm" stop killing replies |
| 3 | Bi-temporal supersession + "keep the richer fact" reconciliation at idle | Graphiti `edge_operations.py`; mem0 `prompts.py` | M | fixes the known dedupe bug without deleting anything |
| 4 | Candidate-selection → delivery-gating split + unread backoff for proactive turns | N.E.K.O `proactive_chat/`; Nomi cadence | M | "speaks first" feels chosen, not scheduled |
| 5 | Deterministic dream gating (≥N new facts, ≥H hours, idle ≥M min, cancel on speech) + call budget + 40-line profile cap | Honcho dreaming; Memobase | S | the 44 zero-effect digests show the loop needs gates it can explain |
| 6 | Unmute's interruption policy: text-confirmed interrupt immediately, VAD-only needs 3 s of TTS elapsed; 7 s silence marker so Zoe speaks first | `unmute_handler.py` | S | echo-safe barge-in on the panel speaker |
| 7 | Importance-sum reflection trigger reusing `emotional_moment.intensity`, insights with evidence ids | Generative Agents `reflect.py` | S | deterministic "when to reflect", no extra LLM call |
| 8 | Speaker-verification margin rule (distance < θ and ≥0.10 over second-best) + sherpa-onnx embedder in shadow | Omi `speaker_match.py` | S | cheap fix for the two-cluster enrolment trap |
| 9 | `ask_question(answers[{id, sentences}])` primitive with GBNF-constrained recognition | HA `assist_satellite` + speech-to-phrase | M | consent prompts and menus never touch the 4B |
| 10 | Emotion → bounded, auditable prompt modifiers under immutable rules | GLaDOS `constitution.py` | M | turns the emotional-moment tracker into tone without touching rules |
| 11 | Persona/human blocks with `limit`, `read_only`, idle-writer-only, review-before-apply | Letta memory blocks + sleep-time | S | the contract for "Zoe's own thread" |
| 12 | Read-time Ebbinghaus strength + `pinned` / `suppress_proactive` per fact | MemoryBank; MemPalace 3.10; ChatGPT "don't mention this again" | S | proactive recall samples by strength instead of always the same top-3 |
| 13 | `ACTIVITY_SNAPSHOT/DELTA` + fixed A2UI-style component catalog for brain-built cards | AG-UI 1.0; A2UI | M | the generative-tiles plan gets a standard carrier |
| 14 | Domain-prefixed tool names (`ha__`, `ma__`, `memory__`) | HA 2026.9 | S | prevents the collision class HA just hit; do it with the HA sweep |
| 15 | Speaker-gated wake word (openWakeWord custom verifier) or a purpose-trained "Hey Zoe" | openWakeWord docs; 2026 trainers | S | fewer false wakes; less wake-bleed for Moonshine to strip |
| 16 | Sleep-time pre-computation of tomorrow's brief and 3 likely asks per person | Letta paper | S | zero RAM; uses the idle brain; prefix-cache friendly |

Explicitly **not** worth adopting: any speech-to-speech model (all replace the brain rock and
need >8 GB), Pipecat as a framework (re-open triggers are now mostly met — native Moonshine
exists, numpy-2 only bites via its Kokoro extra — but RAM is still unmeasured and the
pieces above can be lifted without the framework), Graphiti/Neo4j, MemOS/MIRIX, Second-Me,
TEN Turn Detection (a 7B model), vLLM on Orin (no MTP).

## 8. Known-problems register and doc drift

The full register (≈90 items across voice, brain, memory, router, identity, UI, music,
Telegram, harness, CI, deploy, deps, security) with file:line sources and unblock steps is
appended as **Appendix A**. Headline pattern: **the box gates everything.** Of the 15
highest-priority items, 9 are downstream of free RAM. Two other clusters: operator
decisions that were waiting when work stopped (face-ID policy, Multica lane collision in
PR #1641, desktop-overhaul decisions 2–7, panel-as-speaker options), and docs that now
disagree with the box (16 contradictions listed in Appendix B — the worst being flags
documented dark but live, and 1.x brain paths in runbooks).

Open PRs: #1641 (multica autonomy program, docs-only, two unresolved Codex threads, size
check red) and #1610 (web-search spike, 62 files, now conflicting). Open issues: #1630,
#1609, #1608, #1607 (condition met — close), #1605, #1588, #863 (already fixed — close),
#715.

## 9. Recommended sequence

**This week (no code, ~1 hour at the box; items marked ✓ were done on 2026-09-25):**
1. Rotate the Telegram token (BotFather) and `sudo journalctl --rotate && sudo journalctl
   --vacuum-time=1s` to drop the lines that carry the old one. ✓ journald is persistent.
2. Physically check the Pi; bring the panel back; verify `ZOE_VAD_TAIL_MS=640` in the
   daemon env.
3. ✓ openclaw-gateway + keepwarm disabled; ✓ health/watchdog scripts fixed; **still to do:**
   `systemctl --user restart zoe-data` (loads the installed package set; poll `/health`),
   then apply the router swap-guard template (`cp` + `daemon-reload` + restart).
4. Maintenance window (brain + Kokoro stopped, §3): shrink zram (B0.1 in the program
   tracker); restart the stack; confirm `free -m` clears ≥1 GB. ✓ The HNSW rebuild is
   DONE (22:26, `/health` recall `ok`) and ✓ the replay gate has passed (22:39); do not
   rebuild again unless `/health` reports `memory_capture` degraded. Still watch that
   the nightly digest does work tonight (M3 in the register).
5. Reconnect YouTube Music (panel Sources → Reconnect), restart zoe-data.
6. Turn `ZOE_MUSIC_DISCOVERY` off until it can run; decide `ZOE_FACE_ID_ENABLED`.
7. Fix the Multica 401; renew Omnigent's login before 2026-10-11; set Copilot review to
   Lite; allow-list the two `pull_request_target` workflows; enable Dependabot alerts.

**Next two weeks (code, one workstream per PR, voice-gated where marked):**
8. llama.cpp rebuild + FA + q8 V-cache (voice-gated).
9. Flip `zoe_flue_client` defaults to `:3579`/wire-2 and enable `ZOE_BRAIN_FAILOVER`
   (voice-gated; today a sidecar blip cans every turn and a fresh box is unbootable without
   the `.env`).
10. ✓ Backup script fixed. Still: log rotation for `~/.zoe-logs/zoe-data.stdout.log`;
    deploy pip contract decision; docs sweep for the 16 contradictions; close stale
    issues (#863, #1607); rebase-or-close #1610; resolve #1641.
10b. **Retire `labs/flue-zoe-telegram/` (the 1.x lab) by removing it** in its own small PR
    after this one merges (13 files, ~930 non-lockfile lines; nothing in deploy or the
    voice gate references it; update the `labs/AGENTS.md` index and the three docs that
    still name its path). That single deletion clears 35 of the 52 Dependabot alerts;
    the 7 in `services/zoe-core/package-lock.json` go with the pi 0.84+ port or that
    tree's retirement.
11. `moonshine-voice` 0.1.5 with `keyterms` (voice-gated); Smart Turn 3.2; Silero 6.2.
12. Flue 2.1.1 in the brain sidecar (voice-gated).

**Next quarter:**
13. Kokoro on ONNX Runtime CUDA (RAM) → then the expressive-lane bake-off.
14. MemPalace 3.10 + Chroma 1.5.x migration on a copy, with the bi-temporal supersession
    work (#3 in §7) designed against the new store.
15. Borrow list #1, #2, #4, #5, #6 in that order — each is a lab spike with a replay gate.
16. HA tool-name sweep → HA 2026.9/10 → MA 2.10 client check; codebase-memory 0.11;
    AG-UI 1.0 + the card carrier.
17. Plan the JetPack 7.2.1 / Python 3.12 rebuild window; Multica/Omnigent upgrades after
    the executor design decision (Flue `init()` handles + durable tools).

---

## Appendix A — Known-problems register (2026-09-25)

State key: BLOCKED · AWAITING OPERATOR · IN PROGRESS · DARK-BY-DESIGN · UNVERIFIED ·
LIVE-BROKEN (verified failing on the box today).

### Voice path
| ID | Problem | Source | State | Unblock |
|---|---|---|---|---|
| V1 | Replay gate: no real PASS in 40 nightly runs (skips <700 MB); voice-path changes undeployable | `~/.cache/zoe/voice_regression_last.json`; `deploy.yml:130-162` | LIVE-BROKEN | RAM plan, then `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py --samples 20` |
| V2 | Panel unreachable | ping/ssh | LIVE-BROKEN | physical check |
| V3 | W1.3 `ZOE_LIVEKIT_STREAM_TTS` merged (#1469) but unset; DoD never done | samantha-evolution-plan §7 | DARK / AWAITING OPERATOR | lab flip + gate |
| V4 | W1.4 M3/M4 latency+RAM measurements never taken | same | stalled | same window |
| V5 | W1.2b acceptance of `ZOE_VAD_TAIL_MS=640` (ear-check + nightly PASS) | IDEAS.md:57 | AWAITING OPERATOR | V1 |
| V6 | #1630 fast-path acks have no Edge/espeak fallback when Kokoro is down | `routers/voice_tts.py:4515,4579,4603` | open, replay-gated | fix + V1 |
| V7 | Baseline sample set swapped; `--update-baseline` decision pending | voice-pipeline.md:376 | AWAITING OPERATOR | after V1 |
| V8 | `ZOE_EXPRESSIVE_TTS=1` enabled 07-28 with a required post-deploy gate run never recorded | PLANS.md:122 | UNVERIFIED | V1 |
| V10 | `GET /api/voice/livekit-token` unauthenticated on LAN | livekit-key-rotation.md:238 | SECURITY | add auth dep |
| V11 | LiveKit keyless-config: `--force-recreate livekit` after merge unrecorded | same:9-40 | UNVERIFIED | recreate + test Talk |
| V12 | Runner-launched tests write `db_pool not initialised` errors into the prod app log | app log 09-04 | hygiene | separate log path |
| V13 | Brain tool-selection 14% flake; blocked on a reproducer | brain-tool-selection-reliability.md | BLOCKED | ≥2 GB quiet window; dropping is legitimate |

### Brain lane
| ID | Problem | Source | State | Unblock |
|---|---|---|---|---|
| B1 | `zoe_flue_client.py` defaults to `:3578` + wire 1 (retired); only `.env` makes it work | `zoe_flue_client.py:19-24,155,224` | IN PROGRESS | voice-gated PR |
| B2 | `ZOE_BRAIN_FAILOVER` unset: sidecar down = every turn canned | CANONICAL.md:70-78 | AWAITING OPERATOR | flip after V1 |
| B3 | Flue parity re-run needs an authenticated test user | PLANS.md:85 | BLOCKED | provision user |
| B4 | Stale 1.x refs in runbooks/env.example/scripts; stale `flue-zoe-brain.service.d` drop-in; `labs/flue-zoe-telegram` (1.x) on disk | memory-pressure-profile.md:354-483 etc. | DOCS DRIFT | sweep |
| B5 | oh-my-pi mined pieces unscheduled | IDEAS.md:70 | PARKED | assign |
| B6 | Executor on Flue 1.x Workflows (removed in 2.0) — design decision | IDEAS.md:68 | BLOCKED (decision) | rebuild on `init()` + durable tools |
| B7 | `expert_dispatch` split-brain: honour `db_memory_context` before rerouting | perf-hardening-plan 3c | open | replay-gated PR |

### Memory
| ID | Problem | Source | State | Unblock |
|---|---|---|---|---|
| M1 | Chroma HNSW corruption; recall degraded since 09-03 | `/health`; app log | LIVE-BROKEN | rebuild (§2.2) |
| M2 | `mempalace_drawers` 96% dead rows | memory-export.log | AWAITING OPERATOR | compaction in the same window |
| M3 | `memory_digest` 44 zero-effect nightly runs | app log 03:00 | LIVE-BROKEN | diagnose after M1 |
| M4 | db-pool exhaustion bursts (09-13, 09-25) coincide with load spikes | app log | RECURRING | correlate |
| M5 | Graph recall boost enablement contested/unverified; migration 0015 missing = silent no-boost | IDEAS.md:40-44 | UNVERIFIED | functional probe + eval |
| M6 | gbrain Part A linkage hygiene not done | gbrain doc:186 | open | build |
| M7 | Two strict-xfail engine gaps (correction not superseded; paraphrase accumulation) | `tests/samantha_live/test_live_dedup.py:97,145` | KNOWN BUG | §7 #3 |
| M8 | 0 real `emotional_moment` rows for jason | buildplan:124 | UNVERIFIED | watch |
| M9 | Consolidation dedup prod-enable on hold | memory note | ON HOLD | §7 #3 |
| M10 | Relationship writes increment-4 gating open; `people_relate` alias | tech-debt:171 | open | small PR |
| M11 | Birthday-capture and LLM-confidence-gate flags dark | flag-enable docs | DARK | after V1 |
| M12 | Proactive mid-turn contact card unbuilt | contacts-people-memory.md:119 | open | replay-gated |
| M13 | Hindsight admission worker inert | zoe-memory-admission-gates.md:53 | DARK | wire |

### Deploy and ops
| ID | Problem | Source | State | Unblock |
|---|---|---|---|---|
| D1 | Structural memory starvation (root of V1, D2, D3, Mu1, R1, M2) | `free -m`; samantha W3 | BLOCKED | §3 |
| D2 | Self-hosted tests flatlined 21 runs | Actions | LIVE-BROKEN | D1 |
| D3 | Deploy gates: 250 MB main, voice gate needs <24 h artifact; 2 of last 3 deploys failed | deploy.yml | RISK | D1 |
| D4 | deploy.yml never restarts executor/kokoro/router/llama-server | deploy.yml | KNOWN GAP | doctrine or extend |
| D6 | `openclaw-gateway` active despite retirement; Hermes health/watchdog/keepwarm noise | systemctl | AWAITING OPERATOR | disable; edit scripts |
| D7 | Root-owned paths in the live checkout (`.pi/`, `.polly/`, `.worktrees/`, `scripts/n8n`) | find | RISK | chown |
| D8 | Untracked module/lab leftovers on the live disk | ls | hygiene | rm |
| D9 | `ZOE_MULTICA_POLL_REF_TIMEOUT_S=300` band-aid | migration doc:190 | AWAITING OPERATOR | revert to 60 |
| D10 | Multica images ~60 releases behind; `:latest` tags elsewhere | compose | open | pin/upgrade |
| D11 | Extra Serenas from the Omnigent container's `/root/.codex/config.toml` (stdio) | pgrep | RECURRING | switch to URL |
| D12 | `zoe-omnigent-runner-reaper.timer` inactive | systemctl | UNVERIFIED | enable |
| D13 | #1609 follow-ups (deploy attribution, MA restart on deploy) | issue | open | small PR + decision |
| D14 | Backup: live-SQLite tar race; pg step skipped on failure | journal 09-25 | DEGRADED | snapshot + `ExecStart=-` |
| D15 | Volatile journald; 445 MB unrotated stdout log | fs | hygiene | persist + logrotate |
| D16 | `functiongemma-router` swap guard template never applied | systemctl show | DEGRADED | cp + reload + restart |

### Telegram
| ID | Problem | State | Unblock |
|---|---|---|---|
| T1 | Bot down (Happy-Eyeballs timeout) | **FIXED today** (drop-in + template) | rotate token; watchdog backoff |
| T2 | Token in journald | SECURITY | rotate + vacuum |
| T3 | Crash-loop watcher env retarget to `-2x` unverified | UNVERIFIED | check |
| T4 | W8 voice notes not started | BLOCKED (D1) | — |

### Router, identity, UI, music, harness, CI, deps, security
| ID | Problem | State | Unblock |
|---|---|---|---|
| R1 | Self-train ratchet rejected the only candidate (6 examples); flag OFF | BLOCKED (D1 + data) | re-mine, dry-run in ≥1.6 GB window |
| R2 | Permanent corpus harness for `route_two_stage()` not found landed | UNVERIFIED | confirm |
| P1 | `ZOE_FACE_ID_ENABLED=true` vs retention policy | CONTRADICTION | decide |
| P2 | W5 speaker-ID shadow week never run | AWAITING OPERATOR | Pi back + enrol |
| P3 | Face enrollment/deletion UX front door unbuilt | open | build to spec |
| P4 | Phase 2.5/3 presence + confidence model; no presence sensors exist | NOT STARTED | hardware |
| P5 | Face deletes take up to 1 h to reach panels | open | push-on-delete |
| U1 | Ask-card conversation PR-1a/1b/1c not started; `voice.html` retirement blocked | NOT STARTED | build (replay-gated) |
| U2 | `ZOE_COMPOSE_UI=1` live vs "flag-dark" doc | CONTRADICTION | confirm |
| U3 | 4 card producers → one contract; CSS/z-index sprawl | stalled | — |
| U4 | Loopback daemons blocked by CSP — decision | AWAITING DECISION | — |
| W1 | Desktop overhaul waves not started (24 P1s incl. 8 XSS areas) | NOT STARTED | Wave 0/1 |
| W2 | Desktop decisions 2–7 | AWAITING OPERATOR | decide |
| Mu1 | `ZOE_MUSIC_DISCOVERY=on` but every run exits 2 | LIVE-BROKEN (silent) | D1 or turn off |
| Mu2 | Spoken "found N albums" hook | open | — |
| Mu3 | YT Music provider dead since ≥08-10 | LIVE-BROKEN | reconnect flow |
| Mu4 | Panel-as-MA-speaker options | AWAITING OPERATOR | decide |
| Mu5 | #1607 condition met | close | — |
| H1 | Multica dispatch paused; executor unit inactive; local workers unbuilt | BLOCKED (go-live) | enable, dispatch full, unpause |
| H2 | Multica API 401 / autopilot health fails daily | LIVE-BROKEN | token |
| H3 | PR #1641: 2 unresolved threads, size red, lane-collision decision | AWAITING OPERATOR | decide |
| H4 | Hermes retirement gates unticked; 592 runtime refs | BLOCKED | H1 first |
| H5 | OpenClaw: router still mounted (`main.py:52,2169`), trigger wired (`main.py:1260`) | IN PROGRESS | gated deletion PRs |
| H6 | TT2 + #1605 fail-closed P2s | BLOCKED on TT2 | build |
| H7 | Omnigent OAuth valid to 2026-10-11; docs say 08-18 | STALE DOC | calendar |
| H8 | omp builder adoption needs a capped OpenRouter key | AWAITING OPERATOR | key |
| H9 | PR #1610 conflicting, stale | BLOCKED | rebase or close |
| H11 | #715 zoe-auth OIDC issuer hard-coded to LAN host | open | public issuer |
| H12 | #1608 brain install ignores lockfile | open | design |
| C1 | Dead module-skipped tests (`test_auth_security.py`, `test_experts.py`) | DEAD TESTS | fix or delete |
| C2 | integration/e2e only on the (broken) self-hosted lane | GAP | D2 |
| C4 | `validate.yml` vs `requirements.txt` pin mismatch (av/aiortc/httpx) | DRIFT | reconcile |
| C6/C7 | #1588 (fold into next AGENTS.md PR); #863 (already fixed, close) | trivial | — |
| Dp1 | deploy installs 9 of 39 packages; file drifted ahead of the box on 6 | KNOWN GAP | decide contract |
| Dp2 | numpy-2 migration leftovers (ctranslate2, faster-whisper, kokoro-onnx installed but dead) | AWAITING OPERATOR | uninstall + pin |
| Dp3 | Flag inventory misses 4 code-read flags; `.env` carries 14 dead names | DRIFT | regen + prune |
| S1–S8 | see §4 | — | — |

## Appendix B — Doc contradictions (two sources disagreeing about live state)

1. Face-ID: policy doc says dark until a delete UI exists; live `.env` has it on.
2. Generative tiles: PLANS says flag-dark pending lab-proof; live `ZOE_COMPOSE_UI=1`.
3. Music discovery: PLANS says flip after a verified manual run; live on, every run fails.
4. Contacts flags: `relationship-memory-flag-enable.md` says merged dark/blocked; live = 1.
5. Intent-dispatch token gate: tech-debt plan and 07-06 handoff say DARK; live = 1.
6. Graph recall boost: enable runbook says live 07-22; gbrain doc says never before the
   gate cleared (07-27).
7. OpenClaw: AGENTS.md/migration doc say fully retired; unit was active, router mounted.
8. Multica: "works, paused, retire by recreation" vs the heading "retire Multica/Hermes/
   OpenClaw" in the same PLANS section.
9. 1.x brain: CANONICAL says source-removed; `labs/flue-zoe-brain/` still on the live disk
   (untracked) and `memory-pressure-profile.md` still targets it.
10. Swap: perf-hardening-plan calls the 3.6 GB figure stale (156 MiB then); today 5.2 GB.
11. Omnigent OAuth: docs say hard deadline 08-18; live refresh valid to 10-11.
12. av/aiortc: `validate.yml` "pinned to what the Jetson runs" vs `requirements.txt`.
13. wyoming-piper: `HARDWARE_COMPATIBILITY.md` lists it active; CANONICAL retired it.
14. Flag names: plan says `ZOE_SMART_TURN`, code `ZOE_SMART_TURN_ENABLED`; packets say
    `ZOE_SPEAKER_ID_SHADOW`, Pi daemon `SPEAKER_ID_SHADOW`.
15. `tests/conftest.py` said zoe-core lives in docs/archive (fixed in this branch).
16. PLANS.md:117 says the 2.x telegram cutover is still "execution remaining"; :118 and
    `runtime-topology.md` say done 08-09/10.

## Appendix D — Proposed `.github/dependabot.yml` (not committed; alerts are enabled)

Advisory only, grouped, monthly, with `ignore:` rules for every deliberate pin, and a low
PR limit because each Dependabot PR costs a review round and cannot be opened as a draft.

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: /services/zoe-data
    schedule: {interval: monthly}
    open-pull-requests-limit: 2
    groups: {zoe-data-safe: {patterns: ["*"], update-types: [minor, patch]}}
    labels: [deps, box-first]
    ignore:
      - {dependency-name: uvicorn, versions: [">=0.50"]}        # websockets-sansio flip, replay-gated
      - {dependency-name: websockets, versions: [">=17"]}       # needs py>=3.11
      - {dependency-name: av, versions: [">=18"]}               # needs py>=3.11
      - {dependency-name: aiortc, update-types: [version-update:semver-minor]}  # moves with av
      - {dependency-name: chromadb}                             # one-way palace migration
      - {dependency-name: mempalace}                            # moves with chromadb
      - {dependency-name: numpy, versions: [">=2"]}
      - {dependency-name: onnxruntime}                          # 1.23.2 = last cp310 aarch64
      - {dependency-name: APScheduler}                          # pytz/jobstore hazard
      - {dependency-name: scikit-learn}                         # router artifact
      - {dependency-name: joblib}
      - {dependency-name: transformers, update-types: [version-update:semver-major]}
      - {dependency-name: moonshine-voice}                      # STT rock, replay-gated
  - package-ecosystem: pip
    directory: /services/zoe-auth
    schedule: {interval: monthly}
    groups: {zoe-auth: {patterns: ["*"], update-types: [minor, patch]}}
    ignore:
      - {dependency-name: bcrypt, versions: [">=5"]}
      - {dependency-name: redis, versions: [">=6"]}
  - package-ecosystem: pip
    directory: /services/homeassistant-mcp-bridge
    schedule: {interval: monthly}
  - package-ecosystem: npm
    directory: /labs/flue-zoe-brain-2x
    schedule: {interval: monthly}
    groups: {flue: {patterns: ["@flue/*"]}, tooling: {patterns: [vite, typescript, "@types/*"]}}
    ignore:
      - {dependency-name: "@earendil-works/pi-ai", update-types: [version-update:semver-minor]}  # 0.84/0.86 breaking
      - {dependency-name: typescript, versions: [">=6"]}
  - package-ecosystem: npm
    directory: /labs/flue-zoe-telegram-2x
    schedule: {interval: monthly}
    groups: {flue: {patterns: ["@flue/*"]}}
  - package-ecosystem: docker-compose
    directory: /
    schedule: {interval: monthly}
  - package-ecosystem: docker
    directory: /modules/omnigent
    schedule: {interval: monthly}
  - package-ecosystem: github-actions
    directory: /
    schedule: {interval: monthly}
```

## Appendix C — Primary sources (selection)

llama.cpp FA/MTP fix: https://github.com/ggml-org/llama.cpp/pull/25148 · MTP CUDA graph:
https://github.com/ggml-org/llama.cpp/pull/28549 · Unsloth Gemma 4 QAT:
https://unsloth.ai/docs/models/gemma-4/qat · moonshine-voice changelog:
https://github.com/moonshine-ai/moonshine/blob/main/CHANGELOGS.md · kokoro-onnx:
https://github.com/thewh1teagle/kokoro-onnx · jetson-containers kokoro-tts-onnx:
https://github.com/dusty-nv/jetson-containers/tree/master/packages/speech/kokoro-tts ·
Jetson RAM guidance: https://www.jetson-ai-lab.com/tutorials/ram-optimization/ · JetPack
7.2: https://developer.nvidia.com/embedded/jetpack · Smart Turn:
https://github.com/pipecat-ai/smart-turn · Silero VAD:
https://github.com/snakers4/silero-vad/releases · LiveKit turn detector:
https://docs.livekit.io/agents/build/turns/turn-detector/ · MemPalace changelog:
https://github.com/MemPalace/mempalace/blob/develop/CHANGELOG.md · MemPalace PyPI
(chromadb constraint): https://pypi.org/pypi/mempalace/json · Flue changelog:
https://github.com/withastro/flue/blob/main/packages/runtime/CHANGELOG.md · Flue
migration (Workflows): https://github.com/withastro/flue/blob/main/apps/docs/src/content/docs/guide/migration.md
· Pi changelog: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/CHANGELOG.md
· MCP 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/changelog ·
Serena #2088: https://github.com/oraios/serena/issues/2088 · Serena #2063:
https://github.com/oraios/serena/issues/2063 · codebase-memory releases:
https://github.com/DeusData/codebase-memory-mcp/releases · AG-UI 1.0:
https://github.com/ag-ui-protocol/ag-ui/releases/tag/release/2026-09-17 and
https://docs.ag-ui.com/migrating-to-1-0.md · A2UI: https://github.com/google/A2UI · HA
2026.9 tool prefixes: https://github.com/home-assistant/core/pull/179938 · HA 2026.8:
https://www.home-assistant.io/blog/2026/08/05/release-20268/ · Music Assistant releases:
https://github.com/music-assistant/server/releases · YT Music auth:
https://www.music-assistant.io/music-providers/youtube-music/ · Claude Code changelog:
https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md · Claude Code weekly
limits: https://support.claude.com/en/articles/15910845-claude-code-may-august-2026-weekly-limits-promotion
· Claude auth policy: https://code.claude.com/docs/en/legal-and-compliance · Codex releases:
https://github.com/openai/codex/releases · Omnigent: https://pypi.org/pypi/omnigent/json ·
Multica releases: https://github.com/multica-ai/multica/releases · GitHub workflow
execution protections: https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/
· Actions retention: https://github.blog/changelog/2026-08-27-actions-retention-will-cover-checks-workflow-runs-and-statuses/
· Copilot billing: https://github.blog/changelog/2026-08-28-upcoming-changes-to-github-copilot-policies-and-billing/
· Copilot code review pricing: https://docs.github.com/en/copilot/concepts/agents/code-review
· Greptile pricing: https://www.greptile.com/pricing · CodeRabbit pricing:
https://www.coderabbit.ai/pricing · SkillSpector: https://github.com/NVIDIA/SkillSpector ·
HF speech-to-speech speculative reopen: https://github.com/huggingface/speech-to-speech ·
Pipecat speculation gate: https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/turns/speculation_gate.py
· LiveKit false-interruption resume: https://raw.githubusercontent.com/livekit/agents/main/livekit-agents/livekit/agents/voice/agent_activity.py
· Unmute handler: https://raw.githubusercontent.com/kyutai-labs/unmute/main/unmute/unmute_handler.py
· GLaDOS constitution: https://github.com/dnhkng/GLaDOS/blob/main/src/glados/autonomy/constitution.py
· Omi speaker match: https://github.com/BasedHardware/omi/blob/main/backend/utils/stt/speaker_match.py
· Omi memory architecture: https://github.com/BasedHardware/omi/blob/main/backend/utils/memory/ARCHITECTURE.md
· Graphiti edge invalidation: https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/utils/maintenance/edge_operations.py
· mem0 prompts: https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/configs/prompts.py
· Honcho dreaming: https://honcho.dev/docs/v3/documentation/features/advanced/dreaming.md ·
Letta sleep-time compute: https://arxiv.org/abs/2504.13171 · Generative Agents reflect:
https://raw.githubusercontent.com/joonspk-research/generative_agents/main/reverie/backend_server/persona/cognitive_modules/reflect.py
· N.E.K.O proactive chat: https://github.com/Project-N-E-K-O/N.E.K.O/tree/main/main_logic/proactive_chat
· When2Talk: https://arxiv.org/abs/2609.12503 · PASK: https://arxiv.org/html/2604.08000v1 ·
openWakeWord custom verifier: https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md
· HA assist_satellite ask_question: https://www.home-assistant.io/integrations/assist_satellite/
