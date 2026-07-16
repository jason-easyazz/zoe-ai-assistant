import pytest
from zoe_memory_layers import (
    ASYNC_ENRICHMENT_LAYERS,
    FAST_CHAT_LAYERS,
    LAYER_POLICIES,
    MemoryLayer,
    layer_policy,
)
from zoe_memory_router import MemoryBackend, route_memory_query

pytestmark = pytest.mark.ci_safe


def test_fast_chat_layers_are_boring_hot_path_only():
    assert FAST_CHAT_LAYERS == (
        MemoryLayer.WORKING_CONTEXT,
        MemoryLayer.CANONICAL_STATE,
        MemoryLayer.EPISODIC_MEMORY,
    )
    assert all(layer_policy(layer).hot_path for layer in FAST_CHAT_LAYERS)


def test_sidecar_layers_are_async_enrichment_not_required_for_chat():
    assert MemoryLayer.REFLECTIVE_MEMORY in ASYNC_ENRICHMENT_LAYERS
    assert MemoryLayer.RELATIONAL_TEMPORAL_MEMORY in ASYNC_ENRICHMENT_LAYERS
    assert not layer_policy(MemoryLayer.REFLECTIVE_MEMORY).hot_path
    assert not layer_policy(MemoryLayer.RELATIONAL_TEMPORAL_MEMORY).hot_path


def test_each_layer_has_failure_policy():
    assert {policy.layer for policy in LAYER_POLICIES} == set(MemoryLayer)
    assert all(policy.failure_policy for policy in LAYER_POLICIES)


def test_default_chat_route_keeps_hindsight_and_graphiti_off_hot_path():
    route = route_memory_query("What do I usually like for breakfast?")

    assert route.primary == MemoryBackend.MEMPALACE
    assert route.memory_layers == FAST_CHAT_LAYERS
    assert MemoryBackend.HINDSIGHT not in route.secondary
    assert MemoryBackend.GRAPHITI not in route.secondary
    assert "never slow chat" in route.observation_policy


def test_reflective_route_is_timeout_bounded_candidate_only():
    route = route_memory_query("What did Zoe learn from that outcome pattern?")

    assert route.primary == MemoryBackend.HINDSIGHT
    assert MemoryLayer.OBSERVATION_EVALUATION in route.memory_layers
    assert "candidate" in route.write_policy


def test_failure_and_fix_queries_route_to_reflective_memory():
    route = route_memory_query("What fix worked for the recurring weather failure?")

    assert route.primary == MemoryBackend.HINDSIGHT
    assert MemoryLayer.REFLECTIVE_MEMORY in route.memory_layers
    assert MemoryBackend.GRAPHITI not in route.secondary


def test_supersession_queries_route_to_relational_memory():
    route = route_memory_query("Which fix superseded the old voice failure?")

    assert route.primary == MemoryBackend.GRAPHITI
    assert MemoryLayer.RELATIONAL_TEMPORAL_MEMORY in route.memory_layers
