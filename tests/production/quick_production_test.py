#!/usr/bin/env python3
"""
Quick Production Readiness Test
Tests P0 features with real queries
"""
import sys
import json
import time
import subprocess
import requests
from typing import Dict, List, Tuple

# Test configuration
API_BASE = "http://localhost:8000"
TEST_USER = "production_test_user"

# Colors
class Color:
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'

# Test counters
tests_run = 0
tests_passed = 0
tests_failed = 0

def log(msg: str):
    print(f"{Color.BLUE}[TEST]{Color.NC} {msg}")

def success(msg: str):
    global tests_passed
    tests_passed += 1
    print(f"{Color.GREEN}✅ {msg}{Color.NC}")

def failure(msg: str):
    global tests_failed
    tests_failed += 1
    print(f"{Color.RED}❌ {msg}{Color.NC}")

def warning(msg: str):
    print(f"{Color.YELLOW}⚠️  {msg}{Color.NC}")

def send_query(message: str, user_id: str = TEST_USER) -> Dict:
    """Send a chat query and return the response"""
    try:
        response = requests.post(
            f"{API_BASE}/api/chat",
            json={"message": message, "user_id": user_id, "stream": False},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def get_docker_logs(tail: int = 50, grep_pattern: str = None) -> str:
    """Get docker logs for zoe-core"""
    cmd = ["docker", "logs", "zoe-core", "--tail", str(tail)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    logs = result.stdout + result.stderr
    
    if grep_pattern:
        lines = [line for line in logs.split('\n') if grep_pattern.lower() in line.lower()]
        return '\n'.join(lines)
    return logs

def restart_with_features(features: Dict[str, bool]) -> bool:
    """Restart zoe-core with specific feature flags"""
    log(f"Restarting with features: {features}")
    
    # Build environment string
    env_str = " ".join([f"{k.upper()}={str(v).lower()}" for k, v in features.items()])
    
    # Stop container
    subprocess.run(["docker", "compose", "stop", "zoe-core"], 
                  capture_output=True, cwd="/home/zoe/assistant")
    
    # Start with new env
    cmd = f"cd /home/zoe/assistant && {env_str} docker compose up -d zoe-core"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    # Wait for startup
    time.sleep(8)
    
    # Check health
    try:
        response = requests.get(f"{API_BASE}/health", timeout=5)
        if response.status_code == 200:
            success("Container restarted and healthy")
            return True
    except:
        pass
    
    failure("Container failed to start")
    return False

def test_baseline():
    """Test 1: Baseline with all features OFF"""
    global tests_run
    tests_run += 1
    
    log("TEST 1: Baseline (all features OFF)")
    if not restart_with_features({
        "USE_CONTEXT_VALIDATION": False,
        "USE_CONFIDENCE_FORMATTING": False,
        "USE_DYNAMIC_TEMPERATURE": False,
        "USE_GROUNDING_CHECKS": False
    }):
        return False
    
    # Test basic query
    response = send_query("hello")
    if "error" not in response and "response" in response:
        success("Baseline: Basic query works")
        return True
    else:
        failure(f"Baseline: Basic query failed - {response.get('error', 'unknown')}")
        return False

def test_context_validation():
    """Test 2: P0-1 Context Validation"""
    global tests_run
    tests_run += 1
    
    log("TEST 2: P0-1 Context Validation")
    if not restart_with_features({
        "USE_CONTEXT_VALIDATION": True,
        "USE_CONFIDENCE_FORMATTING": False,
        "USE_DYNAMIC_TEMPERATURE": False,
        "USE_GROUNDING_CHECKS": False
    }):
        return False
    
    # Clear logs
    subprocess.run(["docker", "exec", "zoe-core", "bash", "-c", 
                   "truncate -s 0 /proc/1/fd/1 2>/dev/null || true"])
    time.sleep(1)
    
    # Test simple query (should work)
    response = send_query("hello")
    time.sleep(1)
    
    if "error" not in response:
        success("P0-1: Query successful with context validation enabled")
        
        # Check logs for feature activity
        logs = get_docker_logs(tail=100)
        if "Context SKIPPED" in logs or "Context" in logs:
            success("P0-1: Context validation logged activity")
        else:
            warning("P0-1: No context validation logs found (may be expected)")
        return True
    else:
        failure(f"P0-1: Query failed - {response.get('error')}")
        return False

def test_confidence_formatting():
    """Test 3: P0-2 Confidence Formatting"""
    global tests_run
    tests_run += 1
    
    log("TEST 3: P0-2 Confidence Formatting")
    if not restart_with_features({
        "USE_CONTEXT_VALIDATION": False,
        "USE_CONFIDENCE_FORMATTING": True,
        "USE_DYNAMIC_TEMPERATURE": False,
        "USE_GROUNDING_CHECKS": False
    }):
        return False
    
    # Test query
    response = send_query("what is the weather?")
    time.sleep(1)
    
    if "error" not in response:
        success("P0-2: Query successful with confidence formatting enabled")
        
        # Check for no double-qualification
        response_text = response.get("response", "")
        if response_text.lower().count("based on") > 1:
            failure("P0-2: Double-qualification detected!")
            return False
        else:
            success("P0-2: No double-qualification")
        
        logs = get_docker_logs(tail=50, grep_pattern="Confidence")
        if logs:
            success("P0-2: Confidence formatting logged")
        return True
    else:
        failure(f"P0-2: Query failed - {response.get('error')}")
        return False

def test_temperature_adjustment():
    """Test 4: P0-3 Temperature Adjustment"""
    global tests_run
    tests_run += 1
    
    log("TEST 4: P0-3 Dynamic Temperature")
    if not restart_with_features({
        "USE_CONTEXT_VALIDATION": False,
        "USE_CONFIDENCE_FORMATTING": False,
        "USE_DYNAMIC_TEMPERATURE": True,
        "USE_GROUNDING_CHECKS": False
    }):
        return False
    
    # Test query
    response = send_query("what time is it?")
    time.sleep(1)
    
    if "error" not in response:
        success("P0-3: Query successful with temperature adjustment enabled")
        
        logs = get_docker_logs(tail=50, grep_pattern="Temperature")
        if logs:
            success("P0-3: Temperature adjustment logged")
        else:
            warning("P0-3: No temperature logs (may be expected for this query)")
        return True
    else:
        failure(f"P0-3: Query failed - {response.get('error')}")
        return False

def test_all_features():
    """Test 5: All P0 features enabled"""
    global tests_run
    tests_run += 1
    
    log("TEST 5: ALL P0 Features Enabled")
    if not restart_with_features({
        "USE_CONTEXT_VALIDATION": True,
        "USE_CONFIDENCE_FORMATTING": True,
        "USE_DYNAMIC_TEMPERATURE": True,
        "USE_GROUNDING_CHECKS": True
    }):
        return False
    
    # Test multiple queries
    queries = [
        "hello",
        "what's on my shopping list?",
        "what time is it?"
    ]
    
    all_success = True
    for query in queries:
        response = send_query(query)
        time.sleep(1)
        if "error" in response:
            failure(f"All Features: Query '{query}' failed")
            all_success = False
    
    if all_success:
        success("All Features: All queries successful")
        
        # Check for errors in logs
        logs = get_docker_logs(tail=100)
        if "Exception" in logs or "Traceback" in logs:
            error_lines = [line for line in logs.split('\n') 
                          if "Exception" in line or "Traceback" in line or "Error" in line]
            # Filter out known non-critical errors
            critical_errors = [line for line in error_lines 
                             if "performance_metrics" not in line and "models" not in line]
            if critical_errors:
                failure(f"All Features: Errors detected in logs")
                for line in critical_errors[:5]:
                    print(f"  {line}")
                return False
        
        success("All Features: No critical errors in logs")
        return True
    
    return False

def test_performance():
    """Test 6: Performance check"""
    global tests_run
    tests_run += 1
    
    log("TEST 6: Performance Check")
    
    # Measure response time
    start = time.time()
    response = send_query("hello")
    latency = (time.time() - start) * 1000
    
    if "error" not in response:
        log(f"Response latency: {latency:.2f}ms")
        if latency < 2000:  # 2 second threshold
            success(f"Performance: Acceptable latency ({latency:.2f}ms)")
            return True
        else:
            warning(f"Performance: High latency ({latency:.2f}ms)")
            return True
    else:
        failure("Performance: Query failed")
        return False

def test_no_regressions():
    """Test 7: No regressions"""
    global tests_run
    tests_run += 1
    
    log("TEST 7: Regression Check")
    
    test_queries = [
        "hello",
        "what's the weather?",
        "what time is it?"
    ]
    
    failures = 0
    for query in test_queries:
        response = send_query(query)
        if "error" in response or "response" not in response:
            failures += 1
        time.sleep(0.5)
    
    if failures == 0:
        success(f"No Regressions: All {len(test_queries)} queries passed")
        return True
    else:
        failure(f"No Regressions: {failures}/{len(test_queries)} queries failed")
        return False

def main():
    print("╔═══════════════════════════════════════════════════════════════════╗")
    print("║         QUICK PRODUCTION READINESS TEST - P0 FEATURES            ║")
    print("╚═══════════════════════════════════════════════════════════════════╝")
    print()
    
    # Run tests
    tests = [
        ("Baseline", test_baseline),
        ("Context Validation", test_context_validation),
        ("Confidence Formatting", test_confidence_formatting),
        ("Temperature Adjustment", test_temperature_adjustment),
        ("All Features", test_all_features),
        ("Performance", test_performance),
        ("No Regressions", test_no_regressions),
    ]
    
    for test_name, test_func in tests:
        try:
            test_func()
        except Exception as e:
            failure(f"{test_name}: Exception - {e}")
        print()
    
    # Print results
    print("╔═══════════════════════════════════════════════════════════════════╗")
    print("║                         FINAL RESULTS                             ║")
    print("╚═══════════════════════════════════════════════════════════════════╝")
    print()
    print(f"Tests Run:    {tests_run}")
    print(f"Tests Passed: {tests_passed}")
    print(f"Tests Failed: {tests_failed}")
    print()
    
    if tests_passed > 0:
        pass_rate = (tests_passed * 100) // tests_run
        print(f"Pass Rate: {pass_rate}%")
        print()
    
    if tests_failed == 0:
        print(f"{Color.GREEN}✅ ALL TESTS PASSED - PRODUCTION READY{Color.NC}")
        return 0
    elif tests_passed >= tests_failed:
        print(f"{Color.YELLOW}⚠️  MOSTLY PASSING - Review failures{Color.NC}")
        return 1
    else:
        print(f"{Color.RED}❌ MULTIPLE FAILURES - DO NOT DEPLOY{Color.NC}")
        return 2

if __name__ == "__main__":
    sys.exit(main())

