#!/usr/bin/env bash
# Build the Kokoro TTS venv (program item B5.7): ~/.zoe/venvs/kokoro-py310.
#
# WHAT IT IS: a venv on the SYSTEM Python 3.10 created with
# --system-site-packages, so the sidecar keeps the exact torch 2.8.0 CUDA wheel,
# kokoro, misaki and transformers it runs today (all in ~/.local), plus one
# addition — an import blocker (scripts/setup/kokoro_import_block.py, installed as
# a .pth) that makes scikit-learn, pandas and pyarrow unimportable IN THIS VENV
# ONLY. transformers pulls them in just because they are installed
# (candidate_generator -> sklearn.metrics -> pandas -> pyarrow); Kokoro needs none
# of them. Nothing is installed, downloaded or uninstalled, and the system 3.10
# site-packages is never touched.
#
# Why not a clean venv with its own torch: the NVIDIA wheel is 1.8 GB installed
# and carries no PEP 610 source (no direct_url.json), so it cannot be reinstalled
# offline or reproducibly — measured 2026-09-28, see docs/knowledge/voice-pipeline.md
# (Kokoro dedicated venv).
#
# Building changes nothing that runs. The service switches only when an operator
# installs the drop-in scripts/setup/systemd/kokoro-tts.service.d/60-kokoro-venv.conf.
#
# Usage:
#   scripts/setup/build_kokoro_venv.sh           # create/converge the venv + blocker, drift check, smoke
#   scripts/setup/build_kokoro_venv.sh --check   # verify only: venv shape, blocker byte-identical, drift, smoke
# Env: ZOE_KOKORO_VENV (venv dir), ZOE_KOKORO_BASE_PYTHON (default /usr/bin/python3),
#      ZOE_KOKORO_PY_MM (expected MAJOR.MINOR, default 3.10),
#      ZOE_KOKORO_VENV_MIN_MEM_MB (smoke needs this much MemAvailable; default 900),
#      ZOE_KOKORO_SMOKE_CUDA (1 = torch.cuda.is_available() must be true; default 1),
#      ZOE_KOKORO_VENV_SKIP_RUNTIME (1 = skip drift + smoke; unit tests only — they have no torch).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

VENV_DIR="${ZOE_KOKORO_VENV:-$HOME/.zoe/venvs/kokoro-py310}"
BASE_PY="${ZOE_KOKORO_BASE_PYTHON:-/usr/bin/python3}"
PY_MM="${ZOE_KOKORO_PY_MM:-3.10}"
MIN_MEM_MB="${ZOE_KOKORO_VENV_MIN_MEM_MB:-900}"
SMOKE_CUDA="${ZOE_KOKORO_SMOKE_CUDA:-1}"
SKIP_RUNTIME="${ZOE_KOKORO_VENV_SKIP_RUNTIME:-0}"
REQ_FILE="$SCRIPT_DIR/requirements-kokoro.txt"
BLOCK_SRC="$SCRIPT_DIR/kokoro_import_block.py"
BLOCK_MOD="zoe_kokoro_import_block"
PTH_LINE="import $BLOCK_MOD"
# The unit's own library path (kokoro-tts.service), so the smoke sees what the service sees.
UNIT_LD_LIBRARY_PATH="$HOME/.local/lib:/usr/local/cuda-12.6/lib64:/usr/lib/aarch64-linux-gnu"
NICE=(nice -n 15)

MODE="build"
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $arg (use --check, or none)" ;;
  esac
done

