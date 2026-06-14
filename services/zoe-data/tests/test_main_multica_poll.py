"""Tests for main.py Multica poll-loop helpers."""

from pathlib import Path

import pytest


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.issues = {}
        self.created = []
        self.labels = []
        self.notes = []
        self.default_list_statuses: set[str] | None = None
        self.list_calls = []

    async def record_progress(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"id": args[0], **kwargs}

    async def get_issue(self, issue_id):
        return self.issues.get(issue_id, {})

    async def list_issues(self, status=None, *, limit=None):
        self.list_calls.append({"status": status, "limit": limit})
        issues = list(self.issues.values()) + list(self.created)
        if status is not None:
            issues = [issue for issue in issues if issue.get("status") == status]
        elif self.default_list_statuses is not None:
            issues = [issue for issue in issues if issue.get("status") in self.default_list_statuses]
        return issues[:limit] if limit is not None else issues

    async def create_issue(self, **kwargs):
        issue = {"id": f"child-{len(self.created) + 1}", "identifier": f"ZOE-C{len(self.created) + 1}", **kwargs}
        self.created.append(issue)
        return issue

    async def attach_label(self, issue_id, label):
        self.labels.append((issue_id, label))
        return {"ok": True}

    async def update_issue(self, issue_id, **kwargs):
        issue = self.issues.setdefault(issue_id, {"id": issue_id})
        issue.update(kwargs)
        return issue

    async def append_issue_note(self, issue_id, note):
        self.notes.append((issue_id, note))
        return {"ok": True}


def test_poll_dispatches_ready_work_only_when_runtime_pause_is_inactive():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    active_branch = source.index("if _wh_ok() and not _dispatch_paused:")
    backfill = source.index("# Backfill existing in-progress runs", active_branch)
    paused_branch = source.index("elif _dispatch_paused:", active_branch)

    assert active_branch < backfill < paused_branch


def test_poll_dispatch_backfills_ready_blocked_pipeline_before_todo():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    blocked_fetch = source.index('blocked_issues = await client.list_issues(status="blocked")')
    blocked_loop = source.index('for _blocked in _blk_window:', blocked_fetch)
    todo_loop = source.index('for _todo in stale_todos or []:', blocked_loop)
    clear_blocker = source.index(
        'clear_blocker=True',
        source.index('elif (_candidate.get("status") or "") == "blocked":'),
    )

    assert blocked_fetch < blocked_loop < todo_loop
    assert clear_blocker < blocked_loop


def test_tracked_multica_engineering_issues_includes_review_and_deduplicates():
    from main import _tracked_multica_engineering_issues

    in_progress = [{"id": "one", "status": "in_progress"}]
    in_review = [
        {"id": "one", "status": "in_review"},
        {"id": "two", "status": "in_review"},
        {"title": "missing id"},
    ]

    assert _tracked_multica_engineering_issues(in_progress, in_review) == [
        in_progress[0],
        in_review[1],
    ]


def test_poll_loop_keeps_blocked_broadcast_out_of_running_branch():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    blocked_branch = source.index('elif chain.get("found") and chain.get("status") == "blocked"')
    running_branch = source.index('elif chain.get("found") and chain.get("status") == "running"')
    inner_except = source.index("except Exception as _inner_exc", running_branch)

    blocked_segment = source[blocked_branch:running_branch]
    running_segment = source[running_branch:inner_except]

    assert "blocker = await _record_blocked_multica_chain" in blocked_segment
    assert '"multica_task_blocked"' in blocked_segment
    assert "_record_running_multica_chain_progress" in running_segment
    assert '"multica_task_progress"' in running_segment
    assert '**({"status": "in_review"} if chain.get("pr_url") else {})' in running_segment
    assert '"status": "in_review" if chain.get("pr_url") else None' not in running_segment
    assert '"multica_task_blocked"' not in running_segment
    assert "blocker" not in running_segment


