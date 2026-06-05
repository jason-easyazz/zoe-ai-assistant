from intent_router import detect_intent


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
