"""Structural guards: the test INSTRUMENTS in CI must be able to say "red".

Architecture deep dive 2 (2026-10-19, lane K, T-1 / T-2) found two instruments that hid
failures:

  * `self-hosted-tests.yml` (the nightly catch-all on the Jetson) had `continue-on-error`
    on every pytest step, so it concluded `success` for at least 14 nights while two of
    its three suites collected ZERO tests and the third carried 16-19 failures (both XSS
    guards among them).
  * `validate.yml`'s four pytest lanes were one bash block of `pytest -x`: a single
    failing test stopped the zoe-data lane at 211 of ~18,500 and the lanes behind it
    never ran.

The fixes are YAML, and YAML can regress silently, so they are asserted here — each
check is also run against a deliberately broken copy (the negative control), because a
guard that cannot fail is the bug this file exists to remove.

The REQUIRED context name `validate` must survive all of this: see
`tests/unit/test_required_gate_workflows.py` (every required context must always report).
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"

# The only step allowed to keep `continue-on-error` in self-hosted-tests.yml: a version
# drift REPORT, not a test. Anything else swallowing an exit code re-opens the 14-green-
# nights hole.
CONTINUE_ON_ERROR_ALLOWLIST = {"drift"}


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(spec: dict, job: str) -> list[dict]:
    return spec["jobs"][job]["steps"]


def _code_lines(step: dict) -> list[str]:
    """The shell a step RUNS: comments and blanks stripped (comments name the very flags
    being asserted, so matching raw text lets a deleted flag hide behind its own comment)."""
    out: list[str] = []
    cont = ""
    for raw in str(step.get("run", "")).splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        # join `\` continuations so a flag on the next physical line belongs to its command
        if ln.endswith("\\"):
            cont += ln[:-1].strip() + " "
            continue
        out.append((cont + ln).strip())
        cont = ""
    if cont:
        out.append(cont.strip())
    return out


# A step RUNS pytest when it invokes it (`-m pytest` / `pytest ...` as a command / the guard
# wrapper); a step that merely mentions the word in an echo or an import does not.
_PYTEST_CMD = re.compile(
    r"^(?:\w+=(?:\"[^\"]*\"|\S*)\s+)*(?:pytest\s|\"?\$?\{?[\w/.${}-]*python[\w.]*\}?\"?\s+-m pytest\b|\S*python[\w.]*\s+\S*pytest_junit_guard\.py)",
    re.I,
)


def _is_pytest_step(step: dict) -> bool:
    return any(_PYTEST_CMD.search(ln) for ln in _code_lines(step))


def _has_tok(step: dict, token: str) -> bool:
    return any(re.search(rf"(?<!\S){re.escape(token)}(?!\S)", ln) for ln in _code_lines(step))


# ── self-hosted-tests.yml ────────────────────────────────────────────────────


def self_hosted_violations(spec: dict) -> list[str]:
    out: list[str] = []
    job = spec["jobs"]["host-tests"]
    if job.get("continue-on-error"):
        out.append("job-level continue-on-error")
    steps = job["steps"]
    for step in steps:
        label = step.get("id") or step.get("name")
        if step.get("continue-on-error") and step.get("id") not in CONTINUE_ON_ERROR_ALLOWLIST:
            out.append(f"{label}: continue-on-error swallows its exit code")

    pytest_steps = [s for s in steps if _is_pytest_step(s)]
    if len(pytest_steps) < 3:
        out.append(f"expected >=3 pytest steps, found {len(pytest_steps)}")

    node_idx = next(
        (i for i, s in enumerate(steps) if s.get("id") == "node"), None
    )
    first_pytest = next((i for i, s in enumerate(steps) if _is_pytest_step(s)), None)
    if node_idx is None:
        out.append("no `node` step: the JS harnesses fail closed without node on PATH")
    else:
        body = "\n".join(_code_lines(steps[node_idx]))
        if ".nvm/current/bin" not in body or "GITHUB_PATH" not in body:
            out.append("node step does not put ~/.nvm/current/bin on GITHUB_PATH")
        if "exit 1" not in body:
            out.append("node step does not fail when node is missing")
        if first_pytest is not None and node_idx > first_pytest:
            out.append("node step runs AFTER the first pytest step")

    interp_idx = next((i for i, s in enumerate(steps) if s.get("id") == "interp"), None)
    if interp_idx is None:
        out.append("no `interp` step: the suites would run on the rollback python3, which has no chromadb")
    else:
        body = "\n".join(_code_lines(steps[interp_idx]))
        if "zoe_data_python.sh" not in body or "ZOE_TEST_PYTHON" not in body:
            out.append("interp step does not resolve the SERVICE interpreter into ZOE_TEST_PYTHON")
        if first_pytest is not None and interp_idx > first_pytest:
            out.append("interp step runs AFTER the first pytest step")
        if "!cancelled()" not in str(steps[interp_idx].get("if", "")):
            out.append("interp step is skipped when an earlier step (node) fails, hiding which python ran")

    for step in pytest_steps:
        label = step.get("id") or step.get("name")
        cond = str(step.get("if", ""))
        if any(re.search(r"(?<![\w/\"$}])python3 -m pytest", ln) for ln in _code_lines(step)):
            out.append(f"{label}: runs `python3 -m pytest` (rollback interpreter) instead of ${{ZOE_TEST_PYTHON}}")
        if "!cancelled()" not in cond:
            out.append(f"{label}: no `!cancelled()` — a failing sibling suite would hide it")
        if step.get("id") == "live_suite":
            continue  # opt-in, run by plain pytest; red on collect-0 on purpose
        if not any("pytest_junit_guard.py" in ln for ln in _code_lines(step)):
            out.append(f"{label}: not run through pytest_junit_guard.py")
            continue
        run = "\n".join(_code_lines(step))
        m = re.search(r"--min-tests\s+(\d+)", run)
        if not m or int(m.group(1)) < 1:
            out.append(f"{label}: no positive --min-tests floor")
        if "--continue-on-collection-errors" not in run:
            out.append(f"{label}: a collection error would hide every later test")
    return out


def test_self_hosted_nightly_is_honest():
    assert self_hosted_violations(_load("self-hosted-tests.yml")) == []


def test_self_hosted_honesty_check_can_fail():
    """Negative controls: each regression the check exists for must be reported."""
    good = _load("self-hosted-tests.yml")
    steps = lambda spec: spec["jobs"]["host-tests"]["steps"]  # noqa: E731

    spec = copy.deepcopy(good)
    next(s for s in steps(spec) if s.get("id") == "zoe_data_full")["continue-on-error"] = True
    assert any("continue-on-error" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["host-tests"]["steps"] = [s for s in steps(spec) if s.get("id") != "node"]
    assert any("no `node` step" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    unit = next(s for s in steps(spec) if s.get("id") == "unit_full")
    unit["run"] = unit["run"].replace("--min-tests", "--no-floor")
    assert any("no positive --min-tests" in v or "not run through" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    unit = next(s for s in steps(spec) if s.get("id") == "unit_full")
    bare = re.sub(r'"\$\{ZOE_TEST_PYTHON[^}]*\}" -m pytest', "python3 -m pytest", unit["run"])
    assert bare != unit["run"], "control did not mutate anything"
    unit["run"] = bare
    assert any("rollback interpreter" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    interp = next(s for s in steps(spec) if s.get("id") == "interp")
    interp.pop("if")
    assert any("hiding which python ran" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["host-tests"]["steps"] = [s for s in steps(spec) if s.get("id") != "interp"]
    assert any("no `interp` step" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    unit = next(s for s in steps(spec) if s.get("id") == "unit_full")
    unit["if"] ="${{ steps.checkout.outcome == 'success' }}"
    assert any("!cancelled()" in v for v in self_hosted_violations(spec))

    spec = copy.deepcopy(good)
    unit = next(s for s in steps(spec) if s.get("id") == "unit_full")
    unit["run"] = unit["run"].replace("--continue-on-collection-errors", "")
    assert any("collection error" in v for v in self_hosted_violations(spec))


def test_self_hosted_nightly_is_not_a_required_context():
    """The nightly must not be able to freeze `main`: its job names are not required."""
    required = {"validate", "secret-scan"}
    # keep the mirror honest: the authority's literal must still be exactly this
    authority = (Path(__file__).parent / "test_required_gate_workflows.py").read_text(encoding="utf-8")
    assert 'REQUIRED_CONTEXTS = ("validate", "secret-scan")' in authority
    names = {job.get("name", job_id) for job_id, job in _load("self-hosted-tests.yml")["jobs"].items()}
    assert not names & required, names


# ── validate.yml ─────────────────────────────────────────────────────────────

LANE_PREFIX = "Lane — "


def validate_violations(spec: dict) -> list[str]:
    out: list[str] = []
    job = spec["jobs"]["validate"]
    # The required context is the job's check-run name. A matrix, or a `name:` that is not
    # the id, renames it ("validate (3.10)") and the required context never reports.
    if "strategy" in job:
        out.append("`validate` has a strategy/matrix: that renames the required check")
    if job.get("name", "validate") != "validate":
        out.append(f"`validate` job renamed to {job.get('name')!r}")
    if job.get("continue-on-error"):
        out.append("`validate` is continue-on-error")
    if job.get("runs-on") != "ubuntu-latest":
        out.append("`validate` no longer runs on ubuntu-latest")

    steps = job["steps"]
    for s in steps:
        if s.get("continue-on-error"):
            out.append(f"{s.get('name')}: continue-on-error in the required job")
        for ln in _code_lines(s):
            if "pytest" in ln and re.search(r"(?<!\S)-x(?!\S)|--exitfirst", ln):
                out.append(f"{s.get('name')}: `-x` stops the run at the first failure")

    lanes = [s for s in steps if str(s.get("name", "")).startswith(LANE_PREFIX)]
    if len(lanes) != 4:
        out.append(f"expected 4 separate pytest lane steps, found {len(lanes)}")
    for s in lanes:
        if not _is_pytest_step(s):
            out.append(f"{s.get('name')}: no pytest invocation")
        if not _has_tok(s, "--maxfail=200"):
            out.append(f"{s.get('name')}: missing --maxfail=200")
        if "!cancelled()" not in str(s.get("if", "")):
            out.append(f"{s.get('name')}: runs only if an earlier lane passed")
    py = next((s for s in steps if "setup-python" in str(s.get("uses", ""))), None)
    if not py or str(py["with"]["python-version"]) != "3.10":
        out.append("required `validate` job no longer pins python 3.10")
    return out


def test_validate_lanes_are_independent_and_not_fail_fast():
    assert validate_violations(_load("validate.yml")) == []


def test_validate_check_can_fail():
    good = _load("validate.yml")

    spec = copy.deepcopy(good)
    lane = next(s for s in spec["jobs"]["validate"]["steps"] if str(s.get("name", "")).startswith(LANE_PREFIX))
    lane["run"] = lane["run"].replace("--maxfail=200", "-x")
    assert any("`-x`" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["validate"]["strategy"] = {"matrix": {"python": ["3.10", "3.12"]}}
    assert any("renames the required check" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    lane = next(s for s in spec["jobs"]["validate"]["steps"] if str(s.get("name", "")).startswith(LANE_PREFIX))
    lane.pop("if")
    assert any("runs only if an earlier lane passed" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["validate"]["name"] = "validate (3.10)"
    assert any("renamed" in v for v in validate_violations(spec))


def _norm(step: dict) -> list[str]:
    return _code_lines(step)


def test_python_312_job_exists_beside_the_required_job_and_matches_its_install():
    spec = _load("validate.yml")
    jobs = spec["jobs"]
    assert "validate" in jobs and "zoe-data-py312" in jobs
    assert jobs["zoe-data-py312"].get("name", "zoe-data-py312") not in ("validate", "secret-scan")

    py312 = next(s for s in jobs["zoe-data-py312"]["steps"] if "setup-python" in str(s.get("uses", "")))
    assert str(py312["with"]["python-version"]) == "3.12"

    def by_name(job: str, name: str) -> dict:
        return next(s for s in jobs[job]["steps"] if s.get("name") == name)

    # Byte-identical (comments aside) installs, so the two cannot drift apart.
    assert _norm(by_name("validate", "Install slim CI deps")) == _norm(by_name("zoe-data-py312", "Install slim CI deps"))
    lane_deps = "Install lane deps (zoe-auth, numpy, LiveKit wheels)"
    assert _norm(by_name("validate", lane_deps)) == _norm(by_name("zoe-data-py312", lane_deps))

    # The same lane command; the 3.12 job is database-free (the ci_safe contract), so it carries no
    # Postgres service, no migrations and no password fallback literal - only the DB-free env.
    assert "services" not in jobs["zoe-data-py312"]
    assert not any("Alembic" in str(s.get("name", "")) for s in jobs["zoe-data-py312"]["steps"])
    lane312 = next(s for s in jobs["zoe-data-py312"]["steps"] if str(s.get("name", "")).startswith(LANE_PREFIX))
    lane310 = by_name("validate", "Lane — zoe-data (ci_safe)")
    strip = lambda s: [ln for ln in _norm(s) if "--version" not in ln]  # noqa: E731
    assert strip(lane312) == strip(lane310)
    db_free = {k: v for k, v in lane310["env"].items() if k != "POSTGRES_URL"}
    assert lane312["env"] == db_free


def test_python_312_job_runs_on_every_pull_request():
    """No `paths:`/job-level `if:` — an informational check is only worth heeding if it is
    reliably present (same rule as voice-gate)."""
    spec = _load("validate.yml")
    assert "if" not in spec["jobs"]["zoe-data-py312"]
    on = spec.get("on", spec.get(True, {}))
    assert "paths" not in (on.get("pull_request") or {}), on
