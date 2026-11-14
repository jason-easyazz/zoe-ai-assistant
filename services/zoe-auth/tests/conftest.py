"""zoe-auth test lane: the home-write guard (the suite itself needs no other shared fixture).

Nothing here touches ``sys.path``: the lane sets its own ``PYTHONPATH`` (validate.yml, scripts/maintenance/zoe_auth_tests.sh).
"""
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def _load_home_write_guard():
    """tools/ci/home_write_guard.py, loaded by path (one module however many lanes' conftests load it)."""
    import importlib.util
    name = "zoe_home_write_guard"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO_ROOT, "tools", "ci", "home_write_guard.py"))
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


_home_write_guard = _load_home_write_guard()
_home_write_guard.install(checkout=_REPO_ROOT)


@pytest.fixture(autouse=True)
def _no_writes_under_the_real_home_or_the_checkout(request):
    """A test that writes under the real home directory or into the checkout fails, naming the path (tools/ci/home_write_guard.py)."""
    yield from _home_write_guard.fixture_body(enforce=True)
