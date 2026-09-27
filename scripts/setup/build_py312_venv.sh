#!/usr/bin/env bash
# Build the zoe-data Python 3.12 virtualenv (program item B0.7) with uv.
#
# WHAT IT BUILDS: a venv OUTSIDE the repo, default ~/.zoe/venvs/zoe-data-py312,
# from services/zoe-data/requirements-py312.txt, on a uv-managed CPython 3.12
# (no root, nothing touches /usr or ~/.local/lib/python3.10). Kokoro and
# llama-server stay on the system Python 3.10 / CUDA 12.6 — this venv is for
# zoe-data ONLY, and building it changes nothing that runs: the service keeps
# its /usr/bin/python3 ExecStart until an operator applies the systemd DROP-IN
# described in docs/knowledge/python-312-venv-migration.md.
#
# Two-phase install, on purpose. Resemblyzer 0.1.4 declares `webrtcvad` (an
# sdist whose module imports pkg_resources, gone from setuptools 84 — it builds
# and then fails to import), the py2 `typing` backport, and an unbounded torch
# (the PyPI aarch64 wheel is a 454 MB CUDA-13 bundle from 2.9). So the manifest
# carries webrtcvad-wheels + the exact CPU torch wheel + librosa/scipy, and
# Resemblyzer itself goes in afterwards with --no-deps.
#
# Usage:
#   scripts/setup/build_py312_venv.sh --dry-run    # print the plan, resolve only, install nothing
#   scripts/setup/build_py312_venv.sh              # build / converge the venv (idempotent; sync removes strays)
#   scripts/setup/build_py312_venv.sh --check      # verify an existing venv: interpreter, drift (incl. extraneous), imports
#   scripts/setup/build_py312_venv.sh --refresh    # deploy.yml: additive install of both phases into an EXISTING venv only
# Env: ZOE_PY312_VENV (venv dir), ZOE_PY312_PYTHON (default 3.12),
#      ZOE_PY312_MIN_MEM_MB (refuse to install below this MemAvailable; default 500).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

VENV_DIR="${ZOE_PY312_VENV:-$HOME/.zoe/venvs/zoe-data-py312}"
PY_VERSION="${ZOE_PY312_PYTHON:-3.12}"
# venv_version reports MAJOR.MINOR, so compare against the selector's MAJOR.MINOR:
# an exact selector like 3.12.13 must not make every later build/--check refuse
# the venv it just created (#1706). A non-numeric selector is compared as given.
PY_MM="$(sed -nE 's/^([0-9]+\.[0-9]+)(\.[0-9]+)?$/\1/p' <<<"$PY_VERSION")"
PY_MM="${PY_MM:-$PY_VERSION}"
MIN_MEM_MB="${ZOE_PY312_MIN_MEM_MB:-500}"
REQ_FILE="$REPO_ROOT/services/zoe-data/requirements-py312.txt"
# Phase 2 (see header). Keep in step with the Resemblyzer block of the manifest.
PHASE2_NO_DEPS=("resemblyzer==0.1.4")
PLATFORM="aarch64-manylinux_2_31"   # Ubuntu 22.04 / glibc 2.35; wheels tagged 2_28/2_31 both fit
NICE=(nice -n 15)

MODE="build"
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --check)   MODE="check" ;;
    --refresh) MODE="refresh" ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $arg (use --dry-run, --check, --refresh, or none)" ;;
  esac
done

UV="${UV_BIN:-$(command -v uv || true)}"
[[ -z "$UV" && -x "$HOME/.local/bin/uv" ]] && UV="$HOME/.local/bin/uv"
[[ -n "$UV" && -x "$UV" ]] || die "uv not found (expected on PATH or at ~/.local/bin/uv): https://docs.astral.sh/uv/"
[[ -f "$REQ_FILE" ]] || die "manifest missing: $REQ_FILE"

