"""A zoe-data restart must never dirty the checkout via CAPABILITIES.md.

agent_sync used to rewrite <checkout>/CAPABILITIES.md on every start, so the tracked
file drifted and blocked fast-forward syncs / the weekly run. The runtime snapshot now
lives under ~/.zoe; the tracked root file is static.
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "services" / "zoe-data"


def _assign_value(name: str) -> str:
    tree = ast.parse((SRC / "agent_sync.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.unparse(node.value)
    raise AssertionError(name)


def test_snapshot_target_is_outside_the_checkout():
    expr = _assign_value("_CAPABILITIES_MD")
    assert "Path.home()" in expr and "'.zoe'" in expr
    assert "/home/zoe/assistant" not in expr


def test_no_runtime_code_uses_the_checkout_copy_by_literal_path():
    for rel in ("agent_sync.py", "routers/chat.py", "routers/system.py"):
        assert "/home/zoe/assistant/CAPABILITIES.md" not in (SRC / rel).read_text(), rel


def test_generator_writes_snapshot_path_never_the_tracked_file():
    text = (SRC / "agent_sync.py").read_text()
    assert "_CAPABILITIES_MD.write_text(" in text
    assert "_CAPABILITIES_MD_TRACKED.write_text" not in text
