#!/usr/bin/env python3
"""
Authentication Security Audit
==============================

Checks all FastAPI routers for proper authentication enforcement.
Prevents the use of insecure patterns like Query("default") or Query(None).

Exit codes:
    0 - All checks passed
    1 - Authentication issues found
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Colors for terminal output
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
BLUE = '\033[94m'
RESET = '\033[0m'

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
ROUTERS_DIR = PROJECT_ROOT / "services/zoe-core/routers"

# Patterns that indicate insecure authentication
INSECURE_PATTERNS = [
    (r'user_id:\s*str\s*=\s*Query\("default"', 'Query("default") - hardcoded default user'),
    (r'user_id\s*=\s*Query\("default"', 'Query("default") - hardcoded default user'),
    (r'user_id:\s*str\s*=\s*Query\(None.*\),(?!\s*session)', 'Query(None) without session authentication'),
]

# Files that are exceptions (with justification)
EXCEPTIONS = {
    'public_memories.py': 'Marked as deprecated, kept for legacy compatibility',
    'auth.py': 'Authentication router itself',
    'health.py': 'Health check endpoint - no user data',
}

def check_file(filepath: Path) -> List[Tuple[int, str, str]]:
    """
    Check a single router file for authentication issues.
    Returns list of (line_number, pattern_description, line_content)
    """
    issues = []
    
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        for line_num, line in enumerate(lines, 1):
            for pattern, description in INSECURE_PATTERNS:
                if re.search(pattern, line):
                    issues.append((line_num, description, line.strip()))
    
    except Exception as e:
        print(f"{YELLOW}⚠️  Could not read {filepath.name}: {e}{RESET}")
    
    return issues

def main():
    print(f"{BLUE}{'=' * 80}{RESET}")
    print(f"{BLUE}🔒 AUTHENTICATION SECURITY AUDIT{RESET}")
    print(f"{BLUE}{'=' * 80}{RESET}\n")
    
    if not ROUTERS_DIR.exists():
        print(f"{RED}❌ Routers directory not found: {ROUTERS_DIR}{RESET}")
        return 1
    
    all_issues = {}
    total_issues = 0
    
    # Check all Python files in routers directory
    for router_file in sorted(ROUTERS_DIR.glob('*.py')):
        if router_file.name.startswith('__'):
            continue
        
        # Check if this file is an exception
        if router_file.name in EXCEPTIONS:
            print(f"{YELLOW}⚠️  {router_file.name:30s} - EXCEPTION: {EXCEPTIONS[router_file.name]}{RESET}")
            continue
        
        issues = check_file(router_file)
        
        if issues:
            all_issues[router_file.name] = issues
            total_issues += len(issues)
            print(f"{RED}❌ {router_file.name:30s} - {len(issues)} issue(s){RESET}")
        else:
            print(f"{GREEN}✅ {router_file.name:30s} - Secure{RESET}")
    
    print(f"\n{BLUE}{'=' * 80}{RESET}")
    
    if total_issues == 0:
        print(f"{GREEN}✅ All routers pass authentication security checks!{RESET}")
        print(f"{BLUE}{'=' * 80}{RESET}\n")
        return 0
    else:
        print(f"{RED}❌ Found {total_issues} authentication issue(s) in {len(all_issues)} file(s){RESET}")
        print(f"{BLUE}{'=' * 80}{RESET}\n")
        
        # Show details
        for filename, issues in all_issues.items():
            print(f"{RED}📄 {filename}{RESET}")
            for line_num, description, line_content in issues:
                print(f"   Line {line_num}: {description}")
                print(f"   {YELLOW}{line_content}{RESET}\n")
        
        print(f"{BLUE}{'=' * 80}{RESET}")
        print(f"{YELLOW}🔧 How to fix:{RESET}")
        print(f"   1. Replace Query(\"default\") with: session: AuthenticatedSession = Depends(validate_session)")
        print(f"   2. Add 'user_id = session.user_id' at the start of the function")
        print(f"   3. Remove any user_id Query parameters when session exists")
        print(f"   4. Run: python3 {PROJECT_ROOT}/scripts/utilities/fix_user_isolation.py")
        print(f"{BLUE}{'=' * 80}{RESET}\n")
        
        return 1

if __name__ == '__main__':
    sys.exit(main())

