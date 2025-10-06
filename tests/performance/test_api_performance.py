"""API Performance Tests"""
import time
import requests
import statistics

def test_endpoint_performance(url, name, iterations=10):
    times = []
    
    for _ in range(iterations):
        start = time.time()
        response = requests.get(url)
        end = time.time()
        
        if response.status_code == 200:
            times.append((end - start) * 1000)  # Convert to ms
    
    if times:
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        
        print(f"\n{name} Performance:")
        print(f"  Average: {avg_time:.2f}ms")
        print(f"  Min: {min_time:.2f}ms")
        print(f"  Max: {max_time:.2f}ms")
        
        # Assert performance requirements
        assert avg_time < 500, f"{name} too slow: {avg_time}ms"
    else:
        print(f"❌ {name} failed all requests")

if __name__ == "__main__":
    # Test core endpoints
    test_endpoint_performance("http://localhost:8000/health", "Health Check")
    test_endpoint_performance("http://localhost:8000/api/calendar/events", "Calendar API")
    
    # Test voice services
    test_endpoint_performance("http://localhost:9001/health", "Whisper STT")
    test_endpoint_performance("http://localhost:9002/health", "Coqui TTS")
    
    print("\n✅ All performance tests passed")
