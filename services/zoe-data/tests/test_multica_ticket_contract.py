import pytest
from multica_ticket_contract import (
    append_child_id,
    describe_ticket,
    find_live_path_references,
    normalize_live_paths,
    parse_ticket_block,
    update_ticket_progress,
    validate_ticket_contract,
    write_ticket_block,
)

pytestmark = pytest.mark.ci_safe


def test_ticket_block_preserves_human_description():
    description = "Human prose stays here."
    updated = write_ticket_block(description, {"schema": 1, "zoe_kind": "bug"})

    assert updated.startswith(description)
    assert parse_ticket_block(updated)["zoe_kind"] == "bug"


def test_ticket_block_replaces_only_zoe_section():
    first = describe_ticket("Fix reminders", zoe_kind="bug", acceptance_criteria=["works"])
    second = write_ticket_block(first, {"schema": 1, "zoe_kind": "child", "parent_issue_id": "p1"})

    assert second.count("```zoe-ticket") == 1
    assert second.startswith("Fix reminders")
    assert parse_ticket_block(second)["parent_issue_id"] == "p1"


def test_progress_and_children_patch_metadata_only():
    description = describe_ticket("Implement widget", zoe_kind="feature")
    progressed = update_ticket_progress(description, phase="verify", pr_url="https://github/pr/1")
    linked = append_child_id(progressed, "child-1")
    linked = append_child_id(linked, "child-1")

    meta = parse_ticket_block(linked)
    assert meta["phase"] == "verify"
    assert meta["pr_url"] == "https://github/pr/1"
    assert meta["child_issue_ids"] == ["child-1"]


def test_progress_records_completion_reason_without_touching_prose():
    description = describe_ticket("Human closure notes", zoe_kind="harness_fix")

    updated = update_ticket_progress(
        description,
        phase="done",
        evidence="merged and deployed",
        completion_reason="PR merged after Greptile 5/5",
    )

    assert updated.startswith("Human closure notes")
    meta = parse_ticket_block(updated)
    assert meta["phase"] == "done"
    assert meta["last_evidence"] == "merged and deployed"
    assert meta["completion_reason"] == "PR merged after Greptile 5/5"


def test_block_only_description_does_not_gain_leading_blank_lines():
    description = describe_ticket("", zoe_kind="bug")
    updated = write_ticket_block(description, {"schema": 1, "zoe_kind": "child"})

    assert updated.startswith("```zoe-ticket")
    assert not updated.startswith("\n")


def test_replacing_middle_block_preserves_suffix_newline():
    description = "Intro\n\n```zoe-ticket\n{\"schema\": 1}\n```\nTrailing prose"
    updated = write_ticket_block(description, {"schema": 1, "zoe_kind": "bug"})

    assert "```\nTrailing prose" in updated


LIVE = "/home/zoe/assistant"


def test_find_live_path_references_detects_root_and_nested(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    text = (
        f"cd {LIVE} && edit {LIVE}/services/zoe-data/x.py, then check "
        f"{LIVE}/scripts/run.sh."
    )
    refs = find_live_path_references(text)
    assert LIVE in refs
    assert f"{LIVE}/services/zoe-data/x.py" in refs
    assert f"{LIVE}/scripts/run.sh" in refs


def test_find_live_path_references_clean_ticket(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    # Worktree-relative paths are fine and must not be flagged.
    assert find_live_path_references("edit services/zoe-data/x.py and run tests") == []


def test_normalize_live_paths_rewrites_to_relative(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    text = f"cd {LIVE} && python {LIVE}/scripts/run.sh services/x.py"
    normalized = normalize_live_paths(text)
    assert normalized == "cd . && python scripts/run.sh services/x.py"
    assert LIVE not in normalized


def test_validate_ticket_contract_blocks_live_path(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    result = validate_ticket_contract(f"Please edit {LIVE}/services/zoe-data/x.py")
    assert result["ok"] is False
    assert any("WORKTREE_PATH_VIOLATION" in v for v in result["violations"])
    assert "normalized_description" in result
    assert LIVE not in result["normalized_description"]


def test_validate_ticket_contract_passes_clean_ticket(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    result = validate_ticket_contract("Edit services/zoe-data/x.py; run focused tests.")
    assert result == {"ok": True, "violations": []}


def test_validate_ticket_contract_honors_custom_live_root():
    result = validate_ticket_contract("touch /srv/app/main.py", live_root="/srv/app")
    assert result["ok"] is False
    assert result["normalized_description"] == "touch main.py"


def test_sibling_path_with_root_prefix_is_not_flagged(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    # Directories that merely share the root as a string prefix (hyphen and dot).
    assert find_live_path_references(f"back up {LIVE}-backup/data first") == []
    assert find_live_path_references(f"see {LIVE}.bak and {LIVE}.config") == []


def test_metadata_block_is_not_scanned_for_live_paths(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    # A blocked_reason that legitimately quotes the offending path must not
    # re-trigger a violation (it lives inside the machine-managed JSON block).
    description = describe_ticket(
        "Edit services/zoe-data/x.py only.",
        zoe_kind="harness_fix",
        acceptance_criteria=["done"],
        evidence_expectations=["tests"],
    )
    blocked = update_ticket_progress(
        description,
        blocker=f"WORKTREE_PATH_VIOLATION: worker read {LIVE}/services/zoe-data/x.py",
    )
    assert find_live_path_references(blocked) == []
    assert validate_ticket_contract(blocked)["ok"] is True


def test_normalize_leaves_metadata_block_intact(monkeypatch):
    monkeypatch.setenv("ZOE_ASSISTANT_ROOT", LIVE)
    description = describe_ticket(
        f"Run {LIVE}/scripts/run.sh.",
        zoe_kind="harness_fix",
        acceptance_criteria=["done"],
        evidence_expectations=["tests"],
    )
    normalized = normalize_live_paths(description)
    # Prose rewritten...
    assert "Run scripts/run.sh." in normalized
    # ...but the fenced block is preserved verbatim and still parses.
    block = description[description.index("```zoe-ticket") :]
    assert block in normalized
    assert parse_ticket_block(normalized).get("schema") == 1
