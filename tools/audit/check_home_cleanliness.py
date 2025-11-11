#!/usr/bin/env python3
"""
Check Home Directory Cleanliness
================================

Validates that /home/pi only contains system files and the zoe/ directory.
Part of the enforcement system to prevent clutter.

Exit Codes:
  0 - /home/pi is clean
  1 - Violations found (files that should be in zoe/)
"""

from pathlib import Path

# Auto-detect project root (works for both Pi and Nano)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
import sys

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

# System files that are allowed in /home/pi
ALLOWED_FILES = {
    # Bash
    '.bash_history', '.bashrc', '.bash_logout', '.bash_profile',
    # Shell
    '.profile', '.zshrc', '.zprofile',
    # Git
    '.gitconfig', '.gitignore_global',
    # Editors
    '.viminfo', '.vimrc', '.nanorc', '.selected_editor',
    # System
    '.lesshst', '.wget-hsts', '.sudo_as_admin_successful',
    # Misc allowed
    'README.md',  # Symlink to zoe/README.md is OK
    'CHANGELOG.md',  # Symlink to zoe/CHANGELOG.md is OK
}

# Allowed directories
ALLOWED_DIRS = {
    'zoe',  # The project!
    # Hidden system dirs
    '.cache', '.config', '.ssh', '.local', '.gnupg',
    '.cloudflared', '.cursor-server', '.cursor',
    # System dirs (on some systems)
    'Desktop', 'Downloads', 'Documents', 'Pictures',
    # Other system
    'checkpoints', 'models', 'pironman5', 'pm_dashboard', 'bfg-1.14.0.jar'
}

def check_home_directory():
    """Check /home/pi for violations"""
    home = Path('/home/pi')
    violations = []
    warnings = []
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BLUE}🏠 HOME DIRECTORY CLEANLINESS CHECK{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
    
    print(f"Checking: {home}\n")
    
    # Check all items
    for item in home.iterdir():
        name = item.name
        
        # Skip if allowed
        if name in ALLOWED_FILES or name in ALLOWED_DIRS:
            continue
        
        # Check if hidden (system file)
        if name.startswith('.'):
            continue
        
        # Found a violation
        if item.is_file():
            violations.append((name, 'file', get_category(name)))
        elif item.is_dir():
            # Some dirs might be OK
            if name in ['scripts', 'config', 'services', 'tests', 'docs', 'data']:
                violations.append((name, 'directory', 'Should be inside zoe/'))
    
    # Report results
    if not violations:
        print(f"{Colors.GREEN}✅ /home/pi is clean!{Colors.RESET}")
        print(f"{Colors.GREEN}   Only system files and zoe/ directory present{Colors.RESET}")
        return True
    else:
        print(f"{Colors.RED}❌ Found {len(violations)} violations in /home/pi:{Colors.RESET}\n")
        
        for name, item_type, category in violations:
            print(f"{Colors.RED}  • {name} ({item_type}){Colors.RESET}")
            print(f"    → Should be in: PROJECT_ROOT / {category}")
        
        print(f"\n{Colors.YELLOW}Fix with:{Colors.RESET}")
        print(f"  cd {PROJECT_ROOT}")
        print(f"  python3 tools/cleanup/clean_home_directory.py")
        
        return False

def get_category(filename):
    """Determine where a file should go"""
    if filename.endswith('.py'):
        if filename.startswith('test_') or '_test' in filename:
            return 'tests/'
        elif any(filename.startswith(p) for p in ['fix_', 'create_', 'update_', 'migrate_']):
            return 'scripts/utilities/'
        else:
            return 'tools/ or scripts/'
    elif filename.endswith('.md'):
        if any(kw in filename.upper() for kw in ['STATUS', 'COMPLETE', 'REPORT', 'SUMMARY']):
            return 'docs/archive/reports/'
        elif any(kw in filename.upper() for kw in ['GUIDE', 'DOCUMENTATION', 'DEMO']):
            return 'docs/archive/technical/'
        else:
            return 'docs/'
    elif filename.endswith(('.conf', '.yml', '.yaml')):
        return 'config/'
    elif filename.endswith('.sh'):
        return 'scripts/utilities/'
    elif filename.endswith('.json'):
        if 'result' in filename or 'test' in filename:
            return 'tests/results/'
        else:
            return 'data/ or config/'
    else:
        return 'appropriate location'

def main():
    """Main check function"""
    clean = check_home_directory()
    
    print(f"\n{Colors.BLUE}{'='*70}{Colors.RESET}")
    if clean:
        print(f"{Colors.GREEN}✅ HOME DIRECTORY CHECK PASSED{Colors.RESET}")
    else:
        print(f"{Colors.RED}❌ HOME DIRECTORY CHECK FAILED{Colors.RESET}")
    print(f"{Colors.BLUE}{'='*70}{Colors.RESET}\n")
    
    return 0 if clean else 1

if __name__ == "__main__":
    sys.exit(main())

