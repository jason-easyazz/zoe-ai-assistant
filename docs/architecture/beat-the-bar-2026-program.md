---
type: program-plan
date: 2026-09-25
audience: Jason + every agent — the single tracker for "Zoe better than anything else"
status: 🔨 active — NEXT ACTION is always §0
---
# Beat the 2026 bar — the program (tracker)

> **Why this exists.** Apple's home device (Siri AI + a 7-inch home hub, expected
> October 2026), Gemini for Home, Alexa+, Meta Muse and OpenAI's GPT-Live set the 2026
> bar for a home companion. The research behind this plan is in
> [`docs/knowledge/state-of-zoe-review-2026-09-25.md`](../knowledge/state-of-zoe-review-2026-09-25.md)
> (§5, §7 and the survey appendices). This file is the **tracker**: one place where every
> item has a state, an owner, a gate, and where the next action is unambiguous. Update
> it when a step lands (same PR). The rocks are fixed (Gemma 4 E4B+MTP, Moonshine,
> Kokoro); everything here optimises around them. RAM is the ceiling on every line.
>
> **State key:** ⬜ not started · 🔨 in progress · ✅ done · ⛔ blocked (says on what) ·
> 🧑 operator step (needs Jason or root on the box)

## 0. NEXT ACTION (keep this current)

1. 🧑 **Root / operator window on the box (one sitting, ~30 min):** shrink zram (B0.1);
   rotate the Telegram token + vacuum the journal (B0.3); rotate the Postgres password
   (B0.14); set `ZOE_DEFAULT_MEDIA_PLAYER` to the real entity and turn `ZOE_MUSIC_DISCOVERY`
   off (§4b); **swap in the verified Gemma re-upload** (B6.2 — tensors are byte-identical,
   only the chat template changed; staged + checksummed, the swap itself was refused to the
   agent as a production deploy) and restart `llama-server`, then run one replay gate; move
   the ignored leftover `modules/zoe-music/` (root-owned `__pycache__`, retired in #1653) out
   of the live checkout — it makes `test_no_zoe_music_module` red locally while CI is green.
2. **Queue state (2026-09-26 pm):** MERGED today — #1695 (B0.12 pt 1), #1698 (B0.6 deps
   contract), #1696 (B1.10 hold + keyterms plumbing), #1693 (B9.1 Omi lab). Still queued:
   #1691 (B10.0) and #1692 (B3.1 lab), each with ~10–30 review threads closed by
   failing-test-first fixes. #1694 (B1.11) parked — see B1.11. Voice-scope PRs need a
   head-bound probe after EVERY `update-branch` (strict mode); the Kokoro-paused window
   (`systemctl --user stop kokoro-tts` → probe with `--service-dir` → start → verify
   `curl http://localhost:10201/health` shows `pipeline_loaded: true` AND `device: cuda`, and `/readyz`
   `dependencies.tts` names the `kokoro-sidecar` provider — `tts.ok` alone is not enough,
   it also goes green on the espeak/edge fallback or on a CPU-mode Kokoro) frees ~2 GB and is
   what made today's runs possible under the 700 MB floor. Hold the
   other PRs (drop `auto-merge`) while a voice PR lands, or it goes behind again.
3. **B0.4 llama.cpp rebuild** (brain-stop window, ~1 h compile) — after the Gemma swap has
   its own replay gate, so the two changes are attributable separately.

## 1. Where Zoe already beats the bar (protect these)

| Capability | Zoe | The bar | Guard |
|---|---|---|---|
| Fully local, offline-capable, nothing leaves the house | ✅ | Apple: personal context on-device but reasoning may go to PCC/Gemini; Google/Amazon: cloud | `test_canonical_invariants.py`; replay gate |
| Per-panel voice + face identity, consented, local | ✅ (flags) | Amazon Omnisense (cloud); Apple: single-user Siri | biometric retention policy |
| Speaks first (spoken morning brief, presence-gated) | ✅ | Gemini Daily Brief is text; Alexa+ nudges | W2 record |
| Per-stage latency budget + said-vs-did replay gate | ✅ | nobody publishes one | `voice_regression_probe.py` |
| Self-evolution harness (edits her own code behind a PR gate) | ✅ (paused) | none | Multica/Flue executor |
| HA + Music Assistant as hidden organs | ✅ | Gemini for Home needs a $10/mo sub | — |

## 2. The 2026 checklist (what a home companion must do) and Zoe's gap

| # | Bar (source) | Zoe today | Gap → program item |
|---|---|---|---|
| 1 | Full-duplex-feeling turn-taking with backchannels; interruptions counted (GPT-Live "80% fewer interruptions", Sesame Turnbench) | barge-in + Smart Turn v3, cancels on false interruptions | B1.1–B1.4 |
| 2 | Sub-second first audio; speak while tools run | ~1 s local; tools block | B1.1, B1.5 |
| 3 | Memory the user can see, edit, regenerate, export (Claude topics, ChatGPT Dreaming, Siri Recap) | Postgres+Chroma, no user surface | B3.4 |
| 4 | Summaries, not transcripts (Siri Recap) | transcripts kept | B3.5 |
| 5 | Actionable daily digest, morning **and evening** (Daily Brief, Home Brief) | morning brief | B2.3 |
| 6 | Standing "watch and tell me" agents (Spark, Copilot Autopilot) | none | B2.4 |
| 7 | Presence-triggered routines: who is here → what happens (Omnisense) | ID exists; not wired to proactivity | B2.1 |
| 8 | Conversation state across days and devices (Alexa+, Siri app) | per-session | B3.6 |
| 9 | Narrated home events + voice-authored automations (Gemini for Home) | HA control only | B2.3, B7.3 |
| 10 | Approval-before-action; connectors off by default | yes (flags) | keep |
| 11 | On-device model with audio input (Gemma 4 E4B, AFM 3) | E4B text; audio unused | B5.5 |
| 12 | "Ask about what you see" (glasses, panel camera) | face-ID only | B7.4 |
| 13 | Cheap speech on every endpoint (Pocket TTS on CPU) | Orin-only TTS | B5.4 |
| 14 | A named, consistent persona; safety-gated companionship | Zoe persona; demo-users guardrail | B3.7 |
| 15 | Household face recognition on a home display (Apple's ~7-inch Siri display, October 2026 per Gurman 2026-09-21) | per-panel face-ID behind flags, no enroll/delete UI | B4.3 → B4.1/B4.2; keep ahead of B7.4 |

## 3. Program items

### B0 — Platform floor (everything else waits on this)
- B0.1 🧑 **zram shrink** (root; with llama-server + kokoro-tts stopped): for each of
  `/dev/zram0..7`: `swapoff`, then **`zramctl --reset /dev/zramN`** (a `disksize` write on an
  initialised device fails EBUSY), then `echo $((244*1024*1024)) > /sys/block/zramN/disksize`,
  `mkswap`, `swapon -p 5`; then change `/ 2 /` → `/ 8 /` in `/etc/systemd/nvzramconfig.sh` so it
  survives a reboot; `echo 3 > /proc/sys/vm/drop_caches` before starting the brain. Gate:
  `free -m` available ≥ 2 GB idle; the next nightly replay gate PASS.
- B0.2 ✅ 2026-09-25 (all verified on the box, evidence in the review §1): Telegram
  Happy-Eyeballs fix; openclaw-gateway + Hermes keep-warm off; health/watchdog scripts fixed;
  router swap guard applied; zoe-data lean restart; HNSW rebuild (recall `ok`); backup script
  SQLite snapshot; journald persistent; daily log rotation + log dir 750/640; docker prune
  12 GB; Dependabot alerts on; security pip set (anyio critical etc.) + 6 drifted pins;
  Node 22.23.3 on both Flue units; zoe-auth image rebuilt; Omnigent container Codex → shared
  Serena URL; Multica 401 explained; **replay gate PASS 13/13** (first in 41 runs).
  **2026-09-26 batch:** transformers 5.17.0, mcp 1.30.0, pydantic 2.13.5, alembic 1.20.0,
  PyJWT 2.15.0, pywebpush 2.5.0, livekit 1.1.20, ddgs 9.16.0, SQLAlchemy 2.0.54 installed
  (dry-run first; fastembed held at 0.8.0); zoe-data restarted (`/readyz` all ok, no new
  errors); Silero VAD file v6.2.1 in place; `ci_safe` lane 442 passed locally; **replay gate
  PASS #2** (13/13 scoreable, 7 EMPTY as baseline; medians stt 406 / brain 1962 / e2e
  1753 ms). Two pip-declared conflicts are pre-existing and belong to packages zoe-data
  does not import (`livekit-agents` 1.5.10 wants livekit 1.1.8; `memu-py` wants
  httpx<0.28) — candidates for removal in B0.7, not blockers.
  **2026-09-26 pm:** the post-merge deploy of #1695 FAILED — `services/homeassistant-mcp-bridge/tests/`
  in the live checkout was root-owned, git could not create the new test file, and the
  aborted `git reset --hard` left the live tree partially updated (HEAD stayed, 7 files
  moved ahead, no service restarted). Fixed by 🧑 `chown -R zoe:zoe` + deploy re-run (green,
  live clean). The HA bridge is a Docker container with the source bind-mounted and deploy
  does NOT restart it — after a SOURCE-only bridge merge, `docker restart
  homeassistant-mcp-bridge` (`/tools/names` live, scheme `legacy` against HA 2026.5.2); after a
  merge that touches the bridge's `requirements.txt` or Dockerfile, a restart reuses the old
  image's packages — rebuild instead: `docker compose up -d --build homeassistant-mcp-bridge`. Other root-owned paths in the
  checkout are runtime data only (`homeassistant/`, `.pi`, `.polly`, `scripts/n8n`).
  Deploys for #1698 and #1696 then ran green; live = main; drift check 0 MISMATCH.
