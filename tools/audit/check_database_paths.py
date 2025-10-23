#!/usr/bin/env python3
"""
Database Path Enforcement Tool
Ensures all code uses DATABASE_PATH env var instead of hardcoded paths

Usage:
    python3 tools/audit/check_database_paths.py
    
Returns:
    Exit 0 if all checks pass
    Exit 1 if violations found
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Forbidden patterns
FORBIDDEN_PATTERNS = [
    r'"/home/pi/zoe/data/\w+\.db"',  # Hardcoded absolute path
    r"'/home/pi/zoe/data/\w+\.db'",  # Hardcoded absolute path (single quotes)
    r'db_path\s*:\s*str\s*=\s*"/home/pi',  # Default parameter with hardcoded path
    r'db_path\s*=\s*"/home/pi',  # Assignment with hardcoded path
    r'DATABASE_PATH\s*=\s*"/home/pi',  # Constant with hardcoded path
]

# Allowed patterns
ALLOWED_PATTERNS = [
    r'os\.getenv\(["\']DATABASE_PATH["\']',
    r'os\.environ\.get\(["\']DATABASE_PATH["\']',
    r'os\.environ\[["\']DATABASE_PATH["\']\]',
]

# Files to skip (paths containing these strings)
SKIP_PATTERNS = [
    '/tests/',  # All test files
    '/docs/',
    '/.git/',
    '/__pycache__/',
    '/venv/',
    '/node_modules/',
    '/scripts/utilities/',  # Utility scripts run on host, not in Docker
    '/tools/audit/',  # Audit tools run on host
    '/tools/cleanup/',  # Cleanup tools run on host
    'check_database_paths.py',  # This file itself (contains examples)
    'add_roadmap_tasks.py',  # One-time data seeding script
]

def should_skip(filepath: Path) -> bool:
    """Check if file should be skipped"""
    filepath_str = str(filepath)
    for pattern in SKIP_PATTERNS:
        if pattern in filepath_str:
            return True
    return False

def check_file(filepath: Path) -> List[Tuple[int, str, str]]:
    """
    Check a single file for database path violations
    
    Returns:
        List of (line_number, line_content, violation_reason)
    """
    violations = []
    
    try:
        content = filepath.read_text(encoding='utf-8')
        lines = content.split('\n')
        
        for i, line in enumerate(lines, 1):
            # Skip comments
            if line.strip().startswith('#'):
                continue
            
            # Skip print statements and docstrings (they contain examples)
            if 'print(' in line or '"""' in line or "'''" in line:
                continue
                
            # Check for forbidden patterns
            for pattern in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    # Check if this line also has an allowed pattern (e.g., in a comment)
                    has_allowed = any(re.search(allowed, line) for allowed in ALLOWED_PATTERNS)
                    if not has_allowed:
                        violations.append((i, line.strip(), pattern))
                        
    except Exception as e:
        print(f"⚠️  Error reading {filepath}: {e}")
        
    return violations

def check_project(root_dir: Path = None) -> bool:
    """
    Check entire project for database path violations
    
    Returns:
        True if all checks pass, False if violations found
    """
    if root_dir is None:
        root_dir = Path(__file__).parent.parent.parent
        
    print("=" * 70)
    print("🔍 DATABASE PATH ENFORCEMENT CHECK")
    print("=" * 70)
    print()
    print("Checking for hardcoded database paths...")
    print(f"Root: {root_dir}")
    print()
    
    all_violations = {}
    
    # Check all Python files
    for py_file in root_dir.rglob('*.py'):
        if should_skip(py_file):
            continue
            
        violations = check_file(py_file)
        if violations:
            all_violations[py_file] = violations
    
    # Report results
    if not all_violations:
        print("✅ DATABASE PATHS: All checks passed")
        print()
        print("All code properly uses DATABASE_PATH environment variable")
        print()
        return True
    else:
        print(f"❌ VIOLATIONS FOUND: {len(all_violations)} files with hardcoded paths")
        print()
        
        for filepath, violations in all_violations.items():
            rel_path = filepath.relative_to(root_dir)
            print(f"📄 {rel_path}")
            for line_num, line_content, pattern in violations:
                print(f"   Line {line_num}: {line_content[:80]}")
                print(f"   ⚠️  Violation: Hardcoded database path (use os.getenv('DATABASE_PATH'))")
            print()
        
        print("=" * 70)
        print("HOW TO FIX:")
        print("=" * 70)
        print()
        print("Replace hardcoded paths like:")
        print('  ❌ def __init__(self, db_path: str = "/home/pi/zoe/data/zoe.db"):')
        print()
        print("With environment variable:")
        print('  ✅ def __init__(self, db_path: str = None):')
        print('        if db_path is None:')
        print('            db_path = os.getenv("DATABASE_PATH", "/home/pi/zoe/data/zoe.db")')
        print()
        
        return False

def main():
    """Main entry point"""
    success = check_project()
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()

