#!/usr/bin/env python3
"""
Bulk Test Runner for All Phases
Runs comprehensive test suite and generates report
"""
import subprocess
import sys
from datetime import datetime


def run_test_suite(test_path, markers=None):
    """Run a test suite and return results"""
    cmd = ["python3", "-m", "pytest", test_path, "-v", "--tb=short"]
    
    if markers:
        cmd.extend(["-m", markers])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/pi/zoe")
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        return {
            "returncode": -1,
            "error": str(e)
        }


def main():
    """Run all test suites"""
    print("=" * 80)
    print("ZOE COMPREHENSIVE TEST SUITE")
    print("=" * 80)
    print(f"Started: {datetime.now().isoformat()}\n")
    
    results = {}
    
    # Phase 1: Security Tests
    print("\n[Phase 1] Running Security Tests...")
    print("-" * 80)
    results["phase1_security"] = run_test_suite("tests/unit/test_auth_security.py")
    print(results["phase1_security"]["stdout"])
    
    # Phase 2: Integration Tests
    print("\n[Phase 2] Running LiteLLM Integration Tests...")
    print("-" * 80)
    results["phase2_litellm"] = run_test_suite("tests/integration/test_litellm_integration.py")
    print(results["phase2_litellm"]["stdout"])
    
    # Phase 3/4: Memory System Tests
    print("\n[Phase 3/4] Running Memory System Tests...")
    print("-" * 80)
    results["phase3_memory"] = run_test_suite("tests/integration/test_memory_system.py")
    print(results["phase3_memory"]["stdout"])
    
    # Phase 5: Performance Tests
    print("\n[Phase 5] Running Performance Tests...")
    print("-" * 80)
    results["phase5_performance"] = run_test_suite("tests/performance/test_latency_budgets.py", "performance")
    print(results["phase5_performance"]["stdout"])
    
    # Phase 5: End-to-End Tests
    print("\n[Phase 5] Running End-to-End Tests...")
    print("-" * 80)
    results["phase5_e2e"] = run_test_suite("tests/integration/test_end_to_end.py")
    print(results["phase5_e2e"]["stdout"])
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    total_suites = len(results)
    passed_suites = sum(1 for r in results.values() if r["returncode"] == 0)
    
    for suite_name, result in results.items():
        status = "PASSED" if result["returncode"] == 0 else "FAILED"
        print(f"{suite_name.upper()}: {status}")
    
    print(f"\nTotal Suites: {total_suites}")
    print(f"Passed: {passed_suites}")
    print(f"Failed: {total_suites - passed_suites}")
    print(f"\nCompleted: {datetime.now().isoformat()}")
    
    sys.exit(0 if passed_suites == total_suites else 1)


if __name__ == "__main__":
    main()
