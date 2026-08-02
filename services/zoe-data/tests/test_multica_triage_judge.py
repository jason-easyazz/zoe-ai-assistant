"""Unit tests for the TRIAGE-JUDGE (TT1) module — deterministic, fake classifier.

Covers: a clean ADMIT; each REJECT reason_code -> correct disposition params
(done/canceled + note + label) matching multica_apply_triage_dispositions.py;
malformed / low-confidence classifier output -> fail-closed non-admit;
zoe_kind carried through; reviewed_ref = ticket ref + commit SHA; and the
default-OFF feature flag.
"""

from __future__ import annotations

import pytest

from multica_triage_judge import (
    ADMIT_REASON_CODE,
    FAIL_CLOSED_REASON_CODE,
    MIN_CONFIDENCE,
    REJECT_REASON_CODES,
    DispositionAction,
    TriageVerdict,
    disposition_action,
    judge_ticket,
    run_triage,
    triage_judge_enabled,
)

pytestmark = pytest.mark.ci_safe

SHA = "0123456789abcdef0123456789abcdef01234567"


def _ticket(**overrides):
    base = {
        "reference": "ZOE-9001",
        "title": "Intent gap: 'what time is it'",
        "description": "backlog ticket body",
        "metadata": {},
        "zoe_kind": "bug",
        "acceptance_criteria": ["clock intent answers"],
        "evidence_expectations": ["focused test"],
    }
    base.update(overrides)
    return base


def _fake(result):
    """Build an injectable classifier returning a fixed raw result."""

    def _classifier(_ticket):
        return result

    return _classifier


def _raising(exc):
    def _classifier(_ticket):
        raise exc

    return _classifier


# --------------------------------------------------------------------------- #
# ADMIT
# --------------------------------------------------------------------------- #


def test_clean_admit():
    verdict = judge_ticket(
        _ticket(),
        classifier=_fake(
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant, actionable bug with acceptance criteria",
                "evidence": [],
                "confidence": 0.92,
            }
        ),
        commit_sha=SHA,
    )
    assert isinstance(verdict, TriageVerdict)
    assert verdict.disposition == "admit"
    assert verdict.is_admit is True
    assert verdict.reason_code == ADMIT_REASON_CODE
    # admit produces no board mutation params
    assert disposition_action(verdict) is None


def test_admit_carries_evidence_and_serializes():
    verdict = judge_ticket(
        _ticket(),
        classifier=_fake(
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant",
                "evidence": [{"kind": "repro", "ref": "log#42"}],
            }
        ),
        commit_sha=SHA,
    )
    d = verdict.to_dict()
    assert d["disposition"] == "admit"
    assert d["evidence"] == [{"kind": "repro", "ref": "log#42"}]
    assert set(d) == {
        "disposition",
        "reason",
        "reason_code",
        "evidence",
        "zoe_kind",
        "reviewed_ref",
    }


# --------------------------------------------------------------------------- #
# REJECT -> disposition params
# --------------------------------------------------------------------------- #

# reason_code -> expected board close status (matches _CLOSE_STATUS contract).
_EXPECTED_STATUS = {
    "duplicate": "done",
    "wont_fix": "done",
    "monitor": "done",
    "already_shipped": "done",
    "stale": "canceled",
    "not_reproducible": "canceled",
    "not_a_bug": "canceled",
    "out_of_scope": "canceled",
    "needs_info": None,
}


@pytest.mark.parametrize("code", sorted(REJECT_REASON_CODES))
def test_reject_disposition_params(code):
    reason = f"judged {code}"
    verdict = judge_ticket(
        _ticket(),
        classifier=_fake(
            {
                "disposition": "reject",
                "reason_code": code,
                "reason": reason,
                "evidence": ["PR #123"] if code == "already_shipped" else [],
                "confidence": 0.88,
            }
        ),
        commit_sha=SHA,
    )
    assert verdict.disposition == "reject"
    assert verdict.reason_code == code

    action = disposition_action(verdict)
    assert isinstance(action, DispositionAction)
    assert action.target_status == _EXPECTED_STATUS[code]
    # note + label match multica_apply_triage_dispositions.py EXACTLY
    assert action.note == f"Triage: {code} - {reason}"
    assert action.label == code.replace("_", "-")
    assert action.closes is (_EXPECTED_STATUS[code] is not None)


def test_reject_note_and_label_match_maintenance_script_for_legacy_lanes():
    # The three lanes the existing script already applies: done + label + note.
    for code in ("duplicate", "wont_fix", "monitor"):
        verdict = judge_ticket(
            _ticket(),
            classifier=_fake(
                {
                    "disposition": "reject",
                    "reason_code": code,
                    "reason": "keep newer sibling",
                }
            ),
            commit_sha=SHA,
        )
        action = disposition_action(verdict)
        assert action.target_status == "done"
        assert action.label == code.replace("_", "-")
        assert action.note == f"Triage: {code} - keep newer sibling"


