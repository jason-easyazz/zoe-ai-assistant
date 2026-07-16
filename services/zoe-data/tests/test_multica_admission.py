"""Tests for safe production backlog admission."""

import pytest
import multica_admission
from multica_admission import select_next_approved_issue, ticket_is_dispatch_approved
from multica_ticket_contract import describe_ticket, parse_ticket_block, write_ticket_block

pytestmark = pytest.mark.ci_safe


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


def _approved_evolution_issue(identifier: str, proposal_id: str, **metadata):
    contract_metadata = {
        "evolution_proposal_id": proposal_id,
        "evolution_contract_schema": "zoe_evolution_proposal",
        "evolution_contract_version": 1,
        "evolution_contract_proposal_id": proposal_id,
        "evolution_contract_autonomy_class": "suggest",
        "evolution_contract_risk": "medium",
        "evolution_contract_status": "pending_approval",
        "evolution_contract_allowed_to_prepare": True,
    }
    contract_metadata.update(metadata)
    return _issue(
        identifier,
        "Evolution proposal",
        dispatch_approved=True,
        source=f"evolution_proposal:{proposal_id}",
        **contract_metadata,
    )


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


def test_live_path_ticket_is_not_dispatch_approved(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", "/home/zoe/assistant")
    clean = _issue("ZOE-1", "Clean fix", dispatch_approved=True)
    assert ticket_is_dispatch_approved(clean, hermes_agent_id=HERMES)

    # Same approved ticket, but the prose pins the worker to the live checkout.
    polluted = dict(clean)
    polluted["description"] = (
        clean["description"] + "\n\nEdit /home/zoe/assistant/services/zoe-data/x.py here."
    )
    assert not ticket_is_dispatch_approved(polluted, hermes_agent_id=HERMES)
    # And it must not be selected for dispatch.
    selected, _held = select_next_approved_issue(
        [polluted], [polluted], hermes_agent_id=HERMES
    )
    assert selected is None


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


def test_evolution_proposal_ticket_requires_matching_contract_marker():
    missing_contract = _issue(
        "ZOE-1",
        "Evolution without contract",
        dispatch_approved=True,
        source="evolution_proposal:proposal-1",
    )
    mismatched_contract = _approved_evolution_issue(
        "ZOE-2",
        "proposal-1",
        evolution_contract_proposal_id="proposal-2",
    )
    approved = _approved_evolution_issue("ZOE-3", "proposal-3")

    assert not ticket_is_dispatch_approved(missing_contract, hermes_agent_id=HERMES)
    assert not ticket_is_dispatch_approved(mismatched_contract, hermes_agent_id=HERMES)
    selected, held = select_next_approved_issue(
        [missing_contract, mismatched_contract, approved],
        [missing_contract, mismatched_contract, approved],
        hermes_agent_id=HERMES,
    )

    assert selected == approved
    assert held == []


def test_blocked_evolution_ticket_without_contract_does_not_halt_lane():
    blocked_without_contract = _issue(
        "ZOE-1",
        "Blocked broken evolution proposal",
        status="blocked",
        dispatch_approved=True,
        source="evolution_proposal:proposal-1",
    )
    approved = _approved_evolution_issue("ZOE-2", "proposal-2")

    selected, held = select_next_approved_issue(
        [approved],
        [blocked_without_contract, approved],
        hermes_agent_id=HERMES,
    )

    assert selected == approved
    assert held == []


def test_execute_evolution_ticket_requires_execution_approval_refs():
    execute_without_refs = _approved_evolution_issue(
        "ZOE-1",
        "proposal-1",
        evolution_contract_autonomy_class="execute",
        evolution_contract_approval_required=["install_or_runtime_change", "pr_evidence"],
    )
    approved = _approved_evolution_issue("ZOE-2", "proposal-2")

    assert not ticket_is_dispatch_approved(execute_without_refs, hermes_agent_id=HERMES)
    selected, held = select_next_approved_issue(
        [execute_without_refs, approved],
        [execute_without_refs, approved],
        hermes_agent_id=HERMES,
    )

    assert selected == approved
    assert held == []


def test_execute_evolution_ticket_with_required_approval_refs_can_dispatch():
    execute_with_refs = _approved_evolution_issue(
        "ZOE-1",
        "proposal-1",
        evolution_contract_autonomy_class="execute",
        evolution_contract_approval_required=["install_or_runtime_change", "pr_evidence"],
        evolution_execution_approval_refs=[
            "approval:install_or_runtime_change:jason:2026-06-10",
            "pr:https://github.com/jason-easyazz/zoe-ai-assistant/pull/303",
        ],
    )

    assert ticket_is_dispatch_approved(execute_with_refs, hermes_agent_id=HERMES)


def test_blocked_execute_ticket_without_approval_refs_does_not_halt_lane():
    blocked_without_refs = _approved_evolution_issue(
        "ZOE-1",
        "proposal-1",
        status="blocked",
        evolution_contract_autonomy_class="execute",
        evolution_contract_approval_required=["install_or_runtime_change", "pr_evidence"],
    )
    approved = _approved_evolution_issue("ZOE-2", "proposal-2")

    selected, held = select_next_approved_issue(
        [approved],
        [blocked_without_refs, approved],
        hermes_agent_id=HERMES,
    )

    assert selected == approved
    assert held == []


def test_promote_evolution_ticket_requires_execution_approval_refs():
    promote_without_refs = _approved_evolution_issue(
        "ZOE-1",
        "proposal-1",
        evolution_contract_autonomy_class="promote",
        evolution_contract_approval_required=["memory_admission", "pr_evidence"],
    )

    assert not ticket_is_dispatch_approved(promote_without_refs, hermes_agent_id=HERMES)


def test_promote_evolution_ticket_with_required_approval_refs_can_dispatch():
    promote_with_refs = _approved_evolution_issue(
        "ZOE-1",
        "proposal-1",
        evolution_contract_autonomy_class="promote",
        evolution_contract_approval_required=["memory_admission", "pr_evidence"],
        evolution_execution_approval_refs=[
            "approval:memory_admission:trace-123",
            "github_pr:https://github.com/jason-easyazz/zoe-ai-assistant/pull/303",
        ],
    )

    assert ticket_is_dispatch_approved(promote_with_refs, hermes_agent_id=HERMES)
