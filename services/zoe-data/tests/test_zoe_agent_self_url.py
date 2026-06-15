"""Regression guard for Zoe's internal HTTP base URL."""

import ast
from pathlib import Path


LOCAL_URL = "http://localhost:8000"


def test_localhost_default_only_exists_in_zoe_base_url() -> None:
    agent_path = Path(__file__).resolve().parents[1] / "zoe_agent.py"
    assert agent_path.is_file(), f"Zoe agent source not found: {agent_path}"
    tree = ast.parse(agent_path.read_text(encoding="utf-8"))
    helper = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_zoe_base_url"
        ),
        None,
    )

    assert helper is not None, "_zoe_base_url() must own Zoe's internal base URL"
    occurrences = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == LOCAL_URL
    ]

    assert len(occurrences) == 1, (
        f"{LOCAL_URL} must appear exactly once in zoe_agent.py; "
        "new self-call sites must use _zoe_base_url()"
    )
    assert helper.lineno <= occurrences[0].lineno <= helper.end_lineno
