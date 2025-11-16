#!/usr/bin/env python3
"""
Structure Validator - Validates project files against manifest
Ensures all files are approved and no junk files exist

Usage:
    python3 tools/audit/validate_structure.py
    python3 tools/audit/validate_structure.py --verbose
"""

import os
import sys
import json
from pathlib import Path
from typing import List, Dict, Set, Tuple
import fnmatch

# Color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
MANIFEST_PATH = PROJECT_ROOT / '.zoe' / 'manifest.json'

def load_manifest() -> Dict:
    """Load the manifest file."""
    if not MANIFEST_PATH.exists():
        print(f"{RED}ERROR: Manifest not found at {MANIFEST_PATH}{RESET}")
        sys.exit(1)
    
    with open(MANIFEST_PATH, 'r') as f:
        return json.load(f)

def get_all_project_files() -> Set[str]:
    """Get all files in the project (excluding .git)."""
    files = set()
    
    for root, dirs, filenames in os.walk(PROJECT_ROOT):
        # Skip .git directory
        if '.git' in dirs:
            dirs.remove('.git')
        
        for filename in filenames:
            full_path = Path(root) / filename
            relative_path = str(full_path.relative_to(PROJECT_ROOT))
            files.add(relative_path)
    
    return files

def matches_pattern(filepath: str, pattern: str) -> bool:
    """Check if filepath matches a glob pattern."""
    # Handle ** patterns
    if '**' in pattern:
        return fnmatch.fnmatch(filepath, pattern) or fnmatch.fnmatch(filepath, pattern.replace('**/', ''))
    return fnmatch.fnmatch(filepath, pattern)

def is_critical_file(filepath: str, manifest: Dict) -> bool:
    """Check if file is in critical files list."""
    for category, files in manifest['critical_files'].items():
        if filepath in files:
            return True
    return False

def matches_approved_pattern(filepath: str, manifest: Dict) -> bool:
    """Check if file matches any approved pattern."""
    for pattern_name, pattern in manifest['approved_patterns'].items():
        if matches_pattern(filepath, pattern):
            return True
    return False

def is_approved_root_file(filepath: str, manifest: Dict) -> bool:
    """Check if file is in approved root files."""
    return filepath in manifest['approved_root_files']

def matches_safe_to_delete(filepath: str, manifest: Dict) -> bool:
    """Check if file matches safe-to-delete patterns."""
    for pattern in manifest['safe_to_delete_patterns']:
        if matches_pattern(filepath, pattern):
            return True
    return False

def is_prohibited_in_root(filepath: str, manifest: Dict) -> bool:
    """Check if file is prohibited in root."""
    # Only check files directly in root
    if '/' in filepath:
        return False
    
    for pattern in manifest['prohibited_in_root']:
        if matches_pattern(filepath, pattern):
            return True
    return False

def categorize_files(all_files: Set[str], manifest: Dict) -> Dict[str, List[str]]:
    """Categorize all files."""
    categories = {
        'critical': [],
        'approved': [],
        'safe_to_delete': [],
        'prohibited': [],
        'orphan': []
    }
    
    for filepath in sorted(all_files):
        if is_critical_file(filepath, manifest):
            categories['critical'].append(filepath)
        elif is_approved_root_file(filepath, manifest) or matches_approved_pattern(filepath, manifest):
            categories['approved'].append(filepath)
        elif matches_safe_to_delete(filepath, manifest):
            categories['safe_to_delete'].append(filepath)
        elif is_prohibited_in_root(filepath, manifest):
            categories['prohibited'].append(filepath)
        else:
            categories['orphan'].append(filepath)
    
    return categories

def check_root_md_limit() -> Tuple[bool, int, List[str]]:
    """Check if root has max 10 .md files."""
    md_files = []
    for f in PROJECT_ROOT.iterdir():
        if f.is_file() and f.suffix == '.md':
            md_files.append(f.name)
    
    return len(md_files) <= 10, len(md_files), md_files

