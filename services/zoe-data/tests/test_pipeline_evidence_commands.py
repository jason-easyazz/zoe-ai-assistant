import json
import sys
import types

from pipeline_evidence_commands import main
from pipeline_store import bootstrap_state, load_latest_state


def test_mark_tested_records_hashed_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:test", start_phase="verify"))
    assert main(["mark-tested", "multica:test", "--passed", "--summary", "pytest passed", "--output", "ok"]) == 0

    out = json.loads(capsys.readouterr().out)
    state = load_latest_state("multica:test")
    assert out["ok"] is True
    assert state is not None
    assert state.evidence[-1].kind == "test"
    assert state.evidence[-1].content_hash


def test_mark_reviewed_requires_zero_critical(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:review", start_phase="review"))
    main(["mark-reviewed", "multica:review", "--critical-count", "2"])

    json.loads(capsys.readouterr().out)
    state = load_latest_state("multica:review")
    assert state is not None
    assert state.evidence[-1].kind == "human"
    assert state.evidence[-1].passed is False


def test_mark_greptile_passes_only_on_five_of_five(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:greptile", start_phase="closeout"))
    main(["mark-greptile", "multica:greptile", "--score", "5/5"])

    json.loads(capsys.readouterr().out)
    state = load_latest_state("multica:greptile")
    assert state is not None
    assert state.evidence[-1].kind == "greptile"
    assert state.evidence[-1].passed is True


def test_split_ticket_does_not_block_parent_when_children_fail(monkeypatch, capsys):
    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            return {}

        async def update_issue(self, *args, **kwargs):
            raise AssertionError("parent must not be mutated when no child was created")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    assert main(["split-ticket", "parent-1", "--packet", '{"child_issue_template":{"title":"child"}}']) == 1

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["reason"] == "no child issues created"
    assert out["child_issue_ids"] == []


def test_split_ticket_does_not_save_pipeline_block_when_children_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:split-fail", start_phase="implement"))

    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            return {}

        async def update_issue(self, *args, **kwargs):
            raise AssertionError("parent must not be mutated when no child was created")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    assert main([
        "split-ticket",
        "parent-1",
        "--task-ref",
        "multica:split-fail",
        "--packet",
        '{"child_issue_template":{"title":"child"}}',
    ]) == 1
    json.loads(capsys.readouterr().out)
    state = load_latest_state("multica:split-fail")
    assert state is not None
    assert state.status == "todo"


def test_split_ticket_requires_packet(monkeypatch):
    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    try:
        main(["split-ticket", "parent-1"])
    except SystemExit as exc:
        assert "requires --packet" in str(exc)
    else:
        raise AssertionError("split-ticket without a packet should fail")
