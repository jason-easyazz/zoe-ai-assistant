"""
Phase 5: Prometheus Metrics Middleware
Tracks request latency, counts, LLM calls, and memory searches
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import time
from typing import Callable

# Metrics
request_count = Counter(
    'zoe_requests_total',
    'Total requests',
    ['method', 'endpoint', 'status']
)

request_latency = Histogram(
    'zoe_request_duration_seconds',
    'Request latency',
    ['method', 'endpoint'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)

active_users = Gauge(
    'zoe_active_users',
    'Currently active users'
)

memory_searches = Counter(
    'zoe_memory_searches_total',
    'Memory searches',
    ['method', 'success']
)

llm_calls = Counter(
    'zoe_llm_calls_total',
    'LLM API calls',
    ['model', 'cached']
)

llm_latency = Histogram(
    'zoe_llm_duration_seconds',
    'LLM call latency',
    ['model'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Record metrics
        request_count.labels(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code
        ).inc()
        
        request_latency.labels(
            method=request.method,
            endpoint=request.url.path
        ).observe(duration)
        
        return response


def get_metrics() -> str:
    """Get Prometheus metrics"""
    return generate_latest().decode('utf-8')
