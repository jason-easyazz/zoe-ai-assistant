#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
TESTS = [
    "tests/test_kanban_adapter.py::test_closeout_body_uses_supported_greploop_launcher",
    "tests/test_greptile_client.py",
    "tests/test_pipeline_evidence_commands.py::test_mark_greptile_passes_only_on_five_of_five",
]
COMPILE_FILES = [
    "services/zoe-data/executors/kanban_adapter.py",
    "services/zoe-data/greptile_client.py",
    "services/zoe-data/pipeline_evidence_commands.py",
    "scripts/maintenance/greploop_guard.py",
]
DEBT_FILES = [
    "services/zoe-data/executors/kanban_adapter.py",
    "services/zoe-data/pipeline_evidence_commands.py",
    "services/zoe-data/pipeline_handoff.py",
    "services/zoe-data/pipeline_store.py",
]

def run(cmd: list[str], cwd: Path = ROOT) -> tuple[bool, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=180)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]

def broad_exception_count() -> int:
    pattern = re.compile(r"except\s+Exception\s*:?|except\s+BaseException\s*:?")
    return sum(len(pattern.findall((ROOT / rel).read_text(errors="replace"))) for rel in DEBT_FILES if (ROOT / rel).exists())

score = 0
results = {}
for name, cmd, cwd, points in [
    ("structure", ["python3", "tools/audit/validate_structure.py"], ROOT, 20),
    ("critical", ["python3", "tools/audit/validate_critical_files.py"], ROOT, 20),
    ("pytest", ["python3", "-m", "pytest", *TESTS, "-q"], ROOT / "services/zoe-data", 30),
]:
    ok, out = run(cmd, cwd)
    results[name] = {"ok": ok, "points": points if ok else 0, "tail": out}
    if ok:
        score += points

compile_ok = True
compile_details = {}
for rel in COMPILE_FILES:
    ok, out = run(["python3", "-m", "py_compile", rel])
    compile_details[rel] = {"ok": ok, "tail": out}
    compile_ok = compile_ok and ok
results["py_compile"] = {"ok": compile_ok, "points": 20 if compile_ok else 0, "files": compile_details}
if compile_ok:
    score += 20

broad = broad_exception_count()
debt_points = max(0, 50 - broad)
score += debt_points
results["pipeline_broad_exception_debt"] = {"count": broad, "points": debt_points, "max_points": 50}
print(f"score: {score}")
print(json.dumps(results, indent=2, sort_keys=True))
