#!/usr/bin/env python3
"""
Comprehensive Project Scanner
Scans EVERY file in the project and validates proper location

Usage:
    python3 tools/audit/comprehensive_scan.py
    python3 tools/audit/comprehensive_scan.py --fix  # Auto-move files to correct location
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

def should_ignore(path: Path) -> bool:
    """Check if path should be ignored."""
    ignore_patterns = [
        '.git', '__pycache__', '.pytest_cache', 'node_modules',
        '*.pyc', '*.pyo', '*.pyd', '.DS_Store'
    ]
    
    path_str = str(path)
    for pattern in ignore_patterns:
        if pattern in path_str:
            return True
    return False

def categorize_file(filepath: str) -> Tuple[str, str, str]:
    """
    Categorize a file and determine if it's in the right location.
    
    Returns: (category, current_location, correct_location)
    """
    parts = Path(filepath).parts
    filename = Path(filepath).name
    ext = Path(filepath).suffix
    
    # Root level files
    if len(parts) == 1:
        # Documentation
        if ext == '.md':
            if filename in ['README.md', 'CHANGELOG.md', 'QUICK-START.md', 
                           'PROJECT_STATUS.md', 'PROJECT_STRUCTURE_RULES.md']:
                return ('essential_docs', 'root', 'root')
            else:
                return ('documentation', 'root', 'docs/governance/')
        
        # Config files
        if filename in ['.cursorrules', '.dockerignore', '.gitignore', '.env', 
                       '.env.example', 'pytest.ini', 'docker-compose.yml',
                       'docker-compose.override.yml', 'docker-compose.mem-agent.yml']:
            return ('config', 'root', 'root')
        
        # Allowed scripts
        if filename in ['verify_updates.sh']:
            return ('allowed_script', 'root', 'root')
        
        # Allowed test
        if filename == 'test_architecture.py':
            return ('allowed_test', 'root', 'root')
        
        # Everything else in root is misplaced
        if ext == '.py':
            return ('python_script', 'root', 'scripts/utilities/')
        if ext == '.sh':
            return ('shell_script', 'root', 'scripts/utilities/')
        if ext == '.txt':
            return ('text_file', 'root', 'docs/developer/')
        if ext in ['.jar', '.log', '.sql', '.conf']:
            return ('misc_file', 'root', 'tools/cleanup/' if ext == '.jar' else 'config/')
        
        return ('unknown', 'root', 'UNKNOWN')
    
    # Files in subdirectories
    base_dir = parts[0]
    
    # Services - generally OK, but check for junk
    if base_dir == 'services':
        if '._' in filename or '_backup' in filename or '_old' in filename:
            return ('junk', filepath, 'DELETE')
        if ext in ['.log', '.tmp', '.cache']:
            return ('temp', filepath, 'DELETE')
        return ('service_file', filepath, filepath)
    
    # Tests - check proper categorization
    if base_dir == 'tests':
        # Pytest special files that belong in tests/
        if filename in ['conftest.py', '__init__.py']:
            return ('test_fixture', filepath, filepath)
        if ext == '.py' and not filename.startswith('test_'):
            return ('utility', filepath, 'scripts/utilities/')
        if ext == '.sh' and not filename.startswith('test_'):
            return ('test_script', filepath, 'scripts/utilities/')
        if ext == '.md':
            return ('test_docs', filepath, filepath)
        return ('test_file', filepath, filepath)
    
    # Scripts - check proper categorization
    if base_dir == 'scripts':
        if len(parts) == 2:  # Scripts directly in scripts/ without category
            if ext == '.py':
                return ('uncategorized_script', filepath, 'scripts/utilities/')
            if ext == '.sh':
                return ('uncategorized_script', filepath, 'scripts/utilities/')
        return ('script_file', filepath, filepath)
    
    # Tools - generally OK
    if base_dir == 'tools':
        if len(parts) == 2:  # Tools directly in tools/ without category
            return ('uncategorized_tool', filepath, 'tools/utilities/')
        return ('tool_file', filepath, filepath)
    
    # Docs - check for proper categorization
    if base_dir == 'docs':
        return ('documentation', filepath, filepath)
    
    # Data - OK
    if base_dir == 'data':
        return ('data_file', filepath, filepath)
    
    # Config - OK
    if base_dir == 'config':
        return ('config_file', filepath, filepath)
    
    # Everything else - OK or needs review
    return ('other', filepath, filepath)

def scan_project() -> Dict[str, List[Tuple[str, str, str]]]:
    """Scan entire project and categorize all files."""
    issues = {
        'misplaced_in_root': [],
        'uncategorized_scripts': [],
        'uncategorized_tools': [],
        'junk_files': [],
        'temp_files': [],
        'should_move': [],
        'ok': []
    }
    
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Skip ignored directories
        dirs[:] = [d for d in dirs if not should_ignore(Path(root) / d)]
        
        for filename in files:
            full_path = Path(root) / filename
            if should_ignore(full_path):
                continue
            
            relative_path = str(full_path.relative_to(PROJECT_ROOT))
            category, current, correct = categorize_file(relative_path)
            
            if current != correct:
                if correct == 'DELETE':
                    if category == 'junk':
                        issues['junk_files'].append((relative_path, category, correct))
                    else:
                        issues['temp_files'].append((relative_path, category, correct))
                elif current == 'root':
                    issues['misplaced_in_root'].append((relative_path, category, correct))
                elif category == 'uncategorized_script':
                    issues['uncategorized_scripts'].append((relative_path, category, correct))
                elif category == 'uncategorized_tool':
                    issues['uncategorized_tools'].append((relative_path, category, correct))
                else:
                    issues['should_move'].append((relative_path, category, correct))
            else:
                issues['ok'].append(relative_path)
    
    return issues

def print_results(issues: Dict[str, List[Tuple[str, str, str]]]) -> int:
    """Print scan results."""
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}COMPREHENSIVE PROJECT SCAN{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    
    total_files = sum(len(files) for files in issues.values())
    total_issues = total_files - len(issues['ok'])
    
    print(f"Total files scanned: {total_files}")
    print(f"{GREEN}Correctly placed: {len(issues['ok'])}{RESET}")
    print(f"{RED}Issues found: {total_issues}{RESET}\n")
    
    has_issues = False
    
    # Misplaced in root
    if issues['misplaced_in_root']:
        has_issues = True
        print(f"{RED}{'─'*80}{RESET}")
        print(f"{RED}❌ FILES MISPLACED IN ROOT ({len(issues['misplaced_in_root'])}){RESET}")
        print(f"{RED}{'─'*80}{RESET}")
        for filepath, category, correct in issues['misplaced_in_root']:
            print(f"  {RED}✗{RESET} {filepath}")
            print(f"    → Should be: {GREEN}{correct}{RESET}")
        print()
    
    # Uncategorized scripts
    if issues['uncategorized_scripts']:
        has_issues = True
        print(f"{YELLOW}{'─'*80}{RESET}")
        print(f"{YELLOW}⚠️  UNCATEGORIZED SCRIPTS ({len(issues['uncategorized_scripts'])}){RESET}")
        print(f"{YELLOW}{'─'*80}{RESET}")
        for filepath, category, correct in issues['uncategorized_scripts']:
            print(f"  {YELLOW}?{RESET} {filepath}")
            print(f"    → Should be: {GREEN}{correct}{RESET}")
        print()
    
    # Uncategorized tools
    if issues['uncategorized_tools']:
        has_issues = True
        print(f"{YELLOW}{'─'*80}{RESET}")
        print(f"{YELLOW}⚠️  UNCATEGORIZED TOOLS ({len(issues['uncategorized_tools'])}){RESET}")
        print(f"{YELLOW}{'─'*80}{RESET}")
        for filepath, category, correct in issues['uncategorized_tools']:
            print(f"  {YELLOW}?{RESET} {filepath}")
            print(f"    → Should be: {GREEN}{correct}{RESET}")
        print()
    
    # Junk files
    if issues['junk_files']:
        has_issues = True
        print(f"{RED}{'─'*80}{RESET}")
        print(f"{RED}🗑️  JUNK FILES TO DELETE ({len(issues['junk_files'])}){RESET}")
        print(f"{RED}{'─'*80}{RESET}")
        for filepath, category, correct in issues['junk_files'][:30]:
            print(f"  {RED}✗{RESET} {filepath}")
        if len(issues['junk_files']) > 30:
            print(f"  ... and {len(issues['junk_files']) - 30} more")
        print()
    
    # Temp files
    if issues['temp_files']:
        has_issues = True
        print(f"{YELLOW}{'─'*80}{RESET}")
        print(f"{YELLOW}🗑️  TEMP FILES TO DELETE ({len(issues['temp_files'])}){RESET}")
        print(f"{YELLOW}{'─'*80}{RESET}")
        for filepath, category, correct in issues['temp_files'][:20]:
            print(f"  {YELLOW}~{RESET} {filepath}")
        if len(issues['temp_files']) > 20:
            print(f"  ... and {len(issues['temp_files']) - 20} more")
        print()
    
    # Other moves
    if issues['should_move']:
        has_issues = True
        print(f"{YELLOW}{'─'*80}{RESET}")
        print(f"{YELLOW}⚠️  SHOULD RELOCATE ({len(issues['should_move'])}){RESET}")
        print(f"{YELLOW}{'─'*80}{RESET}")
        for filepath, category, correct in issues['should_move']:
            print(f"  {YELLOW}→{RESET} {filepath}")
            print(f"    → Should be: {GREEN}{correct}{RESET}")
        print()
    
    # Summary
    print(f"{BLUE}{'='*80}{RESET}")
    
    if not has_issues:
        print(f"{GREEN}✅ PERFECT PROJECT STRUCTURE{RESET}")
        print(f"{GREEN}All files are in the correct locations{RESET}\n")
        return 0
    else:
        print(f"{RED}❌ ISSUES FOUND{RESET}")
        if issues['misplaced_in_root']:
            print(f"{RED}• {len(issues['misplaced_in_root'])} file(s) misplaced in root{RESET}")
        if issues['uncategorized_scripts']:
            print(f"{YELLOW}• {len(issues['uncategorized_scripts'])} uncategorized script(s){RESET}")
        if issues['uncategorized_tools']:
            print(f"{YELLOW}• {len(issues['uncategorized_tools'])} uncategorized tool(s){RESET}")
        if issues['junk_files']:
            print(f"{RED}• {len(issues['junk_files'])} junk file(s) to delete{RESET}")
        if issues['temp_files']:
            print(f"{YELLOW}• {len(issues['temp_files'])} temp file(s) to delete{RESET}")
        
        print(f"\n{YELLOW}Run with --fix to automatically organize files{RESET}\n")
        return 1

def main():
    """Main scan function."""
    fix_mode = '--fix' in sys.argv
    
    print(f"\n{BLUE}Scanning entire project...{RESET}")
    print(f"{BLUE}This may take a moment...{RESET}\n")
    
    issues = scan_project()
    exit_code = print_results(issues)
    
    if fix_mode and exit_code != 0:
        print(f"{YELLOW}Auto-fix mode not yet implemented{RESET}")
        print(f"{YELLOW}Please review issues and move files manually{RESET}\n")
    
    return exit_code

if __name__ == '__main__':
    sys.exit(main())

