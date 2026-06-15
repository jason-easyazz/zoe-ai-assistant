#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
TESTS = [
    "tests/test_multica_operator_intents.py",
    "tests/test_multica_client_helpers.py",
    "tests/test_main_multica_poll.py",
]
COMPILE_FILES = [
    "services/zoe-data/multica_operator.py",
    "services/zoe-data/multica_client.py",
    "services/zoe-data/multica_poll_dispatch.py",
    "services/zoe-data/intent_router.py",
]
DEBT_FILES = [
    "services/zoe-data/multica_operator.py",
    "services/zoe-data/multica_poll_dispatch.py",
    "services/zoe-data/intent_router.py",
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
debt_points = max(0, 60 - broad)
score += debt_points
results["service_broad_exception_debt"] = {"count": broad, "points": debt_points, "max_points": 60}
print(f"score: {score}")
print(json.dumps(results, indent=2, sort_keys=True))
