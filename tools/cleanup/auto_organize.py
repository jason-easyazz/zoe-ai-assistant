#!/usr/bin/env python3
"""
Smart Auto-Organizer - Automatically organize files per structure rules
Analyzes files and moves them to correct locations
"""

from pathlib import Path
import re
import shutil

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Essential docs that must stay in root
ESSENTIAL_DOCS = [
    "README.md",
    "CHANGELOG.md",
    "QUICK-START.md",
    "PROJECT_STATUS.md",
    "FIXES_APPLIED.md",
    "CLEANUP_PLAN.md",
    "CLEANUP_SUMMARY.md",
    "DOCUMENTATION_STRUCTURE.md",
    "REFERENCES_UPDATED_COMPLETE.md",
    "PROJECT_STRUCTURE_RULES.md"
]

# Rules for categorizing docs
DOC_CATEGORIES = {
    'reports': ['report', 'summary', 'complete', 'status', 'results', 'final'],
    'technical': ['fix', 'debug', 'styling', 'update', 'backend', 'frontend', 'api'],
    'guides': ['guide', 'documentation', 'installation', 'integration', 'tutorial'],
}

# Test categorization rules
TEST_CATEGORIES = {
    'unit': ['unit', 'module', 'function', 'class'],
    'integration': ['integration', 'api', 'database', 'service'],
    'performance': ['performance', 'benchmark', 'speed', 'load'],
    'e2e': ['e2e', 'end_to_end', 'system', 'full'],
}

class SmartOrganizer:
    def __init__(self):
        self.moves = []
        self.errors = []
    
    def categorize_doc(self, filename):
        """Determine category for a documentation file"""
        name_lower = filename.lower()
        
        # Check each category
        for category, keywords in DOC_CATEGORIES.items():
            if any(kw in name_lower for kw in keywords):
                return category
        
        return 'other'
    
    def categorize_test(self, filename):
        """Determine category for a test file"""
        name_lower = filename.lower()
        
        # Check content if file exists
        file_path = PROJECT_ROOT / filename
        if file_path.exists():
            try:
                content = file_path.read_text().lower()
                
                # Check each category
                for category, keywords in TEST_CATEGORIES.items():
                    if any(kw in content[:500] for kw in keywords):
                        return category
            except:
                pass
        
        # Fallback to name-based
        for category, keywords in TEST_CATEGORIES.items():
            if any(kw in name_lower for kw in keywords):
                return category
        
        # Default to archived for unknown
        return 'archived'
    
    def categorize_script(self, filename):
        """Determine category for a script file"""
        name_lower = filename.lower()
        
        if any(kw in name_lower for kw in ['setup', 'init', 'install']):
            return 'setup'
        elif any(kw in name_lower for kw in ['backup', 'clean', 'maintain']):
            return 'maintenance'
        elif any(kw in name_lower for kw in ['deploy', 'start', 'stop', 'restart']):
            return 'deployment'
        elif any(kw in name_lower for kw in ['security', 'auth', 'encrypt']):
            return 'security'
        else:
            return 'utilities'
    
    def organize_docs(self):
        """Organize documentation files"""
        print("📄 Organizing documentation files...\n")
        
        md_files = list(PROJECT_ROOT.glob("*.md"))
        
        for md_file in md_files:
            if md_file.name in ESSENTIAL_DOCS:
                print(f"✓ Keep: {md_file.name} (essential)")
                continue
            
            # Categorize and move
            category = self.categorize_doc(md_file.name)
            dest_dir = PROJECT_ROOT / "docs/archive" / category
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                dest = dest_dir / md_file.name
                shutil.move(str(md_file), str(dest))
                self.moves.append((md_file.name, f"docs/archive/{category}/"))
                print(f"→ Moved: {md_file.name} to docs/archive/{category}/")
            except Exception as e:
                self.errors.append((md_file.name, str(e)))
                print(f"✗ Error: {md_file.name} - {e}")
    
    def organize_tests(self):
        """Organize test files"""
        print("\n🧪 Organizing test files...\n")
        
        test_patterns = ["test*.py", "*_test.py"]
        test_files = []
        
        for pattern in test_patterns:
            test_files.extend(PROJECT_ROOT.glob(pattern))
        
        for test_file in test_files:
            # Keep test_architecture.py in root
            if test_file.name == "test_architecture.py":
                print(f"✓ Keep: {test_file.name} (important exception)")
                continue
            
            # Categorize and move
            category = self.categorize_test(test_file.name)
            dest_dir = PROJECT_ROOT / "tests" / category
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                dest = dest_dir / test_file.name
                shutil.move(str(test_file), str(dest))
                self.moves.append((test_file.name, f"tests/{category}/"))
                print(f"→ Moved: {test_file.name} to tests/{category}/")
            except Exception as e:
                self.errors.append((test_file.name, str(e)))
                print(f"✗ Error: {test_file.name} - {e}")
    
    def organize_scripts(self):
        """Organize script files"""
        print("\n📜 Organizing script files...\n")
        
        allowed = ["verify_updates.sh", "start-zoe.sh", "stop-zoe.sh"]
        sh_files = [f for f in PROJECT_ROOT.glob("*.sh") 
                    if f.name not in allowed]
        
        for sh_file in sh_files:
            category = self.categorize_script(sh_file.name)
            dest_dir = PROJECT_ROOT / "scripts" / category
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                dest = dest_dir / sh_file.name
                shutil.move(str(sh_file), str(dest))
                self.moves.append((sh_file.name, f"scripts/{category}/"))
                print(f"→ Moved: {sh_file.name} to scripts/{category}/")
            except Exception as e:
                self.errors.append((sh_file.name, str(e)))
                print(f"✗ Error: {sh_file.name} - {e}")
    
    def organize_all(self):
        """Run full organization"""
        print("\n" + "="*70)
        print("🤖 SMART AUTO-ORGANIZER")
        print("="*70 + "\n")
        
        self.organize_docs()
        self.organize_tests()
        self.organize_scripts()
        
        print("\n" + "="*70)
        print("ORGANIZATION COMPLETE")
        print("="*70)
        print(f"\n✅ Moved {len(self.moves)} files")
        
        if self.errors:
            print(f"⚠️  {len(self.errors)} errors")
        
        # Show summary by destination
        by_dest = {}
        for file, dest in self.moves:
            by_dest[dest] = by_dest.get(dest, 0) + 1
        
        if by_dest:
            print("\n📊 Files organized by destination:")
            for dest, count in sorted(by_dest.items()):
                print(f"   {dest}: {count} files")
        
        print()

if __name__ == "__main__":
    import sys
    
    organizer = SmartOrganizer()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--execute":
        organizer.organize_all()
        
        # Run enforcement check after
        print("\n" + "="*70)
        print("Running structure enforcement check...")
        print("="*70 + "\n")
        
        import subprocess
        result = subprocess.run([
            "python3", 
            str(PROJECT_ROOT / "tools/audit/enforce_structure.py")
        ])
        
        sys.exit(result.returncode)
    else:
        print("\n" + "="*70)
        print("🤖 SMART AUTO-ORGANIZER (Dry Run)")
        print("="*70)
        print("\nThis tool will automatically organize files per PROJECT_STRUCTURE_RULES.md")
        print("\nTo execute, run:")
        print("  python3 tools/cleanup/auto_organize.py --execute")
        print()

