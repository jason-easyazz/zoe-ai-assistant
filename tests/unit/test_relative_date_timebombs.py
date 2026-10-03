"""No test may assert a RELATIVE date ("8 days ago", "yesterday", "(…, today)")
rendered against the real clock.

Incident: ``test_recall_evidence.py`` asserted ``"(Tue 22 Sep, 8 days ago)"`` for a
fixed ``added_ts`` while the renderer read ``time.time()`` — green on merge day
(2026-09-30), red every day after; ``validate`` was red on every PR for three days.
A test whose expected words depend on WHEN it runs is a time-bomb. Inject the clock
(a ``now=`` argument, or ``monkeypatch.setattr`` on the module's ``time``/``datetime``)
or compute the expectation from the same ``now`` the code sees.

Static and deliberately narrow: a test function is flagged when a positive assertion
(``==``/``in``) carries a relative-date literal on its expected side, unless the test,
an autouse fixture, or a fixture it requests from its own module pins the clock, or
the test reads the real clock itself to build its inputs (the "same now" pattern).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SCANNED = (ROOT / "services" / "zoe-data" / "tests", ROOT / "services" / "zoe-auth" / "tests",
           ROOT / "tests" / "unit")

# Rendered relative dates. Bare "today" is everyday prose ("No events on the calendar
# today."), so it counts only in a rendered-date shape: "(Wed 30 Sep, today)", "[today]".
RELATIVE = re.compile(r"\b(?:minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b|\byesterday\b"
                      r"|[\[(,]\s*today\b|\btoday\s*[\])]", re.IGNORECASE)
CLOCK_WORD = re.compile(r"\b(?:time|datetime|date|now|today|clock|utcnow|\w+_now|now_\w+)\b")
NOW_KWARG = re.compile(r"^(?:now|today|clock|now_\w+|\w+_now)$")
# The real clock, read by the test itself to build its inputs.
REAL_CLOCK = re.compile(r"\b(?:datetime|date)\.(?:now|today|utcnow)\(|\btime\.time\(|\bzoe_now\("
                        r"|\btoday_for_zoe_tz\(")
PATCHERS = ("setattr", "patch", "patch.object")


def _constants(node: ast.AST) -> list[str]:
    """Relative-date string constants in an operand. A call's arguments are its
    INPUTS (``normalize_due_date("today")``), not the expectation — not descended."""
    if isinstance(node, ast.Call):
        return []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value] if RELATIVE.search(node.value) else []
    return [s for child in ast.iter_child_nodes(node) for s in _constants(child)]


def _relative_literals(test: ast.AST) -> list[str]:
    """Literals on a POSITIVE assertion; ``not in`` / ``!=`` cannot rot red."""
    if isinstance(test, ast.BoolOp):
        return [s for v in test.values for s in _relative_literals(v)]
    if isinstance(test, ast.Compare) and all(isinstance(op, (ast.Eq, ast.In, ast.Is)) for op in test.ops):
        return [s for operand in (test.left, *test.comparators) for s in _constants(operand)]
    return []


def _pins_or_computes(fn: ast.AST, src: str) -> bool:
    text = ast.get_source_segment(src, fn) or ""
    if REAL_CLOCK.search(text) or "freeze_time" in text or "time_machine" in text:
        return True
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg and NOW_KWARG.match(node.arg):
            return True
        if (isinstance(node, ast.Call) and ast.unparse(node.func).endswith(PATCHERS)
                and any(CLOCK_WORD.search(ast.unparse(a)) for a in node.args)):
            return True
    return False


def scan_source(src: str, rel: str) -> list[str]:
    """``rel::test -> [literals]`` for each unpinned relative-date assertion."""
    tree = ast.parse(src)
    fixtures = {n.name: n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any("fixture" in ast.unparse(d) for d in n.decorator_list)}
    if any("autouse=True" in ast.unparse(d) and _pins_or_computes(f, src)
           for f in fixtures.values() for d in f.decorator_list):
        return []
    findings = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.name.startswith("test"):
            continue
        lits = [s for a in ast.walk(fn) if isinstance(a, ast.Assert) for s in _relative_literals(a.test)]
        if not lits or _pins_or_computes(fn, src):
            continue
        if any(a.arg in fixtures and _pins_or_computes(fixtures[a.arg], src) for a in fn.args.args):
            continue
        findings.append(f"{rel}::{fn.name} -> {lits}")
    return findings


def test_no_test_asserts_a_relative_date_against_the_real_clock():
    findings = []
    for base in SCANNED:
        for path in sorted(base.rglob("test_*.py")):
            findings += scan_source(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    assert not findings, ("relative-date assertion against an unpinned clock (a time-bomb: "
                          "green today, red on a later day) — inject/pin the clock, or compute "
                          "the expectation from the same now:\n  " + "\n  ".join(findings))


# ── negative controls: the scan must see the incident shape, and only that ─────

INCIDENT = '''
def test_tool_result(svc, monkeypatch):
    monkeypatch.setattr(auth, "_TOKEN", "tok")
    body = call("sister flight")
    assert "(Tue 22 Sep, 8 days ago)" in body["packet"]
'''


@pytest.mark.parametrize("src", [
    INCIDENT,
    "def test_a():\n    assert render(TS) == 'Mon 1 Sep, yesterday'\n",
    "async def test_a():\n    out = await run()\n    assert 'x' in out and '- [today] note' in out\n",
    "def test_a():\n    assert {'when': '3 weeks ago'} == f()\n",
])
def test_time_bomb_shapes_are_flagged(src):
    assert scan_source(src, "t.py")


@pytest.mark.parametrize("src", [
    # pinned in the test (the PR #1801 fix)
    INCIDENT.replace('monkeypatch.setattr(auth, "_TOKEN", "tok")',
                     'monkeypatch.setattr(rev, "time", types.SimpleNamespace(time=lambda: NOW))'),
    # pinned in a fixture the test requests (this PR's fix), patch call split over lines
    "import pytest\n@pytest.fixture\ndef svc(monkeypatch):\n"
    "    monkeypatch.setattr(\n        rev,\n        'time',\n        FakeClock(),\n    )\n" + INCIDENT,
    # pinned by an autouse fixture
    "import pytest\n@pytest.fixture(autouse=True)\ndef _clock(monkeypatch):\n"
    "    monkeypatch.setattr(mod, 'datetime', Frozen)\n" + INCIDENT,
    # the clock injected as an argument
    "def test_a():\n    assert rev.date_suffix(meta, now=NOW) == ' (Tue 29 Sep, yesterday)'\n",
    # expectation computed from the same real now
    "def test_a():\n    added = datetime.now(timezone.utc).isoformat()\n"
    "    assert '- [today] x' in render(added)\n",
    # negative assertions and call INPUTS cannot rot red; prose 'today' is not a rendered date
    "def test_a():\n    assert '8 days ago' not in out\n",
    "def test_a():\n    assert normalize_due_date('yesterday') == expected\n",
    "def test_a():\n    assert out == 'No events on the calendar today.'\n",
])
def test_safe_shapes_are_not_flagged(src):
    assert scan_source(src, "t.py") == []
