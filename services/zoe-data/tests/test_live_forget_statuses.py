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
    for name in names:
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        calls = [n.func for n in ast.walk(fn) if isinstance(n, ast.Call)]
        assert not any(isinstance(f, ast.Attribute) and f.attr == "list_by_status" for f in calls), name
        assert any((isinstance(f, ast.Name) and f.id in {"live_rows", "_live_forget_rows"})
                   or (isinstance(f, ast.Attribute) and f.attr == "live_rows") for f in calls), name


def test_every_forget_status_scan_uses_the_shared_definition():
    for filename, names in SCANNERS.items():
        _check((ROOT / filename).read_text(), names)
    tree = ast.parse((ROOT / "turn_forget.py").read_text())
    statuses = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "LIVE_FORGET_STATUSES" for t in n.targets))
    assert ast.literal_eval(statuses) == ("approved", "pending", "disputed")


@pytest.mark.parametrize("filename", SCANNERS)
def test_status_guard_rejects_a_direct_status_scan(filename):
    tree = ast.parse((ROOT / filename).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in SCANNERS[filename]:
            node.body.append(ast.parse('await svc.list_by_status(user_id=user_id, status="approved", limit=1000)').body[0])
    with pytest.raises(AssertionError):
        _check(ast.unparse(tree), SCANNERS[filename])


def _check_helper(source):
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "live_rows")
    assert any(isinstance(n, ast.Name) and n.id == "LIVE_FORGET_STATUSES" for n in ast.walk(fn))
    assert any(isinstance(n, ast.While) for n in ast.walk(fn))
    scans = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "list_by_status"]
    assert scans and all(any(k.arg == "offset" for k in n.keywords) for n in scans)


def test_shared_helper_pages_the_shared_statuses():
    _check_helper((ROOT / "turn_forget.py").read_text())


def test_helper_guard_rejects_private_statuses():
    source = (ROOT / "turn_forget.py").read_text().replace("*LIVE_FORGET_STATUSES", '* ("approved", "pending")')
    with pytest.raises(AssertionError):
        _check_helper(source)


@pytest.mark.parametrize("failure", ["store", "budget"])
def test_shared_helper_never_returns_a_partial_read(failure):
    import asyncio
    from types import SimpleNamespace
    import turn_forget

    calls = []

    class Store:
        async def list_by_status(self, *, status, offset, **kwargs):
            calls.append((status, offset))
            if offset == 0:
                return [SimpleNamespace(id="f-zindle")] * 1000
            if failure == "store":
                raise RuntimeError("invented store failure")
            await asyncio.sleep(1)

    with pytest.raises(RuntimeError if failure == "store" else asyncio.TimeoutError):
        asyncio.run(turn_forget.live_rows(Store(), "demo_zindle", read_budget_s=0.01))
    assert calls == [("approved", 0), ("approved", 1000)]
