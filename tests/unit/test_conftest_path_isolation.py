"""tests/conftest.py must not put services/zoe-auth on sys.path.

zoe-auth's `models/` package and `main.py` collide with zoe-data's `models.py` and
`main.py`; with zoe-auth first, any pytest run that collects tests/unit together with
services/zoe-data/tests fails zoe-data's route tests on
`ImportError: cannot import name 'PersonCreate' from 'models'` (2026-10-04).
"""
import os
import sys

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_zoe_auth_is_not_on_sys_path():
    auth = os.path.join(ROOT, "services", "zoe-auth")
    assert auth not in sys.path, "tests/conftest.py must not insert services/zoe-auth (shadows zoe-data's models/main)"


def test_models_resolves_to_zoe_data():
    import models  # noqa: F401  (zoe-data's models.py, the one `from models import PersonCreate` expects)

    assert models.__file__.endswith(os.path.join("services", "zoe-data", "models.py")), models.__file__
    assert hasattr(models, "PersonCreate")


def test_conftest_source_has_no_zoe_auth_insert():
    src = open(os.path.join(ROOT, "tests", "conftest.py"), encoding="utf-8").read()
    assert "sys.path.insert(0, os.path.join(PROJECT_ROOT, 'services/zoe-auth'))" not in src
