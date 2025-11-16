#!/usr/bin/env python3
"""
Best Practices Check for Zoe Project
Checks for common code quality issues and anti-patterns
"""
import sys
from pathlib import Path
import re

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
SERVICES_ROOT = PROJECT_ROOT / "services" / "zoe-core"

class BestPracticesChecker:
    def __init__(self):
        self.issues = []
        self.warnings = []
    
    def check_router_size(self):
        """Check for overly large router files"""
        routers_dir = SERVICES_ROOT / "routers"
        if not routers_dir.exists():
            return
        
        for router_file in routers_dir.glob("*.py"):
            if router_file.name.startswith("_"):
                continue
            
            lines = len(router_file.read_text().splitlines())
            if lines > 800:
                self.warnings.append(
                    f"⚠️  {router_file.name}: {lines} lines (consider splitting at 800+)"
                )
            elif lines > 1200:
                self.issues.append(
                    f"❌ {router_file.name}: {lines} lines (MUST split - too large!)"
                )
    
    def check_hardcoded_patterns(self):
        """Check for hardcoded command detection patterns"""
        chat_router = SERVICES_ROOT / "routers" / "chat.py"
        if not chat_router.exists():
            return
        
        content = chat_router.read_text()
        
        # Check for pattern matching
        if_pattern = r'if\s+any\(.+in.+message'
        matches = re.findall(if_pattern, content, re.IGNORECASE)
        
        if len(matches) > 3:
            self.warnings.append(
                f"⚠️  chat.py: {len(matches)} hardcoded pattern matches found"
            )
            self.warnings.append(
                "   Consider using intelligent agent routing instead of if/else patterns"
            )
    
    def check_duplicate_routers(self):
        """Check for duplicate router patterns"""
        routers_dir = SERVICES_ROOT / "routers"
        if not routers_dir.exists():
            return
        
        router_files = list(routers_dir.glob("*chat*.py"))
        
        # Acceptable: chat.py, chat_sessions.py, developer_chat.py
        acceptable = ["chat.py", "chat_sessions.py", "developer_chat.py"]
        
        for router_file in router_files:
            if router_file.name not in acceptable and not router_file.name.startswith("_"):
                self.issues.append(
                    f"❌ Unexpected chat router: {router_file.name}"
                )
                self.issues.append(
                    "   Only ONE main chat router should exist (chat.py)"
                )
    
    def check_import_complexity(self):
        """Check main.py for excessive imports"""
        main_file = SERVICES_ROOT / "main.py"
        if not main_file.exists():
            return
        
        content = main_file.read_text()
        
        # Count router imports
        router_imports = len(re.findall(r'from\s+routers\s+import', content))
        
        if router_imports > 5:
            self.warnings.append(
                f"⚠️  main.py: {router_imports} router imports (use auto-discovery instead)"
            )
    
    def check_todos_in_code(self):
        """Check for TODO/FIXME/HACK comments"""
        python_files = list(SERVICES_ROOT.rglob("*.py"))
        
        todos = []
        for py_file in python_files[:50]:  # Limit to avoid slowdown
            if "__pycache__" in str(py_file):
                continue
            
            try:
                content = py_file.read_text()
                for i, line in enumerate(content.splitlines(), 1):
                    if any(marker in line.upper() for marker in ["TODO", "FIXME", "HACK", "XXX"]):
                        todos.append((py_file.name, i, line.strip()[:80]))
            except Exception:
                continue
        
        if len(todos) > 20:
            self.warnings.append(
                f"⚠️  {len(todos)} TODO/FIXME comments found in code"
            )
            self.warnings.append(
                "   Consider creating issues for these instead"
            )
    
    def run_all_checks(self):
        """Run all checks"""
        print("🔍 Running best practices checks...\n")
        
        self.check_router_size()
        self.check_hardcoded_patterns()
        self.check_duplicate_routers()
        self.check_import_complexity()
        self.check_todos_in_code()
        
        # Report results
        if self.warnings:
            print("\n⚠️  WARNINGS:")
            for warning in self.warnings:
                print(f"  {warning}")
        
        if self.issues:
            print("\n❌ ISSUES:")
            for issue in self.issues:
                print(f"  {issue}")
            print("\n❌ Best practices checks failed")
            return False
        
        if not self.warnings and not self.issues:
            print("✓ All best practices checks passed")
        elif not self.issues:
            print("\n✓ No critical issues (warnings are informational)")
        
        return True

if __name__ == "__main__":
    checker = BestPracticesChecker()
    success = checker.run_all_checks()
    sys.exit(0 if success else 1)

