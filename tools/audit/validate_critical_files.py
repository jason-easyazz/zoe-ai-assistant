#!/usr/bin/env python3
"""
Critical Files Validator
Ensures essential files exist before cleanup operations

Usage:
    python3 tools/audit/validate_critical_files.py
    python3 tools/audit/validate_critical_files.py --fix  # Create missing critical files report
"""

import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple

# Color codes for terminal output
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Define critical files that must NEVER be deleted
CRITICAL_FILES = {
    'css': [
        'services/zoe-ui/dist/css/glass.css',
        'services/zoe-ui/dist/css/memories-enhanced.css',
    ],
    'core_js': [
        'services/zoe-ui/dist/js/auth.js',
        'services/zoe-ui/dist/js/common.js',
        'services/zoe-ui/dist/js/widget-system.js',
        'services/zoe-ui/dist/js/widget-base.js',
    ],
    'important_js': [
        'services/zoe-ui/dist/js/ai-processor.js',
        'services/zoe-ui/dist/js/zoe-orb.js',
        'services/zoe-ui/dist/js/journal-api.js',
        'services/zoe-ui/dist/js/journal-ui-enhancements.js',
        'services/zoe-ui/dist/js/memory-graph.js',
        'services/zoe-ui/dist/js/memory-search.js',
        'services/zoe-ui/dist/js/memory-timeline.js',
        'services/zoe-ui/dist/js/settings.js',
        'services/zoe-ui/dist/js/wikilink-parser.js',
    ],
    'widgets': [
        'services/zoe-ui/dist/js/widgets/core/events.js',
        'services/zoe-ui/dist/js/widgets/core/tasks.js',
        'services/zoe-ui/dist/js/widgets/core/time.js',
        'services/zoe-ui/dist/js/widgets/core/weather.js',
        'services/zoe-ui/dist/js/widgets/core/home.js',
        'services/zoe-ui/dist/js/widgets/core/system.js',
        'services/zoe-ui/dist/js/widgets/core/notes.js',
        'services/zoe-ui/dist/js/widgets/core/zoe-orb.js',
    ],
    'components': [
        'services/zoe-ui/dist/components/zoe-orb.html',
        'services/zoe-ui/dist/components/zoe-orb-complete.html',
    ],
    'html_pages': [
        'services/zoe-ui/dist/index.html',
        'services/zoe-ui/dist/auth.html',
        'services/zoe-ui/dist/chat.html',
        'services/zoe-ui/dist/dashboard.html',
        'services/zoe-ui/dist/calendar.html',
        'services/zoe-ui/dist/lists.html',
        'services/zoe-ui/dist/journal.html',
        'services/zoe-ui/dist/memories.html',
        'services/zoe-ui/dist/settings.html',
    ],
    'backend': [
        'services/zoe-core/main.py',
        'services/zoe-core/routers/chat.py',
        'services/zoe-core/ai_client.py',
        'services/zoe-auth/main.py',
    ],
    'config': [
        'docker-compose.yml',
        'services/zoe-ui/nginx.conf',
    ]
}

def check_file_exists(filepath: str) -> bool:
    """Check if a file exists relative to project root."""
    full_path = PROJECT_ROOT / filepath
    return full_path.exists()

def validate_critical_files() -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """
    Validate all critical files exist.
    
    Returns:
        Tuple of (existing_files, missing_files) dictionaries
    """
    existing = {}
    missing = {}
    
    for category, files in CRITICAL_FILES.items():
        existing[category] = []
        missing[category] = []
        
        for filepath in files:
            if check_file_exists(filepath):
                existing[category].append(filepath)
            else:
                missing[category].append(filepath)
    
    return existing, missing

