"""Importing mcp_server must be side-effect-free on the process environment.

mcp_server bootstraps .env secrets for its spawned-stdio-worker case; an
unguarded module-level ``bootstrap_runtime_env()`` also fired on plain import,
injecting the PRODUCTION ``POSTGRES_URL`` (and other secrets) into any process
that imported it — including pytest, where it silently repointed alembic
dialect-render tests at production (bisected 2026-07-06: the victim
``test_memory_consolidation_state_migration`` failed only in full-directory
runs). The bootstrap is now gated on ``__name__ == "__main__"`` so only the
real worker (``python mcp_server.py``) loads secrets.
"""
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep: a subprocess import, no DB/models

_SVC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_importing_mcp_server_does_not_inject_env_secrets():
    """A fresh interpreter importing mcp_server must NOT gain the bootstrap
    keys (POSTGRES_URL etc.) — only the spawned worker may load .env."""
    code = (
        "import os\n"
        "for k in ('POSTGRES_URL', 'ZOE_INTERNAL_TOKEN', 'HERMES_API_KEY'):\n"
        "    os.environ.pop(k, None)\n"
        "import mcp_server\n"
        "leaked = {k: bool(os.environ.get(k))\n"
        "          for k in ('POSTGRES_URL', 'ZOE_INTERNAL_TOKEN', 'HERMES_API_KEY')}\n"
        "assert not any(leaked.values()), f'import leaked env keys: {leaked}'\n"
        "print('CLEAN')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=_SVC, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr[-800:]!r}"
    assert "CLEAN" in proc.stdout
