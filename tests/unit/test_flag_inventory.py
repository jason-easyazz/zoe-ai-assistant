"""Pin tools/audit/flag_inventory.py extraction behaviour on a small fixture.

The repo churns, so the contract under test is the TOOL's extraction — not the
live repo's flag set. Fixture files exercise every read shape (environ.get,
getenv, subscript, typed_env kwarg/positional), the dynamic-default honesty
rule, section classification (prod vs labs vs excluded tests), and the
determinism of the rendered markdown.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "audit" / "flag_inventory.py"
_spec = importlib.util.spec_from_file_location("flag_inventory", _TOOL)
flag_inventory = importlib.util.module_from_spec(_spec)
sys.modules["flag_inventory"] = flag_inventory
_spec.loader.exec_module(flag_inventory)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "svc.py").write_text(
        "import os\n"
        "from typed_env import env_bool, env_int\n"
        'A = os.environ.get("ZOE_ALPHA", "on")\n'
        'B = os.getenv("ZOE_BETA")\n'
        'C = os.environ["ZOE_GAMMA"]\n'
        'D = env_bool("ZOE_DELTA", default=True)\n'
        'E = env_int("ZOE_EPSILON", 42)\n'
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
    assert set(prod) == {"ZOE_ALPHA", "ZOE_BETA", "ZOE_GAMMA", "ZOE_DELTA", "ZOE_EPSILON", "ZOE_DYN"}
    assert prod["ZOE_ALPHA"]["defaults"] == ["'on'"]
    assert prod["ZOE_BETA"]["defaults"] == ["-"]  # no default arg
    assert prod["ZOE_GAMMA"]["defaults"] == ["(required)"]  # bare subscript
    assert prod["ZOE_DELTA"]["defaults"] == ["True"]  # typed_env kwarg
    assert prod["ZOE_EPSILON"]["defaults"] == ["42"]  # typed_env positional
    assert prod["ZOE_DYN"]["defaults"] == ["dynamic"]  # honest non-literal


def test_typed_env_and_env_example_flags(fixture_repo: Path) -> None:
    prod = _scan(fixture_repo)["flags"]["prod"]
    assert prod["ZOE_DELTA"]["typed_env"] is True
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
