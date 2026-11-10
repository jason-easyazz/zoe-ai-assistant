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


def test_the_generator_runs_cleanly_on_this_tree(tmp_path: Path) -> None:
    """The inventory is a LOCAL build product, not a committed file (2026-11-10: the committed copy conflicted on
    nearly every concurrent PR). What this guards instead: the real generator, run exactly as a developer, the
    deploy or a device runs it (``main()``, no network), completes on this tree and writes a parseable JSON plus a
    markdown page that render back to each other. The INVARIANTS the inventory used to stand in for — every
    ``ZOE_*`` read is registered, defaults agree, the legacy-read ratchet — are ``tests/unit/test_flag_registry.py``,
    which scans the code directly."""
    out_json, out_md = tmp_path / "flag-inventory.json", tmp_path / "flag-inventory.md"
    assert flag_inventory.main(["--repo", str(_REPO), "--json", str(out_json), "--markdown", str(out_md)]) == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(data["flags"]["prod"]) > 100, "the scan found almost no flags - the generator is not scanning this tree"
    md = out_md.read_text(encoding="utf-8")
    m = re.search(r"^Last generated: (\d{4}-\d{2}-\d{2})\.", md, re.MULTILINE)
    assert m, "the markdown lost its 'Last generated: YYYY-MM-DD.' line"
    assert flag_inventory.render_markdown(data, m.group(1)) == md, "md and json disagree: the render is not deterministic"


def test_no_precommit_hook_restages_the_generated_inventory():
    """The old ``zoe-flag-inventory-fresh`` hook regenerated the inventory and ``git add``-ed it into every commit.
    That is exactly what made the files conflict on every concurrent PR, so it is gone, and re-adding a hook that
    stages a generated file would silently bring the conflicts back (they are gitignored; ``git add`` would fail)."""
    import yaml

    cfg = yaml.safe_load((_REPO / ".pre-commit-config.yaml").read_text())
    hooks = [h for r in cfg["repos"] if r["repo"] == "local" for h in r.get("hooks", [])]
    assert not any(h["id"] == "zoe-flag-inventory-fresh" for h in hooks)
    for h in hooks:
        entry = str(h.get("entry", ""))
        for generated in ("flag-inventory", "live-state.json", "memory-writer-map"):
            assert not ("git add" in entry and generated in entry), (h["id"], generated)


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


