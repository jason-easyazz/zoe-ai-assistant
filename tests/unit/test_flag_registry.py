"""The flag registry and its CI rules (deep dive B6).

Two halves:

1. REPO rules — the real tree must satisfy them: every ``ZOE_*`` read is registered, every conflicting read
   default is declared/acknowledged, and direct (non-``typed_env``) reads never exceed the ratchet in
   ``docs/knowledge/flag-ratchet.json``.
2. NEGATIVE CONTROLS — the same checks run against a synthetic repo that violates each rule, so the rules are
   proven to fail when they should (a rule that cannot fail is not a rule).

Fix recipes when a repo rule goes red:
- unregistered read  -> ``python3 tools/audit/flag_inventory.py --sync-registry`` then correct the new entry
- default divergence -> use the registry default (``typed_env.flag("ZOE_X")``) or acknowledge the file in the
  entry's ``divergent_ok={path: reason}``
- ratchet            -> read the flag with ``typed_env.flag(...)``/``env_*`` instead of ``os.environ``; to lock in
  a migration run ``python3 tools/audit/flag_inventory.py --tighten-ratchet``
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fi = _load("flag_inventory", _REPO / "tools" / "audit" / "flag_inventory.py")
registry_mod = _load("flag_registry_under_test", _REPO / "services" / "zoe-data" / "flag_registry.py")


@pytest.fixture(scope="module")
def data() -> dict:
    return fi.scan_repo(_REPO)


# ---- half 1: the real tree ----------------------------------------------------------------------

def test_every_zoe_flag_read_is_registered(data):
    missing = fi.unregistered_reads(data)
    assert not missing, (
        f"{len(missing)} ZOE_* flag(s) are read but not in services/zoe-data/flag_registry.py: {missing[:12]}. "
        "Declare them: python3 tools/audit/flag_inventory.py --sync-registry (then correct type/default/doc)."
    )


def test_registry_entries_are_well_formed():
    problems = []
    for name, s in registry_mod.FLAGS.items():
        if not name.startswith("ZOE_"):
            problems.append(f"{name}: registry holds ZOE_* flags only")
        if s.type not in registry_mod.FLAG_TYPES:
            problems.append(f"{name}: unknown type {s.type!r}")
        if s.type == "bool" and not isinstance(s.default, bool):
            problems.append(f"{name}: bool flag with default {s.default!r}")
        if s.type == "int" and (isinstance(s.default, bool) or not isinstance(s.default, int)):
            problems.append(f"{name}: int flag with default {s.default!r}")
        if s.type == "float" and (isinstance(s.default, bool) or not isinstance(s.default, (int, float))):
            problems.append(f"{name}: float flag with default {s.default!r}")
        if s.type == "list" and not isinstance(s.default, (tuple, list)):
            problems.append(f"{name}: list flag with default {s.default!r}")
        if s.type == "str" and not isinstance(s.default, str):
            problems.append(f"{name}: str flag with default {s.default!r}")
        if s.type == "str" and s.default in ("True", "False"):
            # a bool spelled as a string is a sync-inference slip: a mode flag ('off'|'shadow'|'on') would silently get 'False'
            problems.append(f"{name}: str flag with the boolean-looking default {s.default!r} (declare bool, or the real mode default + allowed)")
        if s.allowed:
            if s.type != "str":
                problems.append(f"{name}: allowed values only apply to str flags")
            if s.default not in s.allowed:
                problems.append(f"{name}: default {s.default!r} not in allowed {s.allowed}")
        if s.owner_doc and not (_REPO / s.owner_doc).is_file():
            problems.append(f"{name}: owner_doc {s.owner_doc!r} does not exist")
        for f in s.divergent_ok:
            if not (_REPO / f).is_file():
                problems.append(f"{name}: divergent_ok names a missing file {f!r}")
    assert not problems, "\n".join(problems[:30])


def test_conflicting_defaults_are_all_declared_and_no_reader_contradicts_the_registry(data):
    unacked = [f for f, c in data["conflicts"].items() if not c["acknowledged"]]
    assert not unacked, (
        f"flag(s) whose readers hard-code different defaults, with no divergent_ok in the registry: {unacked}. "
        "Pick the documented/intended default, make the readers use typed_env.flag(...), or acknowledge each file "
        "in the entry's divergent_ok={path: reason}."
    )
    assert not data["divergences"], (
        "a reader hard-codes a default that differs from the registry's: "
        + "; ".join(f"{f} {v}" for f, v in sorted(data["divergences"].items())[:8])
    )


def test_legacy_direct_reads_never_exceed_the_ratchet(data):
    baseline = json.loads((_REPO / fi.RATCHET_REL).read_text(encoding="utf-8"))["legacy_reads"]
    bad = fi.ratchet_violations(data["legacy_reads"], baseline)
    assert not bad, (
        "new direct ZOE_* env reads (they bypass the registry/typed_env):\n  " + "\n  ".join(bad)
        + "\nRead the flag with typed_env.flag('ZOE_X') (register it first). The ceiling only goes down."
    )
    assert sum(data["legacy_reads"].values()) <= sum(baseline.values())


# ---- half 2: negative controls on synthetic repos -------------------------------------------------

def _make_repo(tmp_path: Path, entries: str, **files: str) -> Path:
    """A tmp repo with a REAL-shaped registry (real head, custom entries) and the given source files."""
    real = (_REPO / "services" / "zoe-data" / "flag_registry.py").read_text(encoding="utf-8")
    head = real.split(fi.REGISTRY_MARKER, 1)[0]
    reg_dir = tmp_path / "services" / "zoe-data"
    reg_dir.mkdir(parents=True)
    (reg_dir / "flag_registry.py").write_text(f"{head}{fi.REGISTRY_MARKER}\n_ENTRIES = (\n{entries}\n)\n")
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def _scan(repo: Path) -> dict:
    files = [str(p.relative_to(repo)) for p in sorted(repo.rglob("*")) if p.suffix in (".py", ".ts")]
    return fi.scan_repo(repo, files=files)


def test_control_unregistered_read_is_detected(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_KNOWN', 'bool', False),",
        **{"services/zoe-data/a.py": 'import os\nA = os.environ.get("ZOE_KNOWN")\nB = os.environ.get("ZOE_BRAND_NEW", "1")\n'},
    )
    assert fi.unregistered_reads(_scan(repo)) == ["ZOE_BRAND_NEW"]


def test_control_unregistered_typescript_read_is_detected(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_KNOWN', 'bool', False),",
        **{"services/zoe-core/x.ts": 'const a = process.env.ZOE_TS_NEW ?? "x";\nprocess.env.ZOE_TS_WRITTEN = "y";\n'},
    )
    d = _scan(repo)
    assert fi.unregistered_reads(d) == ["ZOE_TS_NEW"]  # the assignment is a write, not a read
    assert d["flags"]["prod"]["ZOE_TS_NEW"]["defaults"] == ["'x'"]


def test_control_registered_reads_pass(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_KNOWN', 'bool', False),\n    _f('ZOE_OTHER', 'int', 5),",
        **{"services/zoe-data/a.py": 'from typed_env import env_int, flag\nA = flag("ZOE_KNOWN")\nB = env_int("ZOE_OTHER", 5)\n'},
    )
    d = _scan(repo)
    assert fi.unregistered_reads(d) == []
    assert d["legacy_reads"] == {}  # both reads are typed
    assert d["divergences"] == {}


def test_control_reader_default_contradicting_the_registry_is_a_divergence(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_URLISH', 'str', 'http://a'),\n    _f('ZOE_TIMEOUT', 'int', 5),",
        **{
            "services/zoe-data/a.py": 'import os\nU = os.environ.get("ZOE_URLISH", "http://b")\nT = os.environ.get("ZOE_TIMEOUT", "5")\n',
        },
    )
    d = _scan(repo)
    assert list(d["divergences"]) == ["ZOE_URLISH"]  # '5' == 5 is spelling, not a divergence


def test_control_acknowledging_the_file_clears_the_divergence(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_URLISH', 'str', 'http://a', divergent_ok={'services/zoe-data/a.py': 'probe uses its own'}),",
        **{"services/zoe-data/a.py": 'import os\nU = os.environ.get("ZOE_URLISH", "http://b")\n'},
    )
    d = _scan(repo)
    assert d["divergences"] == {}


def test_control_two_readers_two_defaults_is_a_conflict_and_needs_an_ack(tmp_path):
    entries = "    _f('ZOE_URLISH', 'str', 'http://a'),"
    repo = _make_repo(
        tmp_path,
        entries,
        **{
            "services/zoe-data/a.py": 'import os\nU = os.environ.get("ZOE_URLISH", "http://a")\n',
            "services/zoe-data/b.py": 'import os\nU = os.environ.get("ZOE_URLISH")\n',  # unset vs a literal
        },
    )
    c = _scan(repo)["conflicts"]["ZOE_URLISH"]
    assert c["acknowledged"] is False
    assert sorted(c["sites"]) == ["services/zoe-data/a.py", "services/zoe-data/b.py"]


def test_control_an_acknowledgement_must_name_the_conflicting_files(tmp_path):
    sites = {
        "services/zoe-data/a.py": 'import os\nU = os.environ.get("ZOE_URLISH", "http://a")\n',
        "services/zoe-data/b.py": 'import os\nU = os.environ.get("ZOE_URLISH")\n',  # unset vs a literal: a contradiction
    }
    unrelated = "    _f('ZOE_URLISH', 'str', 'http://a', divergent_ok={'services/zoe-data/zzz.py': 'some other file'}),"
    c = _scan(_make_repo(tmp_path / "x", unrelated, **sites))["conflicts"]["ZOE_URLISH"]
    assert c["acknowledged"] is False and c["unacknowledged_sites"] == ["services/zoe-data/b.py"]
    named = "    _f('ZOE_URLISH', 'str', 'http://a', divergent_ok={'services/zoe-data/b.py': 'reads the caller env'}),"
    c = _scan(_make_repo(tmp_path / "y", named, **sites))["conflicts"]["ZOE_URLISH"]
    assert c["acknowledged"] is True and "unacknowledged_sites" not in c


def test_control_int_and_float_wrappers_make_a_number_not_a_bool(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_ANCHOR', 'bool', False),",
        **{"services/zoe-data/n.py": (
            'import os\nHOUR = int(os.environ.get("ZOE_HOUR", "1"))\n'
            'COST = float(os.environ.get("ZOE_COST", "0"))\n'
            'ON = bool(int(os.environ.get("ZOE_ON", "0")))\n'
            'ANCHOR = os.environ.get("ZOE_ANCHOR")\n')},
    )
    files = ["services/zoe-data/n.py"]
    wraps = fi.numeric_wrapped_flags(repo, files)
    assert wraps == {"ZOE_HOUR": {"int"}, "ZOE_COST": {"float"}}   # bool(int(..)) is a boolean read
    fi.sync_registry(repo, fi.scan_repo(repo, files=files, registry={}), files)
    reg = fi.load_registry(repo)
    assert (reg["ZOE_HOUR"]["type"], reg["ZOE_HOUR"]["default"]) == ("int", 1)
    assert (reg["ZOE_COST"]["type"], reg["ZOE_COST"]["default"]) == ("float", 0.0)
    assert reg["ZOE_ON"]["type"] == "bool"


def test_no_bool_declared_flag_is_read_as_a_number():
    # A bool declaration of a number is invisible to the default comparison ('1' normalises to True and
    # True == 1), so a wrong type here would let a real divergence through. Numeric reads must be int/float.
    import subprocess

    files = subprocess.run(["git", "-C", str(_REPO), "ls-files", "*.py"], capture_output=True, text=True, check=True).stdout.split()
    wrong = sorted(f for f, kinds in fi.numeric_wrapped_flags(_REPO, files).items()
                   if registry_mod.is_registered(f) and registry_mod.spec(f).type == "bool")
    assert not wrong, f"registered as bool but read through int()/float(): {wrong}. Declare them int/float with the code default."


def test_control_spelling_differences_and_or_defaults_are_not_conflicts(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_ON', 'bool', True),\n    _f('ZOE_TZ', 'str', 'Australia/Perth'),",
        **{
            "services/zoe-data/a.py": 'import os\nA = os.environ.get("ZOE_ON", "1")\nZ = os.environ.get("ZOE_TZ") or "Australia/Perth"\n',
            "services/zoe-data/b.py": 'import os\nfrom typed_env import env_bool\nA = env_bool("ZOE_ON", True)\nZ = (os.environ.get("ZOE_TZ", "") or "Australia/Perth").strip()\n',
        },
    )
    d = _scan(repo)
    assert d["conflicts"] == {}


def test_control_ratchet_flags_a_new_direct_read_and_ignores_typed_ones(tmp_path):
    baseline = {"services/zoe-data/old.py": 2}
    assert fi.ratchet_violations({"services/zoe-data/old.py": 2}, baseline) == []
    assert fi.ratchet_violations({"services/zoe-data/old.py": 1}, baseline) == []       # going down is fine
    assert fi.ratchet_violations({"services/zoe-data/old.py": 3}, baseline)             # one more read in a legacy file
    assert fi.ratchet_violations({"services/zoe-data/new.py": 1}, baseline)             # a direct read in a new file
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_A', 'bool', False),\n    _f('ZOE_B', 'bool', False),",
        **{"services/zoe-data/n.py": 'import os\nfrom typed_env import flag\nA = flag("ZOE_A")\nB = os.environ.get("ZOE_B")\n'},
    )
    assert _scan(repo)["legacy_reads"] == {"services/zoe-data/n.py": 1}  # only the os.environ read counts


def test_control_non_zoe_env_names_are_inventoried_separately(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_A', 'bool', False),",
        **{"services/zoe-data/n.py": 'import os\nA = os.environ.get("ZOE_A")\nH = os.getenv("HERMES_FAST_PATH", "0")\n'},
    )
    d = _scan(repo)
    assert "HERMES_FAST_PATH" in d["other_env"] and d["other_env"]["HERMES_FAST_PATH"]["defaults"] == ["'0'"]
    assert "HERMES_FAST_PATH" not in d["flags"]["prod"]


def test_sync_registry_adds_new_flags_without_touching_hand_edits(tmp_path):
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_HAND', 'str', 'shadow', allowed=('off', 'shadow'), doc='docs/x.md', note='hand note'),",
        **{"services/zoe-data/n.py": 'import os\nA = os.environ.get("ZOE_HAND", "off")\nB = os.environ.get("ZOE_NEWFLAG", "7")\n'},
    )
    files = ["services/zoe-data/n.py"]
    added = fi.sync_registry(repo, fi.scan_repo(repo, files=files, registry={}), files)
    assert added == ["ZOE_NEWFLAG"]
    reg = fi.load_registry(repo)
    assert reg["ZOE_HAND"]["default"] == "shadow" and reg["ZOE_HAND"]["allowed"] == ["off", "shadow"]
    assert reg["ZOE_HAND"]["note"] == "hand note"
    assert reg["ZOE_NEWFLAG"]["type"] == "int" and reg["ZOE_NEWFLAG"]["default"] == 7
    # idempotent: a second sync adds nothing and rewrites the same bytes
    before = (repo / fi.REGISTRY_REL).read_text()
    assert fi.sync_registry(repo, fi.scan_repo(repo, files=files, registry={}), files) == []
    assert (repo / fi.REGISTRY_REL).read_text() == before


def test_control_chained_typescript_env_reads_are_each_seen(tmp_path):
    """``process.env.A ?? process.env.B ?? "x"``: the second read must not be swallowed as the first's fallback
    (the shape ``labs/flue-zoe-telegram-2x/src/brain.ts`` already has)."""
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_KNOWN', 'bool', False),",
        **{"services/zoe-core/x.ts": 'const a = process.env.ZOE_TS_A ?? process.env.ZOE_TS_B ?? "x";\n'},
    )
    d = _scan(repo)
    assert fi.unregistered_reads(d) == ["ZOE_TS_A", "ZOE_TS_B"]
    assert d["flags"]["prod"]["ZOE_TS_B"]["defaults"] == ["'x'"]


def test_control_a_registry_read_next_to_a_matching_legacy_read_is_not_a_conflict(tmp_path):
    """Migrating one reader to ``typed_env.flag("X")`` (no default argument: the registry supplies it) must not turn a
    flag whose legacy readers agree with the registry into an 'unset vs 5' conflict."""
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_TIMEOUT', 'int', 5),",
        **{
            "services/zoe-data/a.py": 'from typed_env import env_int\nT = env_int("ZOE_TIMEOUT", 5)\n',
            "services/zoe-data/b.py": 'from typed_env import flag\nT = flag("ZOE_TIMEOUT")\n',
        },
    )
    d = _scan(repo)
    assert "ZOE_TIMEOUT" not in d["conflicts"] and "ZOE_TIMEOUT" not in d["divergences"]
    assert d["flags"]["prod"]["ZOE_TIMEOUT"]["defaults"] == ["5"]   # shown as the value it resolves to


def test_control_a_registry_read_next_to_an_unset_legacy_read_is_still_a_conflict(tmp_path):
    """The other half: ``flag("X")`` resolves to the declared 5, so a bare ``os.getenv("X")`` beside it IS a contradiction."""
    repo = _make_repo(
        tmp_path,
        "    _f('ZOE_TIMEOUT', 'int', 5),",
        **{
            "services/zoe-data/a.py": 'import os\nT = os.getenv("ZOE_TIMEOUT")\n',
            "services/zoe-data/b.py": 'from typed_env import flag\nT = flag("ZOE_TIMEOUT")\n',
        },
    )
    c = _scan(repo)["conflicts"]["ZOE_TIMEOUT"]
    assert c["acknowledged"] is False and c["unacknowledged_sites"] == ["services/zoe-data/a.py"]
