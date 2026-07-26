import pytest
import json
import sys
import types

import pipeline_evidence_commands as commands
from pipeline_evidence_commands import main
from pipeline_store import PipelineStateConflict, bootstrap_state, load_latest_state

pytestmark = pytest.mark.ci_safe


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


def test_record_evidence_retries_once_after_journal_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:evidence-race", start_phase="review"))
    original_save = commands.save_state
    calls = 0

    def conflict_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PipelineStateConflict("simulated race")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(commands, "save_state", conflict_once)
    result = commands.record_evidence(
        "multica:evidence-race",
        kind="human",
        summary="review passed",
        passed=True,
    )

    state = load_latest_state("multica:evidence-race")
    assert calls == 2
    assert result["ok"] is True
    assert state is not None
    assert state.evidence[-1].summary == "review passed"


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


def test_split_ticket_does_not_save_pipeline_block_when_parent_update_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:parent-update-fail", start_phase="implement"))

    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            return {"id": "child-1", "description": "child"}

        async def update_issue(self, *args, **kwargs):
            return {}

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    assert main([
        "split-ticket",
        "parent-1",
        "--task-ref",
        "multica:parent-update-fail",
        "--packet",
        '{"child_issue_template":{"title":"child"}}',
    ]) == 1

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["reason"] == "parent update failed; children created but parent not linked"
    assert out["child_issue_ids"] == ["child-1"]

    state = load_latest_state("multica:parent-update-fail")
    assert state is not None
    assert state.status == "todo"


def test_split_ticket_retries_pipeline_conflict_without_duplicate_children(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))
    import asyncio

    asyncio.run(bootstrap_state("multica:split-race", start_phase="implement"))

    class FakeClient:
        child_calls = 0

        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            self.child_calls += 1
            return {"id": "child-1", "description": "child"}

        async def update_issue(self, issue_id, **kwargs):
            return {"id": issue_id, **kwargs}

    client = FakeClient()
    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: client),
    )
    original_save = commands.save_state
    save_calls = 0

    def conflict_once(*args, **kwargs):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise PipelineStateConflict("simulated split race")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(commands, "save_state", conflict_once)

    assert main([
        "split-ticket",
        "parent-1",
        "--task-ref",
        "multica:split-race",
        "--packet",
        '{"child_issue_template":{"title":"child"}}',
    ]) == 0
    json.loads(capsys.readouterr().out)

    assert client.child_calls == 1
    assert save_calls == 2


def test_split_ticket_rejects_missing_task_ref_before_multica_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "runs.jsonl"))

    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            raise AssertionError("missing task-ref must stop before child creation")

        async def update_issue(self, *args, **kwargs):
            raise AssertionError("missing task-ref must stop before parent update")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    assert main([
        "split-ticket",
        "parent-1",
        "--task-ref",
        "multica:missing",
        "--packet",
        '{"child_issue_template":{"title":"child"}}',
    ]) == 1

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["reason"] == "pipeline state not found for task ref: multica:missing"


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


def test_split_ticket_requires_parent_id(monkeypatch):
    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"error": "not_found"}

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    try:
        main(["split-ticket", "parent-1", "--packet", '{"child_issue_template":{"title":"child"}}'])
    except SystemExit as exc:
        assert "Parent issue not found" in str(exc)
    else:
        raise AssertionError("split-ticket should reject parent responses without an id")


def test_split_ticket_requires_object_packet(monkeypatch):
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
        main(["split-ticket", "parent-1", "--packet", '[{"title":"child"}]'])
    except SystemExit as exc:
        assert "must be a JSON object" in str(exc)
    else:
        raise AssertionError("split-ticket with a non-object packet should fail")


def test_split_ticket_requires_at_least_one_child_template(monkeypatch):
    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            raise AssertionError("empty children list must not create a fallback child")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    try:
        main(["split-ticket", "parent-1", "--packet", '{"children":[]}'])
    except SystemExit as exc:
        assert "at least one child template" in str(exc)
    else:
        raise AssertionError("split-ticket should reject empty children lists")


def test_split_ticket_rejects_empty_child_template(monkeypatch):
    class FakeClient:
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "description": "parent"}

        async def create_child_issue(self, parent, template):
            raise AssertionError("empty child template must not create a fallback child")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: FakeClient()),
    )

    try:
        main(["split-ticket", "parent-1", "--packet", '{"child_issue_template":{}}'])
    except SystemExit as exc:
        assert "at least one child template" in str(exc)
    else:
        raise AssertionError("split-ticket should reject empty child templates")


def test_split_ticket_reports_unreadable_packet_file(monkeypatch):
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
        main(["split-ticket", "parent-1", "--packet-file", "/tmp/zoe-missing-split-packet.json"])
    except SystemExit as exc:
        assert "Cannot read file" in str(exc)
    else:
        raise AssertionError("split-ticket should report unreadable packet files cleanly")
