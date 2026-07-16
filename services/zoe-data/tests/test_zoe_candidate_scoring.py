import pytest

from zoe_candidate_scoring import (
    EXAMPLE_CANDIDATES,
    CandidateEvaluation,
    CandidateScore,
    adoption_gate,
    rank_candidates,
)

pytestmark = pytest.mark.ci_safe


def test_candidate_score_validates_range_and_normalizes():
    score = CandidateScore(fit=5, activity=4, license=3, offline=5, security=4, footprint=3, tests=4, maintainability=4, overlap=5)

    assert score.total() == 37
    assert score.normalized() == 37 / 45


def test_candidate_score_rejects_out_of_range_values():
    score = CandidateScore(fit=6, activity=4, license=3, offline=5, security=4, footprint=3, tests=4, maintainability=4, overlap=5)

    with pytest.raises(ValueError, match="fit score must be between 0 and 5"):
        score.validate()


def test_example_candidates_validate_and_rank_mempalace_first():
    ranked = rank_candidates(EXAMPLE_CANDIDATES)

    assert ranked[0].candidate_id == "existing_mempalace"
    assert all(candidate.evidence_refs for candidate in ranked)


def test_adoption_gate_allows_strong_compatible_candidate():
    candidate = EXAMPLE_CANDIDATES[0]

    gate = adoption_gate(candidate)

    assert gate["allowed"] is True
    assert gate["blockers"] == []


def test_adoption_gate_blocks_pi_until_runtime_prerequisites_are_measured():
    candidate = EXAMPLE_CANDIDATES[2]

    gate = adoption_gate(candidate)

    assert gate["allowed"] is False
    assert gate["blockers"] == ["offline:partial", "score_below_threshold"]
    assert candidate.offline_viability == "partial"
    assert candidate.stars == 59100


def test_adoption_gate_blocks_partial_offline_without_ready_evidence():
    candidate = CandidateEvaluation(
        candidate_id="partial_no_evidence",
        name="Partial offline candidate",
        source="github",
        task="test",
        score=CandidateScore(fit=5, activity=5, license=5, offline=5, security=5, footprint=5, tests=5, maintainability=5, overlap=5),
        evidence_refs=("test:evidence",),
        license_risk="compatible",
        offline_viability="partial",
    )

    gate = adoption_gate(candidate)

    assert gate["allowed"] is False
    assert gate["blockers"] == ["offline:partial"]


def test_adoption_gate_allows_partial_offline_with_ready_evidence():
    candidate = CandidateEvaluation(
        candidate_id="partial_ready",
        name="Partial offline ready candidate",
        source="github",
        task="test",
        score=CandidateScore(fit=5, activity=5, license=5, offline=5, security=5, footprint=5, tests=5, maintainability=5, overlap=5),
        evidence_refs=("test:evidence",),
        license_risk="compatible",
        offline_viability="partial",
        metadata={"offline_ready": True},
    )

    gate = adoption_gate(candidate)

    assert gate["allowed"] is True
    assert gate["blockers"] == []


def test_adoption_gate_blocks_incompatible_license():
    candidate = CandidateEvaluation(
        candidate_id="bad_license",
        name="Bad license candidate",
        source="github",
        task="test",
        score=CandidateScore(fit=5, activity=5, license=0, offline=5, security=5, footprint=5, tests=5, maintainability=5, overlap=5),
        evidence_refs=("test:evidence",),
        license_risk="incompatible",
        offline_viability="required",
        recommendation="reject",
    )

    gate = adoption_gate(candidate)

    assert gate["allowed"] is False
    assert "license:incompatible" in gate["blockers"]


def test_candidate_validation_requires_evidence_refs():
    candidate = CandidateEvaluation(
        candidate_id="no_evidence",
        name="No evidence candidate",
        source="github",
        task="test",
        score=CandidateScore(fit=3, activity=3, license=3, offline=3, security=3, footprint=3, tests=3, maintainability=3, overlap=3),
        evidence_refs=(),
    )

    with pytest.raises(ValueError, match="evidence_refs are required"):
        candidate.validate()


def test_candidate_validation_rejects_unknown_recommendation():
    candidate = CandidateEvaluation(
        candidate_id="bad_recommendation",
        name="Bad recommendation candidate",
        source="github",
        task="test",
        score=CandidateScore(fit=3, activity=3, license=3, offline=3, security=3, footprint=3, tests=3, maintainability=3, overlap=3),
        evidence_refs=("test:evidence",),
        recommendation="trail_sidecar",
    )

    with pytest.raises(ValueError, match="unknown recommendation"):
        candidate.validate()


def test_candidate_to_dict_includes_total_and_normalized_score():
    payload = EXAMPLE_CANDIDATES[1].to_dict()

    assert payload["total_score"] == EXAMPLE_CANDIDATES[1].score.total()
    assert payload["normalized_score"] == EXAMPLE_CANDIDATES[1].score.normalized()
    assert payload["overlaps_existing"] == ["graphiti_relational_memory"]