VENV_PY="$VENV_DIR/bin/python"
CFG="$VENV_DIR/pyvenv.cfg"
py_mm() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }
cfg_get() { sed -nE "s/^$1[[:space:]]*=[[:space:]]*(.*)$/\1/p" "$CFG" 2>/dev/null | head -1; }
mem_available_mb() { awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo; }
site_dir() { "$VENV_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'; }

# A venv is ours only if it is a --system-site-packages venv of the expected
# interpreter. Anything else at the path is refused, never repaired in place.
verify_shape() {
  [[ -f "$CFG" && -x "$VENV_PY" ]] || return 1
  [[ "$(cfg_get include-system-site-packages)" == "true" ]] \
    || die "$VENV_DIR is not a --system-site-packages venv — it cannot see the system torch; remove it deliberately first"
  local v; v="$(py_mm "$VENV_PY")"
  [[ "$v" == "$PY_MM" ]] || die "$VENV_DIR is Python ${v:-?}, expected $PY_MM — remove it deliberately first"
  return 0
}

blocker_state() {  # prints "ok" when both installed files match the tracked source
  local site; site="$(site_dir)"
  if cmp -s "$BLOCK_SRC" "$site/$BLOCK_MOD.py" && [[ "$(cat "$site/$BLOCK_MOD.pth" 2>/dev/null)" == "$PTH_LINE" ]]; then
    echo ok
  else
    echo stale
  fi
}

drift() {
  log "drift ($REQ_FILE vs what the venv interpreter imports):"
  "${NICE[@]}" "$VENV_PY" "$REPO_ROOT/scripts/maintenance/requirements_drift_check.py" "$REQ_FILE" --quiet \
    || die "Kokoro stack drift (above) — something moved the system 3.10 site-packages; reconcile requirements-kokoro.txt in a replay-gated PR"
}

smoke() {
  local avail; avail="$(mem_available_mb)"
  (( avail >= MIN_MEM_MB )) || die "only ${avail} MB available (< ${MIN_MEM_MB}); the smoke imports torch+kokoro (~560 MB) — not on a starved box (ZOE_KOKORO_VENV_MIN_MEM_MB overrides)"
  local sys_torch
  sys_torch="$("$BASE_PY" -c 'import importlib.util as u; s = u.find_spec("torch"); print(s.origin if s else "")')"
  log "import smoke (venv interpreter, unit LD_LIBRARY_PATH, CUDA required=$SMOKE_CUDA):"
  LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-$UNIT_LD_LIBRARY_PATH}" PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync \
    "${NICE[@]}" "$VENV_PY" - "$VENV_DIR" "$sys_torch" "$SMOKE_CUDA" <<'PY'
import importlib, importlib.util, os, sys, time, warnings
warnings.filterwarnings("ignore")
venv, sys_torch, need_cuda = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
failed = 0
def step(name, fn):
    global failed
    try: print(f"  ok   {name}: {fn()}")
    except Exception as e:
        failed += 1; print(f"  FAIL {name}: {type(e).__name__}: {str(e)[:160]}")
def rss_mb():
    return int(open("/proc/self/status").read().split("VmRSS:")[1].split()[0]) // 1024
def prefix():
    assert os.path.realpath(sys.prefix) == os.path.realpath(venv), sys.prefix
    return f"{sys.version.split()[0]} prefix={sys.prefix}"
step("interpreter is the venv", prefix)
def pth_ran():
    # Must be checked BEFORE importing the module by name below: that import would
    # itself run install() and mask a .pth that never fired.
    assert "zoe_kokoro_import_block" in sys.modules, "the .pth did not run at start-up"
    return "zoe_kokoro_import_block loaded by the .pth"
step("blocker ran at interpreter start-up", pth_ran)
import zoe_kokoro_import_block as blk   # already loaded; this only reads BLOCKED
def blocked():
    for b in blk.BLOCKED:
        assert importlib.util.find_spec(b) is None, f"find_spec({b!r}) is not None"
        try: importlib.import_module(b)
        except ModuleNotFoundError: continue
        raise AssertionError(f"import {b} succeeded")
    return f"{', '.join(blk.BLOCKED)} unimportable"
step("import blocker active", blocked)
t0 = time.perf_counter()
for m in ("fastapi", "uvicorn", "pydantic", "torch", "kokoro", "misaki.en", "misaki.espeak",
          "transformers.generation.candidate_generator"):
    step(m, lambda m=m: getattr(importlib.import_module(m), "__version__", "imported"))
step("KPipeline class", lambda: importlib.import_module("kokoro").KPipeline.__name__)
def none_loaded():
    live = sorted(k for k, v in sys.modules.items() if v is not None and k.split(".")[0] in blk.BLOCKED)
    assert not live, live
    return "none of the blocked packages loaded"
step("import stack clean", none_loaded)
def same_torch():
    import torch
    assert os.path.realpath(torch.__file__) == os.path.realpath(sys_torch), (torch.__file__, sys_torch)
    assert torch.version.cuda, "not a CUDA build of torch"
    return f"torch {torch.__version__} cuda {torch.version.cuda} = the system interpreter's copy"
step("same torch as system python", same_torch)
if need_cuda:
    def cuda():
        import torch
        # is_available() only counts devices — no CUDA context, no nvmap allocation.
        assert torch.cuda.is_available(), "torch.cuda.is_available() is False"
        return f"{torch.cuda.device_count()} device(s)"
    step("torch.cuda.is_available()", cuda)
print(f"  info import stack {time.perf_counter() - t0:.1f} s, VmRSS {rss_mb()} MB")
sys.exit(1 if failed else 0)
PY
}