- B0.14 ✅ 2026-09-26 (audit merged as #1687 — redacted; corrections #1688; the
  un-redacted #1684 was closed and its branch deleted because household data had reached a
  public branch. Fixes in **PR #1689** — rebuilt clean after a fake-password fixture tripped
  GitGuardian's history scan — each with a negative-controlled test, plus the reminder
  same-day fallback, ZOE_TIMEZONE-relative dates, no titles in logs, lenient stored times.
  All five live-verified on the box after the 2026-09-26 restart — people endpoint 200,
  scheduler line redacted, a date-only reminder for a test user fired via the same-day
  fallback with id-only logging, media-player fail-soft, honest capabilities. Remaining 🧑
  steps below. Original list): (1) `proactive/scheduler.py` logs the Postgres password at
  startup — redact, then 🧑 rotate the password per the secret-topology runbook; (2)
  `GET /api/memories/people` 500 (`COLLATE NOCASE` on Postgres) + sweep the class; (3)
  reminders: date-only rows skipped and a literal `"tomorrow"` due date swallowed; (4)
  `ZOE_DEFAULT_MEDIA_PLAYER` names a non-existent HA entity → fail soft + 🧑 set the right id;
  (5) web search advertised to the brain but no tool registered → make the capability honest
  (the tool itself is B10). Also 🧑 set `MEMORY_DIGEST_MODEL` in the live `.env` to the real
  model name (stale `gpt-4o-mini`, harmless, misleading).
