from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from zoe_capability_profile import DEFAULT_CAPABILITY_PROFILES
from zoe_capability_profile_multica_handoff import build_capability_profile_multica_handoff
from zoe_capability_profile_patch_writer import build_capability_profile_patch_plan
from zoe_capability_profile_promotion import build_capability_profile_promotion_plan
from zoe_capability_profile_ticket_writer import create_capability_profile_handoff_ticket
from zoe_capability_trust_review import review_capability_trust_update_plan
from zoe_capability_trust_update import CapabilityTrustUpdateCandidate, CapabilityTrustUpdatePlan

pytestmark = pytest.mark.ci_safe


def _load_runner():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "maintenance" / "capability_profile_pr_edit_workflow.py"
    spec = importlib.util.spec_from_file_location("capability_profile_pr_edit_workflow", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _source_text():
    return (Path(__file__).parent.parent / "zoe_capability_profile.py").read_text(encoding="utf-8")


def _candidate():
    return CapabilityTrustUpdateCandidate(
        capability_id="hindsight_reflective_memory",
        proposal_id="proposal_profile_pr_edit_workflow",
        proposal_candidate_id="hindsight_reflective_memory",
        current_trust_level="experimental",
        proposed_trust_level="assisted",
        reason="Verified retained outcome supports a reviewable promotion.",
        evidence_refs=("pytest:test_capability_profile_pr_edit_workflow", "approval:multica:ZOE-427"),
        source_event_id="event_profile_pr_edit_workflow",
        source_admission_id="admit_profile_pr_edit_workflow",
        retained_backend="zoe-project-jason",
        metadata={"source": "evolution_outcome_retain"},
    )


def _handoff():
    review = review_capability_trust_update_plan(
        CapabilityTrustUpdatePlan(candidates=(_candidate(),)),
        reviewer_id="multica:reviewer",
        approval_refs=("approval:trust-review:ZOE-427",),
        approved_capability_ids=("hindsight_reflective_memory",),
        profiles=DEFAULT_CAPABILITY_PROFILES,
    )
    promotion = build_capability_profile_promotion_plan(
        review,
        pr_refs=("pr:427",),
        rollback_refs=("rollback:revert-pr-427",),
        verification_refs=("pytest:test_capability_profile_pr_edit_workflow",),
    )
    patch = build_capability_profile_patch_plan(promotion, source_text=_source_text())
    return build_capability_profile_multica_handoff(
        promotion,
        patch,
        title="Promote Hindsight reflective memory profile",
        parent_issue_id="ZOE-427",
    )


class FakeMulticaClient:
    def __init__(self):
        self.create_calls = []

    async def create_issue(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"id": "issue-427", "identifier": "ZOE-427"}


async def _write_fixture_files(tmp_path: Path):
    handoff = _handoff()
    client = FakeMulticaClient()
    result = await create_capability_profile_handoff_ticket(
        handoff,
        operator_id="operator:jason",
        approval_refs=("approval:operator:ZOE-427",),
        evidence_refs=("pytest:test_capability_profile_pr_edit_workflow",),
        client=client,
    )
    assert result.created is True
    files = {
        "ticket": tmp_path / "ticket.md",
        "source": tmp_path / "source.py",
        "patch": tmp_path / "profile.patch",
        "manifest": tmp_path / "manifest.json",
    }
    files["ticket"].write_text(client.create_calls[0]["description"], encoding="utf-8")
    files["source"].write_text(_source_text(), encoding="utf-8")
    files["patch"].write_text(handoff.patch_text, encoding="utf-8")
    files["manifest"].write_text(handoff.promotion_manifest, encoding="utf-8")
    return handoff, files


@pytest.mark.asyncio
async def test_workflow_cli_outputs_allowed_plan(tmp_path, capsys):
    runner = _load_runner()
    handoff, files = await _write_fixture_files(tmp_path)

    rc = runner.main(
        [
            "--ticket-id", "ZOE-427",
            "--ticket-description-file", str(files["ticket"]),
            "--current-source-file", str(files["source"]),
            "--patch-file", str(files["patch"]),
            "--promotion-manifest-file", str(files["manifest"]),
            "--pr-ref", "https://github.com/jason-easyazz/zoe-ai-assistant/pull/427",
            "--rollback-ref", "rollback:revert-pr-427",
            "--verification-ref", "pytest:test_capability_profile_pr_edit_workflow",
            "--greptile-ref", "greptile:pass:427",
            "--metadata-json", '{"operator":"jason"}',
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed_to_prepare_pr_edit"] is True
    assert payload["metadata"]["extra"] == {"operator": "jason"}
    assert payload["patch_text"] == handoff.patch_text


@pytest.mark.asyncio
async def test_workflow_cli_render_patch_outputs_allowed_patch(tmp_path, capsys):
    runner = _load_runner()
    handoff, files = await _write_fixture_files(tmp_path)

    rc = runner.main(
        [
            "--ticket-id", "ZOE-427",
            "--ticket-description-file", str(files["ticket"]),
            "--current-source-file", str(files["source"]),
            "--patch-file", str(files["patch"]),
            "--promotion-manifest-file", str(files["manifest"]),
            "--pr-ref", "https://github.com/jason-easyazz/zoe-ai-assistant/pull/427",
            "--rollback-ref", "rollback:revert-pr-427",
            "--verification-ref", "pytest:test_capability_profile_pr_edit_workflow",
            "--greptile-ref", "greptile:pass:427",
            "--render-patch",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == handoff.patch_text
    assert captured.err == ""


@pytest.mark.asyncio
async def test_workflow_cli_blocks_without_greptile_ref(tmp_path, capsys):
    runner = _load_runner()
    _handoff, files = await _write_fixture_files(tmp_path)

    rc = runner.main(
        [
            "--ticket-id", "ZOE-427",
            "--ticket-description-file", str(files["ticket"]),
            "--current-source-file", str(files["source"]),
            "--patch-file", str(files["patch"]),
            "--promotion-manifest-file", str(files["manifest"]),
            "--pr-ref", "pr:427",
            "--rollback-ref", "rollback:revert-pr-427",
            "--verification-ref", "pytest:test_capability_profile_pr_edit_workflow",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed_to_prepare_pr_edit"] is False
    assert "missing_greptile_refs" in payload["blockers"]
    assert payload["patch_text"] == ""


@pytest.mark.asyncio
async def test_workflow_cli_render_patch_fails_closed_when_blocked(tmp_path, capsys):
    runner = _load_runner()
    _handoff, files = await _write_fixture_files(tmp_path)

    rc = runner.main(
        [
            "--ticket-id", "ZOE-427",
            "--ticket-description-file", str(files["ticket"]),
            "--current-source-file", str(files["source"]),
            "--patch-file", str(files["patch"]),
            "--promotion-manifest-file", str(files["manifest"]),
            "--pr-ref", "pr:427",
            "--rollback-ref", "rollback:revert-pr-427",
            "--verification-ref", "pytest:test_capability_profile_pr_edit_workflow",
            "--render-patch",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "missing_greptile_refs" in captured.err
    assert "cannot render capability profile PR edit patch" in captured.err


def test_workflow_cli_bad_file_path_fails_cleanly(tmp_path, capsys):
    runner = _load_runner()
    missing = tmp_path / "missing.md"

    with pytest.raises(SystemExit, match="could not read ticket description file"):
        runner.main(
            [
                "--ticket-id", "ZOE-427",
                "--ticket-description-file", str(missing),
                "--current-source-file", str(missing),
                "--patch-file", str(missing),
                "--promotion-manifest-file", str(missing),
            ]
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
