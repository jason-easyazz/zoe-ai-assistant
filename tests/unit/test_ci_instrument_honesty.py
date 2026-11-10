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
#
# Since 2026-11-10 the required gate is SHARDED: the lanes run in parallel jobs and the job named `validate` is an
# aggregator that `needs:` all of them (see the header of validate.yml). These checks follow the lanes into
# whichever gate job holds them.

LANE_PREFIX = "Lane — "
AGGREGATOR = "validate"
# The informational jobs in validate.yml: not part of the required gate, so not needed by the aggregator.
NOT_GATE = {"zoe-data-py312", "deps-resolvable", "secret-scan"}
# shard job -> the lane it splits and the aggregator's `--lane` label for it
SHARD_JOBS = {"zoe-data-shard": "zoe-data", "unit-shard": "tests/unit"}
EXPECTED_LANES = {
    "Lane — zoe-data (ci_safe) shard",
    "Lane — tests/unit (ci_safe) shard",
    "Lane — Home Assistant MCP bridge",
    "Lane — zoe-auth (full)",
}
# Gate jobs that test a service on ITS container's interpreter from ITS own manifest, not the shared 3.10 ci-deps.
OWN_RUNTIME_JOBS = {"zoe-auth"}
SPLIT_RE = re.compile(r"--shard-count (\d+) --shard-index \$\{\{ matrix\.group \}\} --shard-weights=tools/ci/shard_weights\.json")
# `=` is load-bearing: a SPACE-separated path value is read as a positional test path while pytest determines its
# rootdir (before plugin options are parsed), which can shift every node id (tests/unit/test_shard_ledger.py).


def gate_jobs(spec: dict) -> dict:
    return {j: cfg for j, cfg in spec["jobs"].items() if j not in NOT_GATE and j != AGGREGATOR}


