"""Pin tools/audit/flag_inventory.py extraction behaviour on a small fixture.

The repo churns, so the contract under test is the TOOL's extraction — not the
live repo's flag set. Fixture files exercise every read shape (environ.get,
getenv, subscript, typed_env kwarg/positional), the dynamic-default honesty
rule, section classification (prod vs labs vs excluded tests), and the
determinism of the rendered markdown.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_REPO = Path(__file__).resolve().parents[2]
_TOOL = _REPO / "tools" / "audit" / "flag_inventory.py"
_spec = importlib.util.spec_from_file_location("flag_inventory", _TOOL)
flag_inventory = importlib.util.module_from_spec(_spec)
sys.modules["flag_inventory"] = flag_inventory
_spec.loader.exec_module(flag_inventory)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "svc.py").write_text(
        "import os\n"
        "from typed_env import env_bool, env_int, env_str, env_float, env_list\n"
        'A = os.environ.get("ZOE_ALPHA", "on")\n'
        'B = os.getenv("ZOE_BETA")\n'
        'C = os.environ["ZOE_GAMMA"]\n'
        'D = env_bool("ZOE_DELTA", default=True)\n'
        'E = env_int("ZOE_EPSILON", 42)\n'
        'S = env_str("ZOE_ZETA", "v")\n'
        'FL = env_float("ZOE_ETA", 1.5)\n'
        'L = env_list("ZOE_THETA", [])\n'
        'os.environ["ZOE_WRITTEN"] = "set-not-read"\n'
        'del os.environ["ZOE_DELETED"]\n'
        'F = os.environ.get("ZOE_DYN", compute_default())\n'
        'G = os.environ.get("NOT_ZOE_FLAG", "x")\n'
        'H = os.environ.get(variable_key, "x")\n'
    )
    (tmp_path / "labs").mkdir()
    (tmp_path / "labs" / "exp.py").write_text('import os\nX = os.getenv("ZOE_LAB_ONLY", "1")\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text('import os\nos.environ.get("ZOE_TEST_ONLY")\n')
    (tmp_path / ".env.example").write_text("ZOE_ALPHA=on\n# ZOE_BETA=commented\n")
    return tmp_path


def _scan(repo: Path) -> dict:
    files = [str(p.relative_to(repo)) for p in sorted(repo.rglob("*.py"))]
    return flag_inventory.scan_repo(repo, files=files)


def test_extraction_shapes_and_defaults(fixture_repo: Path) -> None:
    prod = _scan(fixture_repo)["flags"]["prod"]
    assert set(prod) == {
        "ZOE_ALPHA", "ZOE_BETA", "ZOE_GAMMA", "ZOE_DELTA", "ZOE_EPSILON",
        "ZOE_ZETA", "ZOE_ETA", "ZOE_THETA", "ZOE_DYN",
    }
    # writes are NOT reads: assignment/deletion targets must not appear at
    # all, let alone as (required) — Greptile P1, PR #1434
    assert "ZOE_WRITTEN" not in prod
    assert "ZOE_DELETED" not in prod
    assert prod["ZOE_ALPHA"]["defaults"] == ["'on'"]
    assert prod["ZOE_BETA"]["defaults"] == ["-"]  # no default arg
    assert prod["ZOE_GAMMA"]["defaults"] == ["(required)"]  # bare subscript
    assert prod["ZOE_DELTA"]["defaults"] == ["True"]  # typed_env kwarg
    assert prod["ZOE_EPSILON"]["defaults"] == ["42"]  # typed_env positional
    assert prod["ZOE_DYN"]["defaults"] == ["dynamic"]  # honest non-literal


def test_typed_env_and_env_example_flags(fixture_repo: Path) -> None:
    prod = _scan(fixture_repo)["flags"]["prod"]
    assert prod["ZOE_DELTA"]["typed_env"] is True
    assert prod["ZOE_ZETA"]["typed_env"] is True   # env_str
    assert prod["ZOE_ETA"]["typed_env"] is True    # env_float
    assert prod["ZOE_THETA"]["typed_env"] is True  # env_list
    assert prod["ZOE_ALPHA"]["typed_env"] is False
    assert prod["ZOE_ALPHA"]["in_env_example"] is True
    assert prod["ZOE_BETA"]["in_env_example"] is True  # commented line still counts
    assert prod["ZOE_GAMMA"]["in_env_example"] is False
    assert prod["ZOE_ALPHA"]["readers"] == ["services/svc.py"]


def test_sections_lab_vs_prod_and_tests_excluded(fixture_repo: Path) -> None:
    flags = _scan(fixture_repo)["flags"]
    assert set(flags["lab"]) == {"ZOE_LAB_ONLY"}
    assert "ZOE_LAB_ONLY" not in flags["prod"]
    assert "ZOE_TEST_ONLY" not in flags["prod"]
    assert "ZOE_TEST_ONLY" not in flags["lab"]


def test_markdown_deterministic_and_dateless_body(fixture_repo: Path) -> None:
    data = _scan(fixture_repo)
    md1 = flag_inventory.render_markdown(data, "2026-01-01")
    md2 = flag_inventory.render_markdown(_scan(fixture_repo), "2026-01-01")
    assert md1 == md2
    body = md1.split("## Production flags", 1)[1]
    assert "2026-01-01" not in body  # no timestamps in the table body
    assert "GENERATED" in md1 and "flag_inventory.py" in md1
    # date only differs in header, table body identical
    other = flag_inventory.render_markdown(data, "2027-12-31")
    assert other.split("## Production flags", 1)[1] == body


def test_committed_inventory_matches_generator_output() -> None:
    """Drift guard: the committed generated files match a fresh generator run.

    The generator is deterministic by construction (sorted output; the only
    date lives in the markdown header), so a byte-exact comparison is cheap
    and honest: render with the date already committed in the markdown, then
    require both files to match exactly. On failure, regenerate and commit:
    ``python3 tools/audit/flag_inventory.py``.
    """
    md_path = _REPO / "docs" / "knowledge" / "flag-inventory.md"
    json_path = _REPO / "docs" / "knowledge" / "flag-inventory.json"
    committed_md = md_path.read_text(encoding="utf-8")
    committed_json = json_path.read_text(encoding="utf-8")

    m = re.search(r"^Last generated: (\d{4}-\d{2}-\d{2})\.", committed_md, re.MULTILINE)
    assert m, "committed markdown is missing its 'Last generated: YYYY-MM-DD.' line"

    data = flag_inventory.scan_repo(_REPO)
    regen_hint = "stale inventory — rerun: python3 tools/audit/flag_inventory.py"
    assert flag_inventory.render_markdown(data, m.group(1)) == committed_md, regen_hint
    assert json.dumps(data, indent=2, sort_keys=True) + "\n" == committed_json, regen_hint


def test_wrapper_spellings_are_scanned_and_value_shape_is_skipped(tmp_path):
    """(name, default)-shaped wrappers must be recorded; the (value, default)-
    shaped coercers sharing the same names in pi_* modules must NOT produce
    rows, because their call sites pass variables, not ZOE_* constants —
    the exact confusion that kept 15+ real flags out of the inventory."""
    (tmp_path / "m.py").write_text(
        'X = _env_flag("ZOE_WRAP_A", True)\n'
        'Y = _env_float("ZOE_WRAP_B", 4.0)\n'
        'Z = _int_from_env("ZOE_WRAP_C", 7)\n'
        'raw = os.environ.get("ZOE_WRAP_D")\n'
        'W = _env_bool(raw, default=True)\n'  # value-shaped: must add no row
        'V = _env_flag("NOT_ZOE_FLAG", False)\n'  # non-ZOE constant: FLAG_RE must reject
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    names = set(data["flags"]["prod"]) | set(data["flags"]["lab"])
    # EXACT set, not subset (polly cross-review, #1575): the old negative
    # assertion ("raw" not in names) was near-vacuous — a variable argument is
    # an ast.Name and could never be recorded even by a broken filter. Exact
    # equality is what actually catches an over-recording regression, including
    # the value-shaped coercer call which must add no row.
    assert names == {"ZOE_WRAP_A", "ZOE_WRAP_B", "ZOE_WRAP_C", "ZOE_WRAP_D"}, names


def test_wrapper_delegating_to_typed_env_records_a_typed_read(tmp_path):
    """A local wrapper whose body returns a typed_env call is a TYPED read;
    a wrapper reading os.environ raw is not (Greptile P1, #1575 — recording
    delegating wrappers as untyped misreported typed-env adoption)."""
    (tmp_path / "m.py").write_text(
        "def _env_float(name, default):\n"
        "    return env_float(name, default)\n"
        "def _env_flag(name, default):\n"
        "    raw = os.environ.get(name)\n"
        "    return default if raw is None else raw == '1'\n"
        "A = _env_float(\"ZOE_TYPED_WRAP\", 4.0)\n"
        "B = _env_flag(\"ZOE_RAW_WRAP\", True)\n"
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    flat = {**data["flags"]["prod"], **data["flags"]["lab"]}
    assert flat["ZOE_TYPED_WRAP"]["typed_env"] is True
    assert flat["ZOE_RAW_WRAP"]["typed_env"] is False
