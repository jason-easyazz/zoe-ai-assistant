from pathlib import Path

import pytest

from multica_ticket_contract import parse_ticket_block
from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_multica_handoff import build_capability_profile_multica_handoff
from zoe_capability_profile_patch_writer import build_capability_profile_patch_plan
from zoe_capability_profile_promotion import build_capability_profile_promotion_plan
from zoe_capability_profile_ticket_writer import (
    CAPABILITY_PROFILE_TICKET_LABEL,
    CAPABILITY_PROFILE_TICKET_WRITER_SOURCE,
    CapabilityProfileTicketWriterResult,
    create_capability_profile_handoff_ticket,
)
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan

pytestmark = pytest.mark.ci_safe


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


def _candidate(**overrides):
    values = {
        "capability_id": "hindsight_reflective_memory",
        "proposal_id": "proposal_hindsight_ticket_writer",
        "proposal_candidate_id": "hindsight_reflective_memory",
        "current_trust_level": "experimental",
        "proposed_trust_level": "assisted",
        "reason": "Verified retained outcome supports a reviewable promotion.",
        "evidence_refs": ("pytest:test_zoe_capability_profile_ticket_writer", "approval:multica:ZOE-411"),
        "source_event_id": "event_hindsight_ticket_writer",
        "source_admission_id": "admit_hindsight_ticket_writer",
        "retained_backend": "zoe-project-jason",
        "metadata": {"source": "evolution_outcome_retain"},
    }
    values.update(overrides)
    return CapabilityTrustUpdateCandidate(**values)


def _handoff(*, pr_refs=("pr:411",)):
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-411",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    promotion = build_capability_profile_promotion_plan(
        review,
        pr_refs=pr_refs,
        rollback_refs=("rollback:revert-pr-411",),
        verification_refs=("pytest:test_zoe_capability_profile_ticket_writer",),
    )
    patch = build_capability_profile_patch_plan(promotion, source_text=_source_text())
    return build_capability_profile_multica_handoff(
        promotion,
        patch,
        title="Promote Hindsight reflective memory profile",
        parent_issue_id="ZOE-411",
    )


class FakeMulticaClient:
    def __init__(self, issue=None):
        self.issue = issue or {"id": "issue-411", "identifier": "ZOE-411"}
        self.create_calls = []
        self.label_calls = []

    async def create_issue(self, **kwargs):
        self.create_calls.append(kwargs)
        return dict(self.issue)

    async def attach_label(self, issue_id, label_name):
        result = {"issue_id": issue_id, "label_name": label_name, "ok": True}
        self.label_calls.append(result)
        return result


@pytest.mark.asyncio
async def test_profile_ticket_writer_creates_issue_only_after_gate_allows(monkeypatch):
    client = FakeMulticaClient()
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-agent")

    result = await create_capability_profile_handoff_ticket(
        _handoff(),
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-411",),
        evidence_refs=("pytest:test_zoe_capability_profile_ticket_writer",),
        client=client,
        metadata={"source_pr": 411},
    )

    assert result.created is True
    assert result.blockers == ()
    assert result.metadata["source"] == CAPABILITY_PROFILE_TICKET_WRITER_SOURCE
    assert result.gate_decision.allowed_to_create_ticket is True
    assert result.issue["id"] == "issue-411"
    assert client.create_calls[0]["title"] == "Promote Hindsight reflective memory profile"
    assert client.create_calls[0]["status"] == "backlog"
    assert client.create_calls[0]["assignee_id"] == "hermes-agent"
    assert client.label_calls == [{"issue_id": "issue-411", "label_name": CAPABILITY_PROFILE_TICKET_LABEL, "ok": True}]
    description = client.create_calls[0]["description"]
    metadata = parse_ticket_block(description)
    assert metadata["ticket_writer_source"] == CAPABILITY_PROFILE_TICKET_WRITER_SOURCE
    assert metadata["profile_ticket_gate"]["allowed_to_create_ticket"] is True
    assert "## Promotion Manifest" in description
    assert "hindsight_reflective_memory" in description
    assert "## Profile Patch" in description
    assert "+        trust_level=\"assisted\"," in description


@pytest.mark.asyncio
async def test_profile_ticket_writer_blocks_without_operator_approval_and_does_not_call_client():
    client = FakeMulticaClient()

    result = await create_capability_profile_handoff_ticket(
        _handoff(),
        operator_id="operator:jason",
        approval_refs=(),
        client=client,
    )

    assert result.created is False
    assert "missing_ticket_writer_approval_refs" in result.blockers
    assert result.issue == {}
    assert client.create_calls == []
    assert client.label_calls == []


@pytest.mark.asyncio
async def test_profile_ticket_writer_blocks_uncreateable_handoff_and_does_not_call_client():
    client = FakeMulticaClient()

    result = await create_capability_profile_handoff_ticket(
        _handoff(pr_refs=()),
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-411",),
        client=client,
    )

    assert result.created is False
    assert "handoff_not_createable" in result.blockers
    assert "missing_pr_refs" in result.blockers
    assert client.create_calls == []


@pytest.mark.asyncio
async def test_profile_ticket_writer_reports_multica_create_failure():
    client = FakeMulticaClient(issue={"error": "offline"})

    result = await create_capability_profile_handoff_ticket(
        _handoff(),
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-411",),
        client=client,
    )

    assert result.created is False
    assert "multica_issue_not_created" in result.blockers
    assert "multica_create_issue_error" in result.blockers
    assert result.issue == {"error": "offline"}


class FailingLabelClient(FakeMulticaClient):
    async def attach_label(self, issue_id, label_name):
        self.label_calls.append({"issue_id": issue_id, "label_name": label_name})
        raise RuntimeError("label service offline")


@pytest.mark.asyncio
async def test_profile_ticket_writer_returns_created_result_when_label_attach_fails():
    client = FailingLabelClient()

    result = await create_capability_profile_handoff_ticket(
        _handoff(),
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-411",),
        client=client,
    )

    assert result.created is True
    assert result.issue["id"] == "issue-411"
    assert result.blockers == ()
    assert result.label_results[0]["label_name"] == CAPABILITY_PROFILE_TICKET_LABEL
    assert result.label_results[0]["error"] == "label service offline"


def test_profile_ticket_writer_result_rejects_issue_id_with_blockers():
    with pytest.raises(ValueError, match="created issue cannot carry blockers"):
        CapabilityProfileTicketWriterResult(
            gate_decision=create_capability_profile_handoff_ticket,  # type: ignore[arg-type]
            issue={"id": "issue-411"},
            blockers=("manual_blocker",),
        )
