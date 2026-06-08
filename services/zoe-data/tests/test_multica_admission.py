"""Tests for safe production backlog admission."""

import multica_admission
from multica_admission import select_next_approved_issue, ticket_is_dispatch_approved
from multica_ticket_contract import describe_ticket, parse_ticket_block, write_ticket_block


HERMES = "hermes-agent"


def _issue(identifier: str, title: str, *, status: str = "backlog", **metadata):
    description = describe_ticket(
        "Small production ticket.",
        zoe_kind=metadata.pop("zoe_kind", "harness_fix"),
        acceptance_criteria=["Change is complete."],
        evidence_expectations=["Focused tests", "PR URL"],
        source=metadata.pop("source", "operator_approved"),
    )
    ticket = parse_ticket_block(description)
    ticket.update(metadata)
    return {
        "id": identifier.lower(),
        "identifier": identifier,
        "title": title,
        "description": write_ticket_block(description, ticket),
        "status": status,
        "priority": "medium",
        "assignee_id": HERMES,
        "assignee_type": "agent",
    }


def test_unapproved_or_unstructured_backlog_is_not_admitted():
    unapproved = _issue("ZOE-1", "Small fix")
    legacy = {
        "id": "legacy",
        "identifier": "ZOE-2",
        "title": "Legacy fix",
        "description": "no ticket contract",
        "assignee_id": HERMES,
    }

    assert not ticket_is_dispatch_approved(unapproved, hermes_agent_id=HERMES)
    assert select_next_approved_issue(
        [unapproved, legacy],
        [unapproved, legacy],
        hermes_agent_id=HERMES,
    )[0] is None


def test_selects_one_approved_issue_by_queue_order():
    second = _issue("ZOE-2", "Second fix", dispatch_approved=True, queue_order=2)
    first = _issue("ZOE-1", "First fix", dispatch_approved=True, queue_order=1)

    selected, held = select_next_approved_issue(
        [second, first],
        [second, first],
        hermes_agent_id=HERMES,
    )

    assert selected == first
    assert held == []


def test_approved_blocked_ticket_halts_single_ticket_lane():
    blocked = _issue(
        "ZOE-1",
        "First fix",
        status="blocked",
        dispatch_approved=True,
        queue_order=1,
    )
    next_issue = _issue(
        "ZOE-2",
        "Second fix",
        dispatch_approved=True,
        queue_order=2,
    )

    selected, held = select_next_approved_issue(
        [next_issue],
        [blocked, next_issue],
        hermes_agent_id=HERMES,
    )

    assert selected is None
    assert held == ["single ticket lane halted by approved blocked ticket(s): ZOE-1"]


def test_non_hermes_blocked_ticket_does_not_halt_lane():
    blocked = _issue(
        "ZOE-1",
        "Other workflow",
        status="blocked",
        dispatch_approved=True,
    )
    blocked["assignee_id"] = "another-agent"
    next_issue = _issue("ZOE-2", "Hermes fix", dispatch_approved=True)

    selected, held = select_next_approved_issue(
        [next_issue],
        [blocked, next_issue],
        hermes_agent_id=HERMES,
    )

    assert selected == next_issue
    assert held == []


def test_ticket_metadata_is_parsed_once_per_backlog_issue(monkeypatch):
    first = _issue("ZOE-1", "First fix", dispatch_approved=True, queue_order=1)
    second = _issue("ZOE-2", "Second fix", dispatch_approved=True, queue_order=2)
    real_parse = multica_admission.parse_ticket_block
    calls = 0

    def counting_parse(description):
        nonlocal calls
        calls += 1
        return real_parse(description)

    monkeypatch.setattr(multica_admission, "parse_ticket_block", counting_parse)

    selected, _held = select_next_approved_issue(
        [second, first],
        [second, first],
        hermes_agent_id=HERMES,
    )

    assert selected == first
    assert calls == 2


def test_malformed_queue_order_cannot_wedge_admission():
    malformed = _issue("ZOE-1", "Malformed", dispatch_approved=True, queue_order="soon")
    ordered = _issue("ZOE-2", "Ordered", dispatch_approved=True, queue_order=2)

    selected, _held = select_next_approved_issue(
        [malformed, ordered],
        [malformed, ordered],
        hermes_agent_id=HERMES,
    )

    assert selected == ordered


def test_zero_queue_order_is_highest_priority():
    later = _issue("ZOE-2", "Later", dispatch_approved=True, queue_order=1)
    first = _issue("ZOE-1", "First", dispatch_approved=True, queue_order=0)

    selected, _held = select_next_approved_issue(
        [later, first],
        [later, first],
        hermes_agent_id=HERMES,
    )

    assert selected == first


def test_phased_ticket_waits_for_predecessor():
    phase_one = _issue(
        "ZOE-1",
        "card-upgrade: Phase 1 - contract",
        status="in_progress",
        dispatch_approved=True,
    )
    phase_two = _issue(
        "ZOE-2",
        "card-upgrade: Phase 2 - renderer",
        dispatch_approved=True,
    )

    selected, held = select_next_approved_issue(
        [phase_two],
        [phase_one, phase_two],
        hermes_agent_id=HERMES,
    )

    assert selected is None
    assert held == ["ZOE-2: card-upgrade phase 2 waiting for predecessor"]


def test_smoke_sources_and_parent_tickets_are_never_auto_admitted():
    smoke = _issue("ZOE-1", "Smoke", dispatch_approved=True, source="codex_live_e2e")
    parent = _issue(
        "ZOE-2",
        "Parent",
        dispatch_approved=True,
        zoe_kind="parent",
    )

    assert select_next_approved_issue(
        [smoke, parent],
        [smoke, parent],
        hermes_agent_id=HERMES,
    )[0] is None
