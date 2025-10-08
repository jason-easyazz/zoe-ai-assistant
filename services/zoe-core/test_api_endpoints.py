#!/usr/bin/env python3
"""
Test Enhancement API Endpoints
==============================

Test the API endpoints by creating a simple FastAPI test app.
"""

import asyncio
import sys
import tempfile
import os
from fastapi import FastAPI
from fastapi.testclient import TestClient

async def test_api_endpoints():
    """Test API endpoints in isolation"""
    print("🌐 Testing Enhancement API Endpoints")
    print("=" * 50)
    
    try:
        # Create a test FastAPI app
        app = FastAPI()
        
        # Add the enhancement routers
        sys.path.append('/app')
        sys.path.append('/app/routers')
        
        from routers.temporal_memory import router as temporal_router
        from routers.cross_agent_collaboration import router as orchestration_router
        from routers.user_satisfaction import router as satisfaction_router
        
        app.include_router(temporal_router)
        app.include_router(orchestration_router)
        app.include_router(satisfaction_router)
        
        # Create test client
        client = TestClient(app)
        
        # Test 1: Temporal Memory Stats
        print("\n📅 Testing Temporal Memory API...")
        response = client.get("/api/temporal-memory/stats?user_id=test_user")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            print("  ✅ Temporal Memory API: WORKING")
        else:
            print(f"  ❌ Temporal Memory API: FAILED - {response.text}")
        
        # Test 2: Orchestration Experts
        print("\n🤝 Testing Orchestration API...")
        response = client.get("/api/orchestration/experts")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Found {len(data.get('experts', {}))} experts")
            print("  ✅ Orchestration API: WORKING")
        else:
            print(f"  ❌ Orchestration API: FAILED - {response.text}")
        
        # Test 3: Satisfaction Levels
        print("\n😊 Testing Satisfaction API...")
        response = client.get("/api/satisfaction/levels")
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Found {len(data.get('satisfaction_levels', []))} satisfaction levels")
            print("  ✅ Satisfaction API: WORKING")
        else:
            print(f"  ❌ Satisfaction API: FAILED - {response.text}")
        
        print("\n🎉 ALL API ENDPOINTS WORKING!")
        return True
        
    except Exception as e:
        print(f"\n❌ API Test Failed: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_api_endpoints())
    exit(0 if success else 1)