def print_results(existing: Dict[str, List[str]], missing: Dict[str, List[str]]) -> int:
    """
    Print validation results.
    
    Returns:
        Exit code (0 if all files exist, 1 if any missing)
    """
    total_files = sum(len(files) for files in CRITICAL_FILES.values())
    total_existing = sum(len(files) for files in existing.values())
    total_missing = sum(len(files) for files in missing.values())
    
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}CRITICAL FILES VALIDATION{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    
    print(f"Total critical files: {total_files}")
    print(f"{GREEN}Existing: {total_existing}{RESET}")
    print(f"{RED}Missing: {total_missing}{RESET}\n")
    
    # Print detailed results by category
    has_missing = False
    
    for category in CRITICAL_FILES.keys():
        category_name = category.replace('_', ' ').title()
        print(f"{YELLOW}━━━ {category_name} ━━━{RESET}")
        
        # Show existing files
        if existing[category]:
            print(f"{GREEN}✓ Existing ({len(existing[category])}):{RESET}")
            for filepath in sorted(existing[category]):
                print(f"  {GREEN}✓{RESET} {filepath}")
        
        # Show missing files
        if missing[category]:
            has_missing = True
            print(f"{RED}✗ MISSING ({len(missing[category])}):{RESET}")
            for filepath in sorted(missing[category]):
                print(f"  {RED}✗{RESET} {filepath}")
        
        print()
    
    # Print summary
    print(f"{BLUE}{'='*80}{RESET}")
    
    if has_missing:
        print(f"\n{RED}❌ VALIDATION FAILED{RESET}")
        print(f"{RED}Missing {total_missing} critical file(s){RESET}\n")
        print(f"{YELLOW}⚠️  DO NOT PROCEED WITH CLEANUP OPERATIONS{RESET}")
        print(f"{YELLOW}⚠️  Restore missing files before continuing{RESET}\n")
        print("Recovery command:")
        print(f"  {BLUE}git log --all --full-history -- <filepath>{RESET}")
        print(f"  {BLUE}git show <commit>:<filepath> > <filepath>{RESET}\n")
        return 1
    else:
        print(f"\n{GREEN}✅ VALIDATION PASSED{RESET}")
        print(f"{GREEN}All {total_existing} critical files present{RESET}\n")
        print(f"{YELLOW}⚠️  Before cleanup:{RESET}")
        print(f"  1. Create safety commit: {BLUE}git commit -am 'Pre-cleanup safety'{RESET}")
        print(f"  2. Work in feature branch: {BLUE}git checkout -b cleanup-$(date +%Y%m%d){RESET}")
        print(f"  3. Delete files incrementally (5-10 at a time)")
        print(f"  4. Test after each deletion")
        print(f"  5. Run this validator again after cleanup\n")
        return 0

def check_for_dangerous_patterns(directory: Path = PROJECT_ROOT) -> List[str]:
    """
    Check for dangerous file patterns that indicate backups/duplicates.
    
    Returns:
        List of potentially dangerous files
    """
    dangerous_patterns = [
        '*_backup.*',
        '*_old.*',
        '*_v2.*',
        '*_v3.*',
        '*_new.*',
        '*_fixed.*',
        '*_temp.*',
        '*.bak',
    ]
    
    dangerous_files = []
    
    # Only check in services directory to avoid false positives
    services_dir = directory / 'services'
    if not services_dir.exists():
        return dangerous_files
    
    for pattern in dangerous_patterns:
        for filepath in services_dir.rglob(pattern):
            # Skip if in git directory
            if '.git' in str(filepath):
                continue
            dangerous_files.append(str(filepath.relative_to(directory)))
    
    return dangerous_files

def main():
    """Main validation function."""
    print(f"\n{BLUE}Starting critical files validation...{RESET}\n")
    
    # Validate critical files
    existing, missing = validate_critical_files()
    exit_code = print_results(existing, missing)
    
    # Check for dangerous file patterns
    dangerous_files = check_for_dangerous_patterns()
    
    if dangerous_files:
        print(f"{YELLOW}{'='*80}{RESET}")
        print(f"{YELLOW}⚠️  DANGEROUS FILE PATTERNS DETECTED{RESET}")
        print(f"{YELLOW}{'='*80}{RESET}\n")
        print(f"Found {len(dangerous_files)} files with backup/duplicate patterns:\n")
        
        for filepath in sorted(dangerous_files)[:20]:  # Show first 20
            print(f"  {YELLOW}⚠{RESET} {filepath}")
        
        if len(dangerous_files) > 20:
            print(f"\n  ... and {len(dangerous_files) - 20} more\n")
        
        print(f"\n{YELLOW}These files may indicate:")
        print(f"  • Unnecessary duplicates")
        print(f"  • Old backup files")
        print(f"  • Files that should be in git history instead")
        print(f"\n⚠️  Review these files before deletion{RESET}\n")
    
    # Final recommendations
    if exit_code == 0:
        print(f"{GREEN}{'='*80}{RESET}")
        print(f"{GREEN}SYSTEM READY FOR CLEANUP OPERATIONS{RESET}")
        print(f"{GREEN}{'='*80}{RESET}\n")
    
    return exit_code

if __name__ == '__main__':
    sys.exit(main())

