import greptile_client


def test_parse_confidence_score_from_nested_sources():
    payload = {
        "description": "No score here",
        "codeReviews": [{"body": "Confidence Score: 4/5\nLooks close"}],
    }

    assert greptile_client.parse_confidence_score(payload) == 4


def test_parse_confidence_score_prefers_direct_numeric_value():
    assert greptile_client.parse_confidence_score({"confidenceScore": 5}) == 5


def test_normalize_pr_comment_maps_common_fields():
    comment = {
        "id": 123,
        "filePath": "services/zoe-data/example.py",
        "lineStart": 42,
        "lineEnd": 43,
        "body": "Fix this",
        "suggestedCode": "pass",
        "url": "https://github.example/comment",
    }

    normalized = greptile_client.normalize_pr_comment(comment)

    assert normalized["id"] == "123"
    assert normalized["file_path"] == "services/zoe-data/example.py"
    assert normalized["line"] == 42
    assert normalized["line_end"] == 43
    assert normalized["url"] == "https://github.example/comment"
    assert normalized["has_suggestion"] is True


def test_review_is_running_detects_pending_states():
    assert greptile_client.review_is_running({"reviewCompleteness": "in_progress"}) is True
    assert greptile_client.review_is_running({"codeReviews": [{"status": "REVIEWING_FILES"}]}) is True
    assert greptile_client.review_is_running({"reviewCompleteness": "reviewed"}) is False
