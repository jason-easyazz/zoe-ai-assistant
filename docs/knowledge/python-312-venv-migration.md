---
type: Runbook
title: zoe-data Python 3.12 venv migration (B0.7)
description: Measured cp312/aarch64 resolvability of every zoe-data pin, the uv venv build recipe, the systemd drop-in that switches the interpreter, the verification gates in order, rollback, and the hard blockers found — for moving zoe-data ONLY off the EOL system Python 3.10 while Kokoro and llama-server stay on 3.10 / CUDA 12.6.
tags: [python-3.12, venv, uv, zoe-data, dependencies, runbook, b0.7]
timestamp: 2026-09-26T00:00:00Z
---

# zoe-data Python 3.12 venv migration (B0.7)

Program item **B0.7** in [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md).
Motivation: [state-of-zoe-review-2026-09-25.md](state-of-zoe-review-2026-09-25.md) — system
Python 3.10.12 is EOL 2026-10-31, and three pins are already stuck on it (`onnxruntime` 1.23.2 is
the last cp310 wheel, `websockets` 16.1.1 the last release for 3.10, `av` 18 needs 3.11).

**Scope.** zoe-data only. Kokoro (`kokoro-tts.service`) and llama-server keep the system
Python 3.10 / CUDA 12.6 stack; nothing here touches them, `/usr`, or
`~/.local/lib/python3.10/site-packages`.

**Status 2026-09-26: venv BUILT and import-verified in a worktree; NOT applied to the service.**
Everything in §1–§3 is measured on the Orin (uv 0.10.11, uv-managed CPython 3.12.13 aarch64,
glibc 2.35). §5 gates 1–2 ran; gates 3–5 need the operator restart.

## 0. What was measured, and how (re-derivable)