@pytest.mark.asyncio
async def test_record_running_multica_chain_progress_records_phase_without_status_none():
    from main import _record_running_multica_chain_progress

    client = RecordingClient()

    changed = await _record_running_multica_chain_progress(
        client,
        "issue-running",
        {
            "status": "running",
            "pipeline": {"phase": "implement"},
        },
        issue={"id": "issue-running", "status": "in_progress", "description": ""},
    )

    assert changed is True
    assert client.calls[0][1]["phase"] == "implement"
    assert "pr_url" not in client.calls[0][1]
    assert "status" not in client.calls[0][1]


@pytest.mark.asyncio
async def test_record_running_multica_chain_progress_records_pr_url_and_review_status():
    from main import _record_running_multica_chain_progress

    client = RecordingClient()
    client.issues["issue-running"] = {"id": "issue-running", "status": "in_progress", "description": ""}

    changed = await _record_running_multica_chain_progress(
        client,
        "issue-running",
        {
            "status": "running",
            "pr_url": "https://github.com/jason-easyazz/zoe-ai-assistant/pull/999",
            "pipeline": {"phase": "verify"},
        },
        issue=client.issues["issue-running"],
    )

    assert changed is True
    assert client.calls[0][0] == ("issue-running",)
    assert client.calls[0][1]["phase"] == "verify"
    assert client.calls[0][1]["pr_url"] == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/999"
    assert client.calls[0][1]["status"] == "in_review"
    assert client.calls[0][1]["clear_blocker"] is True


@pytest.mark.asyncio
async def test_record_running_multica_chain_progress_skips_unchanged_metadata():
    from main import _record_running_multica_chain_progress
    from multica_ticket_contract import describe_ticket

    description = describe_ticket(
        "Already synced",
        metadata={"phase": "verify", "pr_url": "https://github.com/jason-easyazz/zoe-ai-assistant/pull/999"},
    )
    client = RecordingClient()
    issue = {"id": "issue-running", "status": "in_review", "description": description}

    changed = await _record_running_multica_chain_progress(
        client,
        "issue-running",
        {
            "status": "running",
            "pr_url": "https://github.com/jason-easyazz/zoe-ai-assistant/pull/999",
            "pipeline": {"phase": "verify"},
        },
        issue=issue,
    )

    assert changed is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_record_completed_multica_chain_records_explicit_retro_completion_metadata():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(
        client,
        "issue-1",
        {
            "status": "done",
            "pipeline": {"phase": "retro"},
            "pr_url": "https://github.com/o/r/pull/1",
        },
    )

    assert client.calls == [
        (
            ("issue-1",),
            {
                "phase": "retro",
                "evidence": "Engineering run done after retro",
                "pr_url": "https://github.com/o/r/pull/1",
                "clear_blocker": True,
                "status": "done",
            },
        )
    ]


@pytest.mark.asyncio
async def test_record_completed_multica_chain_preserves_legacy_closeout_completion():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(client, "issue-2", {"status": "done"})

    assert "pr_url" not in client.calls[0][1]
    assert client.calls[0][1]["phase"] == "closeout"
    assert client.calls[0][1]["evidence"] == "Engineering run done"
    assert client.calls[0][1]["status"] == "done"


@pytest.mark.asyncio
async def test_record_completed_multica_chain_records_non_retro_pipeline_phase():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(
        client,
        "issue-3",
        {"status": "done", "pipeline": {"phase": "closeout"}},
    )

    assert client.calls[0][1]["phase"] == "closeout"
    assert client.calls[0][1]["evidence"] == "Engineering run done"
    assert client.calls[0][1]["status"] == "done"


@pytest.mark.asyncio
async def test_record_completed_multica_chain_omits_absent_pr_url():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(
        client,
        "issue-no-pr",
        {"status": "done", "pipeline": {"phase": "closeout"}},
    )

    assert "pr_url" not in client.calls[0][1]
    assert client.calls[0][1]["status"] == "done"


