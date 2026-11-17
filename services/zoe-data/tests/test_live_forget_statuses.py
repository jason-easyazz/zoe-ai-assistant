"""Forget scans share live statuses; recall-only scans keep their own policy."""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
# Other entry points (memory_space, memory_curation, forget_record) resolve through these scanners.
SCANNERS = {
    "turn_forget.py": ("rows_from_turn",),
    "provenance_answers.py": ("_live_forget_rows", "twins_of"),
    "forget_topic.py": ("_owned_rows",),
    "ask_to_remember.py": ("siblings_of",),
    "memory_forget_alias.py": ("collect_texts",),
    "intent_router.py": ("execute_intent",),
}


def _check(source, names):
    tree = ast.parse(source)
    # Expand named module constants, e.g. the alias scanner's all-status tuple.
    assignments = {n.targets[0].id: n.value for n in tree.body
                   if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}

    def shared(node):
        if isinstance(node, ast.Attribute) and node.attr == "LIVE_FORGET_STATUSES":
            return True
        if isinstance(node, ast.Name):
            return node.id == "LIVE_FORGET_STATUSES" or (node.id in assignments and shared(assignments[node.id]))
        return any(shared(c) for c in ast.iter_child_nodes(node))

    for name in names:
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        scans = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "list_by_status"]
        if not scans:
            assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_live_forget_rows"
                       for n in ast.walk(fn)), name
            continue
        for scan in scans:
            # Each status argument must be supplied by a loop over the shared tuple.
            status = next(k.value for k in scan.keywords if k.arg == "status")
            assert isinstance(status, ast.Name), name
            loops = [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.comprehension))
                     and isinstance(n.target, ast.Name) and n.target.id == status.id]
            assert loops and all(shared(n.iter) for n in loops), name


def test_every_forget_status_scan_uses_the_shared_definition():
    for filename, names in SCANNERS.items():
        _check((ROOT / filename).read_text(), names)
    tree = ast.parse((ROOT / "turn_forget.py").read_text())
    statuses = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "LIVE_FORGET_STATUSES" for t in n.targets))
    assert ast.literal_eval(statuses) == ("approved", "pending", "disputed")


@pytest.mark.parametrize("filename", SCANNERS)
def test_status_guard_rejects_a_private_status_list(filename):
    source = (ROOT / filename).read_text()
    # Replace uses, preserving import syntax so the control fails an assertion, not parsing.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "LIVE_FORGET_STATUSES":
            node.id = "PRIVATE_STATUSES"
        elif isinstance(node, ast.Attribute) and node.attr == "LIVE_FORGET_STATUSES":
            node.attr = "PRIVATE_STATUSES"
    with pytest.raises(AssertionError):
        _check(ast.unparse(tree), SCANNERS[filename])
