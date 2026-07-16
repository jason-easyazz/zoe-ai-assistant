"""Regression tests for live self-evolution proposal writer closure."""

import pytest
import ast
from pathlib import Path

pytestmark = pytest.mark.ci_safe


DATA_ROOT = Path(__file__).resolve().parents[1]


def _live_python_files():
    for path in DATA_ROOT.rglob("*.py"):
        rel = path.relative_to(DATA_ROOT)
        if {"tests", "alembic", "__pycache__"}.intersection(rel.parts):
            continue
        yield path


def test_live_proposal_writers_do_not_use_legacy_contract_dumpers():
    forbidden_calls = (
        "dump_legacy_evolution_proposal_contract(",
        "dump_mcp_evolution_proposal_contract(",
    )
    allowed_files = {DATA_ROOT / "zoe_evolution_proposal_adapter.py"}
    offenders: list[str] = []

    for path in _live_python_files():
        if path in allowed_files:
            continue
        text = path.read_text(errors="replace")
        for call in forbidden_calls:
            if call in text:
                offenders.append(f"{path.relative_to(DATA_ROOT)} uses {call}")

    assert offenders == []


def test_live_evolution_proposal_inserts_use_runtime_intake():
    intake_markers = (
        "build_runtime_evolution_proposal_intake",
        "build_mcp_runtime_evolution_proposal_intake",
    )
    offenders: list[str] = []

    for path in _live_python_files():
        text = path.read_text(errors="replace")
        if "INSERT INTO evolution_proposals" not in text:
            continue
        tree = ast.parse(text)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if "INSERT INTO evolution_proposals" not in node.value:
                continue
            scope = _enclosing_scope(node, parents)
            source = ast.get_source_segment(text, scope) or text
            if not any(marker in source for marker in intake_markers):
                location = f"{path.relative_to(DATA_ROOT)}:{getattr(node, 'lineno', 1)}"
                offenders.append(location)

    assert offenders == []


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return parent
        current = parent
    return current
