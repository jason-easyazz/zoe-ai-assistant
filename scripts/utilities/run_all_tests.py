#!/usr/bin/env python3
"""
Unified Test Runner - Runs all test suites and provides comprehensive report
Combines:
- natural_language_learning.py (32 tests)
- comprehensive_conversation_test.py (50 tests)  
- test_natural_language_full_system.py (pytest integration tests)
Total: 100+ tests for complete system validation
"""

import subprocess
import sys
import time
from datetime import datetime

def run_command(cmd, name):
    """Run a command and return results"""
    print(f"\n{'='*80}")
    print(f"🧪 RUNNING: {name}")
    print(f"{'='*80}\n")
    
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=900  # 15 minute timeout
        )
        duration = time.time() - start
        
        return {
            "name": name,
            "success": result.returncode == 0,
            "duration": duration,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "success": False,
            "duration": time.time() - start,
            "stdout": "",
            "stderr": "TIMEOUT after 15 minutes",
            "returncode": -1
        }
    except Exception as e:
        return {
            "name": name,
            "success": False,
            "duration": time.time() - start,
            "stdout": "",
            "stderr": str(e),
            "returncode": -2
        }

def main():
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  🎯 ZOE COMPREHENSIVE TEST SUITE RUNNER                      ║
║                     Running 100+ Tests Across All Systems                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
    
    start_time = datetime.now()
    print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Define all test suites
    test_suites = [
        {
            "cmd": "cd /home/zoe/assistant && python3 scripts/utilities/natural_language_learning.py",
            "name": "Natural Language Learning (32 tests)",
            "weight": 1.0
        },
        {
            "cmd": "cd /home/zoe/assistant && python3 scripts/utilities/comprehensive_conversation_test.py",
            "name": "Comprehensive Conversation Tests (50 tests)",
            "weight": 1.5
        },
        {
            "cmd": "cd /home/zoe/assistant && python3 -m pytest tests/integration/test_natural_language_full_system.py -v",
            "name": "Integration Tests (pytest)",
            "weight": 1.0
        }
    ]
    
    results = []
    total_duration = 0
    
    # Run each test suite
    for suite in test_suites:
        result = run_command(suite["cmd"], suite["name"])
        result["weight"] = suite["weight"]
        results.append(result)
        total_duration += result["duration"]
        
        # Print immediate feedback
        status = "✅ PASS" if result["success"] else "❌ FAIL"
        print(f"\n{status} - {suite['name']} ({result['duration']:.1f}s)")
        
        # Show last few lines of output
        if result["stdout"]:
            lines = result["stdout"].split('\n')
            relevant_lines = [l for l in lines if 'PASS' in l or 'FAIL' in l or '%' in l or 'Total' in l][-5:]
            for line in relevant_lines:
                print(f"  {line}")
    
    # Generate comprehensive report
    end_time = datetime.now()
    
    print(f"\n\n{'='*80}")
    print("📊 COMPREHENSIVE TEST RESULTS")
    print(f"{'='*80}\n")
    
    passed_suites = sum(1 for r in results if r["success"])
    total_suites = len(results)
    
    print(f"⏱️  Total Duration: {total_duration:.1f}s")
    print(f"📅 Start Time: {start_time.strftime('%H:%M:%S')}")
    print(f"📅 End Time: {end_time.strftime('%H:%M:%S')}")
    print(f"\n🎯 Suites Passed: {passed_suites}/{total_suites} ({passed_suites/total_suites*100:.1f}%)\n")
    
    print("📋 Detailed Results:\n")
    for result in results:
        status_icon = "✅" if result["success"] else "❌"
        print(f"{status_icon} {result['name']}")
        print(f"   Duration: {result['duration']:.1f}s")
        print(f"   Return Code: {result['returncode']}")
        if not result["success"] and result["stderr"]:
            print(f"   Error: {result['stderr'][:200]}")
        print()
    
    # Calculate weighted score
    weighted_passed = sum(1 * r["weight"] for r in results if r["success"])
    weighted_total = sum(r["weight"] for r in results)
    weighted_score = (weighted_passed / weighted_total * 100) if weighted_total > 0 else 0
    
    print(f"{'='*80}")
    print(f"🎯 WEIGHTED SCORE: {weighted_score:.1f}%")
    print(f"{'='*80}\n")
    
    # Final verdict
    if weighted_score >= 80:
        print("🎉 EXCELLENT: System is performing well!")
        return 0
    elif weighted_score >= 60:
        print("⚠️  GOOD: System is functional with minor issues")
        return 0
    else:
        print("❌ NEEDS WORK: Significant issues detected")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test suite interrupted by user")
        sys.exit(130)

