# Zoe AI Assistant

[![Validate](https://github.com/jason-easyazz/zoe-ai-assistant/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/jason-easyazz/zoe-ai-assistant/actions/workflows/validate.yml)

Privacy-first, self-hosted AI companion for the home. Zoe is a voice-and-touch
assistant that runs on your own hardware: a local LLM, local speech, a memory
that is measured rather than assumed, and a smart-home and life-hub layer on top.
No cloud account is required for the core experience.

The goal (see [docs/VISION.md](docs/VISION.md)) is a warm, always-there
companion that actually knows you: it remembers the thread of your life, says
*why* it said something, forgets what you ask it to forget, and knows when to
stay quiet. Every claim below that a shipped feature does something is backed by
a record in `docs/` and, where it can be, by a benchmark in `scripts/perf/`.

> **Heads up: this is a real, hardware-specific deployment, not a one-command
> demo.** Zoe is developed and run on an **NVIDIA Jetson Orin** (aarch64 + CUDA)
> with a locally-built `llama.cpp`. It will run on other Linux hosts, but the
> LLM and voice steps assume Jetson/CUDA and will need adjustment elsewhere
> (see [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md)). Read the whole
> Install section before starting.

> **How to read the status words in this README.** *On* = enabled by default in
> the code. *Shadow* = the code runs and logs what it would do but changes
> nothing; it ships that way until the owner flips it. *Off* = ships disabled.
> A deployment can set its own flags, so the code default is what is stated
> here; the generated [flag inventory](docs/knowledge/flag-inventory.md) lists
> every `ZOE_*` flag and its default. A feature is only described as "measured"
> when a record in `docs/` holds the numbers.

## By the numbers

Each figure is dated and links to the record that holds it. Records are point-in-time;
the dated record wins over this table.

| Area | Figure | As of | Source |
|------|--------|-------|--------|
| Memory bake-off | Zoe's own memory kept (`KEEP_Z0`): no Hindsight/MemPalace arm passed the pre-registered floors. Hard violations: MemPalace agent arm 50, Hindsight + MemPalace 58, Zoe + MemPalace tier 9. Zoe authority 237/237 | 2026-10-08 | [decision record](docs/research/memory-bakeoff-decision-2026-10-08.md) |
| Forgetting | Bench axis 18/21 in that run; 21/21 offline once the forgotten ledger was supplied to the lab arm | 2026-10-09 | [zoe-memory-bench.md](docs/knowledge/zoe-memory-bench.md) |
| Person bench, floors in `enforce` (in-process, live 4B) | ask-when-ambiguous 20/20, clean goodbye 10/10, hold-the-fact 0/20 flips (live bench: 0/30). A merged-live re-run is still owed | 2026-10-10 | [person-half-guards.md](docs/knowledge/person-half-guards.md), [enforce pack](docs/knowledge/person-half-enforce-pack-2026-10-09.md) |
| Voice/chat lane parity | 5/12 cells passing on the voice lane before, 12/12 after | 2026-10-10 | [voice-lane-parity.md](docs/knowledge/voice-lane-parity.md) |
| First sound after you stop speaking (memory / tool / chat turns) | median 3.3 / 2.8 / 3.4 s today; 2.0 / 2.3 / 2.5 s with three levers that are off by default | 2026-10-09 | [first-sound-latency](docs/knowledge/first-sound-latency-2026-10-09.md) |
| Night mind | 13 cells: 7/13 on the 4B, 9/13 on the 12B in the first trial, before later fixes; re-measurement in progress (PR #1982) | 2026-10-09 | [night-window.md](docs/knowledge/night-window.md) |
| Languages | 6 lexicon files: en, es, fr, de, ja, zh (only English natively reviewed) | 2026-10-11 | [lexicons_data](services/zoe-data/lexicons_data) |
| Engineering | 1846 merged pull requests | 2026-10-11 | `gh pr list --state merged` |
| Hardware | Jetson Orin NX 16 GB, unified memory; Gemma 4 E4B-QAT + MTP on one 8192-token slot | 2026-10-11 | [runtime-topology.md](docs/knowledge/runtime-topology.md), [CANONICAL.md](docs/CANONICAL.md) |

## Architecture

Zoe is a split stack: Docker for the stateful/edge services, host-native
systemd user services for the latency-sensitive ones.

### Core spine

| Layer | Service | Port | How it runs |
|-------|---------|------|-------------|
| Data  | `zoe-database` (PostgreSQL + pgvector) | 5432 (loopback) | Docker |
| Auth  | `zoe-auth`        | 8002  | Docker |
| UI    | `zoe-ui` (nginx)  | 80/443 | Docker |
| Home  | `homeassistant` + `homeassistant-mcp-bridge` | 8123 | Docker |
| Brain (LLM) | `llama-server` (Gemma 4 E4B-QAT + MTP drafter, llama.cpp) | 11434 | systemd (host) |
| API   | `zoe-data` (primary backend: chat, voice path, memory, Skybridge) | 8000 | systemd (host) |
| STT   | Moonshine v2 Medium (in-process in `zoe-data`) | none | systemd (host) |
| TTS   | `kokoro-tts` (Kokoro, then Edge TTS, then espeak-ng waterfall) | 10201 (loopback) | systemd (host) |

### Brain sidecars and modules (opt-in units, enabled on the reference deployment)

| Layer | Service | Port | Notes |
|-------|---------|------|-------|
| Agent brain | `flue-zoe-brain-2x` (Flue 2.x) | 3579 | The brain lane the reference deployment runs, selected with `ZOE_BRAIN_BACKEND=flue`. Source: [`labs/flue-zoe-brain-2x`](labs/flue-zoe-brain-2x/README.md). Ships inert: the installer copies the unit but never enables it |
| Router | `functiongemma-router` | 11436 | Stage 2 of the two-stage tool router (FunctionGemma-270M, CPU). Enabled with `ZOE_ROUTER_HEAD=active` (code default is `off`) |
| Telegram | `flue-zoe-telegram` | n/a | Telegram front door, re-slotted through `/api/chat` |
| Music | `zoe-music-assistant` (Music Assistant) | 8095 | Docker module ([`docker-compose.modules.yml`](docker-compose.modules.yml)), proxied at `/modules/music-assistant/` |

The brain, STT, and TTS models are **locked**: Gemma 4 E4B-QAT+MTP (brain),
Moonshine v2 Medium (STT), Kokoro (TTS). See
[docs/CANONICAL.md](docs/CANONICAL.md). Boot order is documented in
[docs/guides/OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md); the live
runtime map is [docs/knowledge/runtime-topology.md](docs/knowledge/runtime-topology.md).

**Brain dispatch.** `services/zoe-data/brain_dispatch.py` picks the agent brain
once per turn from the environment, in the configured order `flue > core >
legacy`. All three talk to the same Gemma 4 model on `llama-server`.

- **`flue`** (selected with `ZOE_BRAIN_BACKEND=flue`) is the **live brain** on the
  reference deployment: the Flue 2.x sidecar on `:3579`, in place since
  2026-08-09. The earlier Flue 1.x sidecar was retired and removed.
- **`core`** is the **code default** when `ZOE_BRAIN_BACKEND` is unset:
  `services/zoe-core`, the Pi agent. It is kept wired and tested but is the
  dormant lane on the reference deployment.
- **`legacy`** (`zoe_agent.py`) is the last lane, used only when `flue` is not
  selected and `ZOE_USE_CORE_BRAIN` is off.

That order is **configured lane selection, not runtime failover**. Bounded
failover exists behind `ZOE_BRAIN_FAILOVER` (default off). With `flue` selected
and its sidecar down, turns fail on that lane rather than falling through to
`core`. Every turn logs one `BRAIN_LANE` line naming the lane that answered.
Details: [docs/CANONICAL.md](docs/CANONICAL.md) and
[docs/architecture/zoe-flue-integration.md](docs/architecture/zoe-flue-integration.md).
The repo layout is in [docs/guides/REPO_LAYOUT.md](docs/guides/REPO_LAYOUT.md).

## What works today

Examples below use synthetic names.

### The basics

Voice (wake word, Moonshine STT, Kokoro TTS, barge-in) and a kiosk touch panel
on a separate Raspberry Pi; a desktop web UI; Home Assistant control;
calendar, reminders, lists, notes, journal, contacts; Music Assistant playback;
a two-stage tool router (a small classifier shortlists tools, FunctionGemma
decodes the call, the Gemma brain handles every miss). Spoken replies stream
sentence by sentence.

### Memory

Zoe's own memory system: a verbatim, raw-first store (Chroma/MemPalace-style
vector index plus PostgreSQL rows and a relationship graph), wrapped in rules
about who is allowed to change what.

- **Authority ranks (on).** Every memory row records who said it. Something you
  stated or confirmed outranks something Zoe inferred; an inferred write can
  never supersede, archive or contradict it and becomes a disputed candidate
  for you to resolve. Pasted text and other people's quoted words are walled off
  from your facts (with one documented residual gap). [memory-authority.md](docs/knowledge/memory-authority.md),
  [memory-own-words-wall.md](docs/knowledge/memory-own-words-wall.md)
- **"Why did you say that?" (on).** Zoe names the day and your own words that an
  answer rested on, or says plainly that it used no memory. She will not quote
  another household member's rows, a forgotten row, or a sensitive row on voice.
  Also: **"what do you know about me?"** (a bounded, grouped summary where
  private categories are counted, not read, until you ask by name), and **"off
  the record"** (the turn is kept out of every memory writer).
  [samantha-bar.md](docs/knowledge/samantha-bar.md) (section BM5)
- **Exact words (on).** Your original sentence is kept alongside any summary of
  it, so recall can quote you instead of a paraphrase.
- **Changed facts are closed, never deleted.** Rows carry validity metadata, so
  a superseded fact stays as history ("where did I live before?"). Applying it to
  changes you only imply, rather than state or correct, is still off by default
  (`ZOE_MEMORY_IMPLICIT_SUPERSEDE`), and so is retiring a fact by the exact
  sentence that ended it (`ZOE_QUOTE_RETIRE`, shadow).
  [memory-supersession.md](docs/knowledge/memory-supersession.md),
  [memory-quote-retire.md](docs/knowledge/memory-quote-retire.md)
- **Forgetting that stays forgotten (on).** "Forget X" erases the memory rows,
  the exact-words copy and the search-index residue, redacts the span in stored
  chat transcripts, and records a hashed ledger so the name, including near
  spellings, cannot come back through a later digest or write. The shield is
  permanent by default. Copies that existed before the feature (old backups, old
  transcripts) need the one-time operator scrub in
  [forgotten-text-physical-erase.md](docs/knowledge/forgotten-text-physical-erase.md)
  and `scripts/maintenance/redact_backups.py`. Forgetting is still the weakest
  measured axis; see the benchmark below.
- **The people graph.** People and the relationships between them live in
  PostgreSQL with enforced invariants: tiered name resolution (an ambiguous name
  is never guessed or attached to), relationship changes are atomic, edges are
  closed with a reason and never deleted, history is readable `as of` a date,
  and each edge points at the turn that evidenced it.
  [ADR-relationship-memory.md](docs/adr/ADR-relationship-memory.md)
- **Ask-to-remember (on)** ("remember that ..."), and **roles are stated, never
  guessed** (on): Zoe will not decide that an unexplained name is your mother.

**The memory bake-off.** In October 2026 Zoe's memory was measured against
candidate arms built on Hindsight and MemPalace on the Zoe Memory Bench, under a
decision rule fixed before the run. No candidate passed the floors and Zoe's
memory led every axis it shares with them, so **Zoe's own memory was kept** and
neither engine was adopted. The record, with per-axis numbers and the weak spots
it found (forgetting and poisoning, both 18 of 21 in that run):
[docs/research/memory-bakeoff-decision-2026-10-08.md](docs/research/memory-bakeoff-decision-2026-10-08.md).

### The person half

Memory is half of feeling known; the other half is what Zoe does with it. These
behaviours were measured on a person-likeness bench and then moved into code
where a prompt could not fix them on a 4B model
([samantha-person.md](docs/knowledge/samantha-person.md)).

| Behaviour | What it does | Code default |
|-----------|--------------|--------------|
| Hold the fact | Keeps what you told her against a bare "no, I'm sure it's Thursday", changes it on evidence or a second confirmation | shadow |
| Ask when ambiguous | One question naming the choice ("which Marisol: your colleague or your sister?") before acting | shadow |
| Clean goodbye | A farewell stays short, with no hook or question | shadow |
| Restraint | Sensitive rows are withheld from recall and unprompted raises unless you pull them; a spoken mute persists | shadow |
| Recall relevance gate | Chooses which durable rows enter a turn's context | shadow |
| Self-model | Answers "what can you do?", "can you order groceries?", "are you always listening?" from the real tool registry and flags, with an honest no | shadow |
| Pull, not push | The orb shows a count, never content; a proactive worry is delivered once, when you ask | on |

The first four are specified in
[person-half-guards.md](docs/knowledge/person-half-guards.md) and
[restraint.md](docs/knowledge/restraint.md); the measured enforce-versus-shadow
numbers and the operator steps to flip them are in
[person-half-enforce-pack-2026-10-09.md](docs/knowledge/person-half-enforce-pack-2026-10-09.md).
Flipping a floor to `enforce` is the operator's decision; the flag inventory
lists every default.

- **Voice and chat lane parity.** The spoken lane runs the same conversation
  tiers as typed chat (corrections, contact lookups, spoken feedback, and the
  safety cases where "are you sure?" must not confirm a pending write). A
  harness drives the same utterance through the real chat path and the real
  voice path; the measured result was 5 of 12 cells passing on the voice lane
  before and 12 of 12 after. [voice-lane-parity.md](docs/knowledge/voice-lane-parity.md)
- **Distress hand-off (on).** A turn that signals self-harm, abuse or danger
  gets no model reply: a fixed, warm pointer to a trusted person and the local
  crisis line, the turn is kept out of memory, digests and reflection, and the
  household contact is told once, with none of the words. Negation and quotation
  guards lower a hand-off to a gentler check-in (that tier is still shadow), and
  only known idioms such as "dying to see it" are ignored. Setup (country,
  contact) is an operator step, and the detector covers English and Spanish.
  [distress-handoff.md](docs/knowledge/distress-handoff.md),
  [emotional-safety-note.md](docs/governance/emotional-safety-note.md)
- **Language independence.** The words a rule reacts to (negation, "no longer",
  hedges, role words, distress cues, self-model sentences) live as data in
  [`services/zoe-data/lexicons_data/<lang>.json`](services/zoe-data/lexicons_data),
  not as English regexes in code. A language with no lexicon contributes nothing;
  it is never guessed from English. Lexicons exist for English (reviewed),
  Spanish, French, German, Japanese and Chinese; only English is reviewed by a
  native reader, the others are unreviewed first drafts, and the distress
  detector and self-model currently cover English and Spanish.
  [structural-floors.md](docs/knowledge/structural-floors.md) (the
  language-independent claim-row floors ship in shadow)

### The night brain (in development)

Not yet running nightly. Two pieces are built and being measured:

- **The 12B night window.** A nightly window where the live 4B, voice and the
  app go to sleep for about an hour, a bigger model (Gemma 4 12B) runs the night
  jobs at a larger context, and everything is woken again in order with each
  service's `/health` polled. The window itself runs and has been trialled; the
  nightly timer is **not installed**, no job has yet completed end to end on the
  12B, and the box is memory-tight, so this is an owner-gated step.
  [docs/knowledge/night-window.md](docs/knowledge/night-window.md)
- **The night mind.** A reflection pass that turns each member's day into a few
  cited, pointer-shaped observations (threads in progress, what changed, what
  went quiet, what to raise and what to leave alone) so Zoe can say "you
  mentioned the sore knee on Monday, how is it today?". It is **off by
  default** (`ZOE_NIGHT_MIND`), some of its measured cells are still red on the
  small model, and the plan is a shadow week on real days before it serves
  anything. [docs/knowledge/night-mind.md](docs/knowledge/night-mind.md)

## How Zoe is measured

Quality here is a measured thing with regression gates, not a feeling.

| Instrument | What it scores | Where |
|------------|----------------|-------|
| **Samantha bar** | About two dozen scripted multi-day memory and companion scenarios against throwaway demo users through the live API, with a baseline and a negative control on every check | [samantha-bar.md](docs/knowledge/samantha-bar.md), `scripts/perf/samantha_bar.py` |
| **Day-sim** | A week-in-the-life simulation proving the whole knows-you chain for one user | same doc, `scripts/perf/samantha_day_sim.py` |
| **Person bench** | The P family: sycophancy, restraint, goodbyes, ask-versus-tell, with pre-registered bars and confidence intervals | [samantha-person.md](docs/knowledge/samantha-person.md), `scripts/perf/samantha_person.py` |
| **Zoe Memory Bench** | Authority, forgetting, identity, extraction, temporal, poisoning, exact words, reflection, multi-hop and more, with per-axis intervals and the pre-registered bake-off rule | [zoe-memory-bench.md](docs/knowledge/zoe-memory-bench.md), `scripts/perf/zmb/` |
| **Lane parity rig** | The same utterance through the chat and voice paths | [voice-lane-parity.md](docs/knowledge/voice-lane-parity.md) |
| **Voice replay gate** | Recorded real utterances replayed through the live voice path; a change to the voice path cannot go live without a fresh passing run | [voice-pipeline.md](docs/knowledge/voice-pipeline.md), `scripts/maintenance/voice_regression_probe.py` |

The instruments are not all green and say so: the bar and person bench carry
known targets (scenarios expected to fail until a flag is flipped), and the gap
register lists what is still missing, ranked by evidence
([docs/research/samantha-gap-register-2026-10-09.md](docs/research/samantha-gap-register-2026-10-09.md)).
Every instrument must first turn red when the
thing it checks is broken; skip or timeout is never counted as a pass. Found
problems are fixed or written to
[docs/knowledge/open-problems.md](docs/knowledge/open-problems.md).

## Prerequisites

- **Host:** Linux. Reference target is NVIDIA Jetson Orin (JetPack 6, CUDA 12.6).
- **Docker** + Docker Compose plugin.
- **Python 3.10+** with `pip` (host-native services run on system Python).
- **Node.js** (only for the Flue brain sidecar and the Telegram front door).
- **A GGUF chat model** for `llama-server` (~8 GB VRAM for the reference model).
- The repo is expected at `~/assistant`. Some setup scripts assume that path.

## Install

### Quick install (recommended)

One idempotent script does the host: generates `.env` secrets, starts the
Docker spine, runs Postgres migrations, downloads the Gemma 4 brain, and installs
the host-native systemd services.

```bash
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git ~/assistant
cd ~/assistant
./scripts/setup/install-jetson.sh          # add -y for non-interactive
```

It is re-runnable: existing `.env`, models, and running services are left in
place. After it finishes, fill in the user-supplied values it flags in `.env`
(`HA_ACCESS_TOKEN`, and cloud `*_API_KEY`s only if you use the cloud agent
tiers). Useful flags: `--skip-models`, `--skip-docker`, `--skip-systemd`,
`--with-router`, `--models-dir DIR`, `--llama-bin PATH` (`--help` for all).

It enables `llama-server`, `zoe-data` and `kokoro-tts`. It installs but never
enables the opt-in units (`flue-zoe-brain-2x`, `flue-executor`).

> **llama.cpp is platform-specific.** The installer downloads the model but does
> not build llama.cpp. If the `llama-server` binary isn't present it says so and
> skips enabling that unit; build llama.cpp for your platform, point
> `~/.config/systemd/user/llama-server.service` at your binary + GGUFs, then
> `systemctl --user enable --now llama-server`. See
> [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md).

### Choosing the agent brain

The installer gives you the model server, backend, voice and UI. It does not
build an agent brain, and an unset `ZOE_BRAIN_BACKEND` selects the `core` lane
(`services/zoe-core`). To run the brain the way the reference deployment does,
build and enable the Flue 2.x sidecar, then select it:

```bash
cd ~/assistant/labs/flue-zoe-brain-2x && npm ci && npm run build
cp .env.example .env            # fill in the tokens it lists; see the lab README
systemctl --user enable --now flue-zoe-brain-2x
# in services/zoe-data/.env:  ZOE_BRAIN_BACKEND=flue
systemctl --user restart zoe-data
```

The sidecar URL (`:3579`) and wire version (2) are the client defaults. Full
runbook: [labs/flue-zoe-brain-2x/README.md](labs/flue-zoe-brain-2x/README.md).

### Optional add-ons

```bash
# Music Assistant on :8095
docker compose -f docker-compose.modules.yml up -d music-assistant
# Two-stage router decoder model (then enable functiongemma-router and set ZOE_ROUTER_HEAD=active)
./scripts/setup/download_gguf_models.sh --with-router
```

### Manual install

<details>
<summary>Step-by-step equivalent of the quick installer</summary>

```bash
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git ~/assistant
cd ~/assistant

# Secrets: generate strong values for POSTGRES_URL/password + tokens
cp .env.example .env
cp services/zoe-data/.env.example services/zoe-data/.env

# Docker spine
docker compose up -d zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge

# PostgreSQL migrations (alembic + auth DDL)
./scripts/deploy/migrate.sh

# Local brain: Gemma 4 E4B-QAT + MTP drafter
./scripts/setup/download_gguf_models.sh

# Host-native services (edit llama-server.service for your binary + model path)
mkdir -p ~/.config/systemd/user
cp scripts/setup/systemd/{llama-server,zoe-data,kokoro-tts}.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llama-server zoe-data kokoro-tts
```
</details>

See [scripts/setup/systemd/README.md](scripts/setup/systemd/README.md) for the
host-native services and [OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md)
for start/stop order and troubleshooting.

## Verify

```bash
curl -f http://localhost:8000/health   # zoe-data backend
curl -f http://localhost:8002/health   # zoe-auth
curl -f http://localhost:11434/health  # llama-server
docker compose ps
```

- **UI:** `https://localhost` (or `http://localhost`)
- **API docs:** `http://localhost:8000/docs`
- **Home Assistant:** `http://localhost:8123`

## Day-to-day

```bash
# Restart backend after Python changes
systemctl --user restart zoe-data
# Restart UI after nginx/HTML changes
docker compose restart zoe-ui
```

`./RESTART_SERVICES.sh` restarts the common set. The full "restart after X"
matrix is in the [OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md). Merging
to `main` does not by itself change a running box; see
[docs/knowledge/merge-and-deploy.md](docs/knowledge/merge-and-deploy.md).

## Touch panel (optional)

Zoe supports a kiosk touch panel on a separate Linux device (Raspberry Pi). One
script provisions it over SSH, covering the kiosk UI **and** the wake-word/voice
daemon:

```bash
./scripts/setup/install-pi.sh \
  --host <TOUCH_PANEL_IP> \
  --user <USER> \
  --server-url https://<ZOE_SERVER_IP> \
  --panel-id zoe-touch-pi
```

Mint a panel device token on the host (admin: `POST /api/panels/<panel-id>/token`)
and pass it with `--device-token` to enable voice auth in the same run. Use
`--skip-voice` / `--skip-kiosk` to run just one half; `--help` for all flags.
The underlying pieces live in `scripts/setup/touchscreen/` and
`scripts/setup/pi_voice_daemon_install.sh`; guides in [docs/guides/](docs/guides/).

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [docs/VISION.md](docs/VISION.md) | The north star and the principles |
| [docs/CANONICAL.md](docs/CANONICAL.md) | What is locked and live (the models, the brain lanes) |
| [QUICK-START.md](QUICK-START.md) | Start/stop cheat sheet |
| [docs/guides/OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md) | Boot order, env vars, troubleshooting |
| [docs/guides/REPO_LAYOUT.md](docs/guides/REPO_LAYOUT.md) | Where things live |
| [docs/knowledge/runtime-topology.md](docs/knowledge/runtime-topology.md) | Live services, ports, logs |
| [docs/knowledge/](docs/knowledge/index.md) | Reference records for each subsystem named above |
| [docs/knowledge/flag-inventory.md](docs/knowledge/flag-inventory.md) | Every `ZOE_*` flag and its code default (generated) |
| [docs/architecture/](docs/architecture/) | Plans and blueprints, e.g. the [Samantha brain blueprint](docs/architecture/samantha-brain-blueprint-2026-10-09.md) |
| [docs/research/](docs/research/README.md) | Decision records and field research, including the memory bake-off |
| [docs/knowledge/open-problems.md](docs/knowledge/open-problems.md) | The ledger of known, unfixed problems |
| [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md) | Platform notes (Jetson/Pi/x86) |
| [CAPABILITIES.md](CAPABILITIES.md) | Generated MCP tool inventory (its agent-tier table is legacy) |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [AGENTS.md](AGENTS.md) | Conventions for AI coding agents in this repo |

## License

MIT, see [LICENSE](LICENSE).
