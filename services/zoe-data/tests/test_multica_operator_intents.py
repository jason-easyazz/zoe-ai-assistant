import pytest

from intent_router import Intent, detect_intent, execute_intent
import multica_client
import multica_operator


def test_detects_multica_operator_commands():
    assert detect_intent("pause engineering dispatch").name == "engineering_dispatch_pause"
    assert detect_intent("resume multica dispatch").name == "engineering_dispatch_resume"
    move = detect_intent("move ZOE-5423 to todo")
    assert move.name == "engineering_ticket_move_todo"
    assert move.slots["reference"] == "ZOE-5423"
    split = detect_intent("split ZOE-5423 into add the health probe")
    assert split.name == "engineering_ticket_split"
    assert split.slots["title"] == "add the health probe"
    assert detect_intent("show Multica backlog").slots["status"] == "backlog"
    assert detect_intent("what is blocked").slots["status"] == "blocked"
    assert detect_intent("show blocked engineering tickets").slots["status"] == "blocked"
    unrelated = detect_intent("what is blocked in the calendar?")
    assert unrelated is None or unrelated.name != "engineering_ticket_list"


def test_natural_problem_report_routes_to_multica_capture():
    intent = detect_intent("your reminders are broken again")
    assert intent.name == "user_issue_report"
    assert intent.slots["message"] == "your reminders are broken again"


@pytest.mark.asyncio
async def test_board_status_reports_lifecycle_metadata(monkeypatch):
    class Response:
        def json(self):
            return {
                "available": True,
                "groups": {
                    "blocked": [
                        {
                            "id": "issue-1",
                            "identifier": "ZOE-1",
                            "title": "Fix dispatch",
                            "phase": "review",
                            "blocker": "Greptile comment",
                            "child_count": 2,
                            "pr_url": "https://github.com/o/r/pull/1",
                        }
                    ]
                },
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    result = await execute_intent(Intent("board_status", {}))

    assert result is not None
    assert "ZOE-1" in result
    assert "phase: review" in result
    assert "blocked: Greptile comment" in result
    assert "children: 2" in result
    assert "pull/1" in result


@pytest.mark.asyncio
async def test_operator_commands_report_multica_failures(monkeypatch, caplog):
    async def fail_move(*_args, **_kwargs):
        raise RuntimeError("move unavailable")

    async def fail_split(*_args, **_kwargs):
        raise RuntimeError("split unavailable")

    class FailingClient:
        async def list_issues(self, **_kwargs):
            raise RuntimeError("list unavailable")

    monkeypatch.setattr(multica_operator, "move_to_todo", fail_move)
    monkeypatch.setattr(multica_operator, "split_ticket", fail_split)
    monkeypatch.setattr(multica_client, "get_multica_client", lambda: FailingClient())

    move = await execute_intent(Intent("engineering_ticket_move_todo", {"reference": "ZOE-1"}))
    split = await execute_intent(
        Intent("engineering_ticket_split", {"reference": "ZOE-1", "title": "child"})
    )
    listed = await execute_intent(Intent("engineering_ticket_list", {"status": "blocked"}))

    assert move == "I couldn't update that Multica ticket right now."
    assert split == "I couldn't split that Multica ticket right now."
    assert listed == "I couldn't list Multica tickets in `blocked` right now."
    assert "move unavailable" in caplog.text
    assert "split unavailable" in caplog.text
    assert "list unavailable" in caplog.text


@pytest.mark.asyncio
async def test_find_issue_accepts_empty_issue_response(monkeypatch):
    class EmptyClient:
        async def list_issues(self):
            return None

    monkeypatch.setattr(multica_operator, "get_multica_client", lambda: EmptyClient())

    assert await multica_operator.find_issue("ZOE-1") == {}


@pytest.mark.asyncio
async def test_find_issue_fallback_requests_large_issue_page(monkeypatch):
    calls = []

    class Client:
        async def list_issues(self, **kwargs):
            calls.append(kwargs)
            return [{"id": "issue-1", "identifier": "ZOE-1"}]

    monkeypatch.setattr(multica_operator, "get_multica_client", lambda: Client())

    assert await multica_operator.find_issue("ZOE-1") == {"id": "issue-1", "identifier": "ZOE-1"}
    assert calls == [{"limit": 1000}]


@pytest.mark.asyncio
async def test_find_issue_fallback_supports_legacy_list_issues_client(monkeypatch):
    class Client:
        async def list_issues(self):
            return [{"id": "issue-1", "identifier": "ZOE-1"}]

    monkeypatch.setattr(multica_operator, "get_multica_client", lambda: Client())

    assert await multica_operator.find_issue("issue-1") == {"id": "issue-1", "identifier": "ZOE-1"}
