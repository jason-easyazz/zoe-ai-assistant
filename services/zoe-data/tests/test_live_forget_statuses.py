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


STRICT_READS = {
    "turn_forget.py": ("live_rows",),
    "provenance_answers.py": ("forget_it",),
    "memory_space.py": ("forget_row", "guardian_forget"),
    "memories_voice.py": ("forget_fact",),
    "forget_record.py": ("_reject",),
}


def _check_strict(source, names):
    tree = ast.parse(source)
    for name in names:
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        reads = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and ((isinstance(n.func, ast.Attribute) and n.func.attr in {"get", "list_by_status", "gather"}
                       and ast.unparse(n.func.value) in {"svc", "pa"})
                      or isinstance(n.func, ast.Name) and n.func.id == "getter")]
        assert reads, name
        assert all(any(k.arg == "strict" and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in n.keywords) for n in reads), name


@pytest.mark.parametrize("filename,names", STRICT_READS.items())
def test_forget_reads_are_strict(filename, names):
    source = (ROOT / filename).read_text()
    _check_strict(source, names)
    with pytest.raises(AssertionError):
        _check_strict(source.replace("strict=True", "strict=False"), names)


def test_memory_service_read_failure_is_not_absence(monkeypatch):
    import asyncio
    from memory_service import MemoryService

    svc = MemoryService(data_dir="/nonexistent/zoe-test-zindle")
    failure = RuntimeError("invented zindle failure")

    class Collection:
        def get(self, **kwargs):
            raise failure

    monkeypatch.setattr(svc, "_collection", lambda: Collection())
    for read, empty in ((lambda **kw: svc.list_by_status(user_id="demo_zindle", **kw), []),
                        (lambda **kw: svc.get("zindle-row", **kw), None)):
        assert asyncio.run(read()) == empty
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(read(strict=True))
        assert caught.value is failure

    class EmptyCollection:
        def get(self, **kwargs):
            return {"ids": [], "documents": [], "metadatas": []}

    monkeypatch.setattr(svc, "_collection", lambda: EmptyCollection())
    assert asyncio.run(svc.get("zindle-missing", strict=True)) is None


def test_mcp_forget_read_is_strict():
    tree = ast.parse((ROOT / "mcp_server.py").read_text())
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "name == 'memory_forget'")
    source = "async def forget():\n" + "\n".join("    " + line for node in branch.body for line in ast.unparse(node).splitlines())
    _check_strict(source, ("forget",))
    with pytest.raises(AssertionError):
        _check_strict(source.replace("strict=True", "strict=False"), ("forget",))


FORGET_MODULES = (
    "turn_forget.py", "provenance_answers.py", "forget_topic.py", "ask_to_remember.py",
    "memory_space.py", "memory_curation.py", "memory_forget_alias.py", "forget_record.py",
    "intent_router.py", "mcp_server.py", "memories_voice.py", "routers/memories.py",
)
# Function name -> reason: these reads supply replies / displays, never erase targets or completion decisions.
DISPLAY_READS = {
    "_live_equivalent": "Only chooses the already-remembered reply after an explicit teach; never retracts an ask.",
    "asked_rows": "Only called by recall_reply to display approved asks.",
    "memory_list": "MCP listing only; the review and forget branches read their targets again.",
    "list_memories": "REST listing only; mutations read their targets again.",
    "list_review_queue": "Displays proposals and contradiction text; review reads its target again.",
}


def _check_module_reads(source):
    class Reads(ast.NodeVisitor):
        function = ""

        def visit_AsyncFunctionDef(self, node):
            previous, self.function = self.function, node.name
            self.generic_visit(node)
            self.function = previous

        visit_FunctionDef = visit_AsyncFunctionDef

        def visit_If(self, node):
            previous = self.function
            if (isinstance(node.test, ast.Compare) and ast.unparse(node.test.left) == "name"
                    and len(node.test.comparators) == 1
                    and isinstance(node.test.comparators[0], ast.Constant)
                    and node.test.comparators[0].value == "memory_list"):
                self.function = "memory_list"
                for child in node.body:
                    self.visit(child)
                self.function = previous
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

        def visit_Call(self, node):
            f = node.func
            read = (isinstance(f, ast.Attribute)
                    and (f.attr == "list_by_status" or (f.attr == "get" and ast.unparse(f.value) == "svc")))
            read = read or (isinstance(f, ast.Name) and f.id == "getter")
            if read and self.function not in DISPLAY_READS:
                assert any(k.arg == "strict" and isinstance(k.value, ast.Constant) and k.value.value is True
                           for k in node.keywords), (self.function, node.lineno, ast.unparse(node))
            self.generic_visit(node)

    Reads().visit(ast.parse(source))


@pytest.mark.parametrize("filename", FORGET_MODULES)
def test_all_forget_module_point_reads_and_scans_are_strict(filename):
    _check_module_reads((ROOT / filename).read_text())


@pytest.mark.parametrize("filename", FORGET_MODULES)
@pytest.mark.parametrize("read", ['svc.get("zindle-row")', 'svc.list_by_status(status="approved")'])
def test_module_read_guard_rejects_a_lenient_forget_read(filename, read):
    source = (ROOT / filename).read_text() + f"\nasync def forget_zindle():\n    await {read}\n"
    with pytest.raises(AssertionError, match="forget_zindle"):
        _check_module_reads(source)