@pytest.mark.asyncio
async def test_record_completed_multica_chain_creates_retro_followup_ticket():
    from main import _record_completed_multica_chain
    from multica_ticket_contract import parse_ticket_block

    class Client(RecordingClient):
        async def get_issue(self, issue_id):
            return {
                "id": issue_id,
                "identifier": "ZOE-1",
                "assignee_id": "agent-1",
                "assignee_type": "agent",
                "project_id": "project-1",
            }

        async def create_issue(self, **kwargs):
            self.calls.append(("create_issue", kwargs))
            return {"id": "child-1", "identifier": "ZOE-2", **kwargs}

        async def attach_label(self, issue_id, label_name):
            self.calls.append(("attach_label", issue_id, label_name))
            return {"id": label_name}

        async def append_issue_note(self, issue_id, note):
            self.calls.append(("append_issue_note", issue_id, note))
            return {"id": issue_id}

    client = Client()

    await _record_completed_multica_chain(
        client,
        "parent-1",
        {
            "status": "done",
            "pipeline": {
                "phase": "retro",
                "retro_followup": {
                    "title": "Add retry guard regression",
                    "description": "Retro found a missing retry guard test.",
                },
            },
        },
    )

    create_call = next(call for call in client.calls if call[0] == "create_issue")
    payload = create_call[1]
    metadata = parse_ticket_block(payload["description"])
    assert payload["status"] == "backlog"
    assert payload["assignee_id"] == "agent-1"
    assert metadata["zoe_kind"] == "harness_fix"
    assert metadata["parent_issue_id"] == "parent-1"
    assert ("attach_label", "child-1", "harness-fix") in client.calls
    assert any(call[0] == "append_issue_note" for call in client.calls)


@pytest.mark.asyncio
async def test_record_completed_multica_chain_does_not_create_followup_without_retro():
    from main import _record_completed_multica_chain

    class Client(RecordingClient):
        async def get_issue(self, issue_id):
            raise AssertionError("no parent lookup without retro follow-up")

        async def create_issue(self, **kwargs):
            raise AssertionError("no follow-up should be created")

    client = Client()

    await _record_completed_multica_chain(
        client,
        "issue-1",
        {"status": "done", "pipeline": {"phase": "closeout", "retro_followup": {"title": "ignored"}}},
    )

    assert client.calls[0][1]["phase"] == "closeout"


