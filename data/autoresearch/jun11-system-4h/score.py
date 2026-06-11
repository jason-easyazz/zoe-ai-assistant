#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
TESTS = [
    "tests/test_populate_multica_contract.py",
    "tests/test_greptile_client.py",
    "tests/test_multica_ticket_contract.py",
]
PY_COMPILE_FILES = [
    "scripts/setup/populate_multica.py",
    "scripts/maintenance/greploop_guard.py",
    "services/zoe-data/greptile_client.py",
    "services/zoe-data/multica_ticket_contract.py",
    "services/zoe-data/multica_client.py",
]
DEBT_FILES = [
    "services/zoe-data/multica_client.py",
    "services/zoe-data/greptile_client.py",
    "scripts/setup/populate_multica.py",
    "scripts/maintenance/greploop_guard.py",
]

def run(name: str, cmd: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=180)
    output = (proc.stdout + proc.stderr)[-4000:]
    return proc.returncode, output

def broad_exception_count() -> int:
    count = 0
    pattern = re.compile(r"except\s+Exception\s*:?|except\s+BaseException\s*:?")
    for rel in DEBT_FILES:
        p = ROOT / rel
        if p.exists():
            count += len(pattern.findall(p.read_text(errors="replace")))
    return count

results: dict[str, object] = {}
score = 0

checks = [
    ("structure", ["python3", "tools/audit/validate_structure.py"], ROOT, 20),
    ("critical", ["python3", "tools/audit/validate_critical_files.py"], ROOT, 20),
    ("pytest", ["python3", "-m", "pytest", *TESTS, "-q"], ROOT / "services/zoe-data", 30),
]
for name, cmd, cwd, points in checks:
    rc, out = run(name, cmd, cwd)
    ok = rc == 0
    results[name] = {"ok": ok, "points": points if ok else 0, "tail": out}
    if ok:
        score += points

compile_ok = True
compile_details = {}
for rel in PY_COMPILE_FILES:
    rc, out = run(f"compile:{rel}", ["python3", "-m", "py_compile", rel], ROOT)
    compile_details[rel] = {"ok": rc == 0, "tail": out}
    compile_ok = compile_ok and rc == 0
if compile_ok:
    score += 20
results["py_compile"] = {"ok": compile_ok, "points": 20 if compile_ok else 0, "files": compile_details}

broad = broad_exception_count()
debt_points = max(0, 30 - broad)
score += debt_points
results["broad_exception_debt"] = {"count": broad, "points": debt_points, "max_points": 30}

print(f"score: {score}")
print(json.dumps(results, indent=2, sort_keys=True))
