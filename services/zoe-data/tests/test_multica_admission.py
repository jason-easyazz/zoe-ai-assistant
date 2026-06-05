"""Tests for safe production backlog admission."""

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
