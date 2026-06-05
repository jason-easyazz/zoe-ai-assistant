import pytest

from intent_router import Intent, detect_intent, execute_intent


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
