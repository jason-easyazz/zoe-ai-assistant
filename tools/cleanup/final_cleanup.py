#!/usr/bin/env python3
"""
Final comprehensive cleanup - organize all scattered files
"""

from pathlib import Path
import shutil

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Essential docs to keep in root
KEEP_IN_ROOT = [
    "README.md",
    "CHANGELOG.md",
    "QUICK-START.md",
    "PROJECT_STATUS.md",
    "FIXES_APPLIED.md",
    "CLEANUP_PLAN.md",
    "CLEANUP_SUMMARY.md",
    "DOCUMENTATION_STRUCTURE.md",
    "REFERENCES_UPDATED_COMPLETE.md"
]

# Test files to keep in tests/
KEEP_TEST_FILES = [
    "tests/test_enhanced_features.py",
    "tests/test_light_rag.py",
    "test_architecture.py"  # This one is important
]

def analyze_markdown_files():
    """Analyze all .md files in root"""
    print("\n📄 Analyzing markdown files in root...")
    
    md_files = list(PROJECT_ROOT.glob("*.md"))
    
    to_archive = []
    to_keep = []
    
    for md_file in md_files:
        if md_file.name in KEEP_IN_ROOT:
            to_keep.append(md_file.name)
        else:
            to_archive.append(md_file)
    
    print(f"  Keep in root: {len(to_keep)}")
    print(f"  Should archive: {len(to_archive)}")
    
    return to_archive

def analyze_shell_scripts():
    """Analyze all .sh files"""
    print("\n🔧 Analyzing shell scripts...")
    
    # Find all .sh files
    sh_files = []
    for pattern in ["*.sh", "**/*.sh"]:
        sh_files.extend(PROJECT_ROOT.glob(pattern))
    
    # Filter out git, node_modules, backups
    sh_files = [f for f in sh_files if not any(
        skip in str(f) for skip in ['.git', 'node_modules', 'backups', 'tools/aider']
    )]
    
    # Categorize
    in_root = [f for f in sh_files if f.parent == PROJECT_ROOT]
    in_scripts = [f for f in sh_files if 'scripts' in str(f)]
    in_tests = [f for f in sh_files if 'tests' in str(f)]
    
    print(f"  In root: {len(in_root)}")
    print(f"  In scripts/: {len(in_scripts)} (organized)")
    print(f"  In tests/: {len(in_tests)} (organized)")
    
    return in_root

def analyze_test_files():
    """Analyze test .py files"""
    print("\n🧪 Analyzing test files...")
    
    # Find test files in root
    test_patterns = ["test*.py", "*_test.py"]
    root_tests = []
    
    for pattern in test_patterns:
        root_tests.extend(PROJECT_ROOT.glob(pattern))
    
    # Filter out tests/ directory
    root_tests = [f for f in root_tests if 'tests/' not in str(f)]
    
    print(f"  Test files in root: {len(root_tests)}")
    
    # Check if tests/ directory exists
    tests_dir = PROJECT_ROOT / "tests"
    if tests_dir.exists():
        tests_in_dir = list(tests_dir.glob("*.py"))
        print(f"  Test files in tests/: {len(tests_in_dir)} (organized)")
    
    return root_tests

def create_cleanup_plan():
    """Create a cleanup plan"""
    
    print("\n" + "="*60)
    print("CREATING CLEANUP PLAN")
    print("="*60)
    
    to_archive_docs = analyze_markdown_files()
    to_move_scripts = analyze_shell_scripts()
    to_move_tests = analyze_test_files()
    
    # Create plan
    plan = {
        "archive_docs": [],
        "organize_scripts": [],
        "organize_tests": [],
        "delete_temp": []
    }
    
    # Documentation plan
    for doc in to_archive_docs:
        name = doc.name.lower()
        
        # Determine category
        if any(x in name for x in ['report', 'summary', 'complete', 'status', 'review']):
            category = 'reports'
        elif any(x in name for x in ['guide', 'documentation', 'installation', 'integration']):
            category = 'guides'
        elif any(x in name for x in ['fix', 'debug', 'styling', 'api', 'backend', 'frontend']):
            category = 'technical'
        else:
            category = 'other'
        
        plan["archive_docs"].append({
            'file': doc,
            'dest': PROJECT_ROOT / f"docs/archive/{category}",
            'name': doc.name
        })
    
    # Scripts plan
    scripts_dir = PROJECT_ROOT / "scripts" / "utilities"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    
    for script in to_move_scripts:
        # Keep verify_updates.sh in root (it's a tool)
        if script.name == "verify_updates.sh":
            continue
            
        plan["organize_scripts"].append({
            'file': script,
            'dest': scripts_dir,
            'name': script.name
        })
    
    # Tests plan
    tests_dir = PROJECT_ROOT / "tests" / "old"
    tests_dir.mkdir(parents=True, exist_ok=True)
    
    for test in to_move_tests:
        # Keep test_architecture.py in root (important)
        if test.name == "test_architecture.py":
            continue
            
        plan["organize_tests"].append({
            'file': test,
            'dest': tests_dir,
            'name': test.name
        })
    
    return plan

