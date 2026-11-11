"""The evidence-REUSE rule must be at least as strict as what the probe can measure.

voice_gate_check.reuse_relevance() decides which changed paths make a passing probe of
commit P NON-reusable at a later commit H. Its soundness rests on one measured claim:
every file the replay probe's exercised path can load from the tree is voice-relevant.
This test RE-MEASURES that claim on every run, so it cannot rot into a comment:

  1. the static Python import closure of the probe chain (AST, every import statement
     anywhere in a file - function-level and try/except included - plus literal
     importlib.import_module / __import__ strings), resolved the way each process resolves
     it: zoe-data modules from services/zoe-data (+ the replay's own tests/ dir, sys.path[0]
     when replay_samples.py runs as a script); scripts from their own dir + scripts/lib;
  2. every file loaded BY PATH (spec_from_file_location) from inside that closure, which
     the AST import graph cannot see - pinned as an explicit list, so a new path-load
     turns this red until it is classified;
  3. assert every file in 1+2 classifies as voice-relevant for reuse.

A new import that escapes the reuse roots therefore fails the REQUIRED `validate` lane.
Known limits (stated, not hidden): a subprocess-run script or a data file read through a
computed path is invisible to an AST walk; the reuse rule covers those by DIRECTORY
(the whole services/zoe-data tree, the brain lanes, scripts/lib|perf|setup|deploy) and by
REUSE_EXTRA_FILES (night_extract.py's worker, self_model.py's flag inventory).
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
ZD = REPO / "services" / "zoe-data"

_spec = importlib.util.spec_from_file_location("voice_gate_check_closure_under_test",
                                               REPO / "scripts/maintenance/voice_gate_check.py")
vgc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vgc
_spec.loader.exec_module(vgc)

# The processes the probe runs and the file each one starts from.
SEEDS = (
    "services/zoe-data/tests/replay_samples.py",        # the replay (imports zoe-data in-process)
    "services/zoe-data/voice_vad.py",                    # the probe's VAD stage loads it by path
    "scripts/maintenance/voice_regression_probe.py",     # the probe
    "scripts/maintenance/voice_clip_checks.py",          # loaded by path by the probe
    "scripts/perf/measure_voice.py",                     # shelled out to by the probe
    "scripts/perf/measure_cold_first_turn.py",           # the cold-first-turn stage
)

# Path-loads (importlib.util.spec_from_file_location) inside the closure -> the file each
# loads. Keyed by (file, the string constants in the call). A new one must be added here
# AND be reuse-relevant.
# None = the generic `_sibling(name)` loader, whose literal call sites the scan follows.
PATH_LOADS = {
    ("scripts/maintenance/voice_regression_probe.py", (".py",)): None,
    ("scripts/maintenance/voice_regression_probe.py", ("harness_quiet", "harness_quiet.py")):
        "scripts/maintenance/harness_quiet.py",
    ("scripts/maintenance/voice_regression_probe.py", ("_zoe_probe_voice_vad",)):
        "services/zoe-data/voice_vad.py",
    ("scripts/perf/measure_cold_first_turn.py", ("harness_quiet", "harness_quiet.py", "maintenance")):
        "scripts/maintenance/harness_quiet.py",
    ("services/zoe-data/tests/replay_samples.py",
     ("harness_quiet", "harness_quiet.py", "maintenance", "scripts")):
        "scripts/maintenance/harness_quiet.py",
    ("services/zoe-data/kv_miss_alert.py", ("_zoe_clw", "maintenance", "scripts", "zoe_crash_loop_watch.py")):
        "scripts/maintenance/zoe_crash_loop_watch.py",
    ("services/zoe-data/omi_diarize.py", ("zoe_speaker_resnet34_omi",)):
        "scripts/setup/zoe_speaker_resnet34.py",
}


def _roots_for(path: Path) -> list[Path]:
    rel = path.relative_to(REPO).as_posix()
    if rel.startswith("services/zoe-data/"):
        return [ZD, ZD / "tests"]
    return [path.parent, REPO / "scripts" / "lib"]


def _candidates(mod: str, roots: list[Path]) -> list[Path]:
    parts = mod.split(".")
    out = []
    for root in roots:
        for i in range(1, len(parts) + 1):
            base = root.joinpath(*parts[:i])
            for cand in (base.with_suffix(".py"), base / "__init__.py"):
                if cand.is_file():
                    out.append(cand)
    return out


def _scan(path: Path) -> tuple[list[Path], list[tuple[str, tuple[str, ...]]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = _roots_for(path)
    found: list[Path] = []
    loads: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found += _candidates(alias.name, roots)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                tgt = base.joinpath(*(node.module or "").split(".")) if node.module else base
                for alias in node.names:
                    for cand in (tgt.with_suffix(".py"), tgt / "__init__.py",
                                 tgt / f"{alias.name}.py", tgt / alias.name / "__init__.py"):
                        if cand.is_file():
                            found.append(cand)
            else:
                found += _candidates(node.module, roots)
                for alias in node.names:
                    found += _candidates(f"{node.module}.{alias.name}", roots)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in ("import_module", "__import__") and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found += _candidates(node.args[0].value, roots)
            elif name == "_sibling" and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                found.append(path.parent / f"{node.args[0].value}.py")
            elif name == "spec_from_file_location":
                consts = tuple(sorted({c.value for c in ast.walk(node)
                                       if isinstance(c, ast.Constant) and isinstance(c.value, str)}))
                loads.append((path.relative_to(REPO).as_posix(), consts))
    return found, loads


def probe_closure() -> tuple[set[str], list[tuple[str, tuple[str, ...]]]]:
    seen: set[Path] = set()
    loads: list[tuple[str, tuple[str, ...]]] = []
    queue = [REPO / s for s in SEEDS]
    while queue:
        p = queue.pop().resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        found, found_loads = _scan(p)
        loads += found_loads
        queue += found
        for key in found_loads:
            if PATH_LOADS.get(key):
                queue.append(REPO / PATH_LOADS[key])
    return {p.relative_to(REPO).as_posix() for p in seen}, loads


CLOSURE, LOADS = probe_closure()


def test_the_closure_is_real_not_vacuous():
    """Sentinels a broken walker would miss: the per-turn modules and modules the
    VOICE_PATH_PATTERNS do NOT name (the gap the reuse rule exists to cover)."""
    for must in ("services/zoe-data/fast_tiers.py", "services/zoe-data/routers/voice_tts.py",
                 "services/zoe-data/intent_router.py", "services/zoe-data/self_model.py",
                 "services/zoe-data/voice_stt_stream.py", "services/zoe-data/db_pool.py",
                 "scripts/lib/service_python.py", "scripts/maintenance/harness_quiet.py"):
        assert must in CLOSURE, must
    assert len(CLOSURE) > 300, len(CLOSURE)
    gaps = [f for f in CLOSURE if not vgc.touched_voice_files([f], vgc.VOICE_PATH_PATTERNS)]
    assert len(gaps) > 100, "the deploy-scope patterns are a deliberate subset of the closure"


def test_every_path_load_in_the_closure_is_known():
    unknown = sorted({k for k in LOADS if k not in PATH_LOADS})
    assert not unknown, (
        "a file loaded BY PATH from the probe's closure is not classified; add it to "
        f"PATH_LOADS here and make it reuse-relevant in voice_gate_check.py: {unknown}")


def test_every_file_the_probe_can_load_is_reuse_relevant():
    loose = sorted(f for f in CLOSURE | {v for v in PATH_LOADS.values() if v}
                   if not vgc.reuse_relevance(f))
    assert not loose, (
        "files the replay probe's exercised path loads from the tree but evidence REUSE "
        f"would treat as voice-irrelevant (widen voice_gate_check.REUSE_*): {loose}")


def test_negative_control_the_pin_sees_a_narrowed_rule(monkeypatch):
    """Drop the whole-service-tree veto AND allowlist the tree (the realistic mistake: the
    allowlist fallback alone would still refuse it): the pin must go red."""
    monkeypatch.setattr(vgc, "REUSE_EXTRA_PREFIXES",
                        tuple(p for p in vgc.REUSE_EXTRA_PREFIXES if p != "services/zoe-data/"))
    monkeypatch.setattr(vgc, "REUSE_SAFE_PREFIXES", vgc.REUSE_SAFE_PREFIXES + ("services/zoe-data/",))
    assert any(not vgc.reuse_relevance(f) for f in CLOSURE)


def test_no_allowlisted_area_contains_a_closure_file_unless_vetoed():
    """The allowlist must never be what clears a file the probe loads."""
    for f in CLOSURE:
        if f.startswith(vgc.REUSE_SAFE_PREFIXES):
            assert vgc.reuse_relevance(f), f"{f} is allowlisted and nothing vetoes it"
