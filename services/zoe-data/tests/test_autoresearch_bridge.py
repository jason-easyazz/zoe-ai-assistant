"""Tests for the autoresearch -> governed-pipeline promotion bridge."""

from autoresearch_bridge import (
    build_audit_ticket,
    build_pr_body,
    decide_promotion,
    parse_program,
    parse_results,
    prepare_promotion,
    summarize_run,
)
from multica_admission import ticket_is_dispatch_approved
from multica_ticket_contract import parse_ticket_block


PROGRAM = """# Auto Research Run: demo

Goal: improve the onboarding email open rate.
Why: more activated users.

Locked rules:
- Editable asset allowlist: copy/onboarding_email.md and copy/subject.md only.
- Locked scoring command: python3 score.py
- Higher score is better.
- Run model: deepseek/deepseek-chat-v3.1
- Target score: 80

Stop condition: after 5 rounds or score reaches 80.
"""

RESULTS = "round\tcommit\tscore\tstatus\tdescription\n" + "\n".join(
    [
        "1\tc0\t50\tbaseline\tcurrent copy",
        "2\tc1\t40\tdiscard\tshorter subject",
        "3\tc2\t62\tkeep\tadd urgency line",
        "4\tc3\t62\tdiscard\tno-op reword",
        "5\tc4\t71\tkeep\tpersonalize greeting",
    ]
)


def test_parse_program_extracts_contract_and_model():
    program = parse_program(PROGRAM)
    assert program.goal == "improve the onboarding email open rate."
    assert program.why == "more activated users."
    assert program.scoring_command == "python3 score.py"
    assert program.higher_is_better is True
    assert program.model == "deepseek/deepseek-chat-v3.1"
    assert program.target_score == 80
    assert program.asset_paths == ["copy/onboarding_email.md", "copy/subject.md"]


def test_parse_program_defaults_model_none_and_respects_lower_is_better():
    program = parse_program(
        "Goal: cut p95 latency.\nLower score is better.\n"
        "Editable asset allowlist: config/tuning.json only."
    )
    assert program.model is None
    assert program.higher_is_better is False
    assert program.asset_paths == ["config/tuning.json"]


def test_summarize_run_tracks_best_and_net_improvement():
    summary = summarize_run(parse_results(RESULTS), higher_is_better=True)
    assert summary.rounds == 5
    assert summary.baseline_score == 50
    assert summary.kept == 2
    assert summary.discarded == 2
    assert summary.best_score == 71
    assert summary.final_score == 71
    assert summary.improved is True
    assert summary.net_improvement == 21
    assert summary.best_hypothesis == "personalize greeting"
    assert summary.keeper_commits == ["c2", "c4"]


def test_summarize_run_lower_is_better_direction():
    rows = parse_results(
        "round\tcommit\tscore\tstatus\tdescription\n"
        "1\tc0\t100\tbaseline\tbase\n"
        "2\tc1\t80\tkeep\tfaster path\n"
    )
    summary = summarize_run(rows, higher_is_better=False)
    assert summary.best_score == 80
    assert summary.improved is True
    assert summary.net_improvement == -20


def test_decide_promotion_promotes_real_improvement():
    program = parse_program(PROGRAM)
    summary = summarize_run(parse_results(RESULTS), higher_is_better=True)
    decision = decide_promotion(program, summary)
    assert decision["promote"] is True
    assert "net improvement" in decision["reason"]


def test_decide_promotion_blocks_flat_run():
    program = parse_program(PROGRAM)
    flat = summarize_run(
        parse_results(
            "round\tcommit\tscore\tstatus\tdescription\n"
            "1\tc0\t50\tbaseline\tbase\n"
            "2\tc1\t49\tdiscard\tworse\n"
        ),
        higher_is_better=True,
    )
    decision = decide_promotion(program, flat)
    assert decision["promote"] is False
    assert "no kept rounds" in decision["reason"]


def test_decide_promotion_blocks_when_no_asset_allowlist():
    program = parse_program("Goal: x\nHigher is better.")  # no allowlist
    summary = summarize_run(parse_results(RESULTS), higher_is_better=True)
    decision = decide_promotion(program, summary)
    assert decision["promote"] is False
    assert "asset allowlist" in decision["reason"]


def test_pr_body_reports_scores_assets_and_model():
    program = parse_program(PROGRAM)
    summary = summarize_run(parse_results(RESULTS), higher_is_better=True)
    body = build_pr_body("jun11-demo", program, summary)
    assert "run `jun11-demo`" in body
    assert "deepseek/deepseek-chat-v3.1" in body
    assert "Baseline score: 50" in body
    assert "Final score: 71" in body
    assert "`copy/onboarding_email.md`" in body
    assert "Greptile" in body


def test_audit_ticket_is_never_dispatch_approved():
    program = parse_program(PROGRAM)
    summary = summarize_run(parse_results(RESULTS), higher_is_better=True)
    description = build_audit_ticket(
        "jun11-demo", program, summary, pr_url="https://github/pr/1"
    )

    meta = parse_ticket_block(description)
    assert meta["zoe_kind"] == "autoresearch_audit"
    assert meta["source"] == "autoresearch_audit:jun11-demo"
    assert meta["autoresearch_net_improvement"] == 21
    assert meta["autoresearch_pr_url"] == "https://github/pr/1"

    # The crucial safety property: an audit record is inert at the dispatch gate.
    issue = {
        "assignee_id": "hermes-agent",
        "assignee_type": "agent",
        "description": description,
    }
    assert ticket_is_dispatch_approved(issue, hermes_agent_id="hermes-agent") is False


def test_prepare_promotion_reads_run_dir(tmp_path):
    run_dir = tmp_path / "jun11-demo"
    run_dir.mkdir()
    (run_dir / "program.md").write_text(PROGRAM, encoding="utf-8")
    (run_dir / "results.tsv").write_text(RESULTS, encoding="utf-8")

    plan = prepare_promotion(run_dir, pr_url="https://github/pr/9")
    assert plan["run_id"] == "jun11-demo"
    assert plan["promote"] is True
    assert plan["model"] == "deepseek/deepseek-chat-v3.1"
    assert plan["pr_title"] == "autoresearch(jun11-demo): promote validated keeper"
    assert "Final score: 71" in plan["pr_body"]
    assert parse_ticket_block(plan["audit_description"])["source"] == (
        "autoresearch_audit:jun11-demo"
    )


def test_prepare_promotion_no_keeper_is_inert(tmp_path):
    run_dir = tmp_path / "jun11-flat"
    run_dir.mkdir()
    (run_dir / "program.md").write_text(PROGRAM, encoding="utf-8")
    (run_dir / "results.tsv").write_text(
        "round\tcommit\tscore\tstatus\tdescription\n1\tc0\t50\tbaseline\tbase\n",
        encoding="utf-8",
    )
    plan = prepare_promotion(run_dir)
    assert plan["promote"] is False
    assert "pr_body" not in plan
    assert "audit_description" not in plan
