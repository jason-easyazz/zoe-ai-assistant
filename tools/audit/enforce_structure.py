#!/usr/bin/env python3
"""
Enforce project structure rules before commit
Exit code 0 = pass, 1 = fail
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path("/home/pi/zoe")
MAX_ROOT_DOCS = 10
ALLOWED_ROOT_SCRIPTS = ["verify_updates.sh", "start-zoe.sh", "stop-zoe.sh"]
ALLOWED_ROOT_TESTS = ["test_architecture.py"]

def check_root_md_files():
    """Max 10 .md files in root"""
    md_files = list(PROJECT_ROOT.glob("*.md"))
    if len(md_files) > MAX_ROOT_DOCS:
        print(f"❌ RULE VIOLATION: {len(md_files)} .md files in root (max {MAX_ROOT_DOCS})")
        print("   Current files:")
        for f in sorted(md_files):
            print(f"   - {f.name}")
        print("\n   ACTION: Move extras to docs/archive/{category}/")
        return False
    print(f"✅ Documentation: {len(md_files)}/{MAX_ROOT_DOCS} files in root")
    return True

def check_no_root_tests():
    """No test files in root except allowed"""
    test_files = [f for f in PROJECT_ROOT.glob("test*.py") 
                  if f.name not in ALLOWED_ROOT_TESTS]
    test_files += [f for f in PROJECT_ROOT.glob("*_test.py")]
    
    if test_files:
        print(f"❌ RULE VIOLATION: {len(test_files)} test files in root")
        print("   Move to tests/{{unit|integration|performance|archived}}/:")
        for f in test_files:
            print(f"   - {f.name}")
        return False
    print("✅ Tests: Organized (only allowed tests in root)")
    return True

def check_no_root_scripts():
    """No .sh files in root except allowed"""
    sh_files = [f for f in PROJECT_ROOT.glob("*.sh") 
                if f.name not in ALLOWED_ROOT_SCRIPTS]
    
    if sh_files:
        print(f"❌ RULE VIOLATION: {len(sh_files)} .sh files in root")
        print("   Move to scripts/{{setup|maintenance|deployment|utilities}}/:")
        for f in sh_files:
            print(f"   - {f.name}")
        return False
    print("✅ Scripts: Organized (only allowed scripts in root)")
    return True

def check_no_temp_files():
    """No temporary files in git"""
    patterns = ["*.tmp", "*.cache", "*.bak", "*_backup.*", "*.old"]
    temp_files = []
    for pattern in patterns:
        temp_files.extend(PROJECT_ROOT.glob(pattern))
    
    # Filter out backups/ directory
    temp_files = [f for f in temp_files if 'backups/' not in str(f)]
    
    if temp_files:
        print(f"❌ RULE VIOLATION: {len(temp_files)} temp files found")
        print("   These should be in .gitignore:")
        for f in temp_files[:10]:
            print(f"   - {f.name}")
        return False
    print("✅ Temp Files: None found")
    return True

def check_required_docs():
    """Required documentation must exist"""
    required = ["README.md", "CHANGELOG.md", "QUICK-START.md", "PROJECT_STATUS.md"]
    missing = [doc for doc in required if not (PROJECT_ROOT / doc).exists()]
    
    if missing:
        print(f"❌ RULE VIOLATION: Missing required documentation:")
        for doc in missing:
            print(f"   - {doc}")
        return False
    print("✅ Required Docs: All present")
    return True

def check_no_archive_folders():
    """No archive/ folders in services"""
    forbidden_archives = [
        "services/zoe-core/routers/archive",
        "services/zoe-ui/dist/archived",
        "scripts/archive"
    ]
    
    found = []
    for archive_path in forbidden_archives:
        full_path = PROJECT_ROOT / archive_path
        if full_path.exists():
            found.append(archive_path)
    
    if found:
        print(f"❌ RULE VIOLATION: {len(found)} archive folders exist")
        print("   Remove these (use git history instead):")
        for path in found:
            print(f"   - {path}")
        return False
    print("✅ Archive Folders: None (using git history)")
    return True

def check_no_duplicate_configs():
    """No duplicate config files with forbidden suffixes"""
    forbidden_suffixes = ["-old", "-new", "-v2", "-fixed", "-optimized", "-backup", "-temp", "-hub", "-clean"]
    
    # Check for duplicate nginx configs
    nginx_dir = PROJECT_ROOT / "services/zoe-ui"
    if nginx_dir.exists():
        nginx_files = [f for f in nginx_dir.glob("nginx*.conf") 
                      if f.name != "nginx.conf" and f.name != "nginx-dev.conf"]
        
        # Filter for forbidden patterns
        duplicates = []
        for f in nginx_files:
            for suffix in forbidden_suffixes:
                if suffix in f.name:
                    duplicates.append(f)
                    break
        
        if duplicates:
            print(f"❌ RULE VIOLATION: {len(duplicates)} duplicate nginx configs found")
            print("   SINGLE SOURCE OF TRUTH - only nginx.conf and nginx-dev.conf allowed:")
            for f in duplicates:
                print(f"   - {f.name}")
            print("\n   ACTION: Archive to docs/archive/technical/ or delete")
            return False
    
    print("✅ Config Files: Single source of truth (no duplicates)")
    return True

def check_structure_exists():
    """Required folder structure must exist"""
    required_dirs = [
        "tests/unit",
        "tests/integration",
        "scripts/maintenance",
        "docs/archive",
        "tools/audit"
    ]
    
    missing = [d for d in required_dirs if not (PROJECT_ROOT / d).exists()]
    
    if missing:
        print(f"⚠️  WARNING: Missing {len(missing)} required directories:")
        for d in missing:
            print(f"   - {d}")
        print("   Creating them now...")
        for d in missing:
            (PROJECT_ROOT / d).mkdir(parents=True, exist_ok=True)
        return True
    
    print("✅ Folder Structure: Complete")
    return True

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🔒 ZOE PROJECT STRUCTURE ENFORCEMENT")
    print("="*70 + "\n")
    
    checks = [
        ("Required documentation exists", check_required_docs),
        ("Max 10 .md files in root", check_root_md_files),
        ("No test files in root", check_no_root_tests),
        ("No scripts in root", check_no_root_scripts),
        ("No temp files", check_no_temp_files),
        ("No archive folders", check_no_archive_folders),
        ("No duplicate configs", check_no_duplicate_configs),
        ("Folder structure exists", check_structure_exists),
    ]
    
    passed = 0
    failed = []
    
    for name, check_func in checks:
        print(f"Checking: {name}...")
        if check_func():
            passed += 1
            print()
        else:
            failed.append(name)
            print()
    
    print("="*70)
    print(f"RESULTS: {passed}/{len(checks)} checks passed")
    print("="*70 + "\n")
    
    if failed:
        print("❌ STRUCTURE VIOLATIONS DETECTED\n")
        print("Failed checks:")
        for i, check in enumerate(failed, 1):
            print(f"  {i}. {check}")
        print(f"\n⚠️  Fix {len(failed)} violation(s) before committing!")
        print("\nSee PROJECT_STRUCTURE_RULES.md for guidance.")
        sys.exit(1)
    else:
        print("✅ ALL STRUCTURE RULES PASSED")
        print("\n🎉 Project structure is compliant!")
        sys.exit(0)