def validate_violations(spec: dict) -> list[str]:
    out: list[str] = []
    jobs = spec["jobs"]
    agg = jobs.get(AGGREGATOR)
    if agg is None:
        return ["no `validate` job: the required context has no producer"]
    # The required context is the job's check-run name. A matrix, or a `name:` that is not
    # the id, renames it ("validate (3.10)") and the required context never reports.
    if "strategy" in agg:
        out.append("`validate` has a strategy/matrix: that renames the required check")
    if agg.get("name", AGGREGATOR) != AGGREGATOR:
        out.append(f"`validate` job renamed to {agg.get('name')!r}")
    if agg.get("runs-on") != "ubuntu-latest":
        out.append("`validate` no longer runs on ubuntu-latest")
    if str(agg.get("if", "")).replace(" ", "") != "${{always()}}":
        out.append("`validate` is not `if: always()` — a failed/cancelled shard would SKIP the required context")
    needs = agg.get("needs") or []
    needs = [needs] if isinstance(needs, str) else list(needs)
    gate = gate_jobs(spec)
    if set(needs) != set(gate):
        out.append(f"`validate` needs {sorted(needs)} but the gate jobs are {sorted(gate)}: an un-needed job cannot hold the gate")
    verdict = "\n".join(_code_lines(agg["steps"][-1])) if agg.get("steps") else ""
    if "toJSON(needs)" not in str((agg.get("steps") or [{}])[-1].get("env", "")) or '!= "success"' not in verdict:
        out.append("`validate`'s last step does not fail on every needed result that is not exactly 'success'")
    if "always()" not in str((agg.get("steps") or [{}])[-1].get("if", "")):
        out.append("`validate`'s verdict step can be skipped")
    agg_run = "\n".join(ln for s in agg.get("steps") or [] for ln in _code_lines(s))
    if "tools/ci/shard_ledger.py verify" not in agg_run:
        out.append("`validate` no longer verifies the shard ledgers (a dropped test would go unnoticed)")

    for jname, job in [(AGGREGATOR, agg)] + list(gate.items()):
        if job.get("continue-on-error"):
            out.append(f"{jname} is continue-on-error")
        for s in job.get("steps") or []:
            if s.get("continue-on-error"):
                out.append(f"{jname}/{s.get('name')}: continue-on-error in the required gate")
            for ln in _code_lines(s):
                if "pytest" in ln and re.search(r"(?<!\S)-x(?!\S)|--exitfirst", ln):
                    out.append(f"{s.get('name')}: `-x` stops the run at the first failure")

    lanes = {}
    for jname, job in gate.items():
        for s in job.get("steps") or []:
            if str(s.get("name", "")).startswith(LANE_PREFIX):
                lanes[s["name"]] = (jname, s)
    if set(lanes) != EXPECTED_LANES:
        out.append(f"expected the 4 pytest lane steps {sorted(EXPECTED_LANES)}, found {sorted(lanes)}")
    for name, (jname, s) in lanes.items():
        if not _is_pytest_step(s):
            out.append(f"{name}: no pytest invocation")
        if not _has_tok(s, "--maxfail=200"):
            out.append(f"{name}: missing --maxfail=200")
        if "!cancelled()" not in str(s.get("if", "")):
            out.append(f"{name}: runs only if an earlier lane passed")

    for jname, lane in SHARD_JOBS.items():
        job = gate.get(jname)
        if job is None:
            out.append(f"shard job {jname} is missing")
            continue
        strat = job.get("strategy") or {}
        groups = (strat.get("matrix") or {}).get("group")
        if strat.get("fail-fast") is not False:
            out.append(f"{jname}: fail-fast must be false, or one red shard cancels the others and hides their results")
        run = "\n".join(ln for s in job["steps"] for ln in _code_lines(s))
        m = SPLIT_RE.search(run)
        if not m:
            out.append(f"{jname}: no --shard-count/--shard-index invocation with the matrix group")
            continue
        n = int(m.group(1))
        if groups != list(range(1, n + 1)):
            out.append(f"{jname}: matrix groups {groups} do not cover --shard-count {n} exactly (a group would never run)")
        if f"({{{{ matrix.group }}}}/{n})" not in str(job.get("name", "")).replace("${{ matrix.group }}", "{{ matrix.group }}"):
            out.append(f"{jname}: the job name does not say /{n}")
        exp = re.search(rf"--lane {re.escape(lane)} .*?--expect-shards (\d+)", agg_run, re.S)
        if not exp or int(exp.group(1)) != n:
            out.append(f"{jname}: the aggregator's --expect-shards for {lane} does not equal --shard-count {n}")
        if "-p pytest_shard_ledger" not in run:
            out.append(f"{jname}: the shard does not write its ledger")
        lane_env = next((s.get("env") or {} for s in job["steps"] if str(s.get("name", "")).startswith(LANE_PREFIX)), {})
        if "PYTEST_SHARD_LEDGER" not in lane_env:
            out.append(f"{jname}: PYTEST_SHARD_LEDGER is not set, so the ledger plugin is inert")
        up = [s for s in job["steps"] if "upload-artifact" in str(s.get("uses", ""))]
        if not up or "always()" not in str(up[0].get("if", "")):
            out.append(f"{jname}: the ledger upload is not `if: always()`")
        if "--durations=50" not in run:
            out.append(f"{jname}: the shard no longer prints its slowest tests")

    for jname, job in gate.items():
        if jname in OWN_RUNTIME_JOBS:
            out.extend(zoe_auth_violations(spec))
            continue
        deps = [s for s in job.get("steps") or [] if s.get("uses") == "./.github/actions/ci-deps"]
        if not deps or str(deps[0]["with"]["python-version"]) != "3.10":
            out.append(f"{jname}: the required gate no longer installs via ./.github/actions/ci-deps on python 3.10")
    return out


