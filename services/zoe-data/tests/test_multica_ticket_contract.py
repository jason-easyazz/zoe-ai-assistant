from multica_ticket_contract import (
    append_child_id,
    describe_ticket,
    parse_ticket_block,
    update_ticket_progress,
    write_ticket_block,
)


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