- Resolution: `uv pip compile services/zoe-data/requirements-py312.txt --python-version 3.12
  --python-platform aarch64-manylinux_2_31` (the build script's `--dry-run` runs exactly this).
- Wheel existence per resolved pin: PyPI JSON per `pkg==ver`, classifying the file list for a
  `cp312`/`abi3`/`py3` aarch64 wheel and checking every `manylinux_X_Y` tag is ≤ glibc 2.35.
- Then the real thing: full install into a worktree venv (`1.9 GB`, 42 s, 169 dists) and an
  import/exercise smoke of every load-bearing package (§1 "measured" column).
- Undeclared dependencies: an AST walk over every `import` in `services/zoe-data` (non-test),
  each dotted module checked with `importlib.util.find_spec` inside the venv. This is the only
  way to see what the 3.10 site-packages has been carrying transitively.

## 1. Resolved package table

Legend — **current** = `requirements.txt` pin / the 3.10 box; **cp312 target** = what
`requirements-py312.txt` installs (policy: identical to the box wherever a cp312 wheel exists,
so the interpreter is the only variable in the first cut); **step-up** = the newer version that
also resolves on 3.12, held for its own gated move.

| package | current (box) | cp312 target | wheel on PyPI (aarch64) | measured / notes |
|---|---|---|---|---|
| fastapi | 0.141.1 | 0.141.1 | pure | imports |
| uvicorn[standard] | 0.49.0 | 0.49.0 | pure (+uvloop/httptools/watchfiles cp312) | **`--ws auto` → `websockets_impl` (legacy) on 3.12 with websockets 16.1.1 AND 17.1** — the 0.49 cap is about not crossing 0.50, unchanged. 0.54.0 is latest. |
| websockets | 16.1.1 | 16.1.1 | cp312 + pure | **step-up 17.1** (≥3.11): keeps `websockets.legacy.*` and the deprecated `websockets.server.WebSocketServerProtocol` lazy alias that uvicorn 0.49's legacy impl imports (verified in source + by import). Replay-gate the move. |
| pydantic / -core | 2.13.5 | 2.13.5 | cp312 | imports |
| aiosqlite, asyncpg, alembic, psycopg2-binary, python-multipart, httpx, aiohttp | as pinned | same | cp312 / pure | all import; asyncpg 0.31.0 `manylinux_2_28`, psycopg2-binary 2.9.12 `2_27/2_28` |
| python-jose[cryptography], PyJWT | 3.5.0 / 2.15.0 | same | pure; cryptography 50.0.1 cp312 abi3 | imports |
| ag-ui-protocol, python-json-logger, PyYAML, segno | pinned | pinned exact | pure | imports |
| mempalace | 3.3.1 | 3.3.1 | pure | imports on 3.12 (declares `>=3.9`). B0.8 moves it with chromadb. |
| chromadb | 0.6.3 | 0.6.3 | pure + chroma-hnswlib 0.7.6 cp312 `manylinux_2_17` | **PersistentClient add/query works on 3.12.** Its telemetry logs `capture() takes 1 positional argument` against posthog 7.x (box: 7.12.0, same class) — noise, not a failure. 1.5.9 (abi3 aarch64 wheel) is B0.8. |
| numpy | 1.26.4 (`<2`) | **1.26.4 exact** | cp312 | **Nothing in the set declares `numpy<2` on 3.12** — the uncapped resolve picks 2.5.3, and chroma-hnswlib 0.7.6's cp312 wheel imports/adds/queries on 2.5.3. Held at 1.26.4 so the router-head training numpy and STT/embedding numerics are unchanged in cut 1; numpy 2 = step-up, own gate. |
| onnxruntime | 1.23.2 | 1.23.2 | cp312 `2_27/2_28` | Silero VAD `.onnx` session builds. **Step-up 1.30.0** (latest; ≥3.11; cp312 `2_28`). fastembed 0.8.0 allows `>=1.17,!=1.20,!=1.24.0/1` on 3.12. |
| moonshine-voice | 0.0.62 | 0.0.62 | `py3-none-manylinux_2_31_aarch64` | **ABI-independent wheel — the SAME file the 3.10 box runs.** Imports (reports `__version__ 0.1.0`, known). Model load left to the replay gate. |
| transformers | 5.17.0 | 5.17.0 | pure | `WhisperFeatureExtractor` imports |
| fastembed | 0.8.0 | 0.8.0 | pure | imports; 0.8.1 latest |
| scikit-learn / joblib | 1.7.2 / 1.5.3 | 1.7.2 / 1.5.3 | cp312 | **Head loads, `predict_proba` OK.** Step-up 1.9.1 (≥3.11): the 1.7.2 artifact loads with `InconsistentVersionWarning` and predicts **bit-identically** (max Δproba = 0.0 on probe vectors, numpy 2.5.3). Re-export is a CONTRACT matter (`services/zoe-data/AGENTS.md`: pins must equal training pins in `labs/setfit-router/requirements.txt`), not a correctness blocker — retrain/re-export in the same PR that moves the pin. |
| APScheduler / tzlocal / SQLAlchemy | 3.10.4 / 2.1 / 2.0.54 | same exact | pure / cp312 | `SQLAlchemyJobStore` imports, `pytz` present, local zone resolves. **`SQLAlchemy>=2.0,<3.0` resolves to 2.1.1 on 3.12** — pinned to the box's 2.0.54 so B0.9's jobstore question is not silently reopened. |
| pywebpush / py-vapid | 2.5.0 / 1.9.4 | same | pure; `http-ece` 1.2.1 is a **pure-Python sdist** (only sdist in the set) | builds in uv's isolated build env |
| **prometheus-client** | 0.25.0 (box), **undeclared** | 0.25.0 | pure | **Unguarded module-level import** in `memory_metrics.py`, `voice_metrics.py`, `guest_policy.py`. Declared in both manifests this PR. |
| **livekit-protocol** | 1.1.8 (box), **undeclared** | 1.1.8 | pure | `livekit_aiortc.py` does `from livekit.protocol import rtc`; `livekit` does NOT depend on it — it rode in on the unused `livekit-agents` (CI hand-installs it). Found because the ci_safe lane failed collection without it. Declared in both manifests. |
| psutil | 7.2.2 (box), undeclared | 7.2.2 (`drift-optional`) | cp312 | guarded import in `mcp_server.py` status tool |
| livekit | 1.1.20 | 1.1.20 exact | cp312 | `livekit.rtc` imports |
| aiortc / av | 1.15.0 / 17.1.0 | same | pure / **cp311-abi3** `2_28` | `AudioResampler` builds. **av 18 is BLOCKED by aiortc 1.15.0's `av<18` cap** (latest aiortc), not by Python. |
| Resemblyzer | 0.1.4 | 0.1.4 **`--no-deps`** | pure | see §3 blocker 1: its declared deps are wrong for 3.12 |
| ↳ webrtcvad | 2.0.10 | **webrtcvad-wheels 2.0.14** | cp312 `2_17` | 2.0.10 is sdist-only; builds with gcc, then **fails to import** (`import pkg_resources`; setuptools 84 no longer ships it). The fork has no `pkg_resources` import; `Vad(2).is_speech` works. |
| ↳ torch | 2.8.0 | **`torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_aarch64.whl`** (PyTorch CPU index, 159 MB), pinned by URL | — | PyPI's aarch64 wheel is CPU-only through 2.8 (102 MB), then a **454 MB CUDA-13 bundle** from 2.9 that drags in `nvidia-*-cu13` + `triton` (the first resolve did exactly that). `torch.cuda.is_available()` is False in the venv, as intended. |
| ↳ librosa / scipy | 0.11.0 / 1.15.3 | 0.11.0 / 1.17.1 | pure / cp312 | uncapped 3.12 resolve picks librosa **1.0.0** (≥3.12, API removals) — held at 0.11.0 for Resemblyzer |
| ↳ typing | 3.7.4.3 (box) | **dropped** | — | py2 backport; must not be installed on 3.12 |
| edge-tts, ddgs, cloakbrowser | 7.2.8 / 9.16.0 / 0.3.28 | same exact (floors → box) | pure | cloakbrowser 0.5.11 is latest; held. Playwright resolves 1.63.0 (box 1.59.0); its browser binary is a separate download. |
| openinference-* / arize-phoenix-otel | on box | **not included** | pure | `zoe_agent._setup_otel` imports inside `try` behind `_OTEL_ENABLED`; optional tracing, add when wanted |
| mcp, memu-py, livekit-agents, kokoro, ctranslate2 | on box | **not included** | — | zoe-data imports none of them (mcp bridge is its own service; kokoro/ctranslate2 are the sidecar's). The venv is where they finally fall away. |

Not a dependency, but found by the same scan: `routers/memories.py` imports a `user_prefs`
module that exists nowhere in the repo (two opt-out endpoints → 500). Pre-existing, filed
separately; not a 3.12 matter.

## 2. Build recipe (uv, no root, idempotent)

```bash
# inside a worktree (never the live checkout for git; the venv lives OUTSIDE the repo)
scripts/setup/build_py312_venv.sh --dry-run   # prints the plan + resolves the manifest, installs nothing
scripts/setup/build_py312_venv.sh             # ~/.zoe/venvs/zoe-data-py312, ~1.9 GB, ~1 min warm cache
scripts/setup/build_py312_venv.sh --check     # interpreter, drift check against the manifest, import smoke
```

What it does, in order: `uv python install 3.12` (uv-managed CPython under
`~/.local/share/uv/python`, no sudo) → `uv venv <dir> --python 3.12` (skipped when a 3.12 venv is
already there) → `uv pip install -r services/zoe-data/requirements-py312.txt` (phase 1) →
`uv pip install --no-deps resemblyzer==0.1.4` (phase 2, §3 blocker 1) → import smoke (fails the
build if any load-bearing import fails; asserts `--ws auto` still resolves to the legacy impl and
that torch has no CUDA). It refuses to install when `MemAvailable` < 500 MB (`ZOE_PY312_MIN_MEM_MB`)
and runs everything under `nice -n 15`.

**The manifest is the installer here** — the inverse of `requirements.txt`'s box-first rule, and
deliberately so: a fresh interpreter has no site-packages to reconcile *to*. Drift is still
measured, with the existing tool pointed at the venv interpreter:
`~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/requirements_drift_check.py services/zoe-data/requirements-py312.txt`.

## 3. Hard blockers found (all resolved in the manifest, none by hand-patching)

1. **Resemblyzer's declared dependencies are wrong for 3.12** → two-phase install (`--no-deps`),
   with `webrtcvad-wheels`, the CPU torch wheel URL, librosa 0.11 and scipy declared explicitly.
   Any resolver that follows Resemblyzer's metadata on 3.12 gets an un-importable `webrtcvad`, a
   ~2 GB CUDA torch, and the `typing` backport.
2. **Two direct imports were declared nowhere** (`prometheus_client`, `livekit.protocol`). A
   venv built from the old manifest starts, then dies at the first `import memory_metrics` /
   fails to import `livekit_aiortc`. Both now declared (and added to `requirements.txt`, box
   versions, per its "declare what the code imports" rule).
3. **`av` 18 cannot move** until aiortc releases with `av<19` — a package cap, so it stays 17.1.0
   (which has a cp311-abi3 aarch64 wheel and is what the box runs).
4. **`SQLAlchemy` and `librosa` floors resolve to new majors on 3.12** (2.1.1, 1.0.0). Pinned
   exact.

No blocker in: onnxruntime, websockets, numpy, scikit-learn, chromadb/mempalace 0.6.3/3.3.1,
moonshine-voice, fastembed, APScheduler+tzlocal 2.x, python-jose, psycopg2-binary, asyncpg,
cloakbrowser — each has a cp312 (or ABI-independent) aarch64 wheel at the current pin.

## 4. Switching the service interpreter — a DROP-IN, not a template copy

The tracked template `scripts/setup/systemd/zoe-data.service` has `ExecStart=/usr/bin/python3 -m
uvicorn main:app --host 0.0.0.0 --port 8000`. The live unit at
`~/.config/systemd/user/zoe-data.service` carries host-specific edits plus five untracked drop-ins
in `zoe-data.service.d/` (`10-sync-zoe-self.conf`, `20-capture-output.conf`,
`40-memory-tuning.conf`, `memory.conf`, `priority.conf`) — `cp`-ing a template over it clobbers
them, which is the exact failure the systemd README records three times over. So the switch is
one more drop-in:

```ini
# ~/.config/systemd/user/zoe-data.service.d/60-py312-venv.conf
[Service]
# An empty ExecStart= first: systemd APPENDS ExecStart lines for Type=simple, and two
# ExecStart lines is a unit-load error, so the drop-in must clear the template's before
# setting its own.
ExecStart=
ExecStart=%h/.zoe/venvs/zoe-data-py312/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Apply: `systemctl --user daemon-reload && systemctl --user restart zoe-data`, then
`systemctl --user cat zoe-data | grep -n ExecStart` must show exactly the venv line last, and
`readlink /proc/$(systemctl --user show -p MainPID --value zoe-data)/exe` must end in
`cpython-3.12.*/bin/python3.12`. `systemctl is-active` lies about readiness — poll `/readyz`.

Everything else in the unit is untouched and still applies: `EnvironmentFile`s, `LD_LIBRARY_PATH`
(harmless; the venv's onnxruntime/torch are CPU wheels that link nothing under `cuda-12.6`),
`MemoryLow`/`MemorySwapMax=0`, `MALLOC_ARENA_MAX=2`. `sync_zoe_self.sh` (an `ExecStartPre`)
runs no pip and is unaffected.

**`deploy.yml` caveat.** Its "Install / refresh Python deps" step does `pip3 install --user …`
into the 3.10 site-packages. After the switch that step is a no-op for the running service and
must be pointed at the venv (`~/.zoe/venvs/zoe-data-py312/bin/python -m pip` — uv venvs ship no
pip, so use `uv pip install --python …`) or dropped in favour of `build_py312_venv.sh`. Do this in
the cutover PR, not before: until the drop-in is live it would be a wish.

## 5. Verification gates, in order

1. **ci_safe lanes in the venv, under CI's conditions** (network-blocked, `TZ=UTC`) — from a
   worktree, never the live checkout:
   ```bash
   V=~/.zoe/venvs/zoe-data-py312/bin/python
   uv pip install --python $V "pytest==8.3.5" "pytest-asyncio==1.3.0"
   unshare -rn --map-root-user bash -c 'ip link set lo up; TZ=UTC ZOE_DATA_DB=":memory:" \
     ZOE_MEMORY_STARTUP_STRICT=false PYTHONPATH=$PWD/services/zoe-data $V -m pytest \
     services/zoe-data/tests -m ci_safe --ignore=services/zoe-data/tests/samantha_live -q \
     --override-ini=asyncio_mode=auto'
   unshare -rn --map-root-user bash -c 'ip link set lo up; TZ=UTC PYTHONPATH=$PWD:$PWD/services/zoe-data \
     $V -m pytest tests/unit -m ci_safe -q --override-ini=asyncio_mode=auto'
   ```
   The first attempt is what found blocker 2 (`livekit.protocol`): a collection error, not a
   test failure — which is why this gate runs before anything touches the service.
   **Measured 2026-09-26 in the worktree venv (CPython 3.12.13):** zoe-data lane **6445 passed,
   28 skipped, 276 deselected in 81 s**; `tests/unit` lane **1125 passed, 55 skipped, 2 failed in
   198 s** — and both failures reproduce identically under `/usr/bin/python3` (3.10) in the same
   harness, so they are the harness, not the interpreter: `test_mcp_server::
   test_stdio_tools_list_exposes_production_tools` needs Postgres on :5432 (CI provides a pg
   service; the network namespace has none) and `test_reap_stale_serena::
   test_recycle_passes_the_user_bus_env` asserts `/run/user/$(id -u)`, which `--map-root-user`
   turns into uid 0. Run those two outside `unshare` to see them pass.
2. **Replay gate with THIS interpreter, service untouched.** The probe defaults to
   `--stt inprocess`, i.e. Moonshine + onnxruntime + numpy load in the probe's own process — so
   running it with the venv python measures the venv's STT stack against the LIVE brain without a
   restart (`measure_voice` re-invokes `sys.executable`, so the interpreter propagates):
   ```bash
   flock /tmp/zoe-voice-harness.lock ~/.zoe/venvs/zoe-data-py312/bin/python \
     scripts/maintenance/voice_regression_probe.py --service-dir /home/zoe/assistant/services/zoe-data --stt inprocess
   ```
   Said-vs-did must not regress and per-stage medians must stay inside the baseline's ratio; needs
   ≥ 2 GB quiet headroom (the in-process Moonshine load).
3. **Drop-in + restart** (§4) → `/readyz` all `ok`, including `dependencies.stt` and the
   **`memory_recall_probe`** result the startup runs (it self-recalls one real stored row through
   the venv's chromadb + hnswlib; "search returns" is not the bar, "returns the RIGHT row" is).
4. **Replay gate against the live path**: same command with `--stt remote` (STT via the restarted
   service, no second Moonshine load) — the only run that exercises the venv's uvicorn/websockets
   `/ws/voice/` path end to end.
5. Watch one idle→first-utterance cycle and `journalctl --user -u zoe-data` for
   `InconsistentVersionWarning`/`DeprecationWarning` floods; then run
   `requirements_drift_check.py` with the venv interpreter (0 MISMATCH expected).

Then, one at a time, each its own PR + replay gate: websockets 17.1 → onnxruntime 1.30.0 →
numpy 2.x (re-export the router head with sklearn 1.9 in the same PR) → B0.8 (chromadb 1.5.x +
mempalace 3.10 on a palace copy) → B0.9 (APScheduler 3.11 with `export_jobs`/`import_jobs`).

## 6. Rollback

Remove the drop-in and restart — the template's `/usr/bin/python3` ExecStart returns, and the 3.10
site-packages was never modified:

```bash
rm ~/.config/systemd/user/zoe-data.service.d/60-py312-venv.conf
systemctl --user daemon-reload && systemctl --user restart zoe-data   # then poll /readyz
```

The venv directory can stay (nothing references it); `rm -rf ~/.zoe/venvs/zoe-data-py312`
reclaims 1.9 GB. Reverting the two manifest additions (`prometheus-client`, `livekit-protocol`)
is not part of rollback — they describe the box on 3.10 too.

## 7. Open items

- Operator: gates 3–5 (restart), then flip `deploy.yml`'s pip step (§4 caveat) in the cutover PR.
- `docs/knowledge/numpy2-jetson-migration.md` assumed an on-box `onnxruntime-gpu` story; for
  zoe-data the venv makes that moot (CPU wheels), which shrinks that WIP to Kokoro's stack.
- `validate.yml`'s "Resolve every requirements manifest" job runs on 3.10 and does not know
  `requirements-py312.txt`; add it under a 3.12 matrix entry when the cutover lands (it needs the
  `--no-deps` phase expressed, e.g. a `--dry-run` of the build script).
