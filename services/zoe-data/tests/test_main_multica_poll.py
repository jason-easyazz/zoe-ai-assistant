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

    assert client.calls[0][1]["pr_url"] is None
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
