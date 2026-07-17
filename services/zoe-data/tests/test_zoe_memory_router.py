import pytest
from zoe_memory_router import MemoryBackend, route_memory_query

pytestmark = pytest.mark.ci_safe


def test_default_chat_uses_mempalace_fast_path():
    route = route_memory_query("What do I usually like for breakfast?")

    assert route.primary == MemoryBackend.MEMPALACE
    assert route.latency_budget_ms == 300


def test_experience_queries_route_to_hindsight():
    route = route_memory_query("What did Zoe learn from the last outcome?")

    assert route.primary == MemoryBackend.HINDSIGHT
    assert route.write_policy == "retain candidates only; auto-retain disabled by default"


def test_relational_queries_route_to_graphiti():
    route = route_memory_query("Which fix superseded the old voice failure?")

    assert route.primary == MemoryBackend.GRAPHITI
    assert MemoryBackend.HINDSIGHT in route.secondary
    assert "evidence" in route.write_policy


def test_mixed_code_noun_failure_queries_route_to_hindsight():
    route = route_memory_query("What fix worked for the recurring service failure?")

    assert route.primary == MemoryBackend.HINDSIGHT
    assert route.latency_budget_ms == 600


def test_code_questions_route_to_graphify():
    route = route_memory_query("Which router owns chat in the Zoe repo?")

    assert route.primary == MemoryBackend.GRAPHIFY
    assert route.write_policy.startswith("read-only")


def test_self_evolution_routes_through_multica():
    route = route_memory_query("Create an upgrade proposal for a new capability")

    assert route.primary == MemoryBackend.MULTICA
    assert route.secondary == (
        MemoryBackend.HINDSIGHT,
        MemoryBackend.GRAPHITI,
        MemoryBackend.GRAPHIFY,
    )


def test_router_uses_word_boundaries_for_slow_backend_terms():
    examples = [
        "What is the prefix for voice commands?",
        "Show me fixture data",
        "Can you encode this text?",
        "Show this paragraph spacing",
    ]

    for query in examples:
        assert route_memory_query(query).primary == MemoryBackend.MEMPALACE


def test_casual_service_language_stays_on_fast_chat_path():
    examples = [
        "I like the breakfast service at that cafe",
        "The breakfast service runs from 7am",
        "The hotel service was great",
        "My cleaning service handles the laundry",
        "The cleaning services are expensive",
        "What service can you recommend for laundry?",
        "Which service is best for house cleaning?",
        "Which services are nearby?",
        "Remind me to compliment the service team",
    ]

    for query in examples:
        assert route_memory_query(query).primary == MemoryBackend.MEMPALACE


def test_code_service_context_still_routes_to_graphify():
    examples = [
        "Which service owns chat routing?",
        "Which services depend on memory?",
        "What service handles memory writes?",
        "What services own reminders?",
        "Show the backend service dependency map",
        "Which Zoe service handles auth?",
        "What data service feeds the chat router?",
        "Show service module boundaries",
        "Find the service router for voice",
        "Which service owns the calendar sync?",
    ]

    for query in examples:
        assert route_memory_query(query).primary == MemoryBackend.GRAPHIFY
