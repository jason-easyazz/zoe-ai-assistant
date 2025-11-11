#!/usr/bin/env python3
"""
Enforce project structure rules before commit
Exit code 0 = pass, 1 = fail
"""

from pathlib import Path
import sys

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
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
        "tools/audit",
        "data/schema"
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

def check_no_databases_in_git():
    """Database files should not be tracked in git"""
    import subprocess
    
    try:
        result = subprocess.run(
            ['git', 'ls-files', 'data/*.db'],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        
        tracked_dbs = [f for f in result.stdout.strip().split('\n') if f]
        
        if tracked_dbs:
            print(f"❌ RULE VIOLATION: {len(tracked_dbs)} database files tracked in git")
            print("   Databases should NOT be in git (use schemas instead):")
            for db in tracked_dbs:
                print(f"   - {db}")
            print("\n   ACTION: Run 'git rm --cached data/*.db'")
            print("   See docs/guides/MIGRATION_TO_V2.4.md for migration guide")
            return False
        
        print("✅ Databases: Not tracked (schema-only)")
        return True
    except Exception as e:
        print(f"⚠️  WARNING: Could not check git tracking: {e}")
        return True

def check_no_venv_in_git():
    """Virtual environments should not be tracked in git"""
    import subprocess
    
    try:
        result = subprocess.run(
            ['git', 'ls-files', 'venv/', 'mcp_test_env/', '**/venv/'],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        
        tracked_venv = [f for f in result.stdout.strip().split('\n') if f]
        
        if tracked_venv:
            print(f"❌ RULE VIOLATION: {len(tracked_venv)} venv files tracked in git")
            print("   Virtual environments should NOT be in git:")
            venv_dirs = set(f.split('/')[0] for f in tracked_venv[:5])
            for vdir in venv_dirs:
                print(f"   - {vdir}/")
            print(f"\n   ACTION: Run 'git rm -r --cached venv/ mcp_test_env/'")
            return False
        
        print("✅ Virtual Envs: Not tracked")
        return True
    except Exception as e:
        print(f"⚠️  WARNING: Could not check git tracking: {e}")
        return True

def check_dockerignore_exists():
    """Check if .dockerignore exists"""
    dockerignore = PROJECT_ROOT / ".dockerignore"
    
    if not dockerignore.exists():
        print("❌ RULE VIOLATION: .dockerignore not found")
        print("   ACTION: Create .dockerignore to reduce Docker image size")
        return False
    
    print("✅ .dockerignore: Present")
    return True

def check_schema_files_exist():
    """Check that database schema files exist"""
    schema_dir = PROJECT_ROOT / "data" / "schema"
    required_schemas = ["zoe_schema.sql", "memory_schema.sql", "training_schema.sql"]
    
    if not schema_dir.exists():
        print("❌ RULE VIOLATION: data/schema/ directory not found")
        print("   ACTION: Run './scripts/maintenance/export_schema.sh'")
        return False
    
    missing = [s for s in required_schemas if not (schema_dir / s).exists()]
    
    if missing:
        print(f"❌ RULE VIOLATION: {len(missing)} schema files missing")
        for s in missing:
            print(f"   - data/schema/{s}")
        print("   ACTION: Run './scripts/maintenance/export_schema.sh'")
        return False
    
    print("✅ Database Schemas: All present")
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
        ("No databases in git", check_no_databases_in_git),
        ("No venv in git", check_no_venv_in_git),
        (".dockerignore exists", check_dockerignore_exists),
        ("Database schemas exist", check_schema_files_exist),
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