step "Kokoro TTS venv (system Python $PY_MM + import blocker)"
log "base:      $BASE_PY ($(py_mm "$BASE_PY" | sed 's/^$/unusable/'))"
log "venv:      $VENV_DIR"
log "blocker:   $BLOCK_SRC -> <site>/$BLOCK_MOD.py + $BLOCK_MOD.pth"
log "manifest:  $REQ_FILE ($(grep -cvE '^\s*(#|$)' "$REQ_FILE") pins, verified not installed)"
[[ -f "$BLOCK_SRC" ]] || die "blocker source missing: $BLOCK_SRC"
[[ "$(py_mm "$BASE_PY")" == "$PY_MM" ]] || die "$BASE_PY is not Python $PY_MM"

case "$MODE" in
  check)
    step "check"
    verify_shape || die "no venv at $VENV_DIR (run without --check to build it)"
    ok "venv: $(py_mm "$VENV_PY"), include-system-site-packages = true"
    [[ "$(blocker_state)" == ok ]] || die "import blocker missing or differs from $BLOCK_SRC — rebuild (no args)"
    ok "import blocker installed and identical to the tracked source"
    ;;
  build)
    step "build"
    if verify_shape; then
      ok "venv present ($(py_mm "$VENV_PY"), system site-packages) — converging"
    else
      [[ -e "$VENV_DIR" ]] && die "$VENV_DIR exists but is not a venv — remove it deliberately first"
      mkdir -p "$(dirname "$VENV_DIR")"
      # --without-pip: nothing is ever installed into it, and Ubuntu's system
      # python ships no ensurepip.
      "$BASE_PY" -m venv --system-site-packages --without-pip "$VENV_DIR"
      verify_shape || die "venv creation did not produce the expected shape"
      ok "created $VENV_DIR"
    fi
    if [[ "$(blocker_state)" == ok ]]; then
      ok "import blocker already current"
    else
      site="$(site_dir)"
      install -m 0644 "$BLOCK_SRC" "$site/$BLOCK_MOD.py"
      printf '%s\n' "$PTH_LINE" >"$site/$BLOCK_MOD.pth"
      [[ "$(blocker_state)" == ok ]] || die "import blocker did not install cleanly into $site"
      ok "import blocker installed into $site"
    fi
    ;;
esac

if [[ "$SKIP_RUNTIME" == 1 ]]; then
  warn "ZOE_KOKORO_VENV_SKIP_RUNTIME=1 — drift check and smoke SKIPPED (unit tests only)"
  exit 0
fi
drift
smoke && ok "smoke passed" || die "smoke FAILED — the venv is NOT fit to run the Kokoro sidecar"
ok "venv ready: $VENV_PY"
if [[ "$MODE" == build ]]; then
  echo
  echo "Nothing running has changed. To switch the service (brain-window rules apply):"
  echo "  mkdir -p ~/.config/systemd/user/kokoro-tts.service.d"
  if [[ "$VENV_DIR" == "$HOME/.zoe/venvs/kokoro-py310" ]]; then
    echo "  cp scripts/setup/systemd/kokoro-tts.service.d/60-kokoro-venv.conf ~/.config/systemd/user/kokoro-tts.service.d/"
  else
    # ZOE_KOKORO_VENV override: the tracked drop-in's ExecStart names the DEFAULT venv, so a
    # plain copy would restart the sidecar on an interpreter that may not exist (Restart=always
    # loop, voice on fallback TTS). Render the drop-in for the venv that was actually built.
    # sed-escape the path for the replacement side: \ & and the # delimiter are special there.
    venv_sed=$(printf '%s' "$VENV_DIR" | sed 's/[\\&#]/\\&/g')
    echo "  # ZOE_KOKORO_VENV=$VENV_DIR — render the drop-in for THIS venv (the tracked file names the default):"
    echo "  sed 's#%h/.zoe/venvs/kokoro-py310/bin/python#$venv_sed/bin/python#' scripts/setup/systemd/kokoro-tts.service.d/60-kokoro-venv.conf > ~/.config/systemd/user/kokoro-tts.service.d/60-kokoro-venv.conf"
  fi
  echo "  systemctl --user daemon-reload && systemctl --user restart kokoro-tts   # poll :10201/health for pipeline_loaded + cuda"
fi
