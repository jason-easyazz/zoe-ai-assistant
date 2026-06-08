from samantha_memory_router import MemoryBackend, route_memory_query


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