def display_plan(plan):
    """Display the cleanup plan"""
    
    print("\n" + "="*60)
    print("CLEANUP PLAN SUMMARY")
    print("="*60 + "\n")
    
    print("📄 Documentation to Archive:")
    print(f"  Total: {len(plan['archive_docs'])} files")
    by_category = {}
    for item in plan['archive_docs']:
        cat = item['dest'].name
        by_category[cat] = by_category.get(cat, 0) + 1
    for cat, count in by_category.items():
        print(f"    → docs/archive/{cat}/: {count} files")
    
    print(f"\n🔧 Scripts to Organize:")
    print(f"  Total: {len(plan['organize_scripts'])} files")
    print(f"    → scripts/utilities/")
    
    print(f"\n🧪 Tests to Organize:")
    print(f"  Total: {len(plan['organize_tests'])} files")
    print(f"    → tests/old/")
    
    print(f"\n📊 Summary:")
    total = (len(plan['archive_docs']) + 
             len(plan['organize_scripts']) + 
             len(plan['organize_tests']))
    print(f"  Total files to organize: {total}")
    
    print("\n" + "="*60)
    print("WHAT WILL REMAIN IN ROOT:")
    print("="*60)
    print("\n📚 Documentation (9 files):")
    for doc in KEEP_IN_ROOT:
        print(f"  ✓ {doc}")
    
    print("\n🛠️ Tools:")
    print("  ✓ verify_updates.sh")
    print("  ✓ test_architecture.py (important)")
    print("  ✓ *.py (automation tools)")
    
    return total

def execute_cleanup(plan, dry_run=True):
    """Execute the cleanup"""
    
    if dry_run:
        print(f"\n{'='*60}")
        print("DRY RUN MODE - No files will be moved")
        print(f"{'='*60}\n")
        return
    
    print(f"\n{'='*60}")
    print("EXECUTING CLEANUP")
    print(f"{'='*60}\n")
    
    moved = 0
    
    # Archive docs
    for item in plan['archive_docs']:
        try:
            item['dest'].mkdir(parents=True, exist_ok=True)
            shutil.move(str(item['file']), str(item['dest'] / item['name']))
            print(f"✓ Archived: {item['name']} → {item['dest'].name}/")
            moved += 1
        except Exception as e:
            print(f"✗ Failed: {item['name']} - {e}")
    
    # Organize scripts
    for item in plan['organize_scripts']:
        try:
            item['dest'].mkdir(parents=True, exist_ok=True)
            shutil.move(str(item['file']), str(item['dest'] / item['name']))
            print(f"✓ Moved: {item['name']} → scripts/utilities/")
            moved += 1
        except Exception as e:
            print(f"✗ Failed: {item['name']} - {e}")
    
    # Organize tests
    for item in plan['organize_tests']:
        try:
            item['dest'].mkdir(parents=True, exist_ok=True)
            shutil.move(str(item['file']), str(item['dest'] / item['name']))
            print(f"✓ Moved: {item['name']} → tests/old/")
            moved += 1
        except Exception as e:
            print(f"✗ Failed: {item['name']} - {e}")
    
    print(f"\n{'='*60}")
    print(f"CLEANUP COMPLETE - Moved {moved} files")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    import sys
    
    print("\n" + "="*60)
    print("FINAL COMPREHENSIVE CLEANUP")
    print("="*60)
    
    plan = create_cleanup_plan()
    total = display_plan(plan)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        execute_cleanup(plan, dry_run=False)
    else:
        print("\n" + "="*60)
        print("THIS WAS A DRY RUN - No files were moved")
        print("="*60)
        print(f"\nTo execute cleanup, run:")
        print(f"  python3 final_cleanup.py --execute")
    
    print("\n" + "="*60)
    print("✓ Analysis Complete!")
    print("="*60 + "\n")