def _pytest_args(text: str) -> list[str]:
    """The pytest arguments of a zoe-auth run, from `-o` to `-q`, with line continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    m = re.search(r"pytest\s+(-o faulthandler_timeout=\S+.*?--maxfail=200 -q)", joined, re.S)
    return m.group(1).split() if m else []


def zoe_auth_violations(spec: dict) -> list[str]:
    """zoe-auth runs Python X.Y in production (its Dockerfile FROM), so its gate job must run that interpreter and
    install only its own manifest. On the shared 3.10 install a >=3.11-only release failed the whole gate (#2387),
    and zoe-auth was tested on an interpreter it never runs on. The local twin must run the same pytest command."""
    out: list[str] = []
    job = spec["jobs"].get("zoe-auth")
    if job is None:
        return ["no zoe-auth job: its suite would run nowhere"]
    m = re.search(r"^FROM python:(\d+\.\d+)", (REPO / "services" / "zoe-auth" / "Dockerfile").read_text(encoding="utf-8"), re.M)
    want = m.group(1) if m else "?"
    setup = [s for s in job.get("steps") or [] if str(s.get("uses", "")).startswith("actions/setup-python@")]
    if len(setup) != 1 or str((setup[0].get("with") or {}).get("python-version")) != want:
        out.append(f"zoe-auth: must set up exactly Python {want} (services/zoe-auth/Dockerfile FROM)")
    if any(s.get("uses") == "./.github/actions/ci-deps" for s in job.get("steps") or []):
        out.append("zoe-auth: the shared 3.10 ci-deps install is back")
    install = [ln.split() for s in job.get("steps") or [] for ln in _code_lines(s) if "pip install" in ln]
    if install != [["pip", "install", "--quiet", "-r", "services/zoe-auth/requirements.txt"]]:
        out.append(f"zoe-auth: must install only its own manifest, got {install}")
    if "services" in job:
        out.append("zoe-auth: the suite is SQLite-only; a Postgres service would hide a new dependency")
    lane = next((s for s in job.get("steps") or [] if s.get("name") == "Lane — zoe-auth (full)"), {})
    twin = (REPO / "scripts" / "maintenance" / "zoe_auth_tests.sh").read_text(encoding="utf-8")
    if not _pytest_args(str(lane.get("run", ""))) or _pytest_args(str(lane.get("run", ""))) != _pytest_args(twin):
        out.append("zoe-auth: the lane's pytest command differs from scripts/maintenance/zoe_auth_tests.sh")
    return out


def test_zoe_auth_runs_its_container_interpreter():
    assert zoe_auth_violations(_load("validate.yml")) == []


@pytest.mark.parametrize("break_it", ["py310", "shared_install", "extra_flag", "postgres"])
def test_zoe_auth_check_can_fail(break_it):
    spec = copy.deepcopy(_load("validate.yml"))
    steps = spec["jobs"]["zoe-auth"]["steps"]
    if break_it == "py310":
        next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python@"))["with"]["python-version"] = "3.10"
    elif break_it == "shared_install":
        steps.insert(1, {"uses": "./.github/actions/ci-deps", "with": {"python-version": "3.10"}})
    elif break_it == "extra_flag":
        lane = next(s for s in steps if s.get("name") == "Lane — zoe-auth (full)")
        lane["run"] = lane["run"].replace("--maxfail=200 -q", "-k smoke --maxfail=200 -q")
    else:
        spec["jobs"]["zoe-auth"]["services"] = {"postgres": {"image": "pgvector/pgvector:pg17"}}
    assert zoe_auth_violations(spec), break_it
    assert validate_violations(spec), break_it


def test_validate_lanes_are_independent_and_not_fail_fast():
    assert validate_violations(_load("validate.yml")) == []


def test_validate_check_can_fail():
    good = _load("validate.yml")

    def lane(spec, name="Lane — zoe-data (ci_safe) shard"):
        return next(s for j in gate_jobs(spec).values() for s in j["steps"] if s.get("name") == name)

    spec = copy.deepcopy(good)
    lane(spec)["run"] = lane(spec)["run"].replace("--maxfail=200", "-x")
    assert any("`-x`" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["validate"]["strategy"] = {"matrix": {"python": ["3.10", "3.12"]}}
    assert any("renames the required check" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    lane(spec).pop("if")
    assert any("runs only if an earlier lane passed" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["validate"]["name"] = "validate (3.10)"
    assert any("renamed" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["validate"].pop("if")
    assert any("always()" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["validate"]["needs"] = [n for n in spec["jobs"]["validate"]["needs"] if n != "unit-shard"]
    assert any("un-needed job" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["zoe-data-shard"]["strategy"]["matrix"]["group"] = [1, 2, 3]
    assert any("do not cover --shard-count" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    spec["jobs"]["zoe-data-shard"]["strategy"]["fail-fast"] = True
    assert any("fail-fast" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    for s in spec["jobs"]["validate"]["steps"]:
        if "run" in s:
            s["run"] = s["run"].replace("--expect-shards 4", "--expect-shards 3")
    assert any("--expect-shards" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    verdict = spec["jobs"]["validate"]["steps"][-1]
    verdict["run"] = verdict["run"].replace('!= "success"', '== "failure"')
    assert any("not exactly 'success'" in v for v in validate_violations(spec))

    spec = copy.deepcopy(good)
    lane(spec)["continue-on-error"] = True
    assert any("continue-on-error" in v for v in validate_violations(spec))


def _norm(step: dict) -> list[str]:
    return _code_lines(step)


_SHARD_ONLY = (" -p pytest_shard_ledger",
               " --shard-count 4 --shard-index ${{ matrix.group }} --shard-weights=tools/ci/shard_weights.json")


def test_python_312_job_exists_beside_the_required_job_and_matches_its_install():
    spec = _load("validate.yml")
    jobs = spec["jobs"]
    assert "validate" in jobs and "zoe-data-py312" in jobs
    assert jobs["zoe-data-py312"].get("name", "zoe-data-py312") not in ("validate", "secret-scan")

    # ONE install for every lane job (the composite action), so the interpreters are the only difference.
    py312 = next(s for s in jobs["zoe-data-py312"]["steps"] if s.get("uses") == "./.github/actions/ci-deps")
    assert str(py312["with"]["python-version"]) == "3.12"
    action = yaml.safe_load((REPO / ".github" / "actions" / "ci-deps" / "action.yml").read_text(encoding="utf-8"))
    names = [s.get("name") for s in action["runs"]["steps"]]
    assert "Install slim CI deps" in names and "Install lane deps (pytest, numpy, LiveKit wheels)" in names

    # The same lane command as the required zoe-data shards, minus the split; the 3.12 job is database-free (the
    # ci_safe contract), so it carries no Postgres service, no migrations and no password fallback literal.
    assert "services" not in jobs["zoe-data-py312"]
    assert "services" not in jobs["zoe-data-shard"]
    assert not any("Alembic" in str(s.get("name", "")) for s in jobs["zoe-data-py312"]["steps"])
    lane312 = next(s for s in jobs["zoe-data-py312"]["steps"] if str(s.get("name", "")).startswith(LANE_PREFIX))
    shard = next(s for s in jobs["zoe-data-shard"]["steps"] if str(s.get("name", "")).startswith(LANE_PREFIX))

    def unsplit(step: dict) -> list[str]:
        lines = [ln for ln in _norm(step) if "--version" not in ln]
        out = []
        for ln in lines:
            for frag in _SHARD_ONLY:
                ln = ln.replace(frag, "")
            out.append(ln)
        return out

    assert all(frag.strip() in " ".join(_norm(shard)) for frag in _SHARD_ONLY), "the shard lost its split/ledger args"
    assert unsplit(lane312) == unsplit(shard)
    shard_env = dict(shard["env"])
    shard_env.pop("PYTEST_SHARD_LEDGER")
    shard_env["PYTHONPATH"] = shard_env["PYTHONPATH"].replace(":${{ github.workspace }}/tools/ci", "")
    assert lane312["env"] == shard_env


def test_python_312_job_runs_on_every_pull_request():
    """No `paths:`/job-level `if:` — an informational check is only worth heeding if it is
    reliably present (same rule as voice-gate)."""
    spec = _load("validate.yml")
    assert "if" not in spec["jobs"]["zoe-data-py312"]
    on = spec.get("on", spec.get(True, {}))
    assert "paths" not in (on.get("pull_request") or {}), on
