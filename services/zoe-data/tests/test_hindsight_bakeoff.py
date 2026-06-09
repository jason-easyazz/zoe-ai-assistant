from hindsight_bakeoff import EVAL_QUERIES, SYNTHETIC_EVENTS, score_recall_response, synthetic_retain_payloads


def test_synthetic_events_validate_and_include_required_cases():
    payloads = synthetic_retain_payloads()

    assert len(payloads) == len(SYNTHETIC_EVENTS) >= 4
    assert {item["event_type"] for item in payloads} >= {"failure", "fix", "preference", "approval"}
    assert all(item["user_id"] for item in payloads)
    assert all(item["evidence_refs"] for item in payloads)


def test_eval_queries_cover_plan_questions():
    names = {query.name for query in EVAL_QUERIES}

    assert names == {
        "recall_weather_failure",
        "recall_weather_fix",
        "recall_user_preference",
        "recall_governance",
    }


def test_score_recall_response_reports_missing_terms():
    query = EVAL_QUERIES[0]
    score = score_recall_response({"results": [{"text": "weather card had duplicate responses"}]}, query)

    assert score["score"] == 2 / 3
    assert score["missing"] == ["voice"]


def test_synthetic_evidence_refs_are_tuples_not_strings():
    for event in SYNTHETIC_EVENTS:
        assert isinstance(event.evidence_refs, tuple)
        assert all(isinstance(ref, str) for ref in event.evidence_refs)
        assert all(len(ref) > 4 for ref in event.evidence_refs)
