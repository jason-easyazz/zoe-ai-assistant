#!/usr/bin/env python3
"""
Database Path Enforcement Checker
Ensures all code uses DATABASE_PATH environment variable instead of hardcoded paths
Created: 2025-10-26 after user data loss incident
"""

import os
import re
import sys
from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Hardcoded path patterns to detect
# Note: /app/data/ is the standard Docker path and is allowed as a default
FORBIDDEN_PATTERNS = [
    r'PROJECT_ROOT / "data/.*\.db"',  # Hardcoded host paths (should use /app/data/)
    r"PROJECT_ROOT / 'data/.*\.db'",
]

# Allowed patterns (using environment variables)
ALLOWED_PATTERNS = [
    r'os\.getenv\(["\']DATABASE_PATH["\']',
    r'os\.environ\[["\']DATABASE_PATH["\']\]',
    r'DATABASE_PATH\s*=\s*os\.getenv',
]

def check_file(file_path: Path) -> list:
    """Check a single file for hardcoded database paths"""
    violations = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
            
            for i, line in enumerate(lines, 1):
                # Skip comments
                if line.strip().startswith('#'):
                    continue
                
                # Check for forbidden patterns
                for pattern in FORBIDDEN_PATTERNS:
                    if re.search(pattern, line):
                        # Check if this line also has an allowed pattern (might be in a comment)
                        has_allowed = any(re.search(allowed, line) for allowed in ALLOWED_PATTERNS)
                        if not has_allowed:
                            violations.append({
                                'file': str(file_path),
                                'line': i,
                                'content': line.strip(),
                                'pattern': pattern
                            })
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}")
    
    return violations

def main():
    """Main execution"""
    print("=" * 70)
    print("🔍 DATABASE PATH ENFORCEMENT CHECK")
    print("=" * 70)
    print()
    
    root = PROJECT_ROOT
    print(f"Checking for hardcoded database paths...")
    print(f"Root: {root}")
    print()
    
    # Directories to check (excluding utility scripts that run on host)
    check_dirs = [
        root / "services",
    ]
    
    all_violations = []
    
    for check_dir in check_dirs:
        if not check_dir.exists():
            continue
            
        # Find all Python files
        for py_file in check_dir.rglob("*.py"):
            violations = check_file(py_file)
            if violations:
                all_violations.extend(violations)
    
    if all_violations:
        print("❌ DATABASE PATH VIOLATIONS FOUND:")
        print()
        
        for v in all_violations:
            print(f"  File: {v['file']}")
            print(f"  Line {v['line']}: {v['content']}")
            print()
        
        print("=" * 70)
        print("FIX: Replace hardcoded paths with:")
        print()
        print("  import os")
        print("  db_path = os.getenv('DATABASE_PATH', '/app/data/zoe.db')")
        print()
        print("This ensures the code works in both development and Docker environments")
        print("=" * 70)
        
        sys.exit(1)
    else:
        print("✅ DATABASE PATHS: All checks passed")
        print()
        print("All code properly uses DATABASE_PATH environment variable")
        print()
        sys.exit(0)

if __name__ == "__main__":
    main()
