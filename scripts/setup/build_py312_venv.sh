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
#   scripts/setup/build_py312_venv.sh              # build / converge the venv (idempotent)
#   scripts/setup/build_py312_venv.sh --check      # verify an existing venv: interpreter, drift, imports
# Env: ZOE_PY312_VENV (venv dir), ZOE_PY312_PYTHON (default 3.12),
#      ZOE_PY312_MIN_MEM_MB (refuse to install below this MemAvailable; default 500).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

VENV_DIR="${ZOE_PY312_VENV:-$HOME/.zoe/venvs/zoe-data-py312}"
PY_VERSION="${ZOE_PY312_PYTHON:-3.12}"
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
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $arg (use --dry-run, --check, or none)" ;;
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
    echo "    $UV pip install --python $VENV_PY -r $REQ_FILE"
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
    ;;
  check)
    step "check"
    [[ -x "$VENV_PY" ]] || die "no venv at $VENV_DIR (run without --check to build it)"
    v="$(venv_version)"; [[ "$v" == "$PY_VERSION" ]] || die "venv is Python $v, expected $PY_VERSION"
    ok "interpreter $("$VENV_PY" -c 'import sys; print(sys.version.split()[0], sys.executable)')"
    drift="$REPO_ROOT/scripts/maintenance/requirements_drift_check.py"
    if [[ -f "$drift" ]]; then
      log "drift (manifest vs venv):"
      "${NICE[@]}" "$VENV_PY" "$drift" "$REQ_FILE" || warn "drift check reported mismatches (see above)"
    fi
    log "import smoke:"
    smoke && ok "smoke passed" || die "smoke FAILED"
    ;;
  build)
    step "build"
    avail="$(mem_available_mb)"
    (( avail >= MIN_MEM_MB )) || die "only ${avail} MB available (< ${MIN_MEM_MB}); not installing on a starved box (ZOE_PY312_MIN_MEM_MB overrides)"
    log "ensuring uv-managed CPython $PY_VERSION"
    "${NICE[@]}" "$UV" python install "$PY_VERSION"
    if [[ "$(venv_version)" == "$PY_VERSION" ]]; then
      ok "venv present at $VENV_DIR (Python $PY_VERSION) — converging packages"
    else
      [[ -e "$VENV_DIR" ]] && die "$VENV_DIR exists but is not a Python $PY_VERSION venv — remove it deliberately first"
      mkdir -p "$(dirname "$VENV_DIR")"
      "${NICE[@]}" "$UV" venv "$VENV_DIR" --python "$PY_VERSION"
      ok "created $VENV_DIR"
    fi
    log "phase 1: manifest"
    "${NICE[@]}" "$UV" pip install --python "$VENV_PY" -r "$REQ_FILE"
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