def test_needs_info_holds_does_not_close():
    verdict = judge_ticket(
        _ticket(),
        classifier=_fake(
            {
                "disposition": "reject",
                "reason_code": "needs_info",
                "reason": "insufficient detail to reproduce",
            }
        ),
        commit_sha=SHA,
    )
    action = disposition_action(verdict)
    assert action.target_status is None
    assert action.closes is False


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not a dict",
        42,
        {},  # no disposition
        {"disposition": "maybe", "reason_code": "relevant", "reason": "x"},
        {"disposition": "admit", "reason_code": "bogus", "reason": "x"},
        {"disposition": "admit", "reason_code": "duplicate", "reason": "x"},  # admit w/ reject code
        {"disposition": "reject", "reason_code": "relevant", "reason": "x"},  # reject w/ admit code
        {"disposition": "reject", "reason_code": "stale", "reason": ""},  # empty reason
        {"disposition": "reject", "reason_code": "stale"},  # missing reason
    ],
)
def test_malformed_output_fails_closed(bad):
    verdict = judge_ticket(_ticket(), classifier=_fake(bad), commit_sha=SHA)
    assert verdict.disposition == "reject"
    assert verdict.reason_code == FAIL_CLOSED_REASON_CODE
    assert verdict.is_admit is False
    # fail-closed never auto-closes a ticket
    assert disposition_action(verdict).target_status is None


def test_classifier_exception_fails_closed():
    verdict = judge_ticket(_ticket(), classifier=_raising(RuntimeError("boom")), commit_sha=SHA)
    assert verdict.disposition == "reject"
    assert verdict.reason_code == FAIL_CLOSED_REASON_CODE
    assert "boom" in verdict.reason


def test_low_confidence_fails_closed():
    verdict = judge_ticket(
        _ticket(),
        classifier=_fake(
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "looks relevant but unsure",
                "confidence": MIN_CONFIDENCE - 0.01,
            }
        ),
        commit_sha=SHA,
    )
    assert verdict.disposition == "reject"
    assert verdict.reason_code == FAIL_CLOSED_REASON_CODE
    assert verdict.is_admit is False


def test_unparseable_confidence_fails_closed():
    verdict = judge_ticket(
        _ticket(),
        classifier=_fake(
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant",
                "confidence": "high",
            }
        ),
        commit_sha=SHA,
    )
    assert verdict.reason_code == FAIL_CLOSED_REASON_CODE


# --------------------------------------------------------------------------- #
# Carry-through fields
# --------------------------------------------------------------------------- #


def test_zoe_kind_carried_through_from_ticket():
    verdict = judge_ticket(
        _ticket(zoe_kind="harness_fix"),
        classifier=_fake(
            {"disposition": "admit", "reason_code": ADMIT_REASON_CODE, "reason": "relevant"}
        ),
        commit_sha=SHA,
    )
    assert verdict.zoe_kind == "harness_fix"


def test_zoe_kind_carried_through_on_fail_closed():
    verdict = judge_ticket(_ticket(zoe_kind="feature"), classifier=_fake(None), commit_sha=SHA)
    assert verdict.reason_code == FAIL_CLOSED_REASON_CODE
    assert verdict.zoe_kind == "feature"


def test_classifier_may_refine_zoe_kind():
    verdict = judge_ticket(
        _ticket(zoe_kind="operator_task"),
        classifier=_fake(
            {
                "disposition": "reject",
                "reason_code": "not_a_bug",
                "reason": "this is a feature request",
                "zoe_kind": "feature",
            }
        ),
        commit_sha=SHA,
    )
    assert verdict.zoe_kind == "feature"


def test_reviewed_ref_has_ticket_ref_and_sha():
    verdict = judge_ticket(
        _ticket(reference="ZOE-777"),
        classifier=_fake(
            {"disposition": "admit", "reason_code": ADMIT_REASON_CODE, "reason": "relevant"}
        ),
        commit_sha=SHA,
    )
    assert verdict.reviewed_ref == f"ZOE-777@{SHA}"


def test_reviewed_ref_falls_back_to_identifier_then_id():
    verdict = judge_ticket(
        {"identifier": "ZOE-555", "id": "uuid-1"},
        classifier=_fake(
            {"disposition": "admit", "reason_code": ADMIT_REASON_CODE, "reason": "relevant"}
        ),
        commit_sha=SHA,
    )
    assert verdict.reviewed_ref == f"ZOE-555@{SHA}"


# --------------------------------------------------------------------------- #
# Feature flag (default OFF)
# --------------------------------------------------------------------------- #


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_MULTICA_TRIAGE_JUDGE", raising=False)
    assert triage_judge_enabled() is False


def test_run_triage_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("ZOE_MULTICA_TRIAGE_JUDGE", raising=False)
    called = {"n": 0}

    def _classifier(_ticket):
        called["n"] += 1
        return {"disposition": "admit", "reason_code": ADMIT_REASON_CODE, "reason": "relevant"}

    assert run_triage(_ticket(), classifier=_classifier, commit_sha=SHA) is None
    assert called["n"] == 0  # classifier never invoked when the feature is off


def test_run_triage_runs_when_enabled(monkeypatch):
    monkeypatch.setenv("ZOE_MULTICA_TRIAGE_JUDGE", "true")
    verdict = run_triage(
        _ticket(),
        classifier=_fake(
            {"disposition": "admit", "reason_code": ADMIT_REASON_CODE, "reason": "relevant"}
        ),
        commit_sha=SHA,
    )
    assert isinstance(verdict, TriageVerdict)
    assert verdict.disposition == "admit"
