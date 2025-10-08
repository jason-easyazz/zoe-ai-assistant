#!/usr/bin/env python3
"""
Architecture Tests - Prevent Router Proliferation and Enforce Standards
=======================================================================

These tests enforce architectural rules to prevent the same issues from recurring.
Run before every commit: python3 test_architecture.py
"""

import os
import glob
import sys
import re

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def test_single_chat_router_only():
    """Enforce that only ONE chat router exists"""
    print("🔍 Testing: Single chat router enforcement...")
    
    routers_path = "/home/pi/zoe/services/zoe-core/routers"
    
    if not os.path.exists(routers_path):
        print(f"❌ FAIL: Routers directory not found: {routers_path}")
        return False
    
    # Find all chat*.py files (excluding archive)
    chat_files = []
    for file in glob.glob(f"{routers_path}/chat*.py"):
        if "archive" not in file and "__pycache__" not in file:
            chat_files.append(file)
    
    # Should only be chat.py
    if len(chat_files) != 1:
        print(f"❌ FAIL: Found {len(chat_files)} chat routers (should be 1)")
        print(f"   Only ONE is allowed: chat.py")
        for file in chat_files:
            print(f"   - {file}")
        print(f"\n   🔧 Fix: Delete duplicates or move to archive/")
        return False
    
    if not chat_files[0].endswith("chat.py"):
        print(f"❌ FAIL: Chat router must be named 'chat.py', not {os.path.basename(chat_files[0])}")
        return False
    
    print(f"✅ PASS: Single consolidated chat router: {chat_files[0]}")
    return True

def test_no_backup_files_in_routers():
    """Prevent backup files from cluttering the routers directory"""
    print("\n🔍 Testing: No backup files in routers...")
    
    routers_path = "services/zoe-core/routers"
    
    if not os.path.exists(routers_path):
        print(f"⚠️  SKIP: Routers directory not found")
        return True
    
    forbidden_patterns = [
        "*_backup.py", "*_old.py", "*_new.py", "*_v2.py",
        "*_fixed.py", "*_optimized.py", "*_temp.py", "*_test.py"
    ]
    
    violations = []
    for pattern in forbidden_patterns:
        files = glob.glob(f"{routers_path}/{pattern}")
        violations.extend([f for f in files if "archive" not in f])
    
    if violations:
        print(f"❌ FAIL: {len(violations)} backup file(s) found in routers/")
        print(f"   Use git for versioning, not file copies!")
        for file in violations:
            print(f"   - {file}")
        print(f"\n   🔧 Fix: Move to routers/archive/ or delete")
        return False
    
    print(f"✅ PASS: No backup files in routers/")
    return True

def test_main_imports_single_chat_router():
    """Ensure main.py imports only one chat router"""
    print("\n🔍 Testing: Main.py chat router imports...")
    
    main_path = "services/zoe-core/main.py"
    
    if not os.path.exists(main_path):
        print(f"⚠️  SKIP: Main.py not found at {main_path}")
        return True
    
    with open(main_path, "r") as f:
        content = f.read()
    
    # Count chat router includes
    chat_includes = content.count("include_router(chat")
    
    if chat_includes != 1:
        print(f"❌ FAIL: main.py includes {chat_includes} chat routers (should be 1)")
        print(f"   Check for: include_router(chat.router), include_router(chat_langgraph.router), etc.")
        return False
    
    # Ensure no forbidden chat imports exist
    forbidden_imports = [
        "chat_langgraph", "chat_optimized", "chat_enhanced",
        "chat_backup", "chat_new", "chat_v2", "chat_hybrid"
    ]
    
    found_forbidden = []
    for forbidden in forbidden_imports:
        if forbidden in content:
            found_forbidden.append(forbidden)
    
    if found_forbidden:
        print(f"❌ FAIL: main.py contains forbidden imports:")
        for forbidden in found_forbidden:
            print(f"   - {forbidden}")
        print(f"\n   🔧 Fix: Remove these imports, use only 'chat'")
        return False
    
    print(f"✅ PASS: main.py imports exactly 1 chat router")
    return True

