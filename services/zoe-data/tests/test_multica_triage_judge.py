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
    _admit_reviewed_ref_ok,
    _confidence_ok,
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
                "confidence": 0.9,
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
        "confidence",
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
# False-admit guards: confidence gate (fix 1)
# --------------------------------------------------------------------------- #


def _admit_raw(**overrides):
    raw = {
        "disposition": "admit",
        "reason_code": ADMIT_REASON_CODE,
        "reason": "relevant",
        "confidence": 0.9,
    }
    raw.update(overrides)
    return raw


def _assert_needs_info(verdict):
    assert verdict.disposition == "reject"
    assert verdict.reason_code == FAIL_CLOSED_REASON_CODE
    assert verdict.is_admit is False
    # a fail-closed verdict never auto-closes a ticket
    assert disposition_action(verdict).target_status is None


def test_admit_missing_confidence_fails_closed():
    raw = _admit_raw()
    del raw["confidence"]
    _assert_needs_info(judge_ticket(_ticket(), classifier=_fake(raw), commit_sha=SHA))


def test_admit_nan_confidence_fails_closed():
    verdict = judge_ticket(
        _ticket(), classifier=_fake(_admit_raw(confidence=float("nan"))), commit_sha=SHA
    )
    _assert_needs_info(verdict)


def test_admit_positive_inf_confidence_fails_closed():
    verdict = judge_ticket(
        _ticket(), classifier=_fake(_admit_raw(confidence=float("inf"))), commit_sha=SHA
    )
    _assert_needs_info(verdict)


def test_admit_confidence_above_one_fails_closed():
    verdict = judge_ticket(
        _ticket(), classifier=_fake(_admit_raw(confidence=1.5)), commit_sha=SHA
    )
    _assert_needs_info(verdict)


def test_admit_boolean_confidence_fails_closed():
    # bool is a subclass of int; True must NOT be treated as a 1.0 confidence.
    verdict = judge_ticket(
        _ticket(), classifier=_fake(_admit_raw(confidence=True)), commit_sha=SHA
    )
    _assert_needs_info(verdict)


# --------------------------------------------------------------------------- #
# False-admit guards: reviewed_ref binding (fix 2)
# --------------------------------------------------------------------------- #


def test_admit_missing_ticket_ref_fails_closed():
    # ticket has no reference/identifier/id -> no concrete ref -> never admit.
    verdict = judge_ticket(
        {"zoe_kind": "bug", "title": "t"}, classifier=_fake(_admit_raw()), commit_sha=SHA
    )
    _assert_needs_info(verdict)


@pytest.mark.parametrize("bad_sha", ["", "   ", "nothex!!", "12345", "ZZZZZZZ", "g" * 40])
def test_admit_blank_or_malformed_sha_fails_closed(bad_sha):
    verdict = judge_ticket(_ticket(), classifier=_fake(_admit_raw()), commit_sha=bad_sha)
    _assert_needs_info(verdict)


def test_admit_short_hex_sha_is_accepted():
    # a 7-char short SHA is valid per ^[0-9a-f]{7,40}$
    verdict = judge_ticket(
        _ticket(reference="ZOE-1"), classifier=_fake(_admit_raw()), commit_sha="abc1234"
    )
    assert verdict.disposition == "admit"
    assert verdict.reviewed_ref == "ZOE-1@abc1234"


# --------------------------------------------------------------------------- #
# Structural invariant (fix 3): every successful admit is well-formed, and a
# malformed admit cannot be constructed directly.
# --------------------------------------------------------------------------- #


def test_every_successful_admit_has_valid_confidence_and_concrete_reviewed_ref():
    verdict = judge_ticket(_ticket(reference="ZOE-42"), classifier=_fake(_admit_raw()), commit_sha=SHA)
    assert verdict.disposition == "admit"
    # confidence carried, valid, and within the admit band
    assert _confidence_ok(verdict.confidence)
    # reviewed_ref is concrete: <ticket-ref>@<valid-sha>, no 'unknown'
    assert _admit_reviewed_ref_ok(verdict.reviewed_ref)
    ref, _, sha = verdict.reviewed_ref.partition("@")
    assert ref == "ZOE-42"
    assert sha == SHA


@pytest.mark.parametrize(
    "kwargs",
    [
        {"confidence": None},  # missing confidence
        {"confidence": 0.1},  # below MIN_CONFIDENCE
        {"confidence": float("nan")},  # NaN
        {"confidence": float("inf")},  # +inf
        {"confidence": 1.5},  # > 1.0
        {"confidence": True},  # boolean
        {"confidence": 0.9, "reviewed_ref": "unknown@unknown"},  # no concrete ref
        {"confidence": 0.9, "reviewed_ref": "ZOE-1@unknown"},  # invalid sha
        {"confidence": 0.9, "reviewed_ref": "ZOE-1@nothex"},  # malformed sha
    ],
)
def test_admit_verdict_cannot_be_constructed_malformed(kwargs):
    base = dict(
        disposition="admit",
        reason="relevant",
        reason_code=ADMIT_REASON_CODE,
        evidence=[],
        zoe_kind="bug",
        reviewed_ref=f"ZOE-1@{SHA}",
        confidence=0.9,
    )
    base.update(kwargs)
    with pytest.raises(ValueError):
        TriageVerdict(**base)


def test_reject_verdict_needs_no_confidence_or_concrete_ref():
    # the structural guard applies to admits only
    verdict = TriageVerdict(
        disposition="reject",
        reason="held",
        reason_code=FAIL_CLOSED_REASON_CODE,
        evidence=[],
        zoe_kind="bug",
        reviewed_ref="unknown@unknown",
    )
    assert verdict.disposition == "reject"
    assert verdict.confidence is None


# --------------------------------------------------------------------------- #
# Carry-through fields
# --------------------------------------------------------------------------- #


def test_zoe_kind_carried_through_from_ticket():
    verdict = judge_ticket(
        _ticket(zoe_kind="harness_fix"),
        classifier=_fake(
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant",
                "confidence": 0.9,
            }
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
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant",
                "confidence": 0.9,
            }
        ),
        commit_sha=SHA,
    )
    assert verdict.reviewed_ref == f"ZOE-777@{SHA}"


def test_reviewed_ref_falls_back_to_identifier_then_id():
    verdict = judge_ticket(
        {"identifier": "ZOE-555", "id": "uuid-1"},
        classifier=_fake(
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant",
                "confidence": 0.9,
            }
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
            {
                "disposition": "admit",
                "reason_code": ADMIT_REASON_CODE,
                "reason": "relevant",
                "confidence": 0.9,
            }
        ),
        commit_sha=SHA,
    )
    assert isinstance(verdict, TriageVerdict)
    assert verdict.disposition == "admit"