def test_a_read_through_a_module_constant_is_attributed_to_the_flag(tmp_path):
    """``_X_ENV = "ZOE_X"`` then ``os.environ.get(_X_ENV)`` is a read of ZOE_X (2026-10-18: ZOE_SEAM_RECALL_INJECT, live in the brain lane, was absent from the
    inventory for exactly this reason). A name bound more than once at module level, or to a non-flag string, stays unresolved (under-counted, never wrong)."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "m.py").write_text(
        "import os\n"
        '_ONCE_ENV = "ZOE_VIA_CONST"\n'
        'TYPED_ENV: str = "ZOE_VIA_ANN"\n'
        '_TWICE_ENV = "ZOE_REBOUND_A"\n'
        '_TWICE_ENV = "ZOE_REBOUND_B"\n'
        '_NOT_A_FLAG = "HOME"\n'
        "a = (os.environ.get(_ONCE_ENV) or '').strip()\n"
        "b = os.environ[TYPED_ENV]\n"
        "c = os.environ.get(_TWICE_ENV)\n"
        "d = os.environ.get(_NOT_A_FLAG)\n"
        "e = os.environ.get(_UNDEFINED_ENV)\n"
    )
    prod = flag_inventory.scan_repo(tmp_path, files=["services/m.py"])["flags"]["prod"]
    assert set(prod) == {"ZOE_VIA_CONST", "ZOE_VIA_ANN"}
    assert prod["ZOE_VIA_CONST"]["defaults"] == ["-"] and prod["ZOE_VIA_ANN"]["defaults"] == ["(required)"]


def test_the_zoe_data_env_example_counts_as_documentation(tmp_path):
    (tmp_path / "services" / "zoe-data").mkdir(parents=True)
    (tmp_path / "services" / "m.py").write_text('import os\nA = os.environ.get("ZOE_IN_SERVICE_EXAMPLE")\nB = os.environ.get("ZOE_IN_NO_EXAMPLE")\n')
    (tmp_path / "services" / "zoe-data" / ".env.example").write_text("# ZOE_IN_SERVICE_EXAMPLE=1\n")
    prod = flag_inventory.scan_repo(tmp_path, files=["services/m.py"])["flags"]["prod"]
    assert prod["ZOE_IN_SERVICE_EXAMPLE"]["in_env_example"] is True and prod["ZOE_IN_NO_EXAMPLE"]["in_env_example"] is False


def test_from_os_import_environ_and_or_defaults_and_non_zoe_names(tmp_path):
    """B6: ``from os import environ`` reads, ``environ.get(X) or "d"`` defaults, the module-literal default
    (``... or DEFAULT_TZ``) and non-``ZOE_`` names (separate ``other_env`` table, strict read shapes only)."""
    (tmp_path / "m.py").write_text(
        "from os import environ, getenv\n"
        'DEFAULT_TZ = "Australia/Perth"\n'
        'A = environ.get("ZOE_FROM_IMPORT", "7")\n'
        'B = environ["ZOE_FROM_IMPORT_REQ"]\n'
        'C = (environ.get("ZOE_OR_LIT") or "fallback").strip()\n'
        'D = environ.get("ZOE_OR_CONST") or DEFAULT_TZ\n'
        'E = environ.get("ZOE_OR_DYN") or compute()\n'
        'F = getenv("SOME_SERVICE_URL", "http://x")\n'
        'G = _env("NOT_ZOE_WRAPPER_NAME", 1)\n'  # loose wrappers never feed the non-ZOE table
    )
    data = flag_inventory.scan_repo(tmp_path, files=["m.py"])
    prod = data["flags"]["prod"]
    assert prod["ZOE_FROM_IMPORT"]["defaults"] == ["'7'"]
    assert prod["ZOE_FROM_IMPORT_REQ"]["defaults"] == ["(required)"]
    assert prod["ZOE_OR_LIT"]["defaults"] == ["'fallback'"]
    assert prod["ZOE_OR_CONST"]["defaults"] == ["'Australia/Perth'"]
    assert prod["ZOE_OR_DYN"]["defaults"] == ["dynamic"]
    assert set(data["other_env"]) == {"SOME_SERVICE_URL"}
    assert data["legacy_reads"] == {"m.py": 5}  # every ZOE_* read above bypasses typed_env


def test_effective_default_normalisation_is_spelling_blind_but_value_sensitive():
    d = flag_inventory.effective_default
    assert d("'1'") == d("True") == d("1") == True  # noqa: E712 - the point: bool-ish spellings are one default
    assert d("''") is None and d("-") is None       # an empty default and no default are both "unset"
    assert d("dynamic") is flag_inventory._SKIP and d("(required)") is flag_inventory._SKIP
    assert flag_inventory.distinct_defaults([d("'8192'"), d("8192")]) == [8192]
    assert len(flag_inventory.distinct_defaults([d("'http://a'"), d("'http://b'")])) == 2
    assert len(flag_inventory.distinct_defaults([d("False"), d("-")])) == 1   # off == unset for a flag
    assert len(flag_inventory.distinct_defaults([d("'x'"), d("-")])) == 2     # a value vs unset is a real difference


def test_aliased_reads_keyword_names_and_setdefault_are_seen(tmp_path):
    """Review of #2116: an alias must not hide a read. ``from os import environ as E``, ``getenv as ge``, ``env = os.environ``,
    ``env_bool(key="ZOE_X")`` and ``environ.setdefault`` are all reads; a setdefault VALUE is not a reader's default."""
    (tmp_path / "m.py").write_text(
        "import os\n"
        "from os import environ as E, getenv as ge\n"
        "env = os.environ\n"
        'A = E.get("ZOE_VIA_ALIAS", "1")\n'
        'B = ge("ZOE_VIA_GETENV_ALIAS", "2")\n'
        'C = env.get("ZOE_VIA_ASSIGNED", "3")\n'
        'D = env_bool(key="ZOE_VIA_KEYWORD", default=True)\n'
        'os.environ.setdefault("ZOE_VIA_SETDEFAULT", "child-value")\n'
        'F = E["ZOE_VIA_ALIAS_SUBSCRIPT"]\n'
    )
    prod = flag_inventory.scan_repo(tmp_path, files=["m.py"])["flags"]["prod"]
    assert set(prod) == {"ZOE_VIA_ALIAS", "ZOE_VIA_GETENV_ALIAS", "ZOE_VIA_ASSIGNED", "ZOE_VIA_KEYWORD",
                         "ZOE_VIA_SETDEFAULT", "ZOE_VIA_ALIAS_SUBSCRIPT"}
    assert prod["ZOE_VIA_KEYWORD"]["typed_env"] is True and prod["ZOE_VIA_KEYWORD"]["defaults"] == ["True"]
    assert prod["ZOE_VIA_SETDEFAULT"]["defaults"] == ["dynamic"]


def test_nan_and_inf_defaults_are_words_not_numbers():
    d = flag_inventory.effective_default
    assert d("'nan'") == "nan" and d("'nan'") == d("'nan'")   # nan != nan would make a flag conflict with itself
    assert d("'inf'") == "inf"
    assert d("'1.5'") == 1.5
