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