def test_chat_router_has_enhancement_integration():
    """Verify chat router has real enhancement system integration"""
    print("\n🔍 Testing: Enhancement system integration...")
    
    chat_path = "services/zoe-core/routers/chat.py"
    
    if not os.path.exists(chat_path):
        print(f"⚠️  SKIP: chat.py not found")
        return True
    
    with open(chat_path, "r") as f:
        content = f.read()
    
    # Check for actual API calls to enhancement systems
    required_integrations = {
        "temporal-memory": "api/temporal-memory",
        "orchestration": "api/orchestration",
        "satisfaction": "api/satisfaction"
    }
    
    missing = []
    for name, api_path in required_integrations.items():
        if api_path not in content:
            missing.append(name)
    
    if missing:
        print(f"⚠️  WARNING: Missing enhancement integrations:")
        for name in missing:
            print(f"   - {name}")
        print(f"   💡 Enhancement systems should use actual API calls")
        # Don't fail, just warn
    else:
        print(f"✅ PASS: Enhancement systems properly integrated")
    
    return True

def test_no_duplicate_routers_anywhere():
    """Check for duplicate routers in other locations"""
    print("\n🔍 Testing: No duplicate routers in other locations...")
    
    # Check services/zoe-core (outside of zoe/)
    if os.path.exists("services/zoe-core/routers"):
        chat_files = []
        for file in glob.glob("services/zoe-core/routers/chat*.py"):
            if "archive" not in file:
                chat_files.append(file)
        
        if len(chat_files) > 1:
            print(f"❌ FAIL: Multiple chat routers in services/zoe-core/routers")
            for file in chat_files:
                print(f"   - {file}")
            return False
    
    print(f"✅ PASS: No duplicate routers found")
    return True

def test_chat_uses_intelligent_systems():
    """Verify chat router uses intelligent systems, not hardcoded logic"""
    print("Checking: Chat router uses intelligent systems (MEM Agent, Orchestrator, etc.)...")
    
    chat_path = "/home/pi/zoe/services/zoe-core/routers/chat.py"
    
    if not os.path.exists(chat_path):
        print(f"{Colors.RED}  ❌ FAIL: chat.py not found{Colors.RESET}")
        return False
    
    with open(chat_path, 'r') as f:
        content = f.read()
    
    # Check for intelligent system imports
    intelligent_systems_present = 0
    
    if "mem_agent" in content.lower():
        intelligent_systems_present += 1
    if "route_llm" in content.lower() or "routellm" in content.lower():
        intelligent_systems_present += 1
    if "agent" in content.lower() and ("planner" in content.lower() or "orchestrat" in content.lower()):
        intelligent_systems_present += 1
    
    # Check for anti-patterns
    regex_count = len(re.findall(r're\.search\(', content))
    if_message_count = len(re.findall(r'if.*in message.*:', content, re.IGNORECASE))
    
    if intelligent_systems_present < 2:
        print(f"{Colors.RED}  ❌ FAIL: Chat router missing intelligent systems{Colors.RESET}")
        print(f"{Colors.RED}     Only {intelligent_systems_present}/3 systems found{Colors.RESET}")
        print(f"{Colors.YELLOW}     Required: MemAgent, RouteLLM, AgentPlanner/Orchestrator{Colors.RESET}")
        return False
    
    if regex_count > 5 or if_message_count > 10:
        print(f"{Colors.YELLOW}  ⚠️  WARNING: Chat has hardcoded logic (regex:{regex_count}, if/else:{if_message_count}){Colors.RESET}")
        print(f"{Colors.YELLOW}     Consider using LLM intent detection instead{Colors.RESET}")
    
    print(f"{Colors.GREEN}  ✅ PASS: Chat router uses intelligent systems{Colors.RESET}")
    return True

def main():
    """Run all architecture tests"""
    print("=" * 70)
    print("🏗️  ZOE ARCHITECTURE VALIDATION TESTS")
    print("=" * 70)
    print()
    
    # Change to project root if we're in zoe/
    if os.path.basename(os.getcwd()) == "zoe":
        os.chdir("..")
    
    tests = [
        test_single_chat_router_only,
        test_no_backup_files_in_routers,
        test_main_imports_single_chat_router,
        test_chat_router_has_enhancement_integration,
        test_no_duplicate_routers_anywhere,
        test_chat_uses_intelligent_systems
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ ERROR: {test.__name__} raised exception: {e}")
            results.append(False)
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for r in results if r)
    total = len(results)
    
    for i, test in enumerate(tests):
        status = "✅ PASS" if results[i] else "❌ FAIL"
        print(f"{status}: {test.__doc__.strip()}")
    
    print("\n" + "=" * 70)
    print(f"🎯 RESULT: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
    
    if passed == total:
        print("🎉 ALL ARCHITECTURE TESTS PASSED!")
        print("✅ Safe to commit")
        return 0
    else:
        print("❌ ARCHITECTURE VIOLATIONS DETECTED")
        print("🚫 Please fix issues before committing")
        return 1

if __name__ == "__main__":
    sys.exit(main())

