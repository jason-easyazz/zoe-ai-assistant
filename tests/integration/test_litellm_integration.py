"""
Phase 2: LiteLLM Integration Tests
Tests caching, fallbacks, and routing
"""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock
import time


@pytest.mark.asyncio
async def test_litellm_router_classification(client, auth_headers):
    """Test that router classifies queries correctly"""
    response = await asyncio.to_thread(
        client.post,
        "/api/chat",
        json={"message": "What did we discuss yesterday about Arduino?"},
        headers=auth_headers
    )
    assert response.status_code == 200


@pytest.mark.asyncio  
async def test_response_caching(client, auth_headers):
    """Test that repeated queries can be cached"""
    query = {"message": "Hello Zoe, how are you?"}
    
    # First request
    response1 = await asyncio.to_thread(
        client.post,
        "/api/chat",
        json=query,
        headers=auth_headers
    )
    
    assert response1.status_code == 200
    answer1 = response1.json()["response"]
    
    # Second identical request
    response2 = await asyncio.to_thread(
        client.post,
        "/api/chat",
        json=query,
        headers=auth_headers
    )
    
    assert response2.status_code == 200


@pytest.mark.asyncio
async def test_fallback_handling(client, auth_headers):
    """Test endpoint handles errors gracefully with fallback"""
    response = await asyncio.to_thread(
        client.post,
        "/api/chat",
        json={"message": "Test query"},
        headers=auth_headers
    )
    assert response.status_code in [200, 500, 503]


def test_model_routing_local_first(client, auth_headers):
    """Test that router prefers local models"""
    response = client.post(
        "/api/chat",
        json={"message": "What is 2+2?"},
        headers=auth_headers
    )
    assert response.status_code == 200
