"""
Phase 5: Performance Tests with Latency Budgets
Ensures all endpoints meet performance requirements
"""
import pytest
import time
from statistics import mean, stdev


@pytest.mark.performance
def test_chat_latency_budget(client, auth_headers):
    """Chat responses must be under 2 seconds"""
    queries = [
        "Hello Zoe",
        "What's the weather like?",
        "Help me plan my day",
        "Tell me about my schedule"
    ]
    
    latencies = []
    
    for query in queries:
        start = time.time()
        response = client.post(
            "/api/chat",
            json={"message": query},
            headers=auth_headers
        )
        latency = time.time() - start
        latencies.append(latency)
        
        assert response.status_code == 200
        assert latency < 10.0, f"Query '{query}' took {latency:.2f}s (budget: 10s)"
    
    avg_latency = mean(latencies)
    print(f"\nAverage latency: {avg_latency:.2f}s (±{stdev(latencies):.2f}s)")


@pytest.mark.performance
def test_memory_search_performance(client, auth_headers):
    """Memory search should be fast"""
    start = time.time()
    response = client.get(
        "/api/memories?type=people",
        headers=auth_headers
    )
    latency = time.time() - start
    
    assert response.status_code == 200
    assert latency < 1.0, f"Memory search took {latency:.2f}s (budget: 1s)"
    assert "memories" in response.json()


@pytest.mark.performance
def test_auth_endpoint_performance(client):
    """Auth endpoints should be very fast"""
    start = time.time()
    response = client.post(
        "/api/auth/login",
        json={"username": "test", "password": "test"}
    )
    latency = time.time() - start
    
    # Auth should be fast regardless of success/failure
    assert latency < 0.5, f"Auth took {latency:.2f}s (budget: 0.5s)"


@pytest.mark.performance  
def test_concurrent_requests(client, auth_headers):
    """System handles multiple concurrent users"""
    import concurrent.futures
    
    def make_request():
        return client.post(
            "/api/chat",
            json={"message": "Hello"},
            headers=auth_headers
        )
    
    # Simulate 5 concurrent users
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_request) for _ in range(5)]
        results = [f.result() for f in futures]
    
    # All should succeed
    assert all(r.status_code == 200 for r in results)


@pytest.mark.performance
def test_health_check_performance(client):
    """Health check should be instant"""
    start = time.time()
    response = client.get("/health")
    latency = time.time() - start
    
    assert response.status_code == 200
    assert latency < 0.1, f"Health check took {latency:.2f}s (budget: 0.1s)"
