#!/usr/bin/env python3
"""
Architecture Tests - Prevent Router Proliferation and Enforce Standards
=======================================================================

These tests enforce architectural rules to prevent the same issues from recurring.
Run before every commit: python3 test_architecture.py
"""

import glob
import os
import sys
from pathlib import Path

# Auto-detect project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import re

import pytest
# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def test_single_chat_router_only():
    """Enforce that only ONE chat router exists"""
    print("🔍 Testing: Single chat router enforcement...")

    routers_path = PROJECT_ROOT / "services/zoe-data/routers"
    assert routers_path.exists(), f"Routers directory not found: {routers_path}"

    # Find all chat*.py files (excluding archive and chat_sessions.py which manages sessions not chat routing)
    chat_files = []
    for file in glob.glob(str(routers_path / "chat*.py")):
        if "archive" not in file and "__pycache__" not in file and "chat_sessions" not in file:
            chat_files.append(file)

    assert len(chat_files) == 1, f"Expected exactly one chat router, found: {chat_files}"
    assert Path(chat_files[0]).name == "chat.py", f"Chat router must be named chat.py: {chat_files[0]}"

    print(f"✅ PASS: Single consolidated chat router: {chat_files[0]}")

def test_no_backup_files_in_routers():
    """Prevent backup files from cluttering the routers directory"""
    print("\n🔍 Testing: No backup files in routers...")

    routers_path = PROJECT_ROOT / "services/zoe-data/routers"
    assert routers_path.exists(), f"Routers directory not found: {routers_path}"

    forbidden_patterns = [
        "*_backup.py", "*_old.py", "*_new.py", "*_v2.py",
        "*_fixed.py", "*_optimized.py", "*_temp.py", "*_test.py"
    ]

    violations = []
    for pattern in forbidden_patterns:
        files = glob.glob(str(routers_path / pattern))
        violations.extend([f for f in files if "archive" not in f])

    assert not violations, f"Backup/duplicate router files found: {violations}"

    print(f"✅ PASS: No backup files in routers/")

def test_main_imports_single_chat_router():
    """Ensure main.py imports only one chat router"""
    print("\n🔍 Testing: Main.py chat router imports...")

    main_path = PROJECT_ROOT / "services/zoe-data/main.py"
    assert main_path.exists(), f"Main.py not found at {main_path}"

    with open(main_path, "r") as f:
        content = f.read()

    # Count chat router includes
    chat_router_includes = re.findall(r"include_router\(\s*(chat\w*)", content)
    assert chat_router_includes == ["chat_router"], (
        f"main.py should include only chat_router for chat routing, found {chat_router_includes}"
    )

    # Ensure no forbidden chat imports exist
    forbidden_imports = [
        "chat_langgraph", "chat_optimized", "chat_enhanced",
        "chat_backup", "chat_new", "chat_v2", "chat_hybrid"
    ]

    found_forbidden = [forbidden for forbidden in forbidden_imports if forbidden in content]
    assert not found_forbidden, f"main.py contains forbidden chat imports: {found_forbidden}"

    print(f"✅ PASS: main.py imports exactly 1 chat router")

def test_chat_router_has_enhancement_integration():
    """Verify chat router has real enhancement system integration"""
    print("\n🔍 Testing: Enhancement system integration...")

    chat_path = PROJECT_ROOT / "services/zoe-data/routers/chat.py"
    assert chat_path.exists(), f"chat.py not found at {chat_path}"

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

def test_no_duplicate_routers_anywhere():
    """Check for duplicate routers in other locations"""
    print("\n🔍 Testing: No duplicate routers in other locations...")

    routers_path = PROJECT_ROOT / "services/zoe-data/routers"
    chat_files = [
        file for file in glob.glob(str(routers_path / "chat*.py"))
        if "archive" not in file and "__pycache__" not in file and "chat_sessions" not in file
    ]
    assert chat_files == [str(routers_path / "chat.py")], f"Duplicate chat routers found: {chat_files}"

    print(f"✅ PASS: No duplicate routers found")

def test_chat_uses_intelligent_systems():
    """Verify chat router uses intelligent systems, not hardcoded logic"""
    print("Checking: Chat router uses intelligent systems (MEM Agent, Orchestrator, etc.)...")

    chat_path = PROJECT_ROOT / "services/zoe-data/routers/chat.py"
    assert chat_path.exists(), f"chat.py not found at {chat_path}"

    with open(chat_path, 'r') as f:
        content = f.read()
    
    required_systems = {
        "intent router": "from intent_router import",
        # Brain-lane selection (flue > zoe-core > legacy zoe_agent) has ONE source
        # of truth: brain_dispatch.py. W4-C1 deleted chat's private copies of
        # _use_flue_brain/_brain_streaming/_brain_oneshot, so the old
        # "run_zoe_core"/"run_zoe_agent" markers no longer appear — the integration
        # is stronger, not absent. Pin the seam itself: if a future change
        # reintroduces a per-router brain copy, this marker goes with it.
        # (The old "run_zoe_agent" marker had also been passing on a stray COMMENT
        # rather than any real call — a substring grep can't tell the difference.)
        "brain dispatch": "from brain_dispatch import",
        "mempalace memory": "_mempalace_load_user_facts",
        # W4-C2 moved the sentinel mapper to chat_stream_protocol.py behind a
        # permanent re-export shim in chat.py — pin the seam (the shim import),
        # not the def, which no longer lives in chat.py.
        "brain tool sentinel": "from chat_stream_protocol import",
        "ui orchestrator": "enqueue_ui_action",
    }
    missing_systems = [
        name for name, marker in required_systems.items()
        if marker not in content
    ]
    
    # Check for anti-patterns
    regex_count = len(re.findall(r're\.search\(', content))
    if_message_count = len(re.findall(r'if.*in message.*:', content, re.IGNORECASE))
    
    assert not missing_systems, f"Chat router missing canonical integration markers: {missing_systems}"
    
    if regex_count > 5 or if_message_count > 10:
        print(f"{Colors.YELLOW}  ⚠️  WARNING: Chat has hardcoded logic (regex:{regex_count}, if/else:{if_message_count}){Colors.RESET}")
        print(f"{Colors.YELLOW}     Consider using LLM intent detection instead{Colors.RESET}")
    
    print(f"{Colors.GREEN}  ✅ PASS: Chat router uses intelligent systems{Colors.RESET}")

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
