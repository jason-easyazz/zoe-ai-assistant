from routers.chat import _extract_memory_candidates


def test_extracts_preference_signal():
    results = _extract_memory_candidates(
        "I prefer quiet mornings for deep work",
        "Noted. I can help protect focus time.",
    )
    assert results
    assert any(item["memory_type"] == "preference" for item in results)


def test_no_signal_returns_empty():
    results = _extract_memory_candidates(
        "Can you show my calendar?",
        "Here is your calendar.",
    )
    assert results == []