@pytest.mark.asyncio
async def test_record_completed_multica_chain_swallow_followup_creation_failure():
    from main import _record_completed_multica_chain

    class Client(RecordingClient):
        async def get_issue(self, issue_id):
            return {"id": issue_id, "identifier": "ZOE-1"}

        async def create_issue(self, **kwargs):
            raise RuntimeError("multica create failed")

    client = Client()

    await _record_completed_multica_chain(
        client,
        "parent-1",
        {
            "status": "done",
            "pipeline": {
                "phase": "retro",
                "retro_followup": {"title": "Retry guard", "description": "Add coverage"},
            },
        },
    )

    assert client.calls[0][1]["phase"] == "retro"


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_records_terminal_block_metadata():
    from main import _record_blocked_multica_chain

    client = RecordingClient()

    await _record_blocked_multica_chain(
        client,
        "issue-blocked",
        {
            "status": "blocked",
            "blocker": "implement blocked",
            "pr_url": "https://github.com/o/r/pull/7",
            "pipeline": {"phase": "implement", "terminal_block": True},
        },
    )

    assert client.calls == [
        (
            ("issue-blocked",),
            {
                "phase": "implement",
                "evidence": "Engineering run blocked",
                "pr_url": "https://github.com/o/r/pull/7",
                "blocker": "terminal block: implement blocked",
                "status": "blocked",
                "dispatch_approved": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_creates_iteration_budget_followup():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import parse_ticket_block

    client = RecordingClient()
    client.issues["parent-iteration"] = {
        "id": "parent-iteration",
        "identifier": "ZOE-ITER",
        "status": "blocked",
        "description": "Parent description",
        "assignee_id": "agent-1",
        "assignee_type": "agent",
        "project_id": "project-1",
    }

    blocker = await _record_blocked_multica_chain(
        client,
        "parent-iteration",
        {
            "pipeline": {"phase": "implement", "block_reason": "ITERATION_BUDGET"},
        },
    )

    assert blocker == "ITERATION_BUDGET"
    assert len(client.created) == 1
    child = client.created[0]
    metadata = parse_ticket_block(child["description"])
    assert child["title"] == "Harness: follow up ITERATION_BUDGET for ZOE-ITER"
    assert metadata["source"] == "engineering_blocker_followup"
    assert metadata["source_blocker"] == "ITERATION_BUDGET"
    assert metadata["parent_issue_id"] == "parent-iteration"
    assert ("child-1", "harness-fix") in client.labels
    assert ("child-1", "iteration-budget") in client.labels
    assert client.notes == [("parent-iteration", "Harness follow-up created for ITERATION_BUDGET: ZOE-C1")]


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_uses_non_terminal_block_reason_without_prefix():
    from main import _record_blocked_multica_chain

    client = RecordingClient()

    blocker = await _record_blocked_multica_chain(
        client,
        "issue-non-terminal",
        {"pipeline": {"phase": "verify", "block_reason": "tests failed"}},
    )

    assert blocker == "tests failed"
    assert client.calls[0][1]["phase"] == "verify"
    assert client.calls[0][1]["blocker"] == "tests failed"
    assert client.calls[0][1]["dispatch_approved"] is False
    assert not client.calls[0][1]["blocker"].startswith("terminal block:")


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_falls_back_to_classification_and_default():
    from main import _record_blocked_multica_chain

    client = RecordingClient()

    classified = await _record_blocked_multica_chain(
        client,
        "issue-classified",
        {"pipeline": {"phase": "review", "block_classification": "blocked_external"}},
    )
    defaulted = await _record_blocked_multica_chain(
        client,
        "issue-defaulted",
        {"pipeline": {"phase": "retro"}},
    )

    assert classified == "blocked_external"
    assert defaulted == "pipeline blocked at retro"
    assert client.calls[0][1]["blocker"] == "blocked_external"
    assert client.calls[0][1]["dispatch_approved"] is False
    assert client.calls[1][1]["blocker"] == "pipeline blocked at retro"
    assert client.calls[1][1]["dispatch_approved"] is False


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_creates_budget_followup_once():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import parse_ticket_block

    client = RecordingClient()
    client.default_list_statuses = {"backlog", "todo", "in_progress", "blocked", "in_review"}
    client.issues["issue-budget"] = {
        "id": "issue-budget",
        "identifier": "ZOE-1",
        "description": "Parent prose",
        "assignee_id": "hermes",
        "assignee_type": "agent",
        "project_id": "project-1",
    }
    chain = {
        "pipeline": {
            "phase": "implement",
            "block_reason": "IMPLEMENT_BUDGET: code-enforced tool budget exceeded",
        }
    }

    blocker = await _record_blocked_multica_chain(client, "issue-budget", chain)

    assert blocker == "IMPLEMENT_BUDGET: code-enforced tool budget exceeded"
    assert client.calls[0][1]["dispatch_approved"] is False
    assert len(client.created) == 1
    created = client.created[0]
    metadata = parse_ticket_block(created["description"])
    assert metadata["source"] == "engineering_blocker_followup"
    assert metadata["source_blocker"] == "IMPLEMENT_BUDGET"
    assert metadata["parent_issue_id"] == "issue-budget"
    assert ("child-1", "harness-fix") in client.labels
    assert ("child-1", "implement-budget") in client.labels
    parent_metadata = parse_ticket_block(client.issues["issue-budget"]["description"])
    assert parent_metadata["child_issue_ids"] == ["child-1"]
    assert client.notes


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_does_not_create_recursive_harness_followup():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import describe_ticket

    client = RecordingClient()
    client.issues["issue-budget"] = {
        "id": "issue-budget",
        "identifier": "ZOE-5448",
        "description": describe_ticket(
            "Existing harness follow-up",
            zoe_kind="harness_fix",
            source="engineering_blocker_followup",
            parent_issue_id="parent-root",
            acceptance_criteria=["Focused tests cover blocker path"],
            evidence_expectations=["Focused tests", "PR URL"],
        ),
        "assignee_id": "hermes",
        "assignee_type": "agent",
        "project_id": "project-1",
    }

    blocker = await _record_blocked_multica_chain(
        client,
        "issue-budget",
        {
            "pipeline": {
                "phase": "implement",
                "block_reason": "ITERATION_BUDGET: Hermes iteration budget reached",
            }
        },
    )

    assert blocker == "ITERATION_BUDGET: Hermes iteration budget reached"
    assert client.calls[0][1]["dispatch_approved"] is False
    assert client.created == []
    assert client.labels == []
    assert client.notes == []


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_reuses_existing_in_progress_budget_followup():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import describe_ticket, parse_ticket_block, write_ticket_block

    client = RecordingClient()
    client.issues["issue-budget"] = {
        "id": "issue-budget",
        "identifier": "ZOE-1",
        "description": "Parent prose",
        "assignee_id": "hermes",
    }
    existing_description = describe_ticket(
        "Existing follow-up",
        zoe_kind="harness_fix",
        source="engineering_blocker_followup",
        parent_issue_id="issue-budget",
    )
    existing_metadata = parse_ticket_block(existing_description)
    existing_metadata["source_blocker"] = "IMPLEMENT_BUDGET"
    client.issues["existing-child"] = {
        "id": "existing-child",
        "identifier": "ZOE-C1",
        "status": "in_progress",
        "description": write_ticket_block(existing_description, existing_metadata),
    }

    await _record_blocked_multica_chain(
        client,
        "issue-budget",
        {
            "pipeline": {
                "phase": "implement",
                "block_reason": "IMPLEMENT_BUDGET: code-enforced tool budget exceeded",
            }
        },
    )

    assert client.created == []
    assert client.labels == []
    assert client.notes == []


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_returns_after_first_matching_followup_bucket():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import describe_ticket, parse_ticket_block, write_ticket_block

    client = RecordingClient()
    client.issues["issue-budget"] = {
        "id": "issue-budget",
        "identifier": "ZOE-1",
        "description": "Parent prose",
        "assignee_id": "hermes",
    }
    existing_description = describe_ticket(
        "Existing backlog follow-up",
        zoe_kind="harness_fix",
        source="engineering_blocker_followup",
        parent_issue_id="issue-budget",
    )
    existing_metadata = parse_ticket_block(existing_description)
    existing_metadata["source_blocker"] = "IMPLEMENT_BUDGET"
    client.issues["existing-child"] = {
        "id": "existing-child",
        "identifier": "ZOE-C1",
        "status": "backlog",
        "description": write_ticket_block(existing_description, existing_metadata),
    }

    await _record_blocked_multica_chain(
        client,
        "issue-budget",
        {
            "pipeline": {
                "phase": "implement",
                "block_reason": "IMPLEMENT_BUDGET: code-enforced tool budget exceeded",
            }
        },
    )

    assert client.created == []
    assert client.list_calls == [{"status": "backlog", "limit": 1000}]


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_reuses_done_budget_followup():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import describe_ticket, parse_ticket_block, write_ticket_block

    client = RecordingClient()
    client.default_list_statuses = {"backlog", "todo", "in_progress", "blocked", "in_review"}
    client.issues["issue-budget"] = {
        "id": "issue-budget",
        "identifier": "ZOE-1",
        "description": "Parent prose",
        "assignee_id": "hermes",
    }
    existing_description = describe_ticket(
        "Done follow-up",
        zoe_kind="harness_fix",
        source="engineering_blocker_followup",
        parent_issue_id="issue-budget",
    )
    existing_metadata = parse_ticket_block(existing_description)
    existing_metadata["source_blocker"] = "IMPLEMENT_BUDGET"
    client.issues["done-child"] = {
        "id": "done-child",
        "identifier": "ZOE-C-DONE",
        "status": "done",
        "description": write_ticket_block(existing_description, existing_metadata),
    }

    await _record_blocked_multica_chain(
        client,
        "issue-budget",
        {
            "pipeline": {
                "phase": "implement",
                "block_reason": "IMPLEMENT_BUDGET: code-enforced tool budget exceeded",
            }
        },
    )

    assert client.created == []
    assert client.labels == []
    assert client.notes == []


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_creates_protocol_followup():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import parse_ticket_block

    client = RecordingClient()
    client.issues["issue-protocol"] = {
        "id": "issue-protocol",
        "identifier": "ZOE-2",
        "description": "Parent prose",
        "assignee_id": "hermes",
        "status": "blocked",
    }

    await _record_blocked_multica_chain(
        client,
        "issue-protocol",
        {"pipeline": {"phase": "implement", "block_reason": "PROTOCOL_VIOLATION: missing terminal handoff"}},
    )

    assert len(client.created) == 1
    metadata = parse_ticket_block(client.created[0]["description"])
    assert metadata["source_blocker"] == "PROTOCOL_VIOLATION"
    assert metadata["parent_issue_id"] == "issue-protocol"
    assert ("child-1", "protocol-violation") in client.labels


@pytest.mark.asyncio
async def test_record_blocked_multica_chain_reuses_existing_protocol_followup():
    from main import _record_blocked_multica_chain
    from multica_ticket_contract import describe_ticket, parse_ticket_block, write_ticket_block

    client = RecordingClient()
    client.issues["issue-protocol"] = {
        "id": "issue-protocol",
        "identifier": "ZOE-2",
        "description": "Parent prose",
        "assignee_id": "hermes",
    }
    existing_description = describe_ticket(
        "Existing protocol follow-up",
        zoe_kind="harness_fix",
        source="engineering_blocker_followup",
        parent_issue_id="issue-protocol",
    )
    existing_metadata = parse_ticket_block(existing_description)
    existing_metadata["source_blocker"] = "PROTOCOL_VIOLATION"
    client.issues["existing-protocol-child"] = {
        "id": "existing-protocol-child",
        "identifier": "ZOE-C2",
        "status": "backlog",
        "description": write_ticket_block(existing_description, existing_metadata),
    }

    await _record_blocked_multica_chain(
        client,
        "issue-protocol",
        {"pipeline": {"phase": "implement", "block_reason": "PROTOCOL_VIOLATION: missing terminal handoff"}},
    )

    assert client.created == []
    assert client.labels == []
    assert client.notes == []


@pytest.mark.asyncio
async def test_poll_chain_guarded_returns_sentinel_on_timeout(monkeypatch):
    import asyncio

    import executor_registry
    from main import _poll_chain_guarded

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(executor_registry, "poll_ref", _hang)
    chain = await _poll_chain_guarded("multica:dead", issue={"id": "dead"}, timeout=0.01)
    assert chain == {"found": False, "status": "poll_timeout", "timed_out": True}


@pytest.mark.asyncio
async def test_poll_chain_guarded_returns_sentinel_on_error(monkeypatch):
    import executor_registry
    from main import _poll_chain_guarded

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("executor gone")

    monkeypatch.setattr(executor_registry, "poll_ref", _boom)
    chain = await _poll_chain_guarded("multica:x", issue={"id": "x"}, timeout=1.0)
    assert chain["found"] is False
    assert chain["status"] == "poll_error"


@pytest.mark.asyncio
async def test_poll_chain_guarded_passes_through_live_result(monkeypatch):
    import executor_registry
    from main import _poll_chain_guarded

    async def _ok(ref, *, issue=None):
        return {"found": True, "status": "running", "ref": ref}

    monkeypatch.setattr(executor_registry, "poll_ref", _ok)
    chain = await _poll_chain_guarded("multica:live", issue={"id": "live"}, timeout=1.0)
    assert chain == {"found": True, "status": "running", "ref": "multica:live"}


@pytest.mark.asyncio
async def test_recover_stale_in_progress_resets_dead_chain_and_frees_lane():
    import datetime as dt

    from main import _recover_stale_in_progress_issues

    now = dt.datetime(2026, 6, 14, 12, 0, tzinfo=dt.timezone.utc)
    old = (now - dt.timedelta(hours=72)).isoformat()
    client = RecordingClient()

    zombie = {"id": "zombie", "identifier": "ZOE-DEAD", "assignee_id": "hermes", "updated_at": old}

    async def _poll(_issue):
        return {"found": False, "status": "poll_timeout", "timed_out": True}

    live = await _recover_stale_in_progress_issues(
        client, [zombie], hermes_id="hermes", poll_chain=_poll, now=now, max_age_hours=6
    )

    assert live == []  # lane freed
    assert len(client.calls) == 1
    args, kwargs = client.calls[0]
    assert args[0] == "zombie"
    assert kwargs["status"] == "blocked"
    assert "stale in_progress reset" in kwargs["blocker"]


@pytest.mark.asyncio
async def test_recover_stale_in_progress_keeps_live_and_foreign_issues():
    import datetime as dt

    from main import _recover_stale_in_progress_issues

    now = dt.datetime(2026, 6, 14, 12, 0, tzinfo=dt.timezone.utc)
    old = (now - dt.timedelta(hours=72)).isoformat()
    client = RecordingClient()

    live_run = {"id": "live", "assignee_id": "hermes", "updated_at": old}
    foreign = {"id": "foreign", "assignee_id": "other-agent", "updated_at": old}
    autopilot = {"id": "ap", "assignee_id": "hermes", "title": "Autopilot: tracker", "updated_at": old}

    async def _poll(issue):
        # Only the genuine run is active; the others are skipped on
        # ownership/title before any age-based reset is considered.
        if issue["id"] == "live":
            return {"found": True, "status": "running"}
        return {"found": False, "status": "not_found"}

    kept = await _recover_stale_in_progress_issues(
        client,
        [live_run, foreign, autopilot],
        hermes_id="hermes",
        poll_chain=_poll,
        now=now,
        max_age_hours=6,
    )

    assert kept == [live_run, foreign, autopilot]
    assert client.calls == []  # nothing reset


@pytest.mark.asyncio
async def test_recover_stale_in_progress_keeps_issue_when_reset_fails():
    import datetime as dt

    from main import _recover_stale_in_progress_issues

    now = dt.datetime(2026, 6, 14, 12, 0, tzinfo=dt.timezone.utc)
    old = (now - dt.timedelta(hours=72)).isoformat()

    class _FailingClient:
        async def record_progress(self, *_args, **_kwargs):
            raise RuntimeError("multica unreachable")

    zombie = {"id": "zombie", "assignee_id": "hermes", "updated_at": old}

    async def _poll(_issue):
        return {"found": False, "status": "poll_timeout", "timed_out": True}

    # If the reset call fails, the stale issue is conservatively kept in the lane
    # (better than dropping it from tracking) and the failure is surfaced.
    live = await _recover_stale_in_progress_issues(
        _FailingClient(), [zombie], hermes_id="hermes", poll_chain=_poll, now=now, max_age_hours=6
    )
    assert live == [zombie]


def test_bounded_blocked_resume_window_empty_or_nonpositive_budget():
    from main import _bounded_blocked_resume_window

    assert _bounded_blocked_resume_window([], 0, 4) == ([], 0)
    assert _bounded_blocked_resume_window([{"id": "a"}], 0, 0) == ([], 0)
    assert _bounded_blocked_resume_window([{"id": "a"}], 0, -1) == ([], 0)


def test_bounded_blocked_resume_window_returns_all_and_preserves_offset_when_budget_covers():
    from main import _bounded_blocked_resume_window

    items = [{"id": "a"}, {"id": "b"}]
    # budget covers the whole list -> return all, but keep the caller's place
    # (mod length) so rotation survives a list that shrinks then grows back.
    window, nxt = _bounded_blocked_resume_window(items, 5, 4)
    assert window == items
    assert nxt == 1  # 5 % 2


def test_bounded_blocked_resume_window_rotates_and_covers_all_across_cycles():
    from main import _bounded_blocked_resume_window

    items = [{"id": x} for x in "abcde"]  # 5 items, budget 2
    offset = 0
    seen: list[str] = []
    for _ in range(3):
        window, offset = _bounded_blocked_resume_window(items, offset, 2)
        assert len(window) == 2
        seen.extend(i["id"] for i in window)
    assert set("abcde").issubset(set(seen))
    # Offset advances by budget modulo length: 0 -> 2 -> 4 -> 1.
    assert offset == 1


def test_bounded_blocked_resume_window_wraps_at_end():
    from main import _bounded_blocked_resume_window

    items = [{"id": x} for x in "abcd"]  # 4 items, budget 3, start near end
    window, nxt = _bounded_blocked_resume_window(items, 3, 3)
    assert [i["id"] for i in window] == ["d", "a", "b"]
    assert nxt == 2