def print_results(categories: Dict[str, List[str]], verbose: bool = False):
    """Print validation results."""
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}PROJECT STRUCTURE VALIDATION{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    
    # Summary
    total = sum(len(files) for files in categories.values())
    print(f"Total files analyzed: {total}")
    print(f"{GREEN}Critical files: {len(categories['critical'])}{RESET}")
    print(f"{GREEN}Approved files: {len(categories['approved'])}{RESET}")
    print(f"{YELLOW}Safe to delete: {len(categories['safe_to_delete'])}{RESET}")
    print(f"{RED}Prohibited in root: {len(categories['prohibited'])}{RESET}")
    print(f"{RED}Orphan files: {len(categories['orphan'])}{RESET}\n")
    
    # Check root .md limit
    md_ok, md_count, md_files = check_root_md_limit()
    if md_ok:
        print(f"{GREEN}✓ Root .md files: {md_count}/10{RESET}")
    else:
        print(f"{RED}✗ Root .md files: {md_count}/10 (EXCEEDS LIMIT){RESET}")
    
    # Show problems
    has_issues = False
    
    if categories['prohibited']:
        has_issues = True
        print(f"\n{RED}{'─'*80}{RESET}")
        print(f"{RED}❌ PROHIBITED FILES IN ROOT ({len(categories['prohibited'])}){RESET}")
        print(f"{RED}{'─'*80}{RESET}")
        for filepath in categories['prohibited'][:20]:
            print(f"  {RED}✗{RESET} {filepath}")
        if len(categories['prohibited']) > 20:
            print(f"  ... and {len(categories['prohibited']) - 20} more")
    
    if categories['orphan']:
        has_issues = True
        print(f"\n{YELLOW}{'─'*80}{RESET}")
        print(f"{YELLOW}⚠️  ORPHAN FILES (not in manifest) ({len(categories['orphan'])}){RESET}")
        print(f"{YELLOW}{'─'*80}{RESET}")
        for filepath in categories['orphan'][:30]:
            print(f"  {YELLOW}?{RESET} {filepath}")
        if len(categories['orphan']) > 30:
            print(f"  ... and {len(categories['orphan']) - 30} more")
    
    if categories['safe_to_delete'] and verbose:
        print(f"\n{YELLOW}{'─'*80}{RESET}")
        print(f"{YELLOW}🗑️  SAFE TO DELETE ({len(categories['safe_to_delete'])}){RESET}")
        print(f"{YELLOW}{'─'*80}{RESET}")
        for filepath in categories['safe_to_delete'][:20]:
            print(f"  {YELLOW}~{RESET} {filepath}")
        if len(categories['safe_to_delete']) > 20:
            print(f"  ... and {len(categories['safe_to_delete']) - 20} more")
    
    # Final verdict
    print(f"\n{BLUE}{'='*80}{RESET}")
    
    if not has_issues and md_ok:
        print(f"{GREEN}✅ VALIDATION PASSED{RESET}")
        print(f"{GREEN}Project structure is clean{RESET}\n")
        return 0
    else:
        print(f"{RED}❌ VALIDATION FAILED{RESET}")
        if categories['prohibited']:
            print(f"{RED}• {len(categories['prohibited'])} prohibited file(s) in root{RESET}")
        if categories['orphan']:
            print(f"{YELLOW}• {len(categories['orphan'])} orphan file(s) not in manifest{RESET}")
        if not md_ok:
            print(f"{RED}• Too many .md files in root ({md_count}/10){RESET}")
        print(f"\n{YELLOW}Run with safe_cleanup.py to fix issues{RESET}\n")
        return 1

def main():
    """Main validation function."""
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    
    print(f"\n{BLUE}Loading manifest...{RESET}")
    manifest = load_manifest()
    
    print(f"{BLUE}Scanning project files...{RESET}")
    all_files = get_all_project_files()
    
    print(f"{BLUE}Categorizing files...{RESET}")
    categories = categorize_files(all_files, manifest)
    
    exit_code = print_results(categories, verbose)
    
    return exit_code

if __name__ == '__main__':
    sys.exit(main())

