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
    """Since the single queue (migration 0062) the ONLY insert is ``evolution_queue.propose`` (the one-writer rule is pinned in
    ``test_evolution_queue.py``). The intake rule moved with it: every legacy writer - a ``propose`` call that carries the legacy
    signal record (``legacy_evidence=``) - builds it through the runtime intake in the same scope.
    Negative control: drop the intake builder from ``record_user_issue`` -> red."""
    intake_markers = (
        "build_runtime_evolution_proposal_intake",
        "build_mcp_runtime_evolution_proposal_intake",
    )
    offenders: list[str] = []
    legacy_calls = 0

    for path in _live_python_files():
        text = path.read_text(errors="replace")
        if ".propose(" not in text:
            continue
        tree = ast.parse(text)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "propose"):
                continue
            if not any(kw.arg == "legacy_evidence" for kw in node.keywords):
                continue
            legacy_calls += 1
            scope = _enclosing_scope(node, parents)
            source = ast.get_source_segment(text, scope) or text
            if not any(marker in source for marker in intake_markers):
                offenders.append(f"{path.relative_to(DATA_ROOT)}:{node.lineno}")

    assert legacy_calls >= 5, "the four evolution_notice writers and the MCP tool all write through evolution_queue.propose"
    assert offenders == []


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    current = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return parent
        current = parent
    return current
