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


def test_precommit_keeps_the_inventory_fresh_hook_wired():
    """The freshness hook must stay wired — a stale committed inventory cost six
    separate incidents (red CI + merge conflicts) on 2026-07-27 before it
    existed. Pins presence and the properties that make it correct: it runs the
    real generator, fires on Python changes, and stays date-insensitive (the
    -I stamp filters), or every commit on a later calendar day false-fails."""
    import yaml

    cfg = yaml.safe_load((_REPO / ".pre-commit-config.yaml").read_text())
    # ALL local blocks, not just the first — moving the hook intact to a second
    # `repo: local` block is correctly wired and must not false-fail (polly
    # cross-review, non-blocking finding).
    hooks = [h for r in cfg["repos"] if r["repo"] == "local" for h in r.get("hooks", [])]
    hook = next((h for h in hooks if h["id"] == "zoe-flag-inventory-fresh"), None)
    assert hook is not None, "zoe-flag-inventory-fresh hook removed from pre-commit"
    # Structural, not substring: the generator must be the FIRST command the
    # entry executes — `echo tools/audit/flag_inventory.py …` mentions the path
    # without running it and must fail here (Codex P2 on the earlier substring).
    import shlex as _shlex
    outer = _shlex.split(hook["entry"])
    assert outer[:2] == ["bash", "-c"] and len(outer) >= 3, \
        f"unexpected entry shape: {hook['entry']!r}"
    script_tokens = _shlex.split(outer[2])
    assert script_tokens[:2] == ["python3", "tools/audit/flag_inventory.py"], \
        "entry's first command no longer executes the real generator"
    import re as _re
    assert _re.search(hook["files"], "services/zoe-data/example.py"), \
        "hook must fire on Python changes"
    # scan_repo() reads .env.example for every flag's in_env_example bit — an
    # .env.example-only commit must also trigger regeneration (Codex P2).
    assert _re.search(hook["files"], ".env.example"), \
        "hook must fire on .env.example changes"
    # BOTH -I filters: the JSON's `timestamp:` line churns with the date too —
    # dropping either filter false-fails every later-calendar-day commit while
    # a single-filter assertion stays green (polly cross-review, blocking #1).
    assert '-I "Last generated:"' in hook["entry"], "date-insensitivity dropped (md stamp)"
    assert '-I "^timestamp:"' in hook["entry"], "date-insensitivity dropped (json timestamp)"


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
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    names = set(data["flags"]["prod"]) | set(data["flags"]["lab"])
    # EXACT equality, not subset + a sentinel that FLAG_RE could never admit:
    # any spurious row from the value-shaped call — whatever the scanner would
    # name it — makes this fail (polly cross-review: the old "raw" negative
    # assertion was vacuous by construction).
    assert names == {"ZOE_WRAP_A", "ZOE_WRAP_B", "ZOE_WRAP_C", "ZOE_WRAP_D"}, \
        f"unexpected flag set from wrapper sweep: {sorted(names)}"


def test_typed_delegator_wrappers_count_as_typed(tmp_path):
    """A module-local wrapper that bare-delegates to a typed_env helper is a
    typed read; one with its own logic stays untyped (Greptile P1 on #1575:
    voice_tts/main.py delegators were inflating the untyped count)."""
    (tmp_path / "m.py").write_text(
        "def _env_int(name, default):\n"
        '    """doc"""\n'
        "    return env_int(name, default)\n"
        "def _env_float(name, default):\n"
        "    try:\n"
        "        return float(os.getenv(name, default))\n"
        "    except ValueError:\n"
        "        return default\n"
        'A = _env_int("ZOE_TD_A", 1)\n'
        'B = _env_float("ZOE_TD_B", 2.0)\n'
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    rows = {**data["flags"]["prod"], **data["flags"]["lab"]}
    assert rows["ZOE_TD_A"]["typed_env"] is True, "bare delegator must count as typed"
    assert rows["ZOE_TD_B"]["typed_env"] is False, "wrapper with own logic must stay untyped"


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


def test_non_forwarding_wrapper_is_not_a_delegator(tmp_path):
    """A wrapper returning a typed call that IGNORES its name parameter must not
    classify as a delegator — its callers' first args are not the flags being
    read (Codex, #1577)."""
    (tmp_path / "m.py").write_text(
        "def enabled(label):\n"
        "    return env_str(\"SOME_FIXED_KEY\")\n"
        "E = enabled(\"ZOE_NOT_ACTUALLY_READ\")\n"
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    flat = {**data["flags"]["prod"], **data["flags"]["lab"]}
    # the wrapper reads SOME_FIXED_KEY (non-ZOE, filtered); ZOE_NOT_ACTUALLY_READ
    # is never an env read at all and must not appear
    assert "ZOE_NOT_ACTUALLY_READ" not in flat, flat.keys()


def test_nested_delegator_name_collision_does_not_type_the_raw_wrapper(tmp_path):
    """The discriminating nested-def case is a NAME COLLISION (Codex, #1577 —
    the earlier test passed even without the fix): a nested bare delegator
    named like a raw module-level wrapper must not type reads through the raw
    one. Module-level functions only may register as delegators."""
    (tmp_path / "m.py").write_text(
        "def outer(name):\n"
        "    def _env_int(name):\n"        # nested BARE delegator, colliding name
        "        return env_int(name, 0)\n"
        "    return _env_int(name)\n"
        "def _env_int(name, default):\n"   # module-level RAW wrapper, same name
        "    raw = os.environ.get(name)\n"
        "    return default if raw is None else int(raw)\n"
        "A = _env_int(\"ZOE_RAW_COLLIDE\", 3)\n"
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    flat = {**data["flags"]["prod"], **data["flags"]["lab"]}
    assert flat["ZOE_RAW_COLLIDE"]["typed_env"] is False, flat["ZOE_RAW_COLLIDE"]
