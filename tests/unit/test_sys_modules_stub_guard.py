"""The conftest sys.modules stub guard classifies stubs correctly, and test_intent_router leaves nothing behind."""
from __future__ import annotations

import subprocess
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

from tests.unit.conftest import stubbed_repo_modules

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def test_a_magicmock_namespace_or_fileless_module_under_a_repo_name_is_a_stub():
    fake = {
        "memory_digest": types.ModuleType("memory_digest"),  # no __file__
        "database": mock.MagicMock(),
        "db_pool": types.SimpleNamespace(),
    }
    assert set(stubbed_repo_modules(fake)) == {"memory_digest", "database", "db_pool"}


def test_a_real_module_and_a_non_repo_name_are_not_stubs():
    real = types.ModuleType("memory_digest")
    real.__file__ = "/x/memory_digest.py"
    assert stubbed_repo_modules({"memory_digest": real, "psycopg2": mock.MagicMock()}) == {}


def test_deselecting_every_intent_router_test_leaves_the_real_memory_digest_importable():
    """The 22-failure class: stubs installed at import survived when the module's tests were deselected."""
    code = (
        "import sys, pytest\n"
        "rc = pytest.main(['tests/unit/test_intent_router.py', '-k', 'no_such_test_name', '-q', '-p', 'no:cacheprovider'])\n"
        "import memory_digest\n"
        "assert hasattr(memory_digest, '_load_todays_messages'), memory_digest\n"
        "print('REAL')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=300)
    assert "REAL" in r.stdout, (r.stdout[-800:], r.stderr[-800:])


def _run_pytest_on(files: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a throwaway two-module suite UNDER tests/unit (so the conftest guard applies) and clean it up."""
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="stubguard_", dir=REPO / "tests" / "unit"))
    try:
        for name, body in files.items():
            (tmp / name).write_text(body)
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(tmp), "-q", "-p", "no:cacheprovider", "-o", "addopts="],
            cwd=REPO, capture_output=True, text=True, timeout=300,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_LEAKER = (
    "import sys, types\n"
    "sys.modules['memory_digest'] = types.ModuleType('memory_digest')\n"  # import-time stub, never restored
    "def test_leaker():\n    assert True\n"
)
_CONSUMER = (
    "from memory_digest import _load_todays_messages\n"  # module-scope symbol import, at collection time
    "def test_consumer():\n    assert callable(_load_todays_messages)\n"
)


def test_a_leaked_stub_is_evicted_at_collection_so_a_later_module_scope_import_gets_the_real_module():
    """Thread: the fixture alone runs after collection, so a later `from memory_digest import x` died on the stub."""
    r = _run_pytest_on({"test_a_leaker.py": _LEAKER, "test_b_consumer.py": _CONSUMER})
    out = r.stdout + r.stderr
    assert "ImportError" not in out and "cannot import name" not in out, out[-1500:]
    assert "ERROR collecting" not in out, out[-1500:]
    assert "1 passed" in out, out[-1500:]  # the consumer really ran, on the real module
    assert "sys.modules stub leak" in out, out[-1500:]  # and the leak was still reported, not swallowed


def test_one_leak_is_evicted_not_just_reported_so_it_does_not_cascade_into_every_later_module():
    """Thread: without -x the stub stayed in sys.modules after the report, so later modules still hit it."""
    later = (
        "import memory_digest\n"
        "def test_later():\n    assert hasattr(memory_digest, '_load_todays_messages')\n"
    )
    r = _run_pytest_on({"test_a_leaker.py": _LEAKER, "test_b_later.py": later, "test_c_later.py": later})
    out = r.stdout + r.stderr
    assert "2 passed" in out, out[-1500:]  # both later modules saw the real module
    assert out.count("sys.modules stub leak") == 1, out[-1500:]  # one leak, one failure
