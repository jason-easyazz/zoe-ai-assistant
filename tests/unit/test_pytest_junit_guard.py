"""The nightly lane's anti-vacuous-green wrapper, with negative controls.

`scripts/maintenance/pytest_junit_guard.py` exists because `self-hosted-tests.yml`
reported 14 green nights with ZERO tests collected in two of its three suites
(2026-10-19 deep dive, lane K). A guard that can only ever say "ok" is the same
bug again, so every refusal path is exercised here against a fake pytest that
writes the JUnit XML the guard reads.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "pytest_junit_guard.py"

# A stand-in for pytest: argv[1] is a JSON-ish spec, the last arg is --junitxml=PATH.
FAKE = textwrap.dedent(
    """
    import sys
    spec = dict(kv.split("=") for kv in sys.argv[1].split(","))
    path = sys.argv[-1].split("=", 1)[1]
    if spec.get("write", "1") == "1":
        open(path, "w").write(
            '<?xml version="1.0"?><testsuites><testsuite name="pytest" errors="%s" failures="%s" '
            'skipped="%s" tests="%s"></testsuite></testsuites>'
            % (spec["errors"], spec["failures"], spec["skipped"], spec["tests"])
        )
    sys.exit(int(spec.get("rc", "0")))
    """
)


def _run(
    tmp_path: Path, spec: str, min_tests: int = 10, extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess:
    fake = tmp_path / "fake_pytest.py"
    fake.write_text(FAKE)
    return subprocess.run(
        [sys.executable, str(GUARD), "--label", "t", "--min-tests", str(min_tests), *extra,
         "--junit", str(tmp_path / "out" / "junit.xml"), "--", sys.executable, str(fake), spec],
        capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin"},
    )


def test_healthy_run_passes(tmp_path):
    r = _run(tmp_path, "tests=50,errors=0,failures=0,skipped=5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "45 executed" in r.stdout


def test_zero_tests_collected_fails_even_when_pytest_exits_zero(tmp_path):
    """The exact nightly failure: nothing ran and something upstream called it fine."""
    r = _run(tmp_path, "tests=0,errors=0,failures=0,skipped=0")
    assert r.returncode == 1, r.stdout
    assert "only 0 tests executed" in r.stdout


def test_everything_skipped_is_not_a_pass(tmp_path):
    r = _run(tmp_path, "tests=500,errors=0,failures=0,skipped=500")
    assert r.returncode == 1
    assert "0 executed" in r.stdout


def test_collection_error_fails_even_when_pytest_exits_zero(tmp_path):
    """e.g. a wrapper that masked pytest's exit code 2."""
    r = _run(tmp_path, "tests=100,errors=3,failures=0,skipped=0")
    assert r.returncode == 1
    assert "3 testcase ERROR" in r.stdout


def test_missing_junit_xml_fails(tmp_path):
    r = _run(tmp_path, "write=0,tests=0,errors=0,failures=0,skipped=0")
    assert r.returncode == 1
    assert "no readable JUnit XML" in r.stdout


def test_pytest_failure_exit_code_is_propagated(tmp_path):
    r = _run(tmp_path, "tests=100,errors=0,failures=4,skipped=0,rc=1")
    assert r.returncode == 1
    r = _run(tmp_path, "tests=100,errors=0,failures=0,skipped=0,rc=2")
    assert r.returncode == 2


def test_stale_junit_from_an_earlier_run_cannot_satisfy_this_one(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "junit.xml").write_text(
        '<testsuites><testsuite tests="9999" errors="0" failures="0" skipped="0"/></testsuites>'
    )
    r = _run(tmp_path, "write=0,tests=0,errors=0,failures=0,skipped=0")
    assert r.returncode == 1, "a leftover XML satisfied a run that wrote none"


def test_skip_budget_catches_a_missing_dependency_the_floor_cannot_see(tmp_path):
    """500 skips out of 20,000 leaves 19,500 executed - far above any floor - but is exactly what
    an interpreter that lost chromadb looks like (every MemPalace test importorskips)."""
    spec = "tests=20000,errors=0,failures=0,skipped=500"
    assert _run(tmp_path, spec, min_tests=15000).returncode == 0  # floor alone: green
    r = _run(tmp_path, spec, min_tests=15000, extra=("--max-skipped", "100"))
    assert r.returncode == 1 and "over the budget of 100" in r.stdout
    assert _run(tmp_path, spec, min_tests=15000, extra=("--max-skipped", "500")).returncode == 0


def test_a_zero_floor_is_refused():
    """--min-tests 0 would re-create the vacuous check."""
    r = subprocess.run(
        [sys.executable, str(GUARD), "--label", "t", "--min-tests", "0", "--", sys.executable, "-c", "pass"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 2
    assert "--min-tests must be >= 1" in r.stderr
