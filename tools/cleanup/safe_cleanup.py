#!/usr/bin/env python3
"""
Safe Cleanup Tool - Interactive cleanup assistant
Helps identify and remove orphan files safely

Usage:
    python3 tools/cleanup/safe_cleanup.py              # Dry run (default)
    python3 tools/cleanup/safe_cleanup.py --interactive # Interactive mode
    python3 tools/cleanup/safe_cleanup.py --execute     # Execute deletions
"""

import os
import sys
import json
from pathlib import Path
from typing import List, Dict
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
    with open(MANIFEST_PATH, 'r') as f:
        return json.load(f)

def matches_pattern(filepath: str, pattern: str) -> bool:
    """Check if filepath matches a glob pattern."""
    if '**' in pattern:
        return fnmatch.fnmatch(filepath, pattern) or fnmatch.fnmatch(filepath, pattern.replace('**/', ''))
    return fnmatch.fnmatch(filepath, pattern)

def is_safe_to_delete(filepath: str, manifest: Dict) -> bool:
    """Check if file matches safe-to-delete patterns."""
    for pattern in manifest['safe_to_delete_patterns']:
        if matches_pattern(filepath, pattern):
            return True
    return False

def get_safe_delete_files() -> List[str]:
    """Get all files matching safe-to-delete patterns."""
    manifest = load_manifest()
    safe_files = []
    
    for root, dirs, files in os.walk(PROJECT_ROOT):
        if '.git' in dirs:
            dirs.remove('.git')
        
        for filename in files:
            full_path = Path(root) / filename
            relative_path = str(full_path.relative_to(PROJECT_ROOT))
            
            if is_safe_to_delete(relative_path, manifest):
                safe_files.append(relative_path)
    
    return sorted(safe_files)

def calculate_size(files: List[str]) -> int:
    """Calculate total size of files in bytes."""
    total = 0
    for filepath in files:
        full_path = PROJECT_ROOT / filepath
        if full_path.exists():
            total += full_path.stat().st_size
    return total

def format_size(bytes: int) -> str:
    """Format bytes to human readable."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"

def delete_files(files: List[str], dry_run: bool = True) -> int:
    """Delete files."""
    deleted = 0
    failed = 0
    
    for filepath in files:
        full_path = PROJECT_ROOT / filepath
        try:
            if not dry_run:
                full_path.unlink()
            deleted += 1
            if not dry_run:
                print(f"{GREEN}✓{RESET} Deleted: {filepath}")
        except Exception as e:
            failed += 1
            print(f"{RED}✗{RESET} Failed to delete {filepath}: {e}")
    
    return deleted, failed

def main():
    """Main cleanup function."""
    interactive = '--interactive' in sys.argv or '-i' in sys.argv
    execute = '--execute' in sys.argv
    dry_run = not execute
    
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}SAFE CLEANUP TOOL{RESET}")
    print(f"{BLUE}{'='*80}{RESET}\n")
    
    if dry_run:
        print(f"{YELLOW}Running in DRY-RUN mode (no files will be deleted){RESET}")
    else:
        print(f"{RED}Running in EXECUTE mode (files WILL be deleted){RESET}")
    print("")
    
    # Get safe-to-delete files
    print(f"{BLUE}Scanning for safe-to-delete files...{RESET}")
    safe_files = get_safe_delete_files()
    total_size = calculate_size(safe_files)
    
    if not safe_files:
        print(f"\n{GREEN}✅ No files to clean up!{RESET}")
        print(f"{GREEN}Project is already clean.{RESET}\n")
        return 0
    
    # Group by category
    categories = {
        '__pycache__': [],
        '*.pyc': [],
        '._* (Mac)': [],
        '*.tmp': [],
        '*.cache': [],
        '*.log': [],
        'other': []
    }
    
    for filepath in safe_files:
        if '__pycache__' in filepath:
            categories['__pycache__'].append(filepath)
        elif filepath.endswith('.pyc'):
            categories['*.pyc'].append(filepath)
        elif '/._' in filepath or filepath.startswith('._'):
            categories['._* (Mac)'].append(filepath)
        elif filepath.endswith('.tmp'):
            categories['*.tmp'].append(filepath)
        elif filepath.endswith('.cache'):
            categories['*.cache'].append(filepath)
        elif filepath.endswith('.log'):
            categories['*.log'].append(filepath)
        else:
            categories['other'].append(filepath)
    
    # Display summary
    print(f"\n{BLUE}Found {len(safe_files)} files safe to delete ({format_size(total_size)}):{RESET}\n")
    
    for category, files in categories.items():
        if files:
            cat_size = calculate_size(files)
            print(f"{YELLOW}{category}:{RESET} {len(files)} files ({format_size(cat_size)})")
            if interactive or (not dry_run and len(files) <= 10):
                for f in files[:5]:
                    print(f"  • {f}")
                if len(files) > 5:
                    print(f"  ... and {len(files) - 5} more")
    
    print("")
    
    # Interactive mode
    if interactive:
        print(f"{YELLOW}Interactive mode: Review each category{RESET}\n")
        
        for category, files in categories.items():
            if not files:
                continue
            
            print(f"\n{BLUE}Category: {category} ({len(files)} files){RESET}")
            for f in files[:10]:
                print(f"  • {f}")
            if len(files) > 10:
                print(f"  ... and {len(files) - 10} more")
            
            response = input(f"\nDelete these files? [y/N]: ")
            if response.lower() == 'y':
                deleted, failed = delete_files(files, dry_run=False)
                print(f"{GREEN}Deleted {deleted} files{RESET}")
                if failed:
                    print(f"{RED}Failed to delete {failed} files{RESET}")
        
        return 0
    
    # Dry run or execute mode
    if dry_run:
        print(f"{YELLOW}To delete these files, run with --execute flag:{RESET}")
        print(f"  python3 tools/cleanup/safe_cleanup.py --execute\n")
        print(f"{YELLOW}Or use --interactive to review each category:{RESET}")
        print(f"  python3 tools/cleanup/safe_cleanup.py --interactive\n")
        return 0
    
    else:
        # Execute deletion
        print(f"{RED}Deleting {len(safe_files)} files...{RESET}\n")
        deleted, failed = delete_files(safe_files, dry_run=False)
        
        print(f"\n{BLUE}{'='*80}{RESET}")
        print(f"{GREEN}✅ Deleted {deleted} files ({format_size(total_size)}){RESET}")
        if failed:
            print(f"{RED}Failed to delete {failed} files{RESET}")
        print(f"{BLUE}{'='*80}{RESET}\n")
        
        return 0

if __name__ == '__main__':
    sys.exit(main())