VENV_PY="$VENV_DIR/bin/python"
venv_version() { [[ -x "$VENV_PY" ]] && "$VENV_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }
mem_available_mb() { awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo; }

smoke() {
  # Import the load-bearing stack. No model loads (Moonshine/Kokoro are the
  # replay gate's job); the router head + Silero VAD are small enough to prove
  # sklearn/joblib and onnxruntime actually run, not just import.
  "${NICE[@]}" "$VENV_PY" - "$REPO_ROOT" <<'PY'
import importlib, os, sys, warnings
warnings.filterwarnings("ignore")
root = sys.argv[1]; failed = 0
def step(name, fn):
    global failed
    try: print(f"  ok   {name}: {fn()}")
    except Exception as e:
        failed += 1; print(f"  FAIL {name}: {type(e).__name__}: {str(e)[:140]}")
step("python", lambda: sys.version.split()[0])
for m in ("fastapi", "pydantic", "sqlalchemy", "apscheduler", "tzlocal", "pytz", "psycopg2", "asyncpg",
          "alembic", "jose", "jwt", "chromadb", "mempalace", "moonshine_voice", "fastembed", "transformers",
          "av", "aiortc", "livekit.rtc", "edge_tts", "pywebpush", "segno", "ddgs", "cloakbrowser", "yaml",
          "prometheus_client", "webrtcvad", "resemblyzer"):
    step(m, lambda m=m: getattr(importlib.import_module(m), "__version__", "imported"))
def ws():
    from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol
    mod = AutoWebSocketsProtocol.__module__
    assert mod.endswith("websockets_impl"), mod   # the 0.49 cap's whole point
    return mod
step("uvicorn --ws auto -> legacy websockets impl", ws)
def torch_cpu():
    import torch; assert not torch.cuda.is_available(); return torch.__version__
step("torch (CPU build, no CUDA)", torch_cpu)
def head():
    import joblib, numpy as np
    h = joblib.load(os.path.join(root, "services/zoe-data/models/router_head_logreg.joblib"))
    return f"{len(h.classes_)} classes, predict_proba ok" if h.predict_proba(np.zeros((1, h.coef_.shape[1]), dtype="float32")).shape[0] == 1 else "?"
step("router head joblib", head)
def vad():
    import onnxruntime as ort
    p = os.path.expanduser("~/models/silero_vad.onnx")
    if not os.path.exists(p): return "skipped (no ~/models/silero_vad.onnx)"
    return f"onnxruntime {ort.__version__}, inputs {[i.name for i in ort.InferenceSession(p, providers=['CPUExecutionProvider']).get_inputs()]}"
step("Silero VAD onnx session", vad)
sys.exit(1 if failed else 0)
PY
}

step "B0.7 zoe-data Python $PY_VERSION venv"
log "uv:        $UV ($("$UV" --version))"
log "manifest:  $REQ_FILE ($(grep -cvE '^\s*(#|$)' "$REQ_FILE") requirement lines)"
log "venv:      $VENV_DIR (current: ${VENV_PY} -> $(venv_version | sed 's/^$/absent/'))"
log "phase 2:   uv pip install --no-deps ${PHASE2_NO_DEPS[*]}"
log "memory:    $(mem_available_mb) MB available (floor $MIN_MEM_MB)"

case "$MODE" in
  dry-run)
    step "dry run — nothing is installed"
    echo "  would run:"
    echo "    $UV python install $PY_VERSION"
    echo "    $UV venv $VENV_DIR --python $PY_VERSION            # only if absent or not $PY_VERSION"
    echo "    $UV pip compile $REQ_FILE --python $VENV_PY -o <lock>   # resolved closure, installed pins preferred"
    echo "    $UV pip sync --python $VENV_PY <lock>                  # converge: removes anything not in the lock"
    echo "    $UV pip install --python $VENV_PY --no-deps ${PHASE2_NO_DEPS[*]}"
    echo "  then the import smoke, then (operator, separately) the systemd drop-in."
    step "resolving the manifest for cp$PY_VERSION / $PLATFORM (wheel metadata only)"
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    if "${NICE[@]}" "$UV" pip compile "$REQ_FILE" --python-version "$PY_VERSION" --python-platform "$PLATFORM" \
         --no-header --quiet -o "$tmp"; then
      ok "resolvable: $(grep -cE '^[A-Za-z0-9_.-]+(==| @ )' "$tmp") packages"
    else
      die "manifest does NOT resolve for cp$PY_VERSION $PLATFORM — see docs/knowledge/python-312-venv-migration.md"
    fi
    # Phase 2 is not in the manifest, so the resolve above never sees it; resolve it
    # on its own, --no-deps, for the same target — otherwise an unavailable pin
    # passes this dry-run (and CI's deps-resolvable) and fails the real build (#1706).
    step "resolving phase 2 (--no-deps) for cp$PY_VERSION / $PLATFORM"
    # (-o a real file, not /dev/null: uv writes via a temp file BESIDE its output.)
    p2="$(mktemp)"; p2_out="$(mktemp)"; trap 'rm -f "$tmp" "$p2" "$p2_out"' EXIT
    printf '%s\n' "${PHASE2_NO_DEPS[@]}" >"$p2"
    if "${NICE[@]}" "$UV" pip compile "$p2" --no-deps --python-version "$PY_VERSION" --python-platform "$PLATFORM" \
         --no-header --quiet -o "$p2_out"; then
      ok "phase 2 resolvable: ${PHASE2_NO_DEPS[*]}"
    else
      die "phase 2 (${PHASE2_NO_DEPS[*]}) does NOT resolve for cp$PY_VERSION $PLATFORM"
    fi
    ;;
  check)
    step "check"
    [[ -x "$VENV_PY" ]] || die "no venv at $VENV_DIR (run without --check to build it)"
    v="$(venv_version)"; [[ "$v" == "$PY_MM" ]] || die "venv is Python $v, expected $PY_MM"
    ok "interpreter $("$VENV_PY" -c 'import sys; print(sys.version.split()[0], sys.executable)')"
    # Drift is FATAL here: most smoke imports assert no version, so a warning would
    # let --check certify a knowingly drifted venv right before cutover (#1706).
    # The checker also verifies the torch direct-URL pin (version + PEP 610 source).
    drift="$REPO_ROOT/scripts/maintenance/requirements_drift_check.py"
    [[ -f "$drift" ]] || die "drift checker missing: $drift"
    # Phase 2 is installed from PHASE2_NO_DEPS, not the manifest, so it is passed as
    # --no-deps: version-checked, allowed, its declared deps NOT walked (#1706).
    # --extraneous: anything installed that neither phase asks for is drift too —
    # a leftover (e.g. the un-importable webrtcvad sdist, or pytest from gate 1)
    # would otherwise be certified, since the smoke cannot see it (#1706).
    phase2_req="$(mktemp)"; trap 'rm -f "$phase2_req"' EXIT
    printf '%s\n' "${PHASE2_NO_DEPS[@]}" >"$phase2_req"
    log "drift (manifest + phase 2 vs venv, extraneous included):"
    "${NICE[@]}" "$VENV_PY" "$drift" "$REQ_FILE" --no-deps "$phase2_req" --extraneous --quiet \
      || die "venv drift (MISMATCH / MISSING / EXTRANEOUS / DAMAGED above) — rebuild (no args) to converge"
    log "import smoke:"
    smoke && ok "smoke passed" || die "smoke FAILED"
    ;;
  refresh)
    # The deploy path (.github/workflows/deploy.yml, once the service runs this
    # venv): the same two install phases as a build, ADDITIVE (`uv pip install`,
    # not the build's freeze→compile→sync), and nothing else. Additive on purpose:
    # a deploy installs or moves the manifest's pins but never uninstalls from the
    # live interpreter — pruning strays is the operator's build (no args), whose
    # sync + damaged-file repair is not something to run unattended per merge. No
    # `uv python install` and no venv creation — a missing or wrong-version venv
    # under a service that points at it is an operator problem, not something a
    # deploy should paper over by building 1.9 GB. No memory floor — the deploy
    # job's own headroom gate owns that, and an already-satisfied manifest is a
    # no-op audit. No import smoke — the deploy's /health loop after the restart
    # is the runtime proof, and the smoke's torch/chromadb imports are a memory
    # transient the live box should not pay on every deploy.
    step "refresh (deploy)"
    [[ -x "$VENV_PY" ]] || die "no venv at $VENV_DIR — build it first (no args); --refresh never creates one"
    v="$(venv_version)"; [[ "$v" == "$PY_MM" ]] || die "venv is Python $v, expected $PY_MM"
    # --offline FIRST, measured: without it uv re-fetches the torch direct-URL
    # wheel on every run even when that exact wheel is already installed (a dead
    # network fails an otherwise no-op deploy), while --offline audits the
    # installed set in milliseconds. Only when the offline pass cannot satisfy
    # the pins (a real bump, wheel not in uv's cache) does it go to the network —
    # the same pinned set either way, so the fallback changes where bytes come
    # from, never what gets installed.
    refresh_phase() {
      "${NICE[@]}" "$UV" pip install --offline --python "$VENV_PY" "$@" \
        || { log "offline pass could not satisfy the pins — installing from the network"
             "${NICE[@]}" "$UV" pip install --python "$VENV_PY" "$@"; }
    }
    log "phase 1: manifest"
    refresh_phase -r "$REQ_FILE"
    log "phase 2: ${PHASE2_NO_DEPS[*]} --no-deps"
    refresh_phase --no-deps "${PHASE2_NO_DEPS[@]}"
    ok "venv refreshed: $VENV_PY"
    ;;
  build)
    step "build"
    avail="$(mem_available_mb)"
    (( avail >= MIN_MEM_MB )) || die "only ${avail} MB available (< ${MIN_MEM_MB}); not installing on a starved box (ZOE_PY312_MIN_MEM_MB overrides)"
    log "ensuring uv-managed CPython $PY_VERSION"
    "${NICE[@]}" "$UV" python install "$PY_VERSION"
    if [[ "$(venv_version)" == "$PY_MM" ]]; then
      ok "venv present at $VENV_DIR (Python $PY_MM) — converging packages"
    else
      [[ -e "$VENV_DIR" ]] && die "$VENV_DIR exists but is not a Python $PY_MM venv — remove it deliberately first"
      mkdir -p "$(dirname "$VENV_DIR")"
      "${NICE[@]}" "$UV" venv "$VENV_DIR" --python "$PY_VERSION"
      ok "created $VENV_DIR"
    fi
    # Phase 1 CONVERGES, it does not just add: `uv pip install -r` never removes a
    # distribution, so a package dropped from the manifest (or left by an earlier
    # experiment) would keep importing. `uv pip sync` removes everything not in its
    # input — but sync installs its input LITERALLY (no dependency resolution), so
    # it must be given the fully resolved closure, never the manifest itself
    # (measured: syncing the bare manifest would uninstall ~124 transitive deps).
    # So: freeze -> compile with the freeze as preferences (a rerun keeps what is
    # installed unless the manifest moved) -> sync that lock.
    lock="$(mktemp)"; damaged_out="$(mktemp)"; trap 'rm -f "$lock" "$damaged_out"' EXIT
    "${NICE[@]}" "$UV" pip freeze --python "$VENV_PY" >"$lock"
    log "phase 1: resolve the manifest for this interpreter, then sync to it"
    "${NICE[@]}" "$UV" pip compile "$REQ_FILE" --python "$VENV_PY" --no-header --quiet -o "$lock"
    "${NICE[@]}" "$UV" pip sync --python "$VENV_PY" "$lock"
    # Removing a distribution deletes every path in ITS RECORD — including paths
    # another distribution also installed. Measured on #1706: syncing away the stale
    # `webrtcvad` sdist deleted `webrtcvad.py`/`_webrtcvad*.so`, which
    # `webrtcvad-wheels` ships at the same paths; its metadata survived, so sync saw
    # nothing to do. Reinstall whatever lost files (a no-op on a clean venv).
    # Via a file, NOT `mapfile < <(scanner)`: a process substitution drops the
    # scanner's exit status, so a crashed scan read as "no damage" (#1706).
    "${NICE[@]}" "$VENV_PY" "$REPO_ROOT/scripts/maintenance/requirements_drift_check.py" --list-damaged >"$damaged_out" \
      || die "damage scan failed (exit $?) — not continuing on an unverified venv"
    mapfile -t damaged <"$damaged_out"
    if (( ${#damaged[@]} )); then
      warn "files missing from: ${damaged[*]} (shared paths of a removed distribution) — reinstalling"
      reinstall=(); for d in "${damaged[@]}"; do reinstall+=(--reinstall-package "$d"); done
      "${NICE[@]}" "$UV" pip sync --python "$VENV_PY" "${reinstall[@]}" "$lock"
    fi
    # ORDER MATTERS: phase 2 goes in AFTER the sync, because sync removes anything
    # outside the lock — including resemblyzer itself.
    log "phase 2: ${PHASE2_NO_DEPS[*]} --no-deps"
    "${NICE[@]}" "$UV" pip install --python "$VENV_PY" --no-deps "${PHASE2_NO_DEPS[@]}"
    log "import smoke:"
    smoke && ok "smoke passed" || die "smoke FAILED — the venv is built but NOT fit to run zoe-data"
    ok "venv ready: $VENV_PY  ($(du -sh "$VENV_DIR" | cut -f1))"
    echo
    echo "Nothing running has changed. Next (operator, in order — runbook §Verification gates):"
    echo "  1. ci_safe lanes in the venv (network-blocked, TZ=UTC)"
    echo "  2. replay probe with THIS interpreter (--stt inprocess) under flock"
    echo "  3. systemd drop-in ~/.config/systemd/user/zoe-data.service.d/60-py312-venv.conf, restart, /readyz, replay --stt remote"
    ;;
esac