- B0.15 ⬜ **DGX Spark evaluation as BUILDER capacity** (Jason is considering one). The brain
  rock (Gemma 4 E4B-QAT + MTP) is fixed and this item does not touch it. What a 128 GB /
  CUDA-13-on-ARM box adds: (a) a local **builder lane** — Pi through Omnigent's `pi` harness
  against a local OpenAI-compatible server (no metered key) running a 30B-class model for fix
  packets and reviews (candidates: Gemma 4 26B-A4B, Gemma 4 31B, Qwen3.6 35B-A3B, Muse
  Glimmer 30B, gpt-oss-120B 4-bit; watch llama.cpp #29168 MoE-fusion acceptance drop); (b)
  **training on-box** that the Orin cannot do (router self-train, tool-calling fine-tunes of
  E4B on Zoe's corpus — optimising *around* the rock); (c) headroom for the voice stack.
  Gate: a written bake-off (tokens/s, RAM, tool-call accuracy on Zoe's corpus) for the
  builder lane only. Any question about the production brain on new hardware is a separate,
  deliberate CANONICAL decision for Jason, out of scope here.
- B0.3 🧑 Telegram token rotation (BotFather) + `journalctl --rotate && --vacuum-time=1s`.
- B0.4 ⬜ **llama.cpp rebuild at b11194** and re-enable `--flash-attn on` +
  `--cache-type-v q8_0` with MTP (upstream fix PR #25148, 2026-06-30). Keep `--fit off`.
  Gate: 20-turn multi-prompt replay under `flock`; RSS/TTFT vs baseline.
  **2026-09-26 (ecosystem-watch §1):** b11194 ≡ b11178 for this build — 16 commits
  b11178→b11194, none touching CUDA arch 87 / FA / MTP / Gemma / jinja (only cpp-httplib
  0.58.0, #29407); source build stays mandatory (prebuilt arm64 asset is CUDA 13.4).
  **#25522 DROPPED** — every repro is multi-GPU `--split-mode tensor`, N/A on one Orin.
  Inherits #28285 (SM87 MMQ crossover, benchmarked on AGX Orin), #29152 (Gemma 4 FA),
  #28549 (CUDA graph for the MTP draft — ggml's `GGML_CUDA_GRAPHS_DEFAULT` is OFF at b11194,
  confirm the built binary has it on). Gate adds: (1) f16-K / q8_0-V is a MIXED pair vs the
  `GGML_CUDA_FA_QUANTS` default (`q4_0-q4_0;q8_0-q8_0;f16-f16;bf16-bf16`) — verify it lands on a
  compiled FA kernel, else build `-DGGML_CUDA_FA_ALL_QUANTS=ON` or run q8_0/q8_0; (2) keep
  `-np 1` (#28286 draft-MTP cross-slot contamination, fix #29454 open); (3) the replay must
  include multi-tool + thinking turns for #28827 (template re-injects `thinking_text` without
  the leading `\n` → trailing garbage; moot while `enable_thinking=false`, UNVERIFIED on E4B);
  (4) budget the separate MTP CUDA compute arena (#27282 / PR #27489, OOM near the limit).
  Watch #29391 / fix #29467 (b11159+: a slot `bad allocation` under prefix reuse aborts the
  whole server — memory-pressure-triggered). Chat template: llama.cpp uses its own
  `common/jinja` (not minja); every construct in the 2026-07-17 re-upload is supported at
  b11194, so `--jinja` renders; #28511 + #29115 (Gemma 4 typed content / `tool_choice`
  grammar) are in.
- B0.5 ⬜ Retire `labs/flue-zoe-telegram/` (1.x) by removal; update `labs/AGENTS.md`;
  clears 35 Dependabot alerts. Small PR after #1680.
- B0.6 ✅ 2026-09-26 **Deploy pip contract decided: the box is hand-managed, BOX FIRST, FILE
  SECOND** (the header of `services/zoe-data/requirements.txt` already said so; kept). The
  file is reconciled to the box after the gated batch (drift check: 0 MISMATCH; only
  `moonshine-voice` unpinned, owned by #1696); `deploy.yml`'s hard-coded list bumped in the
  same pass (alembic 1.20.0, pywebpush 2.5.0 — it would otherwise DOWNGRADE the box on the
  next deploy); the CI lane's av/aiortc aligned to 17.1.0/1.15.0 (x86_64 cp310 wheels
  re-verified); `tzlocal` declared; `passlib` dropped (no consumer); onnxruntime comment
  corrected (1.23.2 is the last cp310 wheel — moves only with B0.7). Not adopted:
  `pip install -r` in deploy — it would make every deploy a 39-package resolve on the live
  box, which is exactly the class of unobserved runtime change the header forbids.
- B0.7 ⬜ **Python 3.12 venv for zoe-data only** (Kokoro + llama-server stay on 3.10/CUDA 12.6);
  CPU torch for Resemblyzer, onnxruntime 1.30, websockets 17, av 18, numpy 2, sklearn 1.9
  (re-export the router head). Gate: full `ci_safe` lanes in the venv + replay gate +
  `memory_recall_probe`. Target: before 2026-10-31 (3.10 EOL).
  **Two-interpreter split (2026-09-26, §10):** system 3.10 = CUDA consumers from `jp6/cu126`
  (cp310-only: torch, onnxruntime-gpu 1.23/1.24; last upload 2026-04-01) — after EOL frozen
  at ORT 1.23.2, numpy 2.2.6, av 17.1.0, sklearn 1.7.2, websockets 16.1.1 (all dropped cp310
  on PyPI); venv 3.12 = zoe-data (ORT 1.30, numpy 2.5, av 18, chromadb 1.5.9 abi3, CPU torch
  2.14). Needs a per-interpreter `requirements.txt` (markers or two files) + a voice-gate
  probe re-baseline pointed at the interpreter that runs STT (the probe never installs
  requirements — see `reference_voice_gate_instrument_facts`).
- B0.8 ⬜ MemPalace 3.10 + Chroma 1.5.x migration **on a copy** (needs B0.7); reconcile row
  counts against `export_memory_store.py`; self-recall probe.
  **2026-09-26 (§11):** target chromadb **1.5.9** (#6953 preserves legacy `hnsw:` keys —
  MemPalace's `hnsw:space=cosine`; cp39-abi3 aarch64, works on 3.10 and 3.12) + mempalace
  **3.10.0**. Run `mempalace migrate` on the copy — it does the copy
  (`<palace>.pre-migrate.<ts>`, `max_backups=10`), reads drawers from `chroma.sqlite3`,
  probes a write round-trip (0.6→1.5 stores can stay readable while writes silently no-op),
  rebuilds + `os.replace`-swaps with rollback, and prints the reconciliation
  (`Drawers migrated: N` / `WARNING: Expected X, got Y`); then cross-check
  `export_memory_store.py` counts + `memory_recall_probe`. Chroma has no 0.6→1.x tool of its
  own (first open migrates; `chroma-migrate` is 0.4-only). 3.10's `get_collection()` rejects
  names other than the drawers collection — audit callers for `_skip_name_check=True`. Fix the
  `requirements.txt` comment (~line 82): the chromadb bound flipped at mempalace **3.4.0**
  (2026-06-06), not 3.6.0 (`migrate.py`'s docstring is wrong the same way).
- B0.9 ⬜ APScheduler 3.11.3 via `export_jobs`/`import_jobs` with pytz present; `tzlocal>=3`
  in both workflows. Gate: reminder + autopilot row counts unchanged.
- B0.10 ⬜ GitHub review-pipeline housekeeping — split into dated sub-items 2026-09-26
  (ecosystem-watch §9); Greptile to Starter unchanged:
  - (a) 🧑 **before 2026-09-28** — Copilot code-review effort = **Lite** (the Default value
    flips to Balanced, ~5× dearer: Lite ≈ $0.05–1 vs Balanced ≈ $0.25–5 of credits per
    review; changelog 2026-08-28). Repo: Settings → Code, planning, and automation → Copilot →
    Code review → "Review effort level" → Lite (not Default). Account: profile → Copilot
    settings → Code review (also the auto-review toggles). `gh pr edit --add-reviewer
    @copilot` passes no effort — which default it takes is UNVERIFIED.
  - (b) 🧑 **before 2026-11-02** — repo Actions **event policy** allowing
    `pull_request_target` for `.github/workflows/voice-gate.yml` +
    `.github/workflows/break-glass.yml`. Measured 2026-09-26:
    `gh api repos/<owner>/<repo>/actions/policies` → `{"total_count":0}` — no policy exists,
    so the default rule (GA changelog 2026-09-17, evaluate mode now) auto-enforces on 11-02
    and both workflows FAIL with `Event 'pull_request_target' is not allowed …` (a failed
    run, not a skip). UI: Settings → Actions → Policies → new rule, type
    `restrict_action_events`, `allowed_events: [pull_request_target]`, `include` the two
    workflow paths, enforcement `evaluate` first → `active` (a community thread says a
    user-created rule enforces immediately, so verify in the Actions tab filtered on
    `event:pull_request_target` before flipping). API: `POST
    /repos/{owner}/{repo}/actions/policies` via `gh api -X POST … --input body.json` (no `gh`
    subcommand; read the REST page for field names); `GET/PUT/DELETE …/policies/{id}`.
  - (c) ⬜ `ggshield-action` v1.53.0 → v1.55.0 in `validate.yml` (1.55.0 2026-09-24; no
    breaking change to `secret scan ci`, exit codes or `.gitguardian.yaml` in 1.53–1.55) +
    🧑 `ggshield install --mode global --force` on the box: ggshield 1.53 fixed the global hook
    skipping repo-local hooks in git worktrees (all Zoe work is in worktrees) and an existing
    global install only picks the fix up on re-install.
  - (d) 🧑 optional — CodeRabbit (free on public repos; non-blocking, no required check;
    drafts skipped by default): install via coderabbit.ai → Login with GitHub → only this
    repo, plus a minimal `.coderabbit.yaml` (`profile: chill`,
    `request_changes_workflow: false`, `auto_review.drafts: false`). Its inline comments are
    review threads and count toward `required_conversation_resolution`.
  - (e) ⬜ optional review-scoped `.github/copilot-instructions.md` — Copilot review ingests
    `AGENTS.md` wholesale (~600 lines billed per review); a short review-only file bounds it.
  - Review pipeline, 2026-09-26: the **Codex code-review quota was exhausted** mid-day, so the
    cross-vendor pass is unavailable until it refills — fallback for the day's PRs is Greptile
    (label) + Copilot; the deterministic gate is unchanged.
- B0.11 🔨 Omnigent: bake the `url=` Serena entry into the image (patched live 2026-09-25 in
  `/root/.codex/config.toml`; a container recreate reverts it); renew the Claude login before
  2026-10-11; move the polly lane off `claude-sdk` OAuth (policy). Draft PR #1700: the file
  lives in the `omnigent-codex` VOLUME with no tracked owner — now a tracked template
  (`modules/omnigent/codex-mcp.toml`) baked into the image and seeded idempotently by
  `entrypoint.sh` on every boot (hooks.state kept); renewal steps + the polly-lane policy note
  recorded in `docs/knowledge/omnigent-container-config.md`. Post-merge: coordinator rebuilds +
  recreates the container (`docker compose ... up -d --build` from `modules/omnigent/`); the
  login renewal and the policy decision remain operator steps.
- B0.12 🔨 HA tool-name sweep (`domain__Tool` prefixes) → HA 2026.9/10 upgrade; adopt the
  MCP `device_id` meta so panel commands resolve to their room. Then MA 2.10 client check.
  Part 1 = draft PR #1695: sweep found NO live call site (bridge is pure REST; HA's
  `mcp_server` is not loaded on 2026.5.2); `ha_tool_names.py` + `GET /tools/names` centralise
  the spelling with `/api/config` version detection; runbook
  `docs/knowledge/ha-2026-9-upgrade-runbook.md`. Part 2 = the stepped upgrade (operator) then
  MCP-server + `device_id` adoption. **Pre-flight (2026-09-26, §6/§7):** latest HA is
  2026.9.3 (09-18; no 2026.10 beta yet); recorder `SCHEMA_VERSION = 53` on 2026.5.2, 2026.9.3
  and `dev` — no DB migration, rollback-safe on the schema axis; `mcp_server` `require_admin`
  (#180713, lands 2026.10) migrates an EXISTING entry to `require_admin: False`, so Zoe keeps
  access until flipped; `device_id` meta → `LLMContext.device_id` is #182057 (09-13). Check
  the `auth_oidc` v1.2.1 kiosk auto-login path before/after (hass-oidc-auth #422, open
  09-15: `trusted_networks` + `allow_bypass_login` tablets land on `/auth/oidc/welcome` — the
  panel's path). HA 2026.9 removed `VacuumEntityFeature.BATTERY`: any localtuya vacuum with a
  battery DP crashes on load (rospogrigio #2285 open, fix PR #2286 unmerged; xZetsubou master
  fixed 09-05, no tagged release) — if one exists, stop at 2026.8 or move fork. The
  `ToolResult` deprecation (2027.11) has no Zoe impact. MA: pin `2.10.4` (`stable` ≡ same
  digest); bump `music-assistant-client` to 1.5.1 first; probe Sendspin re-pairing
  (aiosendspin 9.1.1, PIN-pairing breaking at 9.0.0), the panel's shairport-sync 5.1 in
  PTP/Automatic mode (support #6243 pattern — pin the streaming mode if silent), and the
  bgutil 2.0.0 localhost bind reachable from MA's namespace (`127.0.0.1:4416`).
- B0.13 ⬜ JetPack 7.2.x reflash window — only after B0.7/B0.8 and when the J401 BSP + an
  Orin wheel index exist.

### B1 — Turn-taking that feels like a person (beats GPT-Live locally)
- B1.1 🔨 **Speculative turn-start with a speculation gate** — draft PR #1685 (flag-dark
  `ZOE_SPECULATIVE_*`, server-side gate + daemon verdict, 30 tests, break-the-fix controls;
  stays dark until phase 2 defers write side-effects to commit; needs the panel on + a replay
  gate bound to its head — the 2026-09-25 attempt skipped on the 700 MB floor, so it waits
  for B0.1 or a quiet nightly): fire the brain on Smart Turn's
  first "complete" (or a short VAD stop), hold TTS frames until the turn is confirmed,
  drop on cancel, keep if the final transcript is equivalent. Source: HF
  `speech-to-speech --speculative_reopen_ms`, Pipecat `speculation_gate.py`, LiveKit
  `_transcripts_equivalent`. Gate: replay corpus; cancellations < 30%; RAM flat. Owner: agent.
- B1.2 ⬜ **False-interruption pause → 2 s timer → resume** if no final transcript arrives
  (LiveKit `agent_activity.py`). Gate: replay with injected 300 ms noise bursts mid-reply.
- B1.3 ⬜ **Unmute interruption policy**: text-confirmed interrupt immediately; VAD-only
  interrupt needs ≥3 s of TTS elapsed and low continue-probability; `MinWords` (2–3) from a
  fast STT pass before cancelling; VAD hysteresis gap 0.15. Gate: false-barge count on the
  corpus with Kokoro playing through the panel speaker.
- B1.4 ⬜ **Smart Turn v3.2 + Silero v6.2 file** (drop-ins) and **backchannels**: reuse the
  Smart Turn "incomplete" score to play a soft "mm-hm" after ≥0.7 s of speech, 2.5 s
  cooldown. Add "interruptions per conversation" and "silence before first audio" to the
  replay harness.
  **2026-09-26 (§12; wording per #1705):** the Silero v6.2.1 file is already in place (B0.2).
  Silero v6.2.2's `silero_vad_16k_sequence.onnx` (GIL-releasing, `sequence=True`) is an
  OFFLINE whole-utterance graph — **NOT a live drop-in**: `services/zoe-data/voice_vad.py`
  runs the streaming model in 512-sample hops with a `(2,1,128)` recurrent state, and a file
  swap would NOT fall back to RMS (RMS is chosen only when the model fails to load; a model
  that loads but rejects streaming inputs makes `process_hops` swallow the error and report
  no speech — a silent failure). Live VAD stays on streaming v6.2.1; evaluate the sequence
  model for the replay/lab path only (whole clip in hand). Validation for ANY live VAD change
  = the VAD unit lanes (`test_livekit_vad_segmentation.py`, `test_voice_barge_in.py`) + a
  live barge-in count on the panel with Kokoro playing (B1.3 metric) — the replay harness
  starts at STT and never exercises VAD or barge-in. New lab item (small, B1/B5): **Parakeet
  Redux** (Moondream 2026-09-22; 178 MB / 149M ternary encoder, CPU, streaming, CC-BY-4.0)
  WER + per-file ms vs Moonshine 0.0.62 on the replay corpus — a benchmark, not a rock swap.
- B1.5 ⬜ **Acknowledge-while-thinking + async tools**: fast tier emits a first clause or
  backchannel in ~300 ms, tool calls run async, the brain revises mid-utterance
  (GPT-Live "delegated backend", Sesame "speak while searching"). Flag-gated.
- B1.6 ⬜ **7 s silence marker** so Zoe speaks first inside a conversation, escalating
  (Unmute `system_prompt.py`); never during TTS or barge-in.
- B1.7 ⬜ Spoken-text-only context: truncate the stored assistant turn to what Kokoro
  actually played; mark `interrupted` (LiveKit tts-aligned transcript).
- B1.8 ⬜ `ask_question(answers[{id, sentences}])` primitive with GBNF-constrained
  recognition for confirmations and menus (HA assist_satellite + speech-to-phrase).
- B1.9 ⬜ Template fast path for the router's top-20 highest-precision intents (zero brain
  call; TTS-safe phonetic normaliser) — axiom-voice-agent.
- B1.10 ⏸ HELD 2026-09-26 — Moonshine 0.1.5 passes said-vs-did (13/13 OK in-process off/on
  keyterms and remote; 7 EMPTY = baseline) but the STT stage is ~1.9× slower per file on the
  Orin. **Root-caused** with the per-session ONNX log (`options={"log_ort_run": True}`):
  encoder / adapter / cross-KV runs identical (~50 ms), same 8 decoder steps, same input
  shapes, but EACH decoder step is 57–70 ms in 0.1.5 vs 20–22 ms in 0.0.62. Not the model
  bundle (0.1.5 lib + old bundle: still 60 ms/step), not the ONNX Runtime binary (0.0.62's
  libonnxruntime swapped into the 0.1.5 package: still ~70 ms/step), not threads
  (`MOONSHINE_ORT_SINGLE_THREAD=1` → 1010 ms median; 2/4-core pinning 860–880; OMP 2 → 584),
  not speculative decoding (off → 508), not the update interval (10 s → 525). The cost is in
  libmoonshine 0.1.x's own decoder-run path. No matching upstream issue (searched 2026-09-26).
  Shipped by **PR #1696 (merged)**: pin `moonshine-voice==0.0.62` recorded, the
  `ZOE_MOONSHINE_KEYTERMS` plumbing (feature-detected, dormant on 0.0.62, visible on `/readyz`),
  runbook `docs/knowledge/moonshine-0-1-5-upgrade.md` §8 with the numbers. Next: 🧑 file
  upstream at moonshine-ai/moonshine with these numbers (outward-facing — Jason's call); retest
  on the next release with the same engine-only A/B. 2026-09-26 (§2): upstream is silent since
  0.1.5 (zero commits, no perf issue filed by anyone); watch moonshine #229 (shared Silero VAD
  across concurrent streams); the issue draft is in ecosystem-watch §2.
- B1.11 ⏸ PARKED 2026-09-26 — Flue 2.1.1 (`@flue/*` 2.0.1 → 2.1.1 in both 2x sidecars; hono /
  nanoid advisories cleared, `npm audit` 0; 209/209 + 44/44 tests; store format unchanged, one
  fold-checkpoint re-fold on first start). Draft **PR #1694** was proven the way the contract
  intends — the 2.1.1 build from the PR worktree ran on a parallel port :3580 with an isolated
  store and the head-bound replay PASSED twice — but the branch upgrades the auto-deployed trees
  IN PLACE and `labs/AGENTS.md` mandates a SIBLING directory for version bumps. 🧑 Jason
  decides: (a) the two-PR sibling + cutover route (as #1675 did for 1.x → 2.x), or (b) amend
  the contract to allow in-place patch/minor dependency bumps that carry head-bound
  parallel-port evidence. Then 2.2.0 for the llama.cpp tool-call fixes.
  **Flue 2.2.0 is NOT drop-in (2026-09-26, §4):** it exists only as `2.2.0-next.1` on npm
  (2026-09-25; no 2.1.2, no 2.2.0 final) and bumps Pi to 0.87.1. Pi ≥0.86 changes the
  `ProviderStreams` input from `Context` to `TranscriptContext` (system prompt + tools travel
  in the leading system message). Zoe's `capped-completions.ts` sets `tools: []` to enforce
  the iteration cap and `context-window.ts:189-190 / 259-260` read `context.systemPrompt` /
  `context.tools` for the budget — under 0.86+ the cap becomes a **silent no-op** and the
  budget reads `undefined`. Port to `getCurrentSystemPrompt()` / `getCurrentTools()` plus a
  tools-removed system message and re-verify with a cap negative control (the cap must still
  trip) BEFORE any 2.2.0 bump. Only #9816 (0.87.0: no strict tool schemas for endpoints that
  do not advertise them) is a Flue-reachable llama.cpp fix; #8275 (thinking budget) came in
  0.84.3; #9528 (`enable_thinking`) is CLI-side. 2.1.1 already swapped the `flue.tool.call.*`
  trace attrs for `gen_ai.tool.call.*` (check trace consumers). Pins are
  `@earendil-works/pi-ai` 0.83.0 (2x sidecar) / `pi-coding-agent` 0.82.1 (`zoe-core`), not
  `@mariozechner/*` (dead scope, last publish 0.73.1).

### B2 — Proactivity with judgement (beats Daily Brief / Alexa+ nudges)
- B2.1 ⬜ **Presence-triggered routines**: emit `person_recognized(panel, person, ts)` from the
  panel ID path into the proactive engine; "Zoe speaks first" on first-recognition-of-the-day
  rather than a 07:30 timer (Omnisense).
- B2.2 ⬜ **Candidate-selection → delivery-gating split** for proactive turns with a
  three-valued verdict (Immediate / Delayed / Silent) scored by a cheap non-LLM trigger over
  structured events using When2Talk's four factors + a Frigate-style 0–2 salience level;
  **unread/ignored backoff** (interval doubling); log Jason's reaction as accept/reject for
  a later reward model (N.E.K.O, Nomi, ProactiveAgent). Lab first: replay 30 days of
  trigger contexts through the rubric and count "should have been Silent".
- B2.3 ⬜ **Evening Home Brief** from HA event history (what happened, what is open, what
  needs a decision), spoken + card; reuse the morning generator (Gemini for Home).
- B2.4 ⬜ **Standing watch agents via Telegram** ("tell me when X"), each with a
  name/role/objective (Spark information agents, Copilot Autopilot). `watchers` table on
  the existing scheduler; first watcher = HA entity threshold → Telegram → panel.
- B2.5 ⬜ Per-fact `pinned` / `suppress_proactive` ("don't mention this again") and
  strength-weighted sampling instead of top-k, so she does not repeat the same three things.
- B2.6 ⬜ Sleep-time precompute: at idle, draft tomorrow's brief and 3 likely asks per
  person (Letta); prefix-cache friendly.

### B3 — Memory that is sound and visible (beats Siri Recap / ChatGPT Dreaming)
- B3.1 🔨 **Bi-temporal supersession + "keep the richer fact"** at idle — lab spike in
  draft PR #1692 (flag-dark `ZOE_BITEMPORAL_SUPERSEDE`, no prod wiring): `valid_from /
  valid_until / expired_at / superseded_by` on the fact rows (Chroma metadata keys) and
  `person_relationships` (Alembic plan, not applied); contradiction only if intervals
  overlap (Graphiti `edge_operations.py`); reconciliation with mem0's update prompt,
  top-10 neighbours, integer ids. Fixes the distilled-vs-richer dedupe bug (M7/M9)
  without deleting anything. Lab: 50-pair SYNTHETIC fixture scores 1.00 with the fake
  judge; controls without the overlap rule / richer rule fail 0/10 historical, 4/8
  richer. Design + gates: `b3-1-bitemporal-supersession.md`. Next: real-brain judge run,
  flip the two `test_live_dedup.py` strict-xfails, idle pass behind B3.2's gates.
- B3.2 🔨 **Deterministic dream gating** for the idle consolidator (first step MERGED as #1682:
  the alert distinguishes idle / no-history / extractor vs processing errors, one cutoff for
  selection and probe, the emotional pass survives a fact-parse failure, consolidation has its
  own verdict vocabulary, counts on the status endpoint) (≥N new facts, ≥H hours,
  idle ≥M min, cancel on speech) + a hard brain-call budget per window + a 40-line profile
  cap (Honcho, Memobase). Explains the 44 zero-effect digests; make them explainable.
- B3.3 ⬜ **Importance-sum reflection** reusing `emotional_moment.intensity`; insights carry
  ≥2 evidence ids (Generative Agents); that is what the emotional follow-up fires on.
- B3.4 ⬜ **User-visible memory page** on the touch UI: consolidated topics, edit/delete,
  "regenerate my summary", ask-my-memory, JSON export/import (Claude, ChatGPT).
- B3.5 ⬜ **Summaries, not transcripts** for episodic memory, sensitive-data exclusion,
  "what did we talk about" surface (Siri Recap).
- B3.6 ⬜ One conversation across surfaces and days (session continuity keyed on person, not
  device) — the samantha plan's W15.
- B3.7 ⬜ **Zoe's own thread**: persona/human blocks with `limit`, `read_only`, idle-writer
  only, review-before-apply (Letta); GBrain takes-vs-facts schema for her opinions
  (`holder=zoe`, confidence, dated).
- B3.8 ⬜ Transition-capturing extraction rule ("switched from X to Y") + LLM-free
  Personalized-PageRank multi-hop over the people graph (HippoRAG 2).
- B3.9 ⬜ Speaker-cluster-gated owner attribution in consolidation (Omi): a "Jason" fact is
  promoted only when the source turn carried a voice-ID match.
- B3.10 ⬜ A 60-item Jason-corpus mini-LongMemEval dominated by *knowledge updates* and
  *abstention*, as a memory replay gate.

### B4 — Identity and presence (where Apple and Amazon are weakest locally)
- B4.1 ⬜ Speaker-verification **margin rule** (distance < θ and ≥0.10 over the second-best
  profile) + sherpa-onnx ERes2Net/CAM++ embedder in shadow during the W5 week (Omi).
  Multi-speaker-detector candidate for the B9.3 shadow week (2026-09-26, §12): NVIDIA
  **Nemotron 3 Diarization** (09-23; ~100M params, streaming/offline, up to 8 speakers,
  320 ms labels; ONNX/GGUF ports 09-23/24) — not on the hot path.
- B4.2 ⬜ Speaker-gated wake word (openWakeWord custom verifier) or a purpose-trained
  "Hey Zoe" (2026 trainers).
- B4.3 🧑 Decide `ZOE_FACE_ID_ENABLED` (on, against the retention policy) → build the
  face enroll/delete UI (ZOE-6129) or turn it off. More urgent, not less (2026-09-26): Apple's
  October home display ships household face recognition (§2 row 15) — keep B4.3 ahead of B7.4.
- B4.4 ⬜ Dual-channel mic (processed for wake/STT, raw for voice-ID) on one panel (VPE).
- B4.5 ⬜ Presence hardware: Raspberry Pi AI Camera (IMX500) person-detect stream as a
  zero-CPU presence signal; HA device presence (Phase 2.5).

### B5 — Voice quality and expressiveness
- B5.1 ⬜ **Kokoro on ONNX Runtime CUDA** (same model, same voices): 2.3 GB → ~0.6–1 GB;
  RTF < 0.3 required; jetson-containers `kokoro-tts-onnx` recipe.
  **Recipe (2026-09-26, §3):** `kokoro-onnx==0.6.1` (2026-08-19; PR #198 re-export with
  `speed`/duration outputs, **fp16 164 MB / int8 114 MB** graphs in release `model-files-v1.1`
  beside the 326 MB fp32) + `onnxruntime_gpu==1.24.0` cp310 aarch64 from
  `https://pypi.jetson-ai-lab.io/jp6/cu126/` (verified 09-26; PyPI ships no aarch64 GPU wheel,
  so it lives on system 3.10 beside llama-server, not in the B0.7 venv). The `[gpu]` extra is
  x86_64-only — install `kokoro-onnx` plain and bring ORT-GPU; pin numpy to the ORT wheel's
  ABI (kokoro-onnx wants 2.x, the Jetson wheel was built on 1.x). No Kokoro weights since
  2025-04 and no published Jetson RAM numbers — the 2.3 GB → ~0.6–1 GB claim is a hypothesis:
  **measure with a CPU-EP control** (same graph on the CPU provider), gate RTF < 0.3 + the
  replay corpus. CPU fallback if the sidecar ever loses CUDA: Moonshine 0.1.5's two-stage
  Kokoro ORT graph (~100–200 MB).
- B5.2 ⬜ Emotion → bounded, auditable prompt modifiers under immutable rules (GLaDOS
  constitution); Gemma emits one expression tag per sentence (OLV convention); a 48-dim
  emotion vector as the shared wire format (Hume schema).
- B5.3 ⬜ Expressive-lane bake-off for W11: Chatterbox Nano/Turbo, Supertonic 3, NeuTTS Air.
- B5.4 ⬜ **Pocket TTS on the Pi panels** (100M, MIT, CPU) for local acks/toasts when the
  Orin is busy or the brain is stopped. Still the strongest candidate at v3.3.0 (2026-09-24;
  retrained ES/IT/PT/DE, new NL/FR, training code released 08-25).
- B5.5 ⬜ Gemma 4 E4B **audio input** for paralinguistics on flagged turns only (BF16 mmproj
  costs RAM — after B0.1/B5.1).
- B5.6 ⬜ Voxtral Realtime as an offline second-opinion ASR judge in the replay harness.

### B6 — Brain headroom (optimise around the rock)
- B6.1 = B0.4 (FA rebuild). B6.2 🔨 **Re-upload staged, swap is an operator step.** Downloaded
  to `~/models/gemma4-e4b-qat/staging-hf-20260717/`, sha256 verified against the HF LFS oids
  (df0fd4ee… / 423074e5…). Diffed with the gguf-py reader: **all 666 + 49 tensors
  byte-identical, tokenizer identical; the ONLY change is `tokenizer.chat_template`** (16804 →
  18808 chars) — `null` tool arguments render as `null`; pre-serialised JSON-string tool
  arguments no longer double-wrap; `image_url`/`input_audio` content parts; `messages and …`
  guards on empty histories; O(1) continuation tracking; a `<|channel>thought` opener after a
  tool response when thinking is enabled. The live server uses the embedded template
  (`--jinja`), so this is a prompt-format change on the tool-calling path and must be
  replay-gated — include multi-tool-call + thinking turns so llama.cpp #28827 (trailing
  garbage from the re-injected `thinking_text`, see B0.4) shows if it reproduces on E4B.
  🧑 Swap (both files, keep the old ones beside them) — run as ONE script: it
  refuses to start on a partial earlier attempt (a leftover `.pre-hf-20260717` backup would
  make a bare `mv -n` skip silently and leave the pair at mixed versions), restores BOTH
  files if anything fails mid-way (so the production names never point at a partial or
  unverified pair), and verifies the installed pair with `sha256sum -c` BEFORE the restart:
  ```
  set -eu; cd ~/models/gemma4-e4b-qat
  A=gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf; B=mtp-gemma-4-E4B-it.gguf; BK=pre-hf-20260717; ST=staging-hf-20260717
  for f in $A $B; do
    [ -e "$f.$BK" ] && { echo "STOP: $f.$BK exists — partial earlier attempt, inspect first"; exit 1; }
    [ -e "$ST/$f" ] || { echo "STOP: staged $ST/$f missing"; exit 1; }
  done
  restore() {  # put the previous pair back; park whatever was moved in as *.failed
    for f in $A $B; do
      if [ -e "$f.$BK" ]; then [ -e "$f" ] && mv -f "$f" "$ST/$f.failed"; mv "$f.$BK" "$f"; fi
    done
    echo "RESTORED the previous pair — verify with the PREVIOUS hashes below before any restart"
  }
  trap restore ERR
  mv "$A" "$A.$BK"; mv "$ST/$A" "$A"; mv "$B" "$B.$BK"; mv "$ST/$B" "$B"
  sha256sum -c <<'SUMS'
  df0fd4ee07072c607c29a0a1cb4f98918426cca12f45a2776bdd6ee6d09a4de3  gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf
  423074e537504b4f9ec5eafed5c639fac82c96631626efccacdd3c4039b20605  mtp-gemma-4-E4B-it.gguf
  SUMS
  trap - ERR; echo "SWAP OK — restart llama-server now"
  ```
  Any failing `mv` or a hash mismatch triggers `restore` (the running server keeps its
  already-open files either way; nothing changes until the restart). Then
  `systemctl --user restart llama-server` (health probe waits for model + draft), then
  `systemctl --user start zoe-voice-regression.service` and read
  `~/.cache/zoe/voice_regression_last.json`. **Rollback after a failed gate** (reverse both
  files, then verify against the PREVIOUS pair's hashes, measured on the box 2026-09-26,
  then restart):
  ```
  set -eu; cd ~/models/gemma4-e4b-qat; BK=pre-hf-20260717; ST=staging-hf-20260717
  for f in gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf mtp-gemma-4-E4B-it.gguf; do   # check BOTH backups first:
    [ -e "$f.$BK" ] || { echo "STOP: $f.$BK missing — already rolled back, or never swapped; nothing moved"; exit 1; }
  done
  for f in gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf mtp-gemma-4-E4B-it.gguf; do mv "$f" "$ST/$f"; mv "$f.$BK" "$f"; done
  sha256sum -c <<'SUMS'
  b3052f962d6449b4eb2075733c068bdec1c51eadb7b237e6c3157bfbb7b1dae0  gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf
  b0005dc39d47ede950c3ec413cb20e832f15b216126eae368d9f572676153cb6  mtp-gemma-4-E4B-it.gguf
  SUMS
  systemctl --user restart llama-server
  ``` B6.3 ⬜ Domain-prefixed tool
  names (`ha__`, `ma__`, `memory__`). B6.4 ⬜ Consider an E2B "fast/cheap turn" lane only
  if RAM allows after B0.1/B5.1 (AICore's variant-by-task split) — not a rock change.
- B6.5 ⬜ Client defaults: `zoe_flue_client` → `:3579`/wire 2; `ZOE_BRAIN_FAILOVER=1` after
  a gate (B1 in the register).

### B7 — Window into Zoe (UI)
- B7.1 ⬜ AG-UI 1.0 (`ACTIVITY_SNAPSHOT/DELTA`) + a fixed A2UI-style component catalog as the
  carrier for brain-built cards (never raw HTML from the 4B).
- B7.2 ⬜ Ask-card conversation mode (PR-1a) → retire `voice.html`.
- B7.3 ⬜ Voice-authored automations via HA (Gemini for Home) — later.
- B7.4 ⬜ "Ask about what you see" via the panel camera, one-shot.

### B8 — Self-evolution (nobody else has it)
- B8.1 ⬜ Rebuild the executor on Flue 2 (`init()` handles + `durable: true` tools);
  unpause Multica; land ≥3 real tickets (Phase 2 exit).
- B8.2 ⬜ SkillSpector 2.12 with the LLM stage on the local llama-server.
- B8.3 ⬜ Standing watchers (B2.4) as the first Zoe-authored background agents.

### B9 — Omi wearable: a roaming, consented microphone (W6 delivery vehicle) — 🔨 plan merged (#1683)
Plan: [`omi-integration-plan.md`](omi-integration-plan.md) (two review rounds: every phase
replay-gated, no transcript text in shadow mode, a real multi-speaker detector, storage-off
firmware mandatory, scoped rows incl. the existing panel writer, "same eligible speaker" rule;
§6.1 lists what is still unverified). Pendant → BLE → the Pi
panel (later a Pi Zero 2 W dock) → Opus decode + Silero → existing `/api/voice/ambient` →
owner-only, speaker-gated `ambient_memory`. Nothing to Omi's cloud; the phone app is out
(one bonded central only; their backend needs Firebase/GCS/Pinecone/OpenAI; their own
maintainers call their speaker-ID "not reliable enough to trust"). The consumer pendant is an
nRF5340 with an SD ring buffer that records whenever powered — a retention Zoe cannot gate
from outside, hence B9.0.
- B9.0 🧑 **Consent posture decisions**: (a) firmware variant `omi-zoe` with offline SD storage
  OFF vs stock + `RING_CLEAR` on connect (= `0x13 CLEAR` on the stock ≥3.0.20 ring
  protocol); (b) default retention window (proposal 7 d); (c) which
  household members may opt into ambient; (d) legal sanity check of the discard-unknown rule
  under the WA Surveillance Devices Act 1998 s5/s9 for guests. Gate: written answers in the
  plan's §8 before B9.4.
- B9.1 🔨 **Lab receive** (`labs/omi-receiver/omi_bridge.py`, bleak + opuslib, off-Orin): connect,
  decode, 10-min WAV, reconnect-on-drop. Code + fixture tests + manual protocol: #1693 (draft;
  the pendant has not been run yet — every gate number is still hardware-only). Gate: <1 % packet gaps at 3 m/one wall; Moonshine WER on
  20 corpus sentences ≤ panel + 5 pts; battery drop/h logged.
  **Protocol facts (2026-09-26, §8, read from `sdks/device/PROTOCOL.md` + the app — the
  docs.omi.me Protocol page is stale, no codec 21):** codec ids **0 = PCM16, 1 = PCM8,
  20 = Opus 160-sample/10 ms (DevKit), 21 = Opus FS320 320-sample/20 ms (CV1)**; **3-byte
  header** (u16 LE packet number + u8 index) then Opus; CV1 = 32 kbps VBR, ~16 kB/s on the
  wire, PCM16 mono 16 kHz out; UUIDs as in `omi_bridge.py`. Ring protocol needs firmware
  **≥3.0.20** — 🧑 confirm via DIS `2A26` before relying on it (CV1 config reports 3.0.21;
  `0x10 INFO` / `0x11 READ` / `0x12 ADVANCE` / `0x13 CLEAR` / `0x03 STOP`, big-endian ints,
  444-byte records = 4-byte timestamp + 440 audio; storage UUID UNVERIFIED). No local STT
  anywhere upstream (cloud stack; "Local AI provider" PR #13538 unmerged) — Moonshine stays
  Zoe's job. Firmware 08-27 removed software VAD for T5838 AAD hardware VAD, so the pendant
  may already drop silence: the "<1 % packet gaps" metric must separate AAD-gated silence
  from BLE loss. No GitHub firmware release since v2.0.4 (2024-11); firmware ships by app OTA.
- B9.2 ⬜ **Bridge thread in the Pi daemon, flag-dark** (`OMI_BRIDGE_ENABLED`,
  `ZOE_AMBIENT_OMI_ENABLED`, both off): `source="omi"`, `device_id`, `speaker_id`, `expires_at`
  on `ambient_memory` (one migration). Gate: ci_safe tests (flag off ⇒ no row); replay gate
  PASS with the thread live; Pi RSS +≤120 MB.
- B9.3 ⬜ **Speaker gate in shadow** on the Orin (sherpa-onnx embedder per B4.1; margin rule;
  multi-speaker ⇒ discard). Gate: one shadow week; negative control — another voice alone for
  10 min ⇒ zero owner verdicts.
- B9.4 ⬜ **Physical consent**: long-press session toggle + haptic + LED; per-profile
  `ambient_consent_at`; retention purge in the idle consolidator; firmware per B9.0. Gate:
  stranger test ⇒ no row/file/log text; toggle-off stops posts ≤1 s.
- B9.5 ⬜ **Attributed storage + promotion** (`ZOE_AMBIENT_OMI_ATTRIBUTE=1`): admission gate,
  `[ambient:omi]` citation, rows on the memory page with delete. Gate: `memory_recall_probe`
  unchanged; 20-item "said near Zoe" set ≥80 % attributed recall.
- B9.6 ⬜ Optional offline drain on reconnect (session-gated ring only). B9.7 ⬜ Optional
  push-to-talk via the button (replies on the nearest panel/Telegram; the CV1 has no speaker).
- Not doing: Omi app/backend/webhooks/MCP (cloud), DevKit 2 purchase, diarization in v1.
- Open questions for Jason are in the plan's §8 (which device, is the phone app paired, where
  the pendant lives during the day, retention, minors, who does the legal check).

### B10 — Web lookup + claim backing (Jason, 2026-07-24)
- B10.0 🔨 **Make the existing chat fallback honest first** — draft PR #1691, pending review
  (found by the audit re-check 2026-09-26): `research_evidence.fetch_web_fallback_results` reaches DuckDuckGo with no brain
  tool, but DDG now answers scripted fetches with a challenge page (HTTP 202) and the function
  swallows it and returns `[]`, so a research turn silently degrades to placeholders. Surface
  "nothing found", and prefer the configured Tavily key (`web_search_provider`) when set.
  #1691: `classify_ddg_response` (challenge = `blocked`, never `no_results`), `fetch_web_fallback`
  → `WebFallbackOutcome` recorded in the package as `web_lookup` + an honest card row,
  `ZOE_WEB_FALLBACK_PROVIDER` (auto = Tavily-first when keyed | duckduckgo | off), one INFO line
  per lookup (query length, never text); mutation-checked negative controls. Voice-gate scope CLEAR.
- B10.1 ⬜ Re-land the Python core of the web-search spike (PR #1610, now closed: DDG/Wikipedia/
  HN scrapers, block detection, consensus merge, ≤350-token voice packet; 44 offline fixture
  tests) as a ≤300-line PR behind the approved research/delegation seam (the cut-list cut a
  direct `web_search` tool on purpose); Tavily stays the opt-in primary. B10.2 ⬜ Wire a `web_search`
  tool into the Flue brain lane behind a flag; "are you sure?" triggers a backed re-answer.
  Gate: fixture tests + replay corpus unchanged + 20 live lookups scored by hand.

## 4. Sequencing (dependencies)

```
B0.1 zram ─┬─> nightly gate PASS ─┬─> B0.4 FA rebuild ─> B6.*, B1.11
           │                      ├─> B1.1..B1.10 (each replay-gated)
           │                      └─> B5.1 Kokoro ONNX ─> B5.2/B5.3/B5.5
B0.7 py3.12 venv ─> B0.8 Chroma/MemPalace ─> B3.1 (design against the new store)
B3.2 dream gating ─> B3.3 reflection ─> B2.2 delivery policy ─> B2.1/B2.3/B2.4
B4.3 face decision ─> B4.1/B4.2 shadow week (Pi on) ─> B3.9
B8.1 executor ─> B8.3 watchers (B2.4 can ship on the plain scheduler first)
B0.1 ─> B9.1 ─> B9.2 ─> (B4.1, B4.3) ─> B9.3 ─> B9.0 ─> B9.4 ─> B9.5 ─> B3.9
```

Diagnosed 2026-09-25 (PR #1682): the 44 zero-effect nightly digests were an **idle
household**, not a bug — no owned user turn since 09-03; the alert now distinguishes "idle"
from "processed 0 of N eligible" (B3.2 gains that verdict as its first gate).

## 4b. Triage of the inherited plans (2026-09-25, on Jason's "do we need to finish this?")

The test for each: does it move Zoe toward the north star, and does what exists actually
work today. Verdicts are recorded here so they are not re-litigated in chat.

| Inherited plan / idea | Verdict | Why |
|---|---|---|
| **oh-my-pi as a builder harness** (`omp-builder-adoption.md`, staged binary, H8) | **RETIRE the staging** | Its own trial found a cost leak, not a quality win; the fence is a wrapper + overlay the doc admits is not the top of the stack; it needs a metered OpenRouter key in a flat-rate economy; hashline was disabled upstream for small models. Keep the five pinned ideas as design inputs (replay invariant → B1.1, fact IDs → B3.1). Remove `/home/zoe/.local/bin/omp` + the container mount; mark the record "evaluated, not adopted". |
| Web-search + claim-backing spike (PR #1610, 62 files, conflicting) | **Re-land small as B10** | Jason asked for live lookups and "are you sure?" backing on 2026-07-24; the valuable part is a few hundred lines of Python. Close #1610, open a ≤300-line PR from its `labs/web-search-spike` core when B1.1 is in review. |
| Ask-card conversation mode (PR-1b/1c) + `voice.html` retirement | Finish | One front door; removes a legacy surface; needed for B1 on the panel. |
| Panel identity W5 shadow week, face enroll/delete UI | Finish (when the panel is on) | Multi-user identity is the edge Apple/Amazon lack locally. Face-ID: build the delete screen or turn the flag off (policy). |
| Relationship graph / recall boost enablement | Finish the measurement only | Merged; verify the running state and measure lift on real data; no more docs. |
| OpenClaw runtime code | Delete | Gateway stopped 2026-09-25; 31 skills never ran; router + trigger still mounted in `main.py`. Gated deletion PRs. |
| Desktop UI overhaul (waves 0–6) | **Park after Wave 0/1 (XSS + data-loss)** | The panel, Telegram and phone are the surfaces; 30k lines of desktop pages are not the product. Revisit after B1–B3. |
| Multica full-autonomy program (PR #1641) + Hermes retirement gates + retirement-gates packet | **Park** | Self-evolution is a pillar, but the warden plan is the most expensive, least user-visible work on the board. Keep the executor's minimal Phase 2 (B8.1); close #1641 as parked with a pointer; do not execute the gates packet. |
| Router self-train loop (`ZOE_ROUTER_SELFTRAIN`) | Park | Ratchet rejected its only candidate; 91.4% is fine; needs data and RAM that B1 needs more. |
| Brain tool-selection flake investigation | Drop | Not user-visible (router decides first); its own doc says dropping is legitimate. |
| Music discovery batch (`ZOE_MUSIC_DISCOVERY`) | Park (flag off) | Never ran (memory gate); revisit after B0.1. |
| Tauri desktop shell, orb-reacts-to-music, Pinterest-style ideas board | Park | Not on the path. |
| Telegram voice notes (W8) | Later, after B1 | Cheap once the panel lane is right. |
| Chat-split / typed-config / voice_tts split (Wave 4 tech debt) | Only when touching those files | Refactor-as-you-go; no standalone PRs. |

## 5. Explicitly NOT doing (and why)
Speech-to-speech models (replace the brain rock, >8 GB); Pipecat as a framework (lift the
pieces instead); Graphiti/Neo4j, MemOS/MIRIX, Second-Me; TEN turn detection (7B);
vLLM on Orin (no MTP); a Jetson reflash before B0.7/B0.8; any LoCoMo leaderboard claim as
a decision input.

## 6. Change log
- 2026-09-26 (pm, fold) — ecosystem-watch 2026-09-26 (#1703; B1.4 wording per #1705) folded
  into the rows: B0.4 b11194 gate (#25522 dropped), B0.7 two-interpreter split, B0.8 migrate
  recipe + 3.4.0 correction, B0.10 split into dated 🧑 sub-items (Copilot Lite 09-28, Actions
  event policy 11-02, ggshield 1.55 + worktree hook, CodeRabbit optional) + Codex quota note,
  B0.12 HA/MA pre-flight, B1.4 Silero sequence-model correction + Parakeet lab item, B1.10
  upstream silence, B1.11 Flue 2.2.0 not-drop-in, B4.1 Nemotron candidate, B4.3 urgency,
  B5.1 ONNX recipe, B5.4 Pocket v3.3, B6.2 replay scope, B9.0/B9.1 protocol facts, §2 row 15.
- 2026-09-26 (pm) — Moonshine 0.1.5 measured + root-caused (decoder-step cost inside the
  0.1.x library), HELD; Flue 2.1.1 PARKED on the sibling-directory contract; deploy
  root-ownership wedge found + fixed; #1693/#1695/#1696/#1698 merged and deployed; bridge
  restarted by hand; ~70 review threads closed across the day's PRs.
- 2026-09-26 — gated Python batch on the box + replay gate PASS #2; B0.6 deps contract
  (this PR); B0.14 ✅ via #1689; B6.2 re-upload diffed (template-only) and staged; B10.0
  (#1691), B0.12 pt 1 (#1695), B1.11 (#1694), B1.10 (#1696) opened by agents; #1692/#1693
  in review-thread rounds; §0 rewritten around the operator window + queue order.
- 2026-09-25 — created from the return-from-absence review; B0.2 done the same day; B1.1
  (#1685), B3.2 first step (#1682), B9 plan (#1683), the feature audit (#1684) and the 1.x
  Telegram-lab retirement (#1681) opened as drafts; B0.14/B0.15 added; §4b triage recorded.
- 2026-09-26 — #1680/#1681/#1682/#1683/#1687 merged; PO-token container recreated on 2.0.0
  and verified; #1684 replaced by the redacted #1687; #1686 replaced by the clean #1689; B9
  linked; B10.0 added (DDG challenge page); §0 refreshed.
